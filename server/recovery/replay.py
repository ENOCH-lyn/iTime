from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid5

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from itime.account_deletion.models import AccountDeletion
from itime.account_deletion.quiesce import quiesce_account
from itime.account_deletion.service import (
    revoke_all_sessions,
    scrub_account_audit,
)
from itime.admin.models import RegistrationInvitation
from itime.auth.models import User
from itime.auth.security import Security
from itime.calendar.lifecycle_service import (
    cleanup_expired_calendar_items,
    expire_calendar_operation_responses,
)
from itime.calendar.models import CalendarSeries, CalendarSource, CalendarSubscription
from itime.calendar.subscription_service import stale_running_refresh
from itime.config import Settings
from itime.database import Database
from itime.execution.difficulty_service import (
    cleanup_expired_difficulties,
    expire_difficulty_operation_responses,
)
from itime.execution.models import DifficultyRecord
from itime.planning.source_conflict_service import (
    supersede_open_source_conflicts,
)
from itime.public_links.service import stop_public_link_fetches_for_task
from itime.review.memory_service import (
    cleanup_expired_memories,
    expire_memory_operation_responses,
)
from itime.review.models import Memory
from itime.tasks.lifecycle_service import (
    cleanup_expired_tasks,
    expire_task_operation_responses,
)
from itime.tasks.models import Task
from itime.tasks.service import emit_change
from recovery.contracts import LedgerEntry, RecoveryLedger
from recovery.ledger import validate_ledger

MAX_LEDGER_AGE = timedelta(hours=1)
RECEIPT_NAMESPACE = UUID("01e5383d-9635-5ccb-9152-a19852386ff4")
MODEL_BY_KIND = {
    "task": Task,
    "difficulty": DifficultyRecord,
    "memory": Memory,
    "calendar-series": CalendarSeries,
    "calendar-source": CalendarSource,
}
CHANGE_KIND = {
    "task": "task",
    "difficulty": "difficulty",
    "memory": "memory",
    "calendar-series": "calendar_series",
    "calendar-source": "calendar_source",
}
STATE_RANK = {"active": 0, "trashed": 1, "purged": 2}


class RecoveryReplayError(RuntimeError):
    """删除台账无法安全重放。"""


@dataclass(frozen=True)
class ReplaySummary:
    applied: int
    skipped: int
    accounts_pending: int
    accounts_purged: int
    objects_trashed: int
    objects_purged: int


@dataclass
class _ReplayCounters:
    applied: int = 0
    skipped: int = 0
    accounts_pending: int = 0
    accounts_purged: int = 0
    objects_trashed: int = 0
    objects_purged: int = 0

    def result(self) -> ReplaySummary:
        return ReplaySummary(
            applied=self.applied,
            skipped=self.skipped,
            accounts_pending=self.accounts_pending,
            accounts_purged=self.accounts_purged,
            objects_trashed=self.objects_trashed,
            objects_purged=self.objects_purged,
        )


def _ledger_time(value: str) -> datetime:
    return datetime.fromisoformat(value.removesuffix("Z") + "+00:00")


def _validate_replay_request(ledger: RecoveryLedger, at: datetime) -> datetime:
    if at.tzinfo is None:
        raise RecoveryReplayError("恢复时间必须包含时区")
    try:
        validate_ledger(ledger)
        generated_at = _ledger_time(ledger.generated_at)
    except (TypeError, ValueError) as error:
        raise RecoveryReplayError("删除台账内容无效") from error
    current = at.astimezone(UTC)
    age = current - generated_at
    if age < timedelta(0):
        raise RecoveryReplayError("删除台账生成时间晚于恢复检查时间")
    if age > MAX_LEDGER_AGE:
        raise RecoveryReplayError("删除台账已超过 1 小时")
    return current


def _receipt_uuid(subject: str, purpose: str) -> UUID:
    return uuid5(RECEIPT_NAMESPACE, f"{purpose}:{subject}")


async def _latest_receipt(
    session: AsyncSession,
    subject: str,
) -> AccountDeletion | None:
    return await session.scalar(
        select(AccountDeletion)
        .where(AccountDeletion.subject_digest == subject)
        .order_by(
            AccountDeletion.updated_at.desc(),
            AccountDeletion.id.desc(),
        )
        .limit(1)
        .with_for_update()
    )


def _new_receipt(
    entry: LedgerEntry,
    *,
    user_id: UUID | None,
    status: str,
    at: datetime,
) -> AccountDeletion:
    requested_at = _ledger_time(entry.deleted_at)
    purge_after = _ledger_time(entry.purge_after)
    completed_at = _ledger_time(entry.purged_at) if entry.purged_at else None
    return AccountDeletion(
        id=_receipt_uuid(entry.subject, "receipt"),
        user_id=user_id,
        subject_digest=entry.subject,
        operation_id=_receipt_uuid(entry.subject, "operation"),
        status=status,
        requested_at=requested_at,
        purge_after=purge_after,
        available_at=purge_after,
        cancelled_at=None,
        started_at=None,
        completed_at=completed_at,
        request_id=str(_receipt_uuid(entry.subject, "request")),
        created_at=requested_at,
        updated_at=at,
    )


def _set_pending_receipt(
    receipt: AccountDeletion,
    entry: LedgerEntry,
    user_id: UUID,
    *,
    at: datetime,
) -> None:
    receipt.user_id = user_id
    receipt.status = "pending"
    receipt.requested_at = _ledger_time(entry.deleted_at)
    receipt.purge_after = _ledger_time(entry.purge_after)
    receipt.available_at = receipt.purge_after
    receipt.lease_id = None
    receipt.lease_until = None
    receipt.cancelled_at = None
    receipt.started_at = None
    receipt.completed_at = None
    receipt.last_error_code = None
    receipt.last_error_message = None
    receipt.updated_at = at


def _set_completed_receipt(
    receipt: AccountDeletion,
    entry: LedgerEntry,
    *,
    at: datetime,
) -> None:
    receipt.user_id = None
    receipt.status = "completed"
    receipt.requested_at = _ledger_time(entry.deleted_at)
    receipt.purge_after = _ledger_time(entry.purge_after)
    receipt.available_at = receipt.purge_after
    receipt.lease_id = None
    receipt.lease_until = None
    receipt.cancelled_at = None
    receipt.started_at = receipt.started_at or receipt.requested_at
    receipt.completed_at = _ledger_time(entry.purged_at or entry.purge_after)
    receipt.last_error_code = None
    receipt.last_error_message = None
    receipt.updated_at = at


async def _replay_accounts(
    session: AsyncSession,
    settings: Settings,
    entries: tuple[LedgerEntry, ...],
    *,
    at: datetime,
    counters: _ReplayCounters,
) -> None:
    security = Security(settings)
    users = list(
        await session.scalars(
            select(User).order_by(User.id).with_for_update()
        )
    )
    users_by_digest = {
        security.keyed_digest(f"account-deletion:{user.id}"): user
        for user in users
    }

    for entry in entries:
        user = users_by_digest.get(entry.subject)
        receipt = await _latest_receipt(session, entry.subject)

        if entry.state == "completed":
            if user is None and receipt is not None and receipt.status == "completed":
                counters.skipped += 1
                continue
            if user is not None:
                await quiesce_account(
                    session,
                    user.id,
                    requested_at=_ledger_time(entry.deleted_at),
                )
                await revoke_all_sessions(session, user.id, at)
                await scrub_account_audit(session, user.id)
                await session.execute(
                    delete(RegistrationInvitation).where(
                        (RegistrationInvitation.email == user.email)
                        | (RegistrationInvitation.consumed_by_user_id == user.id)
                    )
                )
            if receipt is None:
                receipt = _new_receipt(
                    entry,
                    user_id=None,
                    status="completed",
                    at=at,
                )
                session.add(receipt)
            else:
                _set_completed_receipt(receipt, entry, at=at)
            await session.flush()
            if user is not None:
                await session.delete(user)
                await session.flush()
                users_by_digest.pop(entry.subject, None)
            counters.applied += 1
            counters.accounts_purged += 1
            continue

        if user is None or (receipt is not None and receipt.status == "completed"):
            counters.skipped += 1
            continue
        requested_at = _ledger_time(entry.deleted_at)
        if (
            receipt is not None
            and receipt.status == "cancelled"
            and receipt.cancelled_at is not None
            and receipt.cancelled_at >= requested_at
        ):
            counters.skipped += 1
            continue
        if (
            receipt is not None
            and receipt.status == "pending"
            and user.status == "pending_deletion"
            and receipt.requested_at == requested_at
            and receipt.purge_after == _ledger_time(entry.purge_after)
        ):
            counters.skipped += 1
            continue

        if receipt is None or receipt.status == "completed":
            receipt = _new_receipt(
                entry,
                user_id=user.id,
                status="pending",
                at=at,
            )
            session.add(receipt)
        else:
            _set_pending_receipt(receipt, entry, user.id, at=at)
        await session.flush()
        await quiesce_account(session, user.id, requested_at=requested_at)
        await revoke_all_sessions(session, user.id, at)
        if user.status != "pending_deletion":
            user.status = "pending_deletion"
            user.auth_epoch += 1
            user.management_version += 1
        counters.applied += 1
        counters.accounts_pending += 1


def _object_state(item: object) -> str:
    if item.purged_at is not None:
        return "purged"
    if item.deleted_at is not None:
        return "trashed"
    return "active"


async def _invalidate_object(
    session: AsyncSession,
    kind: str,
    item: object,
    *,
    at: datetime,
) -> None:
    if kind == "task":
        await expire_task_operation_responses(session, item.owner_id, item.id)
        await stop_public_link_fetches_for_task(session, item, at=at)
    elif kind == "difficulty":
        await expire_difficulty_operation_responses(session, item.owner_id, item.id)
    elif kind == "memory":
        await expire_memory_operation_responses(session, item.owner_id, item.id)
    elif kind == "calendar-series":
        await expire_calendar_operation_responses(
            session,
            item.owner_id,
            item.id,
            current_time=at,
        )
    elif kind == "calendar-source":
        if item.kind != "ics_subscription":
            raise RecoveryReplayError("删除台账包含不可重放的日历来源")
        subscription = await session.scalar(
            select(CalendarSubscription)
            .where(
                CalendarSubscription.source_id == item.id,
                CalendarSubscription.owner_id == item.owner_id,
            )
            .with_for_update()
        )
        if subscription is None:
            raise RecoveryReplayError("日历订阅聚合不完整")
        await stale_running_refresh(
            session,
            subscription,
            at,
            error_code="calendar_subscription_recovery_replay",
            error_message="恢复重放已使旧刷新结果失效。",
        )
        subscription.fetch_revision += 1
        subscription.refresh_status = "idle"
        subscription.lease_id = None
        subscription.lease_until = None
        subscription.updated_at = at
        subscription.version += 1
        await supersede_open_source_conflicts(
            session,
            item.owner_id,
            item.id,
            at,
        )
        await expire_calendar_operation_responses(
            session,
            item.owner_id,
            item.id,
            current_time=at,
        )


async def _replay_objects(
    session: AsyncSession,
    entries: tuple[LedgerEntry, ...],
    *,
    at: datetime,
    counters: _ReplayCounters,
) -> list[tuple[str, UUID]]:
    staged_purges: list[tuple[str, UUID]] = []
    for entry in entries:
        model = MODEL_BY_KIND[entry.kind]
        object_id = UUID(entry.subject)
        owner_id = UUID(entry.owner_id or "")
        item = await session.scalar(
            select(model).where(model.id == object_id).with_for_update()
        )
        if item is None:
            counters.skipped += 1
            continue
        if item.owner_id != owner_id:
            raise RecoveryReplayError(
                f"删除台账对象所有者不匹配: {entry.kind}/{entry.subject}"
            )

        current_state = _object_state(item)
        if (
            item.version > entry.version
            or STATE_RANK[current_state] > STATE_RANK[entry.state]
            or (
                current_state == entry.state
                and item.version >= entry.version
            )
        ):
            counters.skipped += 1
            continue

        if current_state != "purged":
            await _invalidate_object(session, entry.kind, item, at=at)
        item.deleted_at = _ledger_time(entry.deleted_at)
        item.purge_after = _ledger_time(entry.purge_after)
        item.updated_at = at

        if entry.state == "trashed":
            item.purged_at = None
            item.version = max(item.version + 1, entry.version)
            emit_change(
                session,
                owner_id,
                CHANGE_KIND[entry.kind],
                object_id,
                "deleted",
                item.version,
            )
            counters.objects_trashed += 1
        elif current_state == "purged":
            item.purged_at = _ledger_time(entry.purged_at or entry.purge_after)
            item.version = max(item.version, entry.version)
            counters.objects_purged += 1
        else:
            item.purge_after = min(item.purge_after, at)
            item.purged_at = None
            item.version = max(item.version + 1, entry.version - 1)
            staged_purges.append((entry.kind, object_id))
            counters.objects_purged += 1
        counters.applied += 1
    return staged_purges


async def _drain_cleanup(database: Database) -> None:
    while await cleanup_expired_tasks(database):
        pass
    while await cleanup_expired_difficulties(database):
        pass
    while await cleanup_expired_memories(database):
        pass
    while True:
        result = await cleanup_expired_calendar_items(database)
        if result["series"] + result["sources"] == 0:
            break


async def _require_staged_purges(
    database: Database,
    staged: list[tuple[str, UUID]],
) -> None:
    async with database.transaction() as session:
        for kind, object_id in staged:
            model = MODEL_BY_KIND[kind]
            purged_at = await session.scalar(
                select(model.purged_at).where(model.id == object_id)
            )
            if purged_at is None:
                raise RecoveryReplayError(
                    f"删除台账对象未完成清理: {kind}/{object_id}"
                )


async def replay_ledger(
    settings: Settings,
    ledger: RecoveryLedger,
    *,
    at: datetime,
) -> ReplaySummary:
    replayed_at = _validate_replay_request(ledger, at)
    account_entries = tuple(
        entry for entry in ledger.entries if entry.kind == "account"
    )
    object_entries = tuple(
        entry for entry in ledger.entries if entry.kind != "account"
    )
    counters = _ReplayCounters()
    database = Database(settings)
    try:
        async with database.transaction() as session:
            system_identifier = await session.scalar(
                text("SELECT system_identifier::text FROM pg_control_system()")
            )
            if str(system_identifier) != ledger.source_system_identifier:
                raise RecoveryReplayError("删除台账数据库系统标识不匹配")
            await _replay_accounts(
                session,
                settings,
                account_entries,
                at=replayed_at,
                counters=counters,
            )
            staged = await _replay_objects(
                session,
                object_entries,
                at=replayed_at,
                counters=counters,
            )

        await _drain_cleanup(database)
        await _require_staged_purges(database, staged)
        return counters.result()
    finally:
        await database.close()
