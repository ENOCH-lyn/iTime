import hmac
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from itime.auth.models import DeviceSession, SecurityEvent, User
from itime.auth.security import COOKIE_NAME, digest, new_token, now
from itime.errors import AppError


@dataclass
class Identity:
    session: AsyncSession
    user: User
    device: DeviceSession
    csrf_token: str


def unauthenticated() -> AppError:
    return AppError("unauthenticated", "登录已过期或已退出，请重新登录。", status=401)


def invalid_credentials() -> AppError:
    return AppError("invalid_credentials", "邮箱或密码不正确。", status=401)


def check_user(user: User) -> None:
    if user.status != "active":
        raise AppError("account_suspended", "账号已停用，请联系管理员。", status=403)
    if user.email_verified_at is None:
        raise AppError("email_unverified", "邮箱尚未验证，请查收验证邮件或重新发送。", status=403)


def check_ai_access(user: User) -> None:
    if user.ai_access != "allowed":
        raise AppError(
            "ai_access_frozen",
            "此账号的 AI 功能已暂停，手动查看和编辑仍可使用。",
            status=403,
        )


@asynccontextmanager
async def authorized(request: Request, *, write: bool = False) -> AsyncIterator[Identity]:
    raw = request.cookies.get(COOKIE_NAME, "")
    if len(raw) != 43:
        raise unauthenticated()
    token_hash = digest(raw)
    security = request.app.state.security
    async with request.app.state.database.transaction() as session:
        user_id = await session.scalar(
            select(DeviceSession.user_id).where(DeviceSession.token_hash == token_hash)
        )
        if user_id is None:
            raise unauthenticated()
        statement = select(User).where(User.id == user_id)
        if write:
            # 所有本人写入及撤销/改密按 user -> device 顺序锁定，消除撤销后的写竞态。
            statement = statement.with_for_update()
        user = await session.scalar(statement)
        device = await session.scalar(
            select(DeviceSession).where(DeviceSession.token_hash == token_hash)
        )
        if (
            user is None
            or device is None
            or device.revoked_at is not None
            or device.expires_at <= now()
            or device.auth_epoch != user.auth_epoch
        ):
            raise unauthenticated()
        check_user(user)
        csrf = security.csrf(raw)
        if write and not hmac.compare_digest(
            request.headers.get("x-csrf-token", "").encode(), csrf.encode()
        ):
            raise AppError("invalid_csrf", "页面认证信息已更新，请刷新页面后重试。", status=403)
        # 读取不写热点行；只有实际写操作且超过一分钟才更新活动时刻。
        if write and device.last_seen_at < now() - timedelta(minutes=1):
            device.last_seen_at = now()
        yield Identity(session, user, device, csrf)


def audit(
    request: Request,
    session: AsyncSession,
    user: User,
    action: str,
    target=None,
    *,
    details: dict | None = None,
) -> None:
    session.add(
        SecurityEvent(
            user_id=user.id,
            action=action,
            request_id=request.state.request_id,
            target_id=str(target) if target is not None else None,
            details=details or {},
        )
    )


async def create_session(
    request: Request, session: AsyncSession, user: User, device_name: str
) -> tuple[DeviceSession, str]:
    raw = new_token()
    device = DeviceSession(
        user_id=user.id,
        token_hash=digest(raw),
        auth_epoch=user.auth_epoch,
        device_name=device_name,
        expires_at=now() + timedelta(hours=request.app.state.settings.session_hours),
    )
    session.add(device)
    await session.flush()
    return device, raw


def set_session_cookie(request: Request, response: Response, raw: str) -> None:
    settings = request.app.state.settings
    response.set_cookie(
        COOKIE_NAME,
        raw,
        max_age=settings.session_hours * 3600,
        httponly=True,
        secure=settings.public_origin.startswith("https://"),
        samesite="lax",
        path="/api",
    )


def public_user(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "email_verified": user.email_verified_at is not None,
        "ai_access": user.ai_access,
        "is_admin": user.is_admin,
        "created_at": user.created_at,
    }


def session_result(request: Request, user: User, device: DeviceSession, raw: str) -> dict:
    return {
        "user": public_user(user),
        "session_id": device.id,
        "expires_at": device.expires_at,
        "csrf_token": request.app.state.security.csrf(raw),
    }
