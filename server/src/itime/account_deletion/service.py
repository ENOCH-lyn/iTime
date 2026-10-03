from dataclasses import asdict
from datetime import datetime, timedelta
from uuid import UUID

from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from itime.account_deletion.models import AccountDeletion
from itime.account_deletion.quiesce import QuiesceSummary, quiesce_account
from itime.account_deletion.schemas import (
    AccountDeletionCancel,
    AccountDeletionRequest,
)
from itime.auth.models import DeviceSession, User
from itime.auth.security import now
from itime.auth.session import (
    audit,
    check_user,
    create_session,
    invalid_credentials,
    session_result,
)
from itime.errors import AppError

ACCOUNT_DELETION_GRACE = timedelta(days=7)


def deletion_result(deletion: AccountDeletion) -> dict:
    return {
        "id": deletion.id,
        "status": deletion.status,
        "requested_at": deletion.requested_at,
        "purge_after": deletion.purge_after,
        "cancelled_at": deletion.cancelled_at,
        "completed_at": deletion.completed_at,
        "can_cancel": (
            deletion.status == "pending"
            and now() < deletion.purge_after
        ),
    }


def account_deletion_result(
    deletion: AccountDeletion,
    *,
    summary: QuiesceSummary,
) -> dict:
    return {
        "operation_id": deletion.operation_id,
        "deletion": deletion_result(deletion),
        "stopped": asdict(summary),
        "saved": "saved",
    }


def account_deletion_pending(deletion: AccountDeletion) -> AppError:
    return AppError(
        "account_deletion_pending",
        "账号正在注销，验证后可在期限内撤销。",
        status=409,
        saved="saved",
        details={
            "requested_at": deletion.requested_at.isoformat(),
            "purge_after": deletion.purge_after.isoformat(),
            "can_cancel": (
                deletion.status == "pending"
                and now() < deletion.purge_after
            ),
        },
    )


async def active_deletion_for_user(
    session: AsyncSession,
    owner_id: UUID,
    *,
    lock: bool = False,
) -> AccountDeletion | None:
    statement = (
        select(AccountDeletion)
        .where(
            AccountDeletion.user_id == owner_id,
            AccountDeletion.status.in_(("pending", "processing")),
        )
        .order_by(
            AccountDeletion.requested_at.desc(),
            AccountDeletion.id.desc(),
        )
        .limit(1)
    )
    if lock:
        statement = statement.with_for_update()
    return await session.scalar(statement)


async def revoke_all_sessions(
    session: AsyncSession,
    owner_id: UUID,
    at: datetime,
) -> int:
    result = await session.execute(
        update(DeviceSession)
        .where(
            DeviceSession.user_id == owner_id,
            DeviceSession.revoked_at.is_(None),
        )
        .values(revoked_at=at)
    )
    return result.rowcount


async def _request_account_deletion(
    request: Request,
    *,
    user_id: UUID,
    password_hash: str,
    data: AccountDeletionRequest,
) -> dict:
    async with request.app.state.database.transaction() as session:
        user = await session.scalar(
            select(User)
            .where(User.id == user_id)
            .with_for_update()
        )
        if user is None:
            raise invalid_credentials()
        if user.password_hash != password_hash:
            raise AppError(
                "credentials_changed",
                "密码已变化，请重新登录。",
                status=409,
            )
        if user.status == "pending_deletion":
            existing = await active_deletion_for_user(
                session,
                user.id,
                lock=True,
            )
            if existing is None:
                raise AppError(
                    "account_deletion_state_invalid",
                    "账号注销状态不完整，请保留请求编号并联系管理员。",
                    status=503,
                    retryable=True,
                    saved="unknown",
                )
            raise account_deletion_pending(existing)
        check_user(user)
        if data.email_confirmation != user.email:
            raise AppError(
                "account_confirmation_mismatch",
                "邮箱确认不匹配。",
            )
        if user.is_admin:
            raise AppError(
                "admin_account_protected",
                "管理员账号不能在此注销。",
                status=409,
            )
        operation_owner = await session.scalar(
            select(AccountDeletion.user_id).where(
                AccountDeletion.operation_id == data.operation_id
            )
        )
        if operation_owner is not None:
            raise AppError(
                "operation_id_reused",
                "此操作编号已经使用，请刷新后重试。",
                status=409,
            )

        current = now()
        purge_after = current + ACCOUNT_DELETION_GRACE
        deletion = AccountDeletion(
            user_id=user.id,
            subject_digest=request.app.state.security.keyed_digest(
                f"account-deletion:{user.id}"
            ),
            operation_id=data.operation_id,
            status="pending",
            requested_at=current,
            purge_after=purge_after,
            available_at=purge_after,
            request_id=request.state.request_id,
            created_at=current,
            updated_at=current,
        )
        session.add(deletion)
        await session.flush()

        summary = await quiesce_account(
            session,
            user.id,
            requested_at=current,
        )
        user.status = "pending_deletion"
        user.auth_epoch += 1
        user.management_version += 1
        await revoke_all_sessions(
            session,
            user.id,
            at=current,
        )
        audit(
            request,
            session,
            user,
            "account_deletion_requested",
            deletion.id,
            details={
                "purge_after": purge_after.isoformat(),
                "stopped": asdict(summary),
            },
        )
        await session.flush()
        return account_deletion_result(deletion, summary=summary)


async def request_account_deletion(
    request: Request,
    *,
    user_id: UUID,
    password_hash: str,
    data: AccountDeletionRequest,
) -> dict:
    if not await request.app.state.security.verify_password(
        data.current_password.get_secret_value(),
        password_hash,
    ):
        raise invalid_credentials()
    try:
        return await _request_account_deletion(
            request,
            user_id=user_id,
            password_hash=password_hash,
            data=data,
        )
    except IntegrityError:
        async with request.app.state.database.transaction() as session:
            existing = await active_deletion_for_user(
                session,
                user_id,
            )
            if existing is not None:
                raise account_deletion_pending(existing) from None
        raise AppError(
            "operation_id_reused",
            "此操作编号已经使用，请刷新后重试。",
            status=409,
        ) from None


async def cancel_account_deletion(
    request: Request,
    data: AccountDeletionCancel,
) -> tuple[dict, str]:
    async with request.app.state.database.transaction() as session:
        snapshot = (
            await session.execute(
                select(User.id, User.password_hash).where(
                    User.email == data.email
                )
            )
        ).one_or_none()
    user_id = snapshot[0] if snapshot is not None else None
    password_hash = snapshot[1] if snapshot is not None else None
    if not await request.app.state.security.verify_password(
        data.password.get_secret_value(),
        password_hash,
    ):
        raise invalid_credentials()

    async with request.app.state.database.transaction() as session:
        user = await session.scalar(
            select(User)
            .where(User.id == user_id)
            .with_for_update()
        )
        if user is None or user.password_hash != password_hash:
            raise invalid_credentials()
        if user.status != "pending_deletion":
            if user.status == "suspended":
                check_user(user)
            raise AppError(
                "account_deletion_not_pending",
                "此账号没有可撤销的注销申请。",
                status=409,
            )
        deletion = await active_deletion_for_user(
            session,
            user.id,
            lock=True,
        )
        if deletion is None:
            raise AppError(
                "account_deletion_state_invalid",
                "账号注销状态不完整，请保留请求编号并联系管理员。",
                status=503,
                retryable=True,
                saved="unknown",
            )
        current = now()
        if deletion.status != "pending" or current >= deletion.purge_after:
            raise AppError(
                "account_deletion_expired",
                "账号注销撤销期已经结束，无法恢复。",
                status=410,
                saved="saved",
            )

        deletion.status = "cancelled"
        deletion.cancelled_at = current
        deletion.lease_id = None
        deletion.lease_until = None
        deletion.updated_at = current
        user.status = "active"
        user.management_version += 1
        device, raw = await create_session(
            request,
            session,
            user,
            data.device_name,
        )
        audit(
            request,
            session,
            user,
            "account_deletion_cancelled",
            deletion.id,
        )
        await session.flush()
        return (
            {
                "status": "cancelled",
                "deletion": deletion_result(deletion),
                "session": session_result(
                    request,
                    user,
                    device,
                    raw,
                ),
            },
            raw,
        )
