import asyncio
import hashlib
import hmac
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TypeVar

import anyio
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.fernet import Fernet
from fastapi import Request
from sqlalchemy import case
from sqlalchemy.dialects.postgresql import insert

from itime.auth.models import AuthRateBucket
from itime.config import Settings
from itime.errors import AppError

T = TypeVar("T")
COOKIE_NAME = "itime_session"


def now() -> datetime:
    return datetime.now(UTC)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


class Security:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.cipher = Fernet(settings.encryption_key.get_secret_value())
        self.ai_cipher = Fernet(
            (
                settings.ai_encryption_key
                or settings.encryption_key
            ).get_secret_value()
        )
        self.key = settings.encryption_key.get_secret_value().encode()
        self.hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=1)
        self.dummy_hash = self.hasher.hash(new_token())
        # 每进程最多两个 64 MiB 哈希；排队有界，不占用数据库连接。
        self.slots = asyncio.Semaphore(2)
        self.thread_limiter = anyio.CapacityLimiter(2)

    async def compute(self, function: Callable[[], T]) -> T:
        try:
            async with asyncio.timeout(2):
                await self.slots.acquire()
        except TimeoutError as exc:
            raise AppError(
                "auth_busy", "登录请求较多，请稍后重试。", status=503, retryable=True
            ) from exc
        try:
            # asyncio 请求取消也必须等待线程结束，否则会提前释放内存并发额度。
            task = asyncio.create_task(
                anyio.to_thread.run_sync(function, limiter=self.thread_limiter)
            )
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise
        finally:
            self.slots.release()

    async def hash_password(self, password: str) -> str:
        return await self.compute(lambda: self.hasher.hash(password))

    async def verify_password(self, password: str, encoded: str | None) -> bool:
        def check() -> bool:
            try:
                valid = self.hasher.verify(encoded or self.dummy_hash, password)
                return valid and encoded is not None
            except (VerificationError, InvalidHashError):
                return False

        return await self.compute(check)

    def keyed_digest(self, value: str) -> str:
        return hmac.new(self.key, value.encode(), hashlib.sha256).hexdigest()

    def csrf(self, raw_token: str) -> str:
        return self.keyed_digest("csrf:" + raw_token)


async def rate_limit(request: Request, action: str, identity: str | None = None) -> None:
    """计数使用独立事务，失败认证的回滚不能撤销限流计数。忽略可伪造的转发头。"""
    settings = request.app.state.settings
    security = request.app.state.security
    current = now()
    expires = current + timedelta(seconds=settings.auth_window_seconds)
    checks = [(f"{action}:ip:{request.client.host}", settings.auth_ip_limit)]
    if identity is not None:
        checks.append((f"{action}:identity:{identity}", settings.auth_identity_limit))
    exceeded = False
    async with request.app.state.database.transaction() as session:
        # 不因超额提前返回，否则并发/失败能逃过其他桶；所有端点采用相同加锁顺序。
        for key, limit in sorted(checks):
            statement = insert(AuthRateBucket).values(
                key=security.keyed_digest(key), hits=1, expires_at=expires
            )
            statement = statement.on_conflict_do_update(
                index_elements=[AuthRateBucket.key],
                set_={
                    "hits": case(
                        (AuthRateBucket.expires_at <= current, 1),
                        else_=AuthRateBucket.hits + 1,
                    ),
                    "expires_at": case(
                        (AuthRateBucket.expires_at <= current, expires),
                        else_=AuthRateBucket.expires_at,
                    ),
                },
            ).returning(AuthRateBucket.hits)
            exceeded |= (await session.scalar(statement)) > limit
    if exceeded:
        raise AppError(
            "rate_limited",
            "尝试次数过多，请稍后再试。",
            status=429,
            retryable=True,
            details={"retry_after_seconds": settings.auth_window_seconds},
        )
