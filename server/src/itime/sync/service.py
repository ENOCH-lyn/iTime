from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from itime.auth.security import Security, now
from itime.database import Database
from itime.errors import AppError
from itime.sync.cursor import SyncCursor, decode_sync_cursor, encode_sync_cursor
from itime.sync.models import SyncState
from itime.sync.schemas import (
    OccurrenceStepCompletionInput,
    StepCompletionInput,
)
from itime.tasks.models import ChangeEvent, TaskSchedule, TaskStep
from itime.tasks.progress_service import merge_occurrence_step_completion
from itime.tasks.service import (
    emit_change,
    locked_task,
    replay_operation,
    save_operation,
    task_detail,
)

SYNC_CLEANUP_BATCH_SIZE = 1000


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


async def cleanup_expired_events(
    database: Database,
    *,
    retention_days: int,
    limit: int = SYNC_CLEANUP_BATCH_SIZE,
) -> int:
    if retention_days < 1:
        raise ValueError("retention_days must be positive")
    if limit < 1 or limit > SYNC_CLEANUP_BATCH_SIZE:
        raise ValueError(
            f"limit must be between 1 and {SYNC_CLEANUP_BATCH_SIZE}"
        )
    current_time = now()
    cutoff = current_time - timedelta(days=retention_days)
    async with database.transaction() as session:
        rows = list(
            (
                await session.execute(
                    select(ChangeEvent.sequence, ChangeEvent.owner_id)
                    .where(ChangeEvent.changed_at <= cutoff)
                    .order_by(ChangeEvent.changed_at, ChangeEvent.sequence)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
        if not rows:
            return 0

        owner_ids = sorted({row.owner_id for row in rows}, key=str)
        states = list(
            await session.scalars(
                select(SyncState)
                .where(SyncState.owner_id.in_(owner_ids))
                .order_by(SyncState.owner_id)
                .with_for_update()
            )
        )
        if len(states) != len(owner_ids):
            raise AppError(
                "sync_state_unavailable",
                "同步状态暂时不可用，请保留请求编号并稍后重试。",
                status=503,
                retryable=True,
            )

        discarded_by_owner: dict[UUID, int] = {}
        sequences: list[int] = []
        for row in rows:
            sequences.append(row.sequence)
            discarded_by_owner[row.owner_id] = max(
                discarded_by_owner.get(row.owner_id, 0),
                row.sequence,
            )
        await session.execute(
            delete(ChangeEvent).where(ChangeEvent.sequence.in_(sequences))
        )
        for state in states:
            state.discarded_through_sequence = max(
                state.discarded_through_sequence,
                discarded_by_owner[state.owner_id],
            )
            state.last_cleanup_at = current_time
            state.updated_at = current_time
        return len(rows)


async def merge_task_step_completion(
    session: AsyncSession,
    owner_id: UUID,
    task_id: UUID,
    step_id: UUID,
    data: StepCompletionInput,
) -> dict:
    scope = f"sync.task_step_completion:{task_id}:{step_id}"
    replay = await replay_operation(
        session,
        owner_id,
        data.operation_id,
        scope,
        data,
    )
    if replay is not None:
        return replay

    task = await locked_task(session, owner_id, task_id)
    if data.expected_task_version > task.version:
        raise AppError(
            "sync_version_ahead",
            "待同步步骤基于未知的新版本，请刷新后核对。",
            status=409,
            details={"current_task_version": task.version},
        )
    schedule = await session.get(TaskSchedule, task.id)
    if schedule is not None and schedule.recurrence_frequency != "none":
        raise AppError(
            "repeating_task_requires_occurrence",
            "重复任务的步骤进度必须记录到具体实例。",
            status=409,
        )
    step = await session.scalar(
        select(TaskStep)
        .where(
            TaskStep.id == step_id,
            TaskStep.task_id == task.id,
            TaskStep.owner_id == owner_id,
        )
        .with_for_update()
    )
    if step is None:
        raise AppError(
            "task_steps_changed",
            "目标步骤已变化，请刷新后决定是否保留此操作。",
            status=409,
        )

    task_rebased = task.version != data.expected_task_version
    if step.completed == data.completed:
        merge = "converged"
    else:
        if step.completed != data.base_completed:
            raise AppError(
                "task_step_conflict",
                "此步骤已在其他设备修改，请刷新后选择最终状态。",
                status=409,
                details={"current_completed": step.completed},
            )
        current_time = now()
        step.completed = data.completed
        step.completed_at = current_time if data.completed else None
        step.updated_at = current_time
        task.version += 1
        task.updated_at = current_time
        emit_change(
            session,
            owner_id,
            "step",
            step.id,
            "completed" if data.completed else "updated",
            task.version,
        )
        merge = "rebased" if task_rebased else "applied"
    await session.flush()
    result = {
        "operation_id": data.operation_id,
        "merge": merge,
        "task": await task_detail(session, task),
    }
    save_operation(
        session,
        owner_id,
        data.operation_id,
        scope,
        data,
        result,
    )
    return result


async def merge_task_occurrence_step_completion(
    session: AsyncSession,
    owner_id: UUID,
    data: OccurrenceStepCompletionInput,
) -> dict:
    scope = (
        "sync.occurrence_step:"
        f"{data.task_id}:{data.step_id}"
    )
    replay = await replay_operation(
        session,
        owner_id,
        data.operation_id,
        scope,
        data,
    )
    if replay is not None:
        return replay

    task = await locked_task(session, owner_id, data.task_id)
    task_result, occurrence_result, merge = (
        await merge_occurrence_step_completion(
            session,
            owner_id,
            task,
            occurrence_key=data.occurrence_key,
            step_id=data.step_id,
            expected_task_version=data.expected_task_version,
            expected_occurrence_version=data.expected_occurrence_version,
            completed=data.completed,
            changed_at=now(),
        )
    )
    result = {
        "operation_id": data.operation_id,
        "merge": merge,
        "task": task_result,
        "occurrence": occurrence_result,
    }
    save_operation(
        session,
        owner_id,
        data.operation_id,
        scope,
        data,
        result,
    )
    return result
