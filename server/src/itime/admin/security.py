import hmac
import re
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta

import pyotp
from fastapi import Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from itime.admin.models import AdminCredential, AdminSession
from itime.auth.models import User
from itime.auth.security import digest, new_token, now
from itime.errors import AppError

ADMIN_COOKIE_NAME = "itime_admin_session"
TOTP_ISSUER = "iTime"
RECOVERY_CODE_COUNT = 10


@dataclass
class AdminIdentity:
    session: AsyncSession
    user: User
    device: AdminSession
    csrf_token: str


def public_admin(user: User) -> dict:
    return {"id": user.id, "email": user.email, "is_admin": user.is_admin}


def admin_csrf(request: Request, raw: str) -> str:
    return request.app.state.security.keyed_digest("admin-csrf:" + raw)


def create_recovery_codes(request: Request) -> list[tuple[str, str]]:
    result = []
    for _ in range(RECOVERY_CODE_COUNT):
        raw = secrets.token_hex(10).upper()
        display = "-".join(raw[index:index + 5] for index in range(0, len(raw), 5))
        result.append((display, recovery_digest(request, raw)))
    return result


def normalize_recovery_code(value: str) -> str:
    return re.sub(r"[\s-]", "", value).upper()


def recovery_digest(request: Request, value: str) -> str:
    normalized = normalize_recovery_code(value)
    return request.app.state.security.keyed_digest("admin-recovery:" + normalized)


def verify_totp(secret: str, code: str, last_counter: int | None) -> int | None:
    if not re.fullmatch(r"\d{6}", code):
        return None
    totp = pyotp.TOTP(secret)
    current = now()
    base_counter = totp.timecode(current)
    for offset in (-1, 0, 1):
        counter = base_counter + offset
        if (last_counter is None or counter > last_counter) and hmac.compare_digest(
            totp.at(current, counter_offset=offset), code
        ):
            return counter
    return None


async def create_admin_session(
    request: Request,
    session: AsyncSession,
    user: User,
    device_name: str,
    auth_method: str,
) -> tuple[AdminSession, str]:
    raw = new_token()
    device = AdminSession(
        user_id=user.id,
        token_hash=digest(raw),
        auth_epoch=user.auth_epoch,
        auth_method=auth_method,
        device_name=device_name,
        expires_at=now() + timedelta(hours=request.app.state.settings.admin_session_hours),
    )
    session.add(device)
    await session.flush()
    return device, raw


def admin_session_result(
    request: Request, user: User, device: AdminSession, raw: str
) -> dict:
    return {
        "admin": public_admin(user),
        "session_id": device.id,
        "auth_method": device.auth_method,
        "expires_at": device.expires_at,
        "csrf_token": admin_csrf(request, raw),
    }


def set_admin_cookie(request: Request, response: Response, raw: str) -> None:
    settings = request.app.state.settings
    response.set_cookie(
        ADMIN_COOKIE_NAME,
        raw,
        max_age=settings.admin_session_hours * 3600,
        httponly=True,
        secure=settings.public_origin.startswith("https://"),
        samesite="strict",
        path="/api/v1/admin",
    )


def delete_admin_cookie(response: Response) -> None:
    response.delete_cookie(
        ADMIN_COOKIE_NAME, path="/api/v1/admin", httponly=True, samesite="strict"
    )


def admin_unauthenticated() -> AppError:
    return AppError("admin_unauthenticated", "管理登录已过期或已退出，请重新登录。", status=401)


@asynccontextmanager
async def authorized_admin(
    request: Request, *, write: bool = False
) -> AsyncIterator[AdminIdentity]:
    raw = request.cookies.get(ADMIN_COOKIE_NAME, "")
    if len(raw) != 43:
        raise admin_unauthenticated()
    token_hash = digest(raw)
    async with request.app.state.database.transaction() as session:
        user_id = await session.scalar(
            select(AdminSession.user_id).where(AdminSession.token_hash == token_hash)
        )
        if user_id is None:
            raise admin_unauthenticated()
        user_statement = select(User).where(User.id == user_id)
        if write:
            user_statement = user_statement.with_for_update()
        user = await session.scalar(user_statement)
        device = await session.scalar(
            select(AdminSession).where(AdminSession.token_hash == token_hash)
        )
        credential = await session.scalar(
            select(AdminCredential.user_id).where(AdminCredential.user_id == user_id)
        )
        credential_valid = (
            credential is None
            if device is not None and device.auth_method == "password"
            else credential is not None
        )
        if (
            user is None
            or device is None
            or not credential_valid
            or not user.is_admin
            or user.status != "active"
            or user.email_verified_at is None
            or device.revoked_at is not None
            or device.expires_at <= now()
            or device.auth_epoch != user.auth_epoch
        ):
            raise admin_unauthenticated()
        csrf = admin_csrf(request, raw)
        if write and not hmac.compare_digest(
            request.headers.get("x-csrf-token", "").encode(), csrf.encode()
        ):
            raise AppError("invalid_csrf", "页面认证信息已更新，请刷新页面后重试。", status=403)
        if write and device.last_seen_at < now() - timedelta(minutes=1):
            device.last_seen_at = now()
        yield AdminIdentity(session, user, device, csrf)
