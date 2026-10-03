from datetime import timedelta
from uuid import uuid4

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert

from itime.admin.models import (
    AdminCredential,
    AdminEnrollment,
    AdminRecoveryCode,
    AdminSession,
)
from itime.ai.governance_models import UserPrivacySetting
from itime.auth.models import DeviceSession, SecurityEvent, User
from itime.auth.security import Security, digest, new_token, now
from itime.config import Settings
from itime.database import Database


class AdminOperationError(Exception):
    pass


def normalize_email(value: str) -> str:
    try:
        return validate_email(value.strip(), check_deliverability=False).normalized.lower()
    except EmailNotValidError as exc:
        raise AdminOperationError("邮箱格式不正确。") from exc


def validate_password(value: str) -> None:
    if not 12 <= len(value) <= 128:
        raise AdminOperationError("密码必须为 12—128 个字符。")


async def _new_enrollment(session, user: User, settings: Settings) -> str:
    raw = new_token()
    await session.execute(
        insert(AdminEnrollment)
        .values(
            user_id=user.id,
            token_hash=digest(raw),
            secret_ciphertext=None,
            expires_at=now() + timedelta(minutes=settings.admin_setup_minutes),
        )
        .on_conflict_do_update(
            index_elements=[AdminEnrollment.user_id],
            set_={
                "token_hash": digest(raw),
                "secret_ciphertext": None,
                "created_at": now(),
                "expires_at": now() + timedelta(minutes=settings.admin_setup_minutes),
            },
        )
    )
    session.add(
        SecurityEvent(
            user_id=user.id,
            action="admin_enrollment_issued",
            request_id=str(uuid4()),
            target_id=None,
        )
    )
    return raw


async def create_admin(
    database: Database,
    security: Security,
    settings: Settings,
    email: str,
    password: str,
) -> str:
    normalized = normalize_email(email)
    validate_password(password)
    encoded = await security.hash_password(password)
    async with database.transaction() as session:
        user_id = await session.scalar(
            insert(User)
            .values(
                email=normalized,
                password_hash=encoded,
                email_verified_at=now(),
                is_admin=True,
            )
            .on_conflict_do_nothing(index_elements=[User.email])
            .returning(User.id)
        )
        if user_id is None:
            raise AdminOperationError("此邮箱已存在；不会覆盖或提升既有账号。")
        user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
        session.add(UserPrivacySetting(owner_id=user.id))
        raw = await _new_enrollment(session, user, settings)
    return f"{settings.public_origin}/admin/setup#token={raw}"


async def reset_admin_totp(
    database: Database,
    settings: Settings,
    email: str,
) -> str:
    normalized = normalize_email(email)
    async with database.transaction() as session:
        user = await session.scalar(
            select(User).where(User.email == normalized).with_for_update()
        )
        if user is None or not user.is_admin or user.status != "active":
            raise AdminOperationError("未找到可重置的启用中管理员。")
        user.auth_epoch += 1
        await session.execute(
            update(AdminSession)
            .where(AdminSession.user_id == user.id, AdminSession.revoked_at.is_(None))
            .values(revoked_at=now())
        )
        await session.execute(
            update(DeviceSession)
            .where(DeviceSession.user_id == user.id, DeviceSession.revoked_at.is_(None))
            .values(revoked_at=now())
        )
        await session.execute(
            delete(AdminRecoveryCode).where(AdminRecoveryCode.user_id == user.id)
        )
        await session.execute(delete(AdminCredential).where(AdminCredential.user_id == user.id))
        raw = await _new_enrollment(session, user, settings)
    return f"{settings.public_origin}/admin/setup#token={raw}"
