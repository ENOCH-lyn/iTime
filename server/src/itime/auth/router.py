from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator
from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from itime.account_deletion.service import (
    account_deletion_pending,
    active_deletion_for_user,
)
from itime.admin.models import AdminSession, RegistrationInvitation, RegistrationPolicy
from itime.ai.governance_models import UserPrivacySetting
from itime.auth.mail import queue_email
from itime.auth.models import DeviceSession, EmailToken, User
from itime.auth.security import COOKIE_NAME, digest, now, rate_limit
from itime.auth.session import (
    audit,
    authorized,
    check_user,
    create_session,
    invalid_credentials,
    public_user,
    session_result,
    set_session_cookie,
)
from itime.calendar.models import UserTimeSettings
from itime.calendar.service import validate_time_zone
from itime.errors import AppError
from itime.review.models import MemorySettings
from itime.tasks.models import ChangeEvent

router = APIRouter(prefix="/api/v1/auth", tags=["账号"])


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class EmailInput(Input):
    email: EmailStr

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value):
        return value.strip().lower() if isinstance(value, str) else value


Password = Annotated[SecretStr, Field(min_length=8, max_length=128)]
Token = Annotated[SecretStr, Field(min_length=43, max_length=43)]
InvitationToken = Annotated[SecretStr, Field(min_length=43, max_length=43)]


class RegisterInput(EmailInput):
    password: Password
    invitation_token: InvitationToken | None = None
    time_zone: str | None = Field(default=None, min_length=1, max_length=64)
    device_name: str = Field(default="网页浏览器", min_length=1, max_length=100)

    @field_validator("time_zone")
    @classmethod
    def clean_time_zone(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class LoginInput(EmailInput):
    password: Password
    device_name: str = Field(default="网页浏览器", min_length=1, max_length=100)


class VerifyInput(Input):
    token: Token
    device_name: str = Field(default="网页浏览器", min_length=1, max_length=100)


class ResetInput(Input):
    token: Token
    new_password: Password


class ChangePasswordInput(Input):
    current_password: Password
    new_password: Password
    revoke_other_sessions: bool


EMAIL_RESULT = {
    "status": "accepted",
    "message": "请求已提交",
}


def mail_available(settings) -> bool:
    return (
        settings.smtp_username is not None
        or settings.smtp_host not in {"localhost", "127.0.0.1", "::1"}
    )


def invalid_token() -> AppError:
    return AppError(
        "invalid_email_token", "邮件链接已过期、已使用或已被新链接替换，请重新发送。", status=400
    )


@router.get("/policy")
async def policy(request: Request) -> dict:
    settings = request.app.state.settings
    async with request.app.state.database.transaction() as session:
        registration = await session.get(RegistrationPolicy, 1)
    if registration is None:
        raise AppError(
            "registration_policy_unavailable",
            "注册策略暂时不可用，请稍后重试。",
            status=503,
            retryable=True,
        )
    return {
        "password_min_length": 8,
        "password_max_length": 128,
        "session_hours": settings.session_hours,
        "verification_minutes": settings.verification_minutes,
        "password_reset_minutes": settings.password_reset_minutes,
        "mail_cooldown_seconds": settings.mail_cooldown_seconds,
        "email_available": mail_available(settings),
        "registration_mode": registration.mode,
        "require_email_verification": (
            registration.require_email_verification
        ),
    }


@router.post("/register", status_code=202)
async def register(
    data: RegisterInput,
    request: Request,
    response: Response,
) -> dict:
    await rate_limit(request, "register", data.email)
    time_zone = validate_time_zone(data.time_zone) if data.time_zone is not None else None
    security = request.app.state.security
    encoded = await security.hash_password(data.password.get_secret_value())
    session_payload = None
    session_token = None
    async with request.app.state.database.transaction() as session:
        registration = await session.scalar(
            select(RegistrationPolicy)
            .where(RegistrationPolicy.id == 1)
            .with_for_update(read=True)
        )
        if registration is None:
            raise AppError(
                "registration_policy_unavailable",
                "注册策略暂时不可用，请稍后重试。",
                status=503,
                retryable=True,
            )
        invitation = None
        if registration.mode == "invite":
            raw_invitation = (
                data.invitation_token.get_secret_value()
                if data.invitation_token is not None
                else ""
            )
            invitation = await session.scalar(
                select(RegistrationInvitation)
                .where(RegistrationInvitation.token_hash == digest(raw_invitation))
                .with_for_update()
            )
            if (
                invitation is None
                or invitation.email != data.email
                or invitation.revoked_at is not None
                or invitation.expires_at <= now()
            ):
                raise AppError(
                    "registration_invitation_required",
                    "当前仅接受邀请注册，请使用与此邮箱匹配的有效邀请链接。",
                    status=403,
                )
        # 冲突不覆盖密码；并发注册由唯一键和用户锁保证只创建一个账号/验证请求。
        new_id = await session.scalar(
            insert(User)
            .values(email=data.email, password_hash=encoded)
            .on_conflict_do_nothing(index_elements=[User.email])
            .returning(User.id)
        )
        user = await session.scalar(select(User).where(User.email == data.email).with_for_update())
        if invitation is not None:
            if invitation.consumed_at is None:
                invitation.consumed_at = now()
                invitation.consumed_by_user_id = user.id
            elif invitation.consumed_by_user_id != user.id:
                raise AppError(
                    "registration_invitation_required",
                    "当前仅接受邀请注册，请使用与此邮箱匹配的有效邀请链接。",
                    status=403,
                )
        verification_required = registration.require_email_verification
        if new_id:
            session.add(UserPrivacySetting(owner_id=user.id))
            session.add(MemorySettings(owner_id=user.id))
            if time_zone is not None:
                time_settings = UserTimeSettings(
                    owner_id=user.id,
                    time_zone=time_zone,
                    version=1,
                )
                session.add(time_settings)
                session.add(
                    ChangeEvent(
                        owner_id=user.id,
                        entity_type="time_settings",
                        entity_id=user.id,
                        action="created",
                        entity_version=1,
                    )
                )
            audit(
                request,
                session,
                user,
                "registered_by_invitation" if invitation is not None else "registered",
                invitation.id if invitation is not None else None,
            )
            if not verification_required:
                user.email_verified_at = now()
                device, session_token = await create_session(
                    request,
                    session,
                    user,
                    data.device_name,
                )
                session_payload = session_result(
                    request,
                    user,
                    device,
                    session_token,
                )
                audit(
                    request,
                    session,
                    user,
                    "logged_in_after_registration",
                    device.id,
                )
        if (
            verification_required
            and user.email_verified_at is None
            and user.status == "active"
        ):
            await queue_email(request, session, user, "verify")
    if session_token is not None:
        set_session_cookie(request, response, session_token)
    if session_payload is not None:
        status = "registered"
        message = "注册成功"
    elif verification_required:
        status = "verification_sent"
        message = "验证邮件已发送，请查收"
    else:
        status = "already_registered"
        message = "该邮箱已注册，请直接登录"
    return {
        "status": status,
        "message": message,
        "verification_required": verification_required,
        "session": session_payload,
    }


@router.post("/resend-verification", status_code=202)
async def resend(data: EmailInput, request: Request) -> dict:
    await rate_limit(request, "verify-mail", data.email)
    async with request.app.state.database.transaction() as session:
        user = await session.scalar(select(User).where(User.email == data.email).with_for_update())
        if user and user.email_verified_at is None and user.status == "active":
            await queue_email(request, session, user, "verify")
    return EMAIL_RESULT


async def token_snapshot(request: Request, raw: str, purpose: str) -> tuple[UUID, str]:
    async with request.app.state.database.transaction() as session:
        row = (
            await session.execute(
                select(User.id, User.password_hash)
                .join(EmailToken)
                .where(
                    EmailToken.token_hash == digest(raw),
                    EmailToken.purpose == purpose,
                    EmailToken.used_at.is_(None),
                    EmailToken.expires_at > now(),
                )
            )
        ).first()
        if row is None:
            raise invalid_token()
        return row.id, row.password_hash


async def consume_token(session, raw: str, purpose: str, user_id: UUID) -> None:
    # 调用方先锁 User；再次检查有效性，确保并发重设/验证只有一个成功。
    token = await session.scalar(
        select(EmailToken).where(
            EmailToken.user_id == user_id,
            EmailToken.token_hash == digest(raw),
            EmailToken.purpose == purpose,
            EmailToken.used_at.is_(None),
            EmailToken.expires_at > now(),
        )
    )
    if token is None:
        raise invalid_token()
    token.used_at = now()


@router.post("/verify-email")
async def verify(
    data: VerifyInput,
    request: Request,
    response: Response,
) -> dict:
    raw = data.token.get_secret_value()
    await rate_limit(request, "verify-token")
    user_id, _ = await token_snapshot(request, raw, "verify")
    async with request.app.state.database.transaction() as session:
        user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
        if user is None:
            raise invalid_token()
        await consume_token(session, raw, "verify", user_id)
        if user.status != "active":
            raise AppError("account_suspended", "账号已停用，请联系管理员。", status=403)
        user.email_verified_at = now()
        device, session_token = await create_session(
            request,
            session,
            user,
            data.device_name,
        )
        audit(request, session, user, "email_verified")
        audit(
            request,
            session,
            user,
            "logged_in_after_email_verification",
            device.id,
        )
        result = session_result(
            request,
            user,
            device,
            session_token,
        )
    set_session_cookie(request, response, session_token)
    return result


@router.post("/login")
async def login(data: LoginInput, request: Request, response: Response) -> dict:
    await rate_limit(request, "login", data.email)
    async with request.app.state.database.transaction() as session:
        encoded = await session.scalar(select(User.password_hash).where(User.email == data.email))
    security = request.app.state.security
    if not await security.verify_password(data.password.get_secret_value(), encoded):
        raise invalid_credentials()
    async with request.app.state.database.transaction() as session:
        user = await session.scalar(select(User).where(User.email == data.email).with_for_update())
        if user is None or user.password_hash != encoded:
            raise invalid_credentials()
        if user.status == "pending_deletion":
            deletion = await active_deletion_for_user(session, user.id)
            if deletion is None:
                raise AppError(
                    "account_deletion_state_invalid",
                    "账号注销状态不完整，请保留请求编号并联系管理员。",
                    status=503,
                    retryable=True,
                    saved="unknown",
                )
            raise account_deletion_pending(deletion)
        check_user(user)
        if user.is_admin:
            raise AppError("admin_login_required", "请使用管理入口完成双重验证。", status=403)
        device, raw = await create_session(request, session, user, data.device_name)
        audit(request, session, user, "logged_in", device.id)
        result = session_result(request, user, device, raw)
    set_session_cookie(request, response, raw)
    return result


@router.get("/me")
async def me(request: Request) -> dict:
    async with authorized(request) as identity:
        return {
            "user": public_user(identity.user),
            "session_id": identity.device.id,
            "expires_at": identity.device.expires_at,
            "csrf_token": identity.csrf_token,
        }


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict:
    # 重复请求可恢复：已撤销/过期的 cookie 不再能访问业务，因此只清理浏览器。
    try:
        async with authorized(request, write=True) as identity:
            identity.device.revoked_at = now()
            audit(request, identity.session, identity.user, "logged_out", identity.device.id)
    except AppError as exc:
        if exc.code != "unauthenticated":
            raise
    response.delete_cookie(COOKIE_NAME, path="/api", httponly=True, samesite="lax")
    return {"status": "logged_out"}


@router.post("/forgot-password", status_code=202)
async def forgot(data: EmailInput, request: Request) -> dict:
    await rate_limit(request, "reset-mail", data.email)
    async with request.app.state.database.transaction() as session:
        user = await session.scalar(select(User).where(User.email == data.email).with_for_update())
        if user and user.status in {"active", "pending_deletion"}:
            await queue_email(request, session, user, "reset")
    return EMAIL_RESULT


@router.post("/reset-password")
async def reset(data: ResetInput, request: Request) -> dict:
    raw = data.token.get_secret_value()
    await rate_limit(request, "reset-token")
    user_id, old_hash = await token_snapshot(request, raw, "reset")
    encoded = await request.app.state.security.hash_password(data.new_password.get_secret_value())
    async with request.app.state.database.transaction() as session:
        user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
        if user is None or user.password_hash != old_hash:
            raise invalid_token()
        await consume_token(session, raw, "reset", user_id)
        if user.status not in {"active", "pending_deletion"}:
            raise AppError("account_suspended", "账号已停用，请联系管理员。", status=403)
        pending_deletion = user.status == "pending_deletion"
        user.password_hash = encoded
        user.email_verified_at = user.email_verified_at or now()
        user.auth_epoch += 1
        await session.execute(
            update(DeviceSession)
            .where(DeviceSession.user_id == user.id, DeviceSession.revoked_at.is_(None))
            .values(revoked_at=now())
        )
        await session.execute(
            update(AdminSession)
            .where(AdminSession.user_id == user.id, AdminSession.revoked_at.is_(None))
            .values(revoked_at=now())
        )
        await session.execute(
            update(EmailToken)
            .where(EmailToken.user_id == user.id, EmailToken.used_at.is_(None))
            .values(used_at=now())
        )
        audit(request, session, user, "password_reset")
    return {
        "status": "password_reset",
        "message": (
            "密码已重设；账号仍在注销期，请登录并明确撤销。"
            if pending_deletion
            else "密码已重设，所有设备已退出，请重新登录。"
        ),
    }


@router.post("/change-password")
async def change_password(data: ChangePasswordInput, request: Request, response: Response) -> dict:
    async with authorized(request) as identity:
        user_id, old_hash = identity.user.id, identity.user.password_hash
    await rate_limit(request, "change-password", str(user_id))
    security = request.app.state.security
    if not await security.verify_password(data.current_password.get_secret_value(), old_hash):
        raise invalid_credentials()
    encoded = await security.hash_password(data.new_password.get_secret_value())
    async with authorized(request, write=True) as identity:
        session, user = identity.session, identity.user
        if user.password_hash != old_hash:
            raise AppError("credentials_changed", "密码已在其他设备修改，请重新登录。", status=409)
        user.password_hash = encoded
        if data.revoke_other_sessions:
            user.auth_epoch += 1
            await session.execute(
                update(DeviceSession)
                .where(DeviceSession.user_id == user.id, DeviceSession.revoked_at.is_(None))
                .values(revoked_at=now())
            )
        else:
            identity.device.revoked_at = now()
        # 改密使所有旧找回链接失效；当前浏览器轮换随机令牌。
        await session.execute(
            update(EmailToken)
            .where(EmailToken.user_id == user.id, EmailToken.used_at.is_(None))
            .values(used_at=now())
        )
        device, raw = await create_session(request, session, user, identity.device.device_name)
        audit(request, session, user, "password_changed", device.id)
        result = session_result(request, user, device, raw)
    set_session_cookie(request, response, raw)
    return result


@router.get("/sessions")
async def sessions(
    request: Request,
    before_time: datetime | None = None,
    before_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> dict:
    if (before_time is None) != (before_id is None) or (
        before_time is not None and before_time.tzinfo is None
    ):
        raise AppError("invalid_cursor", "设备列表游标无效，请重新加载。")
    async with authorized(request) as identity:
        statement = select(DeviceSession).where(
            DeviceSession.user_id == identity.user.id,
            DeviceSession.revoked_at.is_(None),
            DeviceSession.expires_at > now(),
            DeviceSession.auth_epoch == identity.user.auth_epoch,
        )
        if before_time is not None:
            statement = statement.where(
                or_(
                    DeviceSession.created_at < before_time,
                    and_(
                        DeviceSession.created_at == before_time,
                        DeviceSession.id < before_id,
                    ),
                )
            )
        devices = list(
            await identity.session.scalars(
                statement.order_by(DeviceSession.created_at.desc(), DeviceSession.id.desc()).limit(
                    limit + 1
                )
            )
        )
        page = devices[:limit]
        return {
            "items": [
                {
                    "id": device.id,
                    "device_name": device.device_name,
                    "created_at": device.created_at,
                    "last_seen_at": device.last_seen_at,
                    "expires_at": device.expires_at,
                    "current": device.id == identity.device.id,
                }
                for device in page
            ],
            "next_cursor": (
                {"before_time": page[-1].created_at, "before_id": page[-1].id}
                if len(devices) > limit
                else None
            ),
        }


@router.delete("/sessions/{device_id}")
async def revoke(device_id: UUID, request: Request, response: Response) -> dict:
    async with authorized(request, write=True) as identity:
        device = await identity.session.scalar(
            select(DeviceSession).where(
                DeviceSession.id == device_id, DeviceSession.user_id == identity.user.id
            )
        )
        if device is None:
            raise AppError("session_not_found", "未找到此设备。", status=404)
        if device.revoked_at is None:
            device.revoked_at = now()
            audit(request, identity.session, identity.user, "device_revoked", device.id)
        if device.id == identity.device.id:
            response.delete_cookie(COOKIE_NAME, path="/api", httponly=True, samesite="lax")
    return {"status": "revoked"}
