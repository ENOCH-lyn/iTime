from uuid import UUID

from sqlalchemy import Text, cast, delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from itime.auth.security import now
from itime.database import Database
from itime.execution.models import DifficultyRecord
from itime.tasks.models import (
    Task,
    TaskLabel,
    TaskLink,
    TaskOccurrenceState,
    TaskSchedule,
    TaskStep,
    UserOperation,
)
from itime.tasks.service import emit_change

PURGE_BATCH_SIZE = 100
PURGED_TASK_TITLE = "已彻底删除的任务"


async def expire_task_operation_responses(
    session: AsyncSession,
    owner_id: UUID,
    task_id: UUID,
) -> int:
    task_reference = str(task_id)
    result = await session.execute(
        update(UserOperation)
        .where(
            UserOperation.owner_id == owner_id,
            UserOperation.response.is_not(None),
            or_(
                UserOperation.scope.contains(task_reference, autoescape=True),
                cast(UserOperation.response, Text).contains(
                    task_reference,
                    autoescape=True,
                ),
            ),
        )
        .values(response=None, response_purged_at=now())
    )
    return result.rowcount or 0


async def _purge_task(session: AsyncSession, task: Task) -> None:
    current = now()
    await expire_task_operation_responses(session, task.owner_id, task.id)
    difficulties = list(
        await session.scalars(
            select(DifficultyRecord)
            .where(DifficultyRecord.task_id == task.id)
            .with_for_update()
        )
    )
    for difficulty in difficulties:
        difficulty.task_id = None
        difficulty.occurrence_key = None
        difficulty.version += 1
        difficulty.updated_at = current
        emit_change(
            session,
            task.owner_id,
            "difficulty",
            difficulty.id,
            "updated",
            difficulty.version,
        )
    for model in (
        TaskOccurrenceState,
        TaskSchedule,
        TaskStep,
        TaskLink,
        TaskLabel,
    ):
        await session.execute(delete(model).where(model.task_id == task.id))

    task.project_id = None
    task.title = PURGED_TASK_TITLE
    task.description = ""
    task.estimated_minutes = None
    task.estimate_source = None
    task.priority = 0
    task.status = "open"
    task.completed_at = None
    task.archived_at = None
    task.purged_at = current
    task.version += 1
    task.updated_at = current
    emit_change(
        session,
        task.owner_id,
        "task",
        task.id,
        "purged",
        task.version,
    )


async def cleanup_expired_tasks(
    database: Database,
    *,
    limit: int = PURGE_BATCH_SIZE,
) -> int:
    if limit < 1 or limit > PURGE_BATCH_SIZE:
        raise ValueError(f"limit must be between 1 and {PURGE_BATCH_SIZE}")
    current = now()
    async with database.transaction() as session:
        tasks = list(
            await session.scalars(
                select(Task)
                .where(
                    Task.deleted_at.is_not(None),
                    Task.purge_after <= current,
                    Task.purged_at.is_(None),
                )
                .order_by(Task.purge_after, Task.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        for task in tasks:
            await _purge_task(session, task)
        return len(tasks)
