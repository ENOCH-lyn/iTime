from datetime import date, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from itime.errors import AppError
from itime.tasks.models import Task, TaskOccurrenceState, TaskSchedule, TaskStep
from itime.tasks.schedule_service import (
    find_task_occurrence_date,
    project_task_occurrence,
    validate_completed_steps,
)
from itime.tasks.service import emit_change, task_detail


async def set_template_step_progress(
    session: AsyncSession,
    owner_id: UUID,
    task: Task,
    completed_step_ids: list[UUID],
    *,
    changed_at: datetime,
) -> tuple[dict, bool]:
    schedule = await session.get(TaskSchedule, task.id)
    if schedule is not None and schedule.recurrence_frequency != "none":
        raise AppError(
            "repeating_task_requires_occurrence",
            "重复任务的步骤进度必须记录到具体实例。",
            status=409,
        )
    steps = list(
        await session.scalars(
            select(TaskStep)
            .where(
                TaskStep.task_id == task.id,
                TaskStep.owner_id == owner_id,
            )
            .order_by(TaskStep.position)
            .with_for_update()
        )
    )
    requested = set(completed_step_ids)
    if len(requested) != len(completed_step_ids) or not requested.issubset(
        {step.id for step in steps}
    ):
        raise AppError(
            "task_steps_changed",
            "任务步骤已变化，请刷新后重新提交进度。",
            status=409,
        )
    changed_steps = [
        step for step in steps if step.completed != (step.id in requested)
    ]
    for step in changed_steps:
        step.completed = step.id in requested
        step.completed_at = changed_at if step.completed else None
        step.updated_at = changed_at
    if changed_steps:
        task.version += 1
        task.updated_at = changed_at
        for step in changed_steps:
            emit_change(
                session,
                owner_id,
                "step",
                step.id,
                "completed" if step.completed else "updated",
                task.version,
            )
    await session.flush()
    return await task_detail(session, task), bool(changed_steps)


async def _locked_occurrence(
    session: AsyncSession,
    owner_id: UUID,
    task: Task,
    occurrence_key: str,
    expected_occurrence_version: int,
) -> tuple[TaskSchedule, TaskOccurrenceState | None, date]:
    schedule = await session.get(TaskSchedule, task.id)
    if schedule is None or schedule.recurrence_frequency == "none":
        raise AppError(
            "task_not_recurring",
            "非重复任务不能更新任务实例。",
            status=409,
        )
    state = await session.scalar(
        select(TaskOccurrenceState)
        .where(
            TaskOccurrenceState.task_id == task.id,
            TaskOccurrenceState.owner_id == owner_id,
            TaskOccurrenceState.occurrence_key == occurrence_key,
        )
        .with_for_update()
    )
    current_version = state.version if state is not None else 0
    if current_version != expected_occurrence_version:
        raise AppError(
            "task_occurrence_version_conflict",
            "此任务实例已在其他页面或设备更新，请刷新后比较再保存。",
            status=409,
            details={"current_version": current_version},
        )
    occurrence_date = (
        state.original_date
        if state is not None
        else await find_task_occurrence_date(
            session,
            owner_id,
            schedule,
            occurrence_key,
        )
    )
    return schedule, state, occurrence_date


async def set_occurrence_progress(
    session: AsyncSession,
    owner_id: UUID,
    task: Task,
    occurrence_key: str,
    expected_occurrence_version: int,
    completed_step_ids: list[UUID],
    *,
    status: str,
    require_current_open: bool,
    changed_at: datetime,
) -> tuple[dict, dict, bool]:
    schedule, state, occurrence_date = await _locked_occurrence(
        session,
        owner_id,
        task,
        occurrence_key,
        expected_occurrence_version,
    )
    if require_current_open and state is not None and state.status != "open":
        raise AppError(
            "task_occurrence_not_open",
            "此任务实例已经完成或跳过，不能继续更新步骤。",
            status=409,
        )
    completed_ids = await validate_completed_steps(
        session,
        task.id,
        completed_step_ids,
    )
    step_count = (
        await session.scalar(
            select(func.count(TaskStep.id)).where(TaskStep.task_id == task.id)
        )
        or 0
    )
    changed = (
        state is None
        or state.status != status
        or state.completed_step_ids != completed_ids
    )
    if state is None:
        state = TaskOccurrenceState(
            task_id=task.id,
            owner_id=owner_id,
            occurrence_key=occurrence_key,
            original_date=occurrence_date,
            kind="base",
            effective_date=occurrence_date,
            effective_local_time=None,
            effective_at=None,
            effective_time_zone=None,
            ambiguous_offset_minutes=None,
            rescheduled=False,
            status=status,
            completed_at=changed_at if status == "completed" else None,
            completed_step_ids=completed_ids,
            step_count_snapshot=step_count,
            makeup_for_key=None,
            makeup_for_date=None,
        )
        session.add(state)
        await session.flush()
    elif changed:
        if state.status != status:
            state.completed_at = changed_at if status == "completed" else None
        state.status = status
        state.completed_step_ids = completed_ids
        state.step_count_snapshot = step_count
        state.version += 1
        state.updated_at = changed_at
    if changed:
        task.version += 1
        task.updated_at = changed_at
        emit_change(
            session,
            owner_id,
            "task_occurrence",
            state.id,
            {
                "completed": "completed",
                "open": "reopened",
                "skipped": "skipped",
            }[status],
            state.version,
        )
        emit_change(
            session,
            owner_id,
            "task",
            task.id,
            "updated",
            task.version,
        )
    await session.flush()
    return (
        await task_detail(session, task),
        project_task_occurrence(
            task,
            schedule,
            occurrence_date,
            state,
            step_count=step_count,
        ),
        changed,
    )


async def set_task_completion_state(
    session: AsyncSession,
    owner_id: UUID,
    task: Task,
    *,
    completed: bool,
    changed_at: datetime,
) -> tuple[dict, bool]:
    schedule = await session.get(TaskSchedule, task.id)
    if schedule is not None and schedule.recurrence_frequency != "none":
        raise AppError(
            "repeating_task_requires_occurrence",
            "重复任务必须明确完成或重新打开某一次实例。",
            status=409,
        )
    desired = "completed" if completed else "open"
    changed = task.status != desired
    if changed:
        task.status = desired
        task.completed_at = changed_at if completed else None
        task.version += 1
        task.updated_at = changed_at
        emit_change(
            session,
            owner_id,
            "task",
            task.id,
            "completed" if completed else "reopened",
            task.version,
        )
    await session.flush()
    return await task_detail(session, task), changed


async def complete_task(
    session: AsyncSession,
    owner_id: UUID,
    task: Task,
    occurrence_key: str | None,
    expected_occurrence_version: int | None,
    *,
    changed_at: datetime,
) -> tuple[dict, dict | None, bool]:
    schedule = await session.get(TaskSchedule, task.id)
    repeating = schedule is not None and schedule.recurrence_frequency != "none"
    if not repeating:
        if occurrence_key is not None or expected_occurrence_version is not None:
            raise AppError(
                "task_not_recurring",
                "非重复任务不能指定任务实例。",
                status=409,
            )
        task_result, changed = await set_task_completion_state(
            session,
            owner_id,
            task,
            completed=True,
            changed_at=changed_at,
        )
        return task_result, None, changed

    if occurrence_key is None or expected_occurrence_version is None:
        raise AppError(
            "task_occurrence_required",
            "重复任务必须明确完成具体实例。",
            status=409,
        )
    schedule, state, occurrence_date = await _locked_occurrence(
        session,
        owner_id,
        task,
        occurrence_key,
        expected_occurrence_version,
    )
    step_count = (
        await session.scalar(
            select(func.count(TaskStep.id)).where(TaskStep.task_id == task.id)
        )
        or 0
    )
    if state is None:
        state = TaskOccurrenceState(
            task_id=task.id,
            owner_id=owner_id,
            occurrence_key=occurrence_key,
            original_date=occurrence_date,
            kind="base",
            effective_date=occurrence_date,
            effective_local_time=None,
            effective_at=None,
            effective_time_zone=None,
            ambiguous_offset_minutes=None,
            rescheduled=False,
            status="completed",
            completed_at=changed_at,
            completed_step_ids=[],
            step_count_snapshot=step_count,
            makeup_for_key=None,
            makeup_for_date=None,
        )
        session.add(state)
        changed = True
        await session.flush()
    else:
        changed = state.status != "completed"
        if changed:
            state.status = "completed"
            state.completed_at = changed_at
            state.version += 1
            state.updated_at = changed_at
    if changed:
        task.version += 1
        task.updated_at = changed_at
        emit_change(
            session,
            owner_id,
            "task_occurrence",
            state.id,
            "completed",
            state.version,
        )
        emit_change(
            session,
            owner_id,
            "task",
            task.id,
            "updated",
            task.version,
        )
    await session.flush()
    return (
        await task_detail(session, task),
        project_task_occurrence(
            task,
            schedule,
            occurrence_date,
            state,
            step_count=step_count,
        ),
        changed,
    )
