from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from itime.auth.security import Security, now
from itime.errors import AppError
from itime.sync.cursor import SyncCursor, decode_sync_cursor, encode_sync_cursor
from itime.sync.models import SyncState
from itime.tasks.models import ChangeEvent


def change_event_result(event: ChangeEvent) -> dict:
    return {
        "sequence": event.sequence,
        "entity_type": event.entity_type,
        "entity_id": event.entity_id,
        "action": event.action,
        "entity_version": event.entity_version,
        "changed_at": event.changed_at,
    }


async def require_sync_state(
    session: AsyncSession,
    owner_id: UUID,
    *,
    lock_for_read: bool = False,
) -> SyncState:
    statement = select(SyncState).where(SyncState.owner_id == owner_id)
    if lock_for_read:
        statement = statement.with_for_update(read=True)
    state = await session.scalar(statement)
    if state is None:
        raise AppError(
            "sync_state_unavailable",
            "同步状态暂时不可用，请保留请求编号并稍后重试。",
            status=503,
            retryable=True,
        )
    return state


async def latest_sequence(session: AsyncSession, owner_id: UUID) -> int:
    return (
        await session.scalar(
            select(func.max(ChangeEvent.sequence)).where(
                ChangeEvent.owner_id == owner_id
            )
        )
        or 0
    )


async def read_change_page(
    session: AsyncSession,
    security: Security,
    owner_id: UUID,
    *,
    cursor_token: str | None,
    limit: int,
) -> dict:
    state = await require_sync_state(
        session,
        owner_id,
        lock_for_read=True,
    )
    event_latest = await latest_sequence(session, owner_id)
    effective_latest = max(
        event_latest,
        state.discarded_through_sequence,
    )
    current_time = now()

    if cursor_token is None:
        return {
            "events": [],
            "next_cursor": encode_sync_cursor(
                security,
                SyncCursor(owner_id, effective_latest),
            ),
            "has_more": False,
            "reset_required": True,
            "reset_reason": "initial",
            "server_time": current_time,
        }

    cursor = decode_sync_cursor(
        security,
        cursor_token,
        owner_id=owner_id,
    )
    reset_reason = None
    if cursor.sequence < state.discarded_through_sequence:
        reset_reason = "cursor_expired"
    elif cursor.sequence > effective_latest:
        reset_reason = "cursor_ahead"
    if reset_reason is not None:
        return {
            "events": [],
            "next_cursor": encode_sync_cursor(
                security,
                SyncCursor(owner_id, effective_latest),
            ),
            "has_more": False,
            "reset_required": True,
            "reset_reason": reset_reason,
            "server_time": current_time,
        }

    rows = list(
        await session.scalars(
            select(ChangeEvent)
            .where(
                ChangeEvent.owner_id == owner_id,
                ChangeEvent.sequence > cursor.sequence,
                ChangeEvent.sequence <= effective_latest,
            )
            .order_by(ChangeEvent.sequence)
            .limit(limit + 1)
        )
    )
    has_more = len(rows) > limit
    page = rows[:limit]
    next_sequence = (
        page[-1].sequence
        if has_more and page
        else effective_latest
    )
    return {
        "events": [change_event_result(event) for event in page],
        "next_cursor": encode_sync_cursor(
            security,
            SyncCursor(owner_id, next_sequence),
        ),
        "has_more": has_more,
        "reset_required": False,
        "reset_reason": None,
        "server_time": current_time,
    }


async def sync_status(
    session: AsyncSession,
    owner_id: UUID,
    *,
    retention_days: int,
) -> dict:
    state = await require_sync_state(session, owner_id)
    latest_change_at: datetime | None = await session.scalar(
        select(func.max(ChangeEvent.changed_at)).where(
            ChangeEvent.owner_id == owner_id
        )
    )
    return {
        "latest_change_at": latest_change_at,
        "retention_days": retention_days,
        "server_time": now(),
        "last_cleanup_at": state.last_cleanup_at,
    }
