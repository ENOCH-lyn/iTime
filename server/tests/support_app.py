"""仅由 pytest 启动的额外探针；不属于产品 API 或部署入口。"""

import hmac
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from fastapi import FastAPI, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import column, func, select, table, text

from itime.account_deletion.models import AccountDeletion
from itime.auth.models import User
from itime.auth.security import digest, new_token, now
from itime.auth.session import audit, authorized
from itime.errors import AppError
from itime.main import create_app


class InputProbe(BaseModel):
    count: int = Field(ge=1, le=10)
    password: str


class AccountDeletionControlInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    ticket: SecretStr = Field(min_length=43, max_length=43)


@dataclass
class AccountDeletionControl:
    user_id: UUID
    email: str
    csrf_token: str
    expired: bool = False


def create_test_app() -> FastAPI:
    app = create_app()
    assert app.state.settings.environment == "test"
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

    return app
