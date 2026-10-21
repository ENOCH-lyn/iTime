from typing import Annotated, Literal
from uuid import UUID

import pyotp
from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlalchemy import delete, select, update

from itime.admin.models import (
    AdminCredential,
    AdminEnrollment,
    AdminRecoveryCode,
    AdminSession,
)
from itime.admin.operations import _new_enrollment
from itime.admin.security import (
    ADMIN_COOKIE_NAME,
    RECOVERY_CODE_COUNT,
    TOTP_ISSUER,
    admin_session_result,
    authorized_admin,
    create_admin_session,
    create_recovery_codes,
    delete_admin_cookie,
    recovery_digest,
    set_admin_cookie,
    verify_totp,
)
from itime.auth.models import DeviceSession, EmailToken, User
from itime.auth.security import digest, now, rate_limit
from itime.auth.session import audit
from itime.errors import AppError

router = APIRouter(prefix="/api/v1/admin/auth", tags=["管理认证"])


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


Password = Annotated[SecretStr, Field(min_length=12, max_length=128)]
EnrollmentToken = Annotated[SecretStr, Field(min_length=43, max_length=43)]
AuthCode = Annotated[SecretStr, Field(min_length=6, max_length=32)]


class SetupInput(Input):
    token: EnrollmentToken
    password: Password


class SetupConfirmInput(SetupInput):
    code: Annotated[SecretStr, Field(min_length=6, max_length=6)]
    device_name: str = Field(default="管理网页", min_length=1, max_length=100)


class AdminLoginInput(Input):
    email: str = Field(min_length=3, max_length=254)
    password: Password
    method: Literal["password", "totp", "recovery"] = "totp"
    code: AuthCode | None = None
    device_name: str = Field(default="管理网页", min_length=1, max_length=100)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value):
        return value.strip().lower() if isinstance(value, str) else value


class RotateRecoveryInput(Input):
    code: Annotated[SecretStr, Field(min_length=6, max_length=6)]


class ChangePasswordInput(Input):
    current_password: Password
    new_password: Password


def invalid_admin_credentials() -> AppError:
    return AppError("invalid_admin_credentials", "邮箱、密码或验证码不正确。", status=401)


def invalid_enrollment() -> AppError:
    return AppError(
        "invalid_admin_enrollment",
        "管理员设置链接已过期、已使用或已被新链接替换，请在服务器重新生成。",
        status=400,
    )


def check_admin(user: User | None) -> None:
    if (
        user is None
        or not user.is_admin
        or user.status != "active"
        or user.email_verified_at is None
    ):
        raise invalid_admin_credentials()


async def enrollment_snapshot(request: Request, raw: str) -> tuple[UUID, str]:
    async with request.app.state.database.transaction() as session:
        row = (
            await session.execute(
                select(User.id, User.password_hash)
                .join(AdminEnrollment, AdminEnrollment.user_id == User.id)
                .where(
                    AdminEnrollment.token_hash == digest(raw),
                    AdminEnrollment.expires_at > now(),
                    User.is_admin.is_(True),
                )
            )
        ).first()
        if row is None:
            raise invalid_enrollment()
        return row.id, row.password_hash


async def locked_enrollment(session, raw: str, user_id: UUID) -> AdminEnrollment:
    enrollment = await session.scalar(
        select(AdminEnrollment).where(
            AdminEnrollment.user_id == user_id,
            AdminEnrollment.token_hash == digest(raw),
            AdminEnrollment.expires_at > now(),
        ).with_for_update()
    )
    if enrollment is None:
        raise invalid_enrollment()
    return enrollment


def decrypt_secret(request: Request, ciphertext: bytes) -> str:
    try:
        return request.app.state.security.cipher.decrypt(ciphertext).decode()
    except InvalidToken as exc:
        raise AppError(
            "admin_secret_unavailable",
            "管理密钥暂时无法读取，请检查服务端加密配置后重试。",
            status=503,
            retryable=True,
        ) from exc


@router.get("/policy")
async def policy(request: Request) -> dict:
    settings = request.app.state.settings
    return {
        "session_hours": settings.admin_session_hours,
        "setup_minutes": settings.admin_setup_minutes,
        "recovery_code_count": RECOVERY_CODE_COUNT,
    }


@router.post("/enrollment")
async def create_manual_enrollment(request: Request) -> dict:
    async with authorized_admin(request) as identity:
        user_id = identity.user.id
    async with request.app.state.database.transaction() as session:
        user = await session.scalar(
            select(User).where(User.id == user_id).with_for_update()
        )
        check_admin(user)
        raw = await _new_enrollment(session, user, request.app.state.settings)
    return {
        "setup_url": (
            f"{request.app.state.settings.public_origin}/admin/setup#token={raw}"
        )
    }


@router.post("/setup")
async def setup(data: SetupInput, request: Request) -> dict:
    raw = data.token.get_secret_value()
    await rate_limit(request, "admin-setup-token")
    user_id, encoded = await enrollment_snapshot(request, raw)
    await rate_limit(request, "admin-setup-user", str(user_id))
    if not await request.app.state.security.verify_password(
        data.password.get_secret_value(), encoded
    ):
        raise invalid_admin_credentials()
    async with request.app.state.database.transaction() as session:
        user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
        check_admin(user)
        if user.password_hash != encoded:
            raise invalid_enrollment()
        enrollment = await locked_enrollment(session, raw, user_id)
        if enrollment.secret_ciphertext is None:
            secret = pyotp.random_base32(length=32)
            enrollment.secret_ciphertext = request.app.state.security.cipher.encrypt(
                secret.encode()
            )
            audit(request, session, user, "admin_totp_setup_started")
        else:
            secret = decrypt_secret(request, enrollment.secret_ciphertext)
        return {
            "email": user.email,
            "secret": secret,
            "provisioning_uri": pyotp.TOTP(secret).provisioning_uri(
                name=user.email, issuer_name=TOTP_ISSUER
            ),
            "expires_at": enrollment.expires_at,
        }


@router.post("/setup/confirm")
async def confirm_setup(
    data: SetupConfirmInput, request: Request, response: Response
) -> dict:
    raw_token = data.token.get_secret_value()
    await rate_limit(request, "admin-setup-confirm-token")
    user_id, encoded = await enrollment_snapshot(request, raw_token)
    await rate_limit(request, "admin-setup-confirm-user", str(user_id))
    if not await request.app.state.security.verify_password(
        data.password.get_secret_value(), encoded
    ):
        raise invalid_admin_credentials()
    recovery = create_recovery_codes(request)
    async with request.app.state.database.transaction() as session:
        user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
        check_admin(user)
        if user.password_hash != encoded:
            raise invalid_enrollment()
        enrollment = await locked_enrollment(session, raw_token, user_id)
        if enrollment.secret_ciphertext is None:
            raise AppError("admin_setup_not_started", "请先扫描二维码，再确认验证码。")
        secret = decrypt_secret(request, enrollment.secret_ciphertext)
        counter = verify_totp(secret, data.code.get_secret_value(), None)
        if counter is None:
            raise AppError("invalid_admin_code", "验证码不正确，请等待新验证码后重试。")
        session.add(
            AdminCredential(
                user_id=user.id,
                totp_secret_ciphertext=enrollment.secret_ciphertext,
                totp_enabled_at=now(),
                last_totp_counter=counter,
            )
        )
        await session.execute(
            delete(AdminRecoveryCode).where(AdminRecoveryCode.user_id == user.id)
        )
        session.add_all(
            AdminRecoveryCode(user_id=user.id, code_hash=code_hash)
            for _, code_hash in recovery
        )
        await session.delete(enrollment)
        device, raw_session = await create_admin_session(
            request, session, user, data.device_name, "totp"
        )
        audit(request, session, user, "admin_totp_enabled", device.id)
        result = admin_session_result(request, user, device, raw_session)
        result["recovery_codes"] = [code for code, _ in recovery]
    set_admin_cookie(request, response, raw_session)
    return result


@router.post("/login")
async def login(data: AdminLoginInput, request: Request, response: Response) -> dict:
    await rate_limit(request, "admin-login", data.email)
    async with request.app.state.database.transaction() as session:
        encoded = await session.scalar(select(User.password_hash).where(User.email == data.email))
    if not await request.app.state.security.verify_password(
        data.password.get_secret_value(), encoded
    ):
        raise invalid_admin_credentials()
    async with request.app.state.database.transaction() as session:
        user = await session.scalar(select(User).where(User.email == data.email).with_for_update())
        check_admin(user)
        if user.password_hash != encoded:
            raise invalid_admin_credentials()
        credential = await session.scalar(
            select(AdminCredential)
            .where(AdminCredential.user_id == user.id)
            .with_for_update()
        )
        if credential is None:
            device, raw = await create_admin_session(
                request, session, user, data.device_name, "password"
            )
            audit(request, session, user, "admin_logged_in_password", device.id)
            result = admin_session_result(request, user, device, raw)
            set_admin_cookie(request, response, raw)
            return result
        if data.code is None:
            raise AppError(
                "admin_totp_required",
                "此管理员已开启双重验证，请输入身份验证器验证码。",
                status=403,
            )
        if data.method == "totp":
            secret = decrypt_secret(request, credential.totp_secret_ciphertext)
            counter = verify_totp(
                secret, data.code.get_secret_value(), credential.last_totp_counter
            )
            if counter is None:
                raise AppError(
                    "invalid_admin_code",
                    "验证码不正确或已经使用，请等待新验证码后重试。",
                    status=401,
                )
            credential.last_totp_counter = counter
        else:
            recovery = await session.scalar(
                select(AdminRecoveryCode).where(
                    AdminRecoveryCode.user_id == user.id,
                    AdminRecoveryCode.code_hash
                    == recovery_digest(request, data.code.get_secret_value()),
                    AdminRecoveryCode.used_at.is_(None),
                ).with_for_update()
            )
            if recovery is None:
                raise AppError(
                    "invalid_admin_code",
                    "恢复码不正确或已经使用。",
                    status=401,
                )
            recovery.used_at = now()
        device, raw = await create_admin_session(
            request, session, user, data.device_name, data.method
        )
        audit(request, session, user, "admin_logged_in", device.id)
        result = admin_session_result(request, user, device, raw)
    set_admin_cookie(request, response, raw)
    return result


@router.get("/me")
async def me(request: Request) -> dict:
    async with authorized_admin(request) as identity:
        return admin_session_result(
            request, identity.user, identity.device,
            request.cookies[ADMIN_COOKIE_NAME],
        )


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict:
    try:
        async with authorized_admin(request, write=True) as identity:
            identity.device.revoked_at = now()
            audit(
                request, identity.session, identity.user,
                "admin_logged_out", identity.device.id,
            )
    except AppError as exc:
        if exc.code != "admin_unauthenticated":
            raise
    delete_admin_cookie(response)
    return {"status": "logged_out"}


@router.post("/recovery-codes")
async def rotate_recovery_codes(data: RotateRecoveryInput, request: Request) -> dict:
    async with authorized_admin(request) as identity:
        user_id = identity.user.id
    await rate_limit(request, "admin-recovery-codes", str(user_id))
    recovery = create_recovery_codes(request)
    async with authorized_admin(request, write=True) as identity:
        credential = await identity.session.scalar(
            select(AdminCredential)
            .where(AdminCredential.user_id == identity.user.id)
            .with_for_update()
        )
        if credential is None:
            raise AppError("admin_setup_required", "请重新设置双重验证。", status=403)
        secret = decrypt_secret(request, credential.totp_secret_ciphertext)
        counter = verify_totp(
            secret, data.code.get_secret_value(), credential.last_totp_counter
        )
        if counter is None:
            raise AppError(
                "invalid_admin_code",
                "验证码不正确或已经使用，请等待新验证码后重试。",
                status=401,
            )
        credential.last_totp_counter = counter
        await identity.session.execute(
            delete(AdminRecoveryCode).where(
                AdminRecoveryCode.user_id == identity.user.id
            )
        )
        identity.session.add_all(
            AdminRecoveryCode(user_id=identity.user.id, code_hash=code_hash)
            for _, code_hash in recovery
        )
        audit(request, identity.session, identity.user, "admin_recovery_codes_rotated")
    return {"recovery_codes": [code for code, _ in recovery]}


@router.post("/change-password")
async def change_password(
    data: ChangePasswordInput, request: Request, response: Response
) -> dict:
    async with authorized_admin(request) as identity:
        user_id, old_hash = identity.user.id, identity.user.password_hash
    await rate_limit(request, "admin-change-password", str(user_id))
    if not await request.app.state.security.verify_password(
        data.current_password.get_secret_value(), old_hash
    ):
        raise invalid_admin_credentials()
    encoded = await request.app.state.security.hash_password(
        data.new_password.get_secret_value()
    )
    async with authorized_admin(request, write=True) as identity:
        user = identity.user
        if user.password_hash != old_hash:
            raise AppError("credentials_changed", "密码已在其他会话修改，请重新登录。", status=409)
        user.password_hash = encoded
        user.auth_epoch += 1
        await identity.session.execute(
            update(AdminSession)
            .where(AdminSession.user_id == user.id, AdminSession.revoked_at.is_(None))
            .values(revoked_at=now())
        )
        await identity.session.execute(
            update(DeviceSession)
            .where(DeviceSession.user_id == user.id, DeviceSession.revoked_at.is_(None))
            .values(revoked_at=now())
        )
        await identity.session.execute(
            update(EmailToken)
            .where(EmailToken.user_id == user.id, EmailToken.used_at.is_(None))
            .values(used_at=now())
        )
        device, raw = await create_admin_session(
            request, identity.session, user, identity.device.device_name,
            identity.device.auth_method,
        )
        audit(request, identity.session, user, "admin_password_changed", device.id)
        result = admin_session_result(request, user, device, raw)
    set_admin_cookie(request, response, raw)
    return result
