"""仅由 pytest 启动的额外探针；不属于产品 API 或部署入口。"""

import asyncio
import hmac
import os
import sys
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import column, func, select, table, text
from sqlalchemy.engine import make_url

from itime.account_deletion.models import AccountDeletion
from itime.auth.models import User
from itime.auth.security import digest, new_token, now
from itime.auth.session import audit, authorized
from itime.calendar.models import CalendarSeries, CalendarSource
from itime.errors import AppError
from itime.main import create_app

SERVER = Path(__file__).resolve().parents[1]


class InputProbe(BaseModel):
    count: int = Field(ge=1, le=10)
    password: str


class AccountDeletionControlInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    ticket: SecretStr = Field(min_length=43, max_length=43)


class CalendarLifecycleExpireInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    series_ids: list[UUID] = Field(default_factory=list, max_length=100)
    source_ids: list[UUID] = Field(default_factory=list, max_length=100)


@dataclass
class AccountDeletionControl:
    user_id: UUID
    email: str
    csrf_token: str
    expired: bool = False


def create_test_app() -> FastAPI:
    app = create_app()
    assert app.state.settings.environment == "test"
    database_url = app.state.settings.database_url.get_secret_value()
    assert (make_url(database_url).database or "").startswith("itime_test_")
    deletion_controls: dict[str, AccountDeletionControl] = {}

    @app.post("/__test/input")
    async def input_probe(body: InputProbe) -> dict:
        return {"count": body.count}

    @app.post("/__test/body")
    async def body_probe(request: Request) -> dict:
        return {"bytes": len(await request.body())}

    @app.get("/__test/error")
    async def unexpected_error() -> None:
        raise ValueError("sensitive-exception-content-do-not-log")

    @app.get("/__test/database")
    async def database_probe() -> dict:
        async with app.state.database.transaction() as session:
            value = await session.scalar(text("SELECT 1"))
        return {"value": value}

    @app.post("/api/__test/authorized-write")
    async def authorized_write(request: Request) -> dict:
        async with authorized(request, write=True) as identity:
            await identity.session.execute(text("SELECT pg_sleep(0.05)"))
            audit(request, identity.session, identity.user, "test_write")
        return {"saved": True}

    @app.post("/api/__test/account-deletion-expiry-ticket")
    async def create_account_deletion_expiry_ticket(request: Request) -> dict:
        async with authorized(request, write=True) as identity:
            raw = new_token()
            deletion_controls[digest(raw)] = AccountDeletionControl(
                user_id=identity.user.id,
                email=identity.user.email,
                csrf_token=identity.csrf_token,
            )
        return {"ticket": raw}

    @app.post("/api/__test/account-deletion-expire")
    async def expire_account_deletion(
        body: AccountDeletionControlInput,
        request: Request,
    ) -> dict:
        control = deletion_controls.get(
            digest(body.ticket.get_secret_value())
        )
        if control is None or control.expired:
            raise AppError(
                "test_control_invalid",
                "测试控制凭据无效或已使用。",
                status=404,
            )
        if not hmac.compare_digest(
            request.headers.get("x-csrf-token", ""),
            control.csrf_token,
        ):
            raise AppError(
                "invalid_csrf",
                "页面认证信息已更新，请刷新页面后重试。",
                status=403,
            )

        current = now()
        expired_at = current - timedelta(seconds=1)
        async with app.state.database.transaction() as session:
            deletion = await session.scalar(
                select(AccountDeletion)
                .where(
                    AccountDeletion.user_id == control.user_id,
                    AccountDeletion.status == "pending",
                )
                .with_for_update()
            )
            if deletion is None:
                raise AppError(
                    "test_account_deletion_not_pending",
                    "测试账号当前没有待处理的注销申请。",
                    status=409,
                )
            deletion.requested_at = current - timedelta(days=8)
            deletion.purge_after = expired_at
            deletion.available_at = expired_at
            deletion.updated_at = current
            deletion_id = deletion.id
        control.expired = True
        return {"status": "expired", "deletion_id": deletion_id}

    @app.post("/api/__test/account-deletion-proof")
    async def account_deletion_proof(
        body: AccountDeletionControlInput,
    ) -> dict:
        control = deletion_controls.get(
            digest(body.ticket.get_secret_value())
        )
        if control is None or not control.expired:
            raise AppError(
                "test_control_invalid",
                "测试控制凭据无效或尚未推进到期。",
                status=404,
            )

        async with app.state.database.transaction() as session:
            owner_tables = list(
                await session.scalars(
                    text(
                        """
                        SELECT table_name
                        FROM information_schema.columns
                        WHERE table_schema='public' AND column_name='owner_id'
                        ORDER BY table_name
                        """
                    )
                )
            )
            owner_counts: dict[str, int] = {}
            for table_name in owner_tables:
                owner_table = table(table_name, column("owner_id"))
                count = await session.scalar(
                    select(func.count())
                    .select_from(owner_table)
                    .where(owner_table.c.owner_id == control.user_id)
                )
                if count:
                    owner_counts[table_name] = count
            user_count = await session.scalar(
                select(func.count())
                .select_from(User)
                .where(User.id == control.user_id)
            )
            email_count = await session.scalar(
                select(func.count())
                .select_from(User)
                .where(User.email == control.email)
            )
            completed_receipt_count = await session.scalar(
                select(func.count())
                .select_from(AccountDeletion)
                .where(
                    AccountDeletion.subject_digest
                    == app.state.security.keyed_digest(
                        f"account-deletion:{control.user_id}"
                    ),
                    AccountDeletion.status == "completed",
                    AccountDeletion.user_id.is_(None),
                )
            )
        return {
            "user_count": user_count,
            "email_count": email_count,
            "owner_row_count": sum(owner_counts.values()),
            "owner_tables": owner_counts,
            "completed_receipt_count": completed_receipt_count,
        }

    @app.post("/api/__test/calendar-lifecycle-expire")
    async def expire_calendar_items(
        body: CalendarLifecycleExpireInput,
        request: Request,
    ) -> dict:
        series_ids = set(body.series_ids)
        source_ids = set(body.source_ids)
        if not series_ids and not source_ids:
            raise AppError(
                "test_calendar_items_required",
                "至少提供一个测试日历对象。",
            )

        async with authorized(request, write=True) as identity:
            series = (
                list(
                    await identity.session.scalars(
                        select(CalendarSeries)
                        .where(
                            CalendarSeries.owner_id == identity.user.id,
                            CalendarSeries.id.in_(series_ids),
                        )
                        .with_for_update()
                    )
                )
                if series_ids
                else []
            )
            sources = (
                list(
                    await identity.session.scalars(
                        select(CalendarSource)
                        .where(
                            CalendarSource.owner_id == identity.user.id,
                            CalendarSource.id.in_(source_ids),
                        )
                        .with_for_update()
                    )
                )
                if source_ids
                else []
            )
            if len(series) != len(series_ids) or len(sources) != len(source_ids):
                raise AppError(
                    "test_calendar_item_not_found",
                    "未找到当前账号的测试日历对象。",
                    status=404,
                )
            if any(
                item.deleted_at is None or item.purged_at is not None
                for item in [*series, *sources]
            ):
                raise AppError(
                    "test_calendar_item_not_trashed",
                    "测试日历对象必须处于未清理的回收状态。",
                    status=409,
                )
            current = now()
            for item in [*series, *sources]:
                item.deleted_at = current - timedelta(days=31)
                item.purge_after = current - timedelta(seconds=1)

        return {
            "status": "expired",
            "series_count": len(series),
            "source_count": len(sources),
        }

    @app.post("/api/__test/calendar-lifecycle-cleanup")
    async def run_calendar_cleanup(request: Request) -> dict:
        async with authorized(request, write=True):
            pass
        worker = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "itime.calendar.cleanup_worker",
            "--once",
            cwd=SERVER,
            env={
                **os.environ,
                "ITIME_ENVIRONMENT": "test",
                "ITIME_DATABASE_URL": database_url,
            },
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            await asyncio.wait_for(worker.communicate(), timeout=30)
        except TimeoutError:
            worker.kill()
            await worker.wait()
            raise AppError(
                "test_calendar_cleanup_timeout",
                "测试日历清理进程超时。",
                status=504,
                retryable=True,
            ) from None
        if worker.returncode != 0:
            raise AppError(
                "test_calendar_cleanup_failed",
                "测试日历清理进程执行失败。",
                status=500,
                retryable=True,
            )
        return {"status": "completed"}

    return app
