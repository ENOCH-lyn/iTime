from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from itime.calendar.models import UserTimeSettings
from itime.errors import AppError
from itime.execution.models import ExecutionRevision, FocusEvent, FocusSession
from itime.execution.schemas import (
    FocusManualCreateInput,
    FocusProgressInput,
    FocusRevisionInput,
    FocusStartInput,
    FocusTaskCompletionInput,
    FocusTransitionInput,
)
from itime.execution.time_math import activity_day_for, resolve_manual_interval
from itime.planning.models import Plan, PlanVersion, ScheduledBlock
from itime.tasks.models import Task, TaskOccurrenceState, TaskSchedule, TaskStep
from itime.tasks.progress_service import (
    complete_task,
    set_occurrence_progress,
    set_template_step_progress,
)
from itime.tasks.schedule_service import find_task_occurrence_date
from itime.tasks.service import (
    emit_change,
    ensure_task_editable,
    ensure_task_readable,
    locked_task,
    replay_operation,
    save_operation,
    task_version_conflict,
    validate_cursor,
)

OPEN_FOCUS_STATUSES = ("running", "paused")
TERMINAL_FOCUS_STATUSES = ("finished", "abandoned")
MAX_FOCUS_EVENTS = 10_000
MAX_FOCUS_RANGE = timedelta(days=93)
FUTURE_EVENT_TOLERANCE = timedelta(minutes=5)
STALE_FOCUS_AFTER = timedelta(hours=12)


def elapsed_seconds(focus: FocusSession, at: datetime) -> int:
    elapsed = focus.accumulated_seconds
    if focus.status == "running":
        if focus.active_started_at is None:
            raise AppError(
                "invalid_focus_state",
                "专注计时状态不完整。",
                status=500,
            )
        elapsed += max(
            0,
            int((at - focus.active_started_at).total_seconds()),
        )
    return elapsed


def countdown_state(
    focus: FocusSession,
    at: datetime,
) -> dict[str, int | bool | None]:
    elapsed = elapsed_seconds(focus, at)
    remaining = (
        focus.target_seconds - elapsed
        if focus.target_seconds is not None
        else None
    )
    return {
        "elapsed_seconds": elapsed,
        "remaining_seconds": (
            max(0, remaining) if remaining is not None else None
        ),
        "overtime_seconds": (
            max(0, -remaining) if remaining is not None else 0
        ),
        "overtime": remaining is not None and remaining <= 0,
    }


def _event_result(event: FocusEvent) -> dict:
    return {
        "id": event.id,
        "sequence": event.sequence,
        "kind": event.kind,
        "occurred_at": event.occurred_at,
        "client_recorded_at": event.client_recorded_at,
        "source": event.source,
        "details": event.details,
        "created_at": event.created_at,
    }


def _revision_result(revision: ExecutionRevision) -> dict:
    return {
        "id": revision.id,
        "session_version": revision.session_version,
        "before_data": revision.before_data,
        "after_data": revision.after_data,
        "reason": revision.reason,
        "created_at": revision.created_at,
    }


async def _task_state(
    session: AsyncSession,
    focus: FocusSession,
) -> dict:
    snapshots = focus.step_snapshots
    result = {
        "task_version": None,
        "occurrence_version": None,
        "task_status": None,
        "steps": snapshots,
    }
    if focus.task_id is None:
        return result
    task = await session.scalar(
        select(Task).where(
            Task.id == focus.task_id,
            Task.owner_id == focus.owner_id,
        )
    )
    if task is None or task.purged_at is not None:
        return result
    result["task_version"] = task.version
    result["task_status"] = task.status
    if focus.occurrence_key is None:
        completed = {
            str(step.id): step.completed
            for step in await session.scalars(
                select(TaskStep)
                .where(TaskStep.task_id == task.id)
                .order_by(TaskStep.position)
            )
        }
    else:
        occurrence = await session.scalar(
            select(TaskOccurrenceState).where(
                TaskOccurrenceState.task_id == task.id,
                TaskOccurrenceState.owner_id == focus.owner_id,
                TaskOccurrenceState.occurrence_key == focus.occurrence_key,
            )
        )
        completed_ids = set(
            occurrence.completed_step_ids if occurrence is not None else []
        )
        completed = {
            str(item["id"]): str(item["id"]) in completed_ids
            for item in snapshots
        }
        result["occurrence_version"] = (
            occurrence.version if occurrence is not None else 0
        )
        result["task_status"] = (
            occurrence.status if occurrence is not None else "open"
        )
    result["steps"] = [
        {**item, "completed": completed.get(str(item["id"]), False)}
        for item in snapshots
    ]
    return result


async def focus_result(
    session: AsyncSession,
    focus: FocusSession,
    current_time: datetime,
    *,
    include_task_state: bool = True,
) -> dict:
    timer = countdown_state(focus, current_time)
    task_state = (
        await _task_state(session, focus)
        if include_task_state
        else {
            "task_version": None,
            "occurrence_version": None,
            "task_status": None,
            "steps": focus.step_snapshots,
        }
    )
    return {
        "id": focus.id,
        "source": focus.source,
        "task_id": focus.task_id,
        "occurrence_key": focus.occurrence_key,
        "scheduled_block_id": focus.scheduled_block_id,
        "title": focus.title_snapshot,
        "task_title": focus.task_title_snapshot,
        **task_state,
        "mode": focus.mode,
        "target_seconds": focus.target_seconds,
        "status": focus.status,
        "started_at": focus.started_at,
        "ended_at": focus.ended_at,
        "active_started_at": focus.active_started_at,
        "accumulated_seconds": focus.accumulated_seconds,
        "effective_started_at": focus.effective_started_at,
        "effective_ended_at": focus.effective_ended_at,
        "effective_duration_seconds": focus.effective_duration_seconds,
        "time_zone": focus.time_zone,
        "version": focus.version,
        "attention_required": (
            focus.status in OPEN_FOCUS_STATUSES
            and current_time - focus.started_at >= STALE_FOCUS_AFTER
        ),
        "revised": await _has_revision(session, focus.id),
        "activity_day": activity_day_for(
            focus.effective_started_at or focus.started_at,
            focus.time_zone,
        ),
        "server_now": current_time,
        **timer,
    }


async def _last_event_sequence(
    session: AsyncSession,
    focus_id: UUID,
) -> int:
    return (
        await session.scalar(
            select(func.max(FocusEvent.sequence)).where(
                FocusEvent.focus_session_id == focus_id
            )
        )
        or 0
    )


async def _has_revision(
    session: AsyncSession,
    focus_id: UUID,
) -> bool:
    return (
        await session.scalar(
            select(ExecutionRevision.id)
            .where(ExecutionRevision.focus_session_id == focus_id)
            .limit(1)
        )
        is not None
    )


async def _owned_focus(
    session: AsyncSession,
    owner_id: UUID,
    focus_id: UUID,
    *,
    lock: bool = False,
) -> FocusSession:
    statement = select(FocusSession).where(
        FocusSession.id == focus_id,
        FocusSession.owner_id == owner_id,
    )
    if lock:
        statement = statement.with_for_update()
    focus = await session.scalar(statement)
    if focus is None:
        raise AppError(
            "focus_session_not_found",
            "未找到此专注记录。",
            status=404,
        )
    return focus


async def _resolve_historical_task(
    session: AsyncSession,
    owner_id: UUID,
    task_id: UUID | None,
    occurrence_key: str | None,
) -> Task | None:
    if task_id is None:
        if occurrence_key is not None:
            raise AppError(
                "task_occurrence_requires_task",
                "任务实例必须同时关联任务。",
            )
        return None
    task = await session.scalar(
        select(Task).where(Task.id == task_id, Task.owner_id == owner_id)
    )
    if task is None:
        raise AppError("task_not_found", "未找到此任务。", status=404)
    ensure_task_readable(task)
    schedule = await session.get(TaskSchedule, task.id)
    repeating = schedule is not None and schedule.recurrence_frequency != "none"
    if repeating and occurrence_key is None:
        raise AppError(
            "task_occurrence_required",
            "重复任务必须关联具体实例。",
            status=409,
        )
    if not repeating and occurrence_key is not None:
        raise AppError(
            "task_not_recurring",
            "非重复任务不能指定任务实例。",
            status=409,
        )
    if occurrence_key is not None and schedule is not None:
        state = await session.scalar(
            select(TaskOccurrenceState).where(
                TaskOccurrenceState.task_id == task.id,
                TaskOccurrenceState.owner_id == owner_id,
                TaskOccurrenceState.occurrence_key == occurrence_key,
            )
        )
        if state is None:
            await find_task_occurrence_date(
                session,
                owner_id,
                schedule,
                occurrence_key,
            )
    return task


async def _resolve_task_reference(
    session: AsyncSession,
    owner_id: UUID,
    data: FocusStartInput,
) -> tuple[Task | None, str | None, UUID | None]:
    task_id = data.task_id
    occurrence_key = data.occurrence_key
    scheduled_block_id = data.scheduled_block_id
    if scheduled_block_id is not None:
        row = (
            await session.execute(
                select(ScheduledBlock, Plan)
                .join(
                    PlanVersion,
                    and_(
                        PlanVersion.id == ScheduledBlock.plan_version_id,
                        PlanVersion.owner_id == ScheduledBlock.owner_id,
                    ),
                )
                .join(
                    Plan,
                    and_(
                        Plan.id == PlanVersion.plan_id,
                        Plan.owner_id == PlanVersion.owner_id,
                    ),
                )
                .where(
                    ScheduledBlock.id == scheduled_block_id,
                    ScheduledBlock.owner_id == owner_id,
                    Plan.current_version_id == ScheduledBlock.plan_version_id,
                )
            )
        ).first()
        if row is None:
            raise AppError(
                "scheduled_block_not_found",
                "未找到当前计划中的这个时间块。",
                status=404,
            )
        block, _ = row
        if task_id is not None and task_id != block.task_id:
            raise AppError(
                "focus_task_mismatch",
                "所选任务与计划时间块不一致。",
                status=409,
            )
        if occurrence_key is not None and occurrence_key != block.occurrence_key:
            raise AppError(
                "focus_occurrence_mismatch",
                "所选任务实例与计划时间块不一致。",
                status=409,
            )
        task_id = block.task_id
        occurrence_key = block.occurrence_key
    if task_id is None:
        return None, None, scheduled_block_id
    task = await session.scalar(
        select(Task).where(Task.id == task_id, Task.owner_id == owner_id)
    )
    if task is None:
        raise AppError("task_not_found", "未找到此任务。", status=404)
    ensure_task_editable(task)
    if task.status == "completed":
        raise AppError(
            "task_already_completed",
            "此任务已经完成，不能开始新的专注。",
            status=409,
        )
    schedule = await session.get(TaskSchedule, task.id)
    if schedule is not None and schedule.recurrence_frequency != "none":
        if occurrence_key is None:
            raise AppError(
                "task_occurrence_required",
                "重复任务必须选择具体实例后开始专注。",
                status=409,
            )
        state = await session.scalar(
            select(TaskOccurrenceState).where(
                TaskOccurrenceState.task_id == task.id,
                TaskOccurrenceState.owner_id == owner_id,
                TaskOccurrenceState.occurrence_key == occurrence_key,
            )
        )
        if state is None:
            await find_task_occurrence_date(
                session,
                owner_id,
                schedule,
                occurrence_key,
            )
        elif state.status != "open":
            raise AppError(
                "task_occurrence_not_open",
                "此任务实例已经完成或跳过，不能开始新的专注。",
                status=409,
            )
    elif occurrence_key is not None:
        raise AppError(
            "task_not_recurring",
            "非重复任务不能指定任务实例。",
            status=409,
        )
    return task, occurrence_key, scheduled_block_id


async def _step_snapshots(
    session: AsyncSession,
    task: Task | None,
) -> list[dict]:
    if task is None:
        return []
    steps = list(
        await session.scalars(
            select(TaskStep)
            .where(TaskStep.task_id == task.id)
            .order_by(TaskStep.position)
        )
    )
    return [
        {
            "id": str(step.id),
            "title": step.title,
            "position": step.position,
            "completed": step.completed,
        }
        for step in steps
    ]


async def start_focus(
    session: AsyncSession,
    owner_id: UUID,
    data: FocusStartInput,
    *,
    current_time: datetime,
) -> dict:
    scope = "focus.start"
    replay = await replay_operation(
        session,
        owner_id,
        data.operation_id,
        scope,
        data,
    )
    if replay is not None:
        return replay
    active = await session.scalar(
        select(FocusSession)
        .where(
            FocusSession.owner_id == owner_id,
            FocusSession.status.in_(OPEN_FOCUS_STATUSES),
        )
        .with_for_update()
    )
    if active is not None:
        current = await focus_result(session, active, current_time)
        raise AppError(
            "focus_session_active",
            "已有进行中或暂停的专注，请先处理当前专注。",
            status=409,
            details={"current_focus": jsonable_encoder(current)},
        )
    time_zone = await session.scalar(
        select(UserTimeSettings.time_zone).where(
            UserTimeSettings.owner_id == owner_id
        )
    )
    if time_zone is None:
        raise AppError(
            "time_settings_required",
            "请先保存当前使用的时区，再开始专注。",
            status=409,
        )
    task, occurrence_key, scheduled_block_id = await _resolve_task_reference(
        session,
        owner_id,
        data,
    )
    title = data.title or (task.title if task is not None else None)
    if title is None:
        raise AppError("focus_title_required", "请输入专注标题。")
    focus = FocusSession(
        owner_id=owner_id,
        source="timer",
        task_id=task.id if task is not None else None,
        occurrence_key=occurrence_key,
        scheduled_block_id=scheduled_block_id,
        title_snapshot=title,
        task_title_snapshot=task.title if task is not None else None,
        step_snapshots=await _step_snapshots(session, task),
        mode=data.mode,
        target_seconds=data.target_seconds,
        status="running",
        started_at=current_time,
        ended_at=None,
        active_started_at=current_time,
        accumulated_seconds=0,
        effective_started_at=None,
        effective_ended_at=None,
        effective_duration_seconds=None,
        time_zone=time_zone,
    )
    session.add(focus)
    await session.flush()
    event = FocusEvent(
        owner_id=owner_id,
        focus_session_id=focus.id,
        sequence=1,
        kind="start",
        occurred_at=current_time,
        client_recorded_at=None,
        source="online",
        details={
            "mode": focus.mode,
            "target_seconds": focus.target_seconds,
        },
    )
    session.add(event)
    emit_change(
        session,
        owner_id,
        "focus_session",
        focus.id,
        "started",
        focus.version,
    )
    await session.flush()
    result = {
        "operation_id": data.operation_id,
        "focus": await focus_result(session, focus, current_time),
        "server_now": current_time,
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


def _allowed_transition(status: str, action: str) -> bool:
    return (
        (status == "running" and action in {"pause", "finish", "abandon"})
        or (status == "paused" and action in {"resume", "finish", "abandon"})
    )


def apply_transition(
    focus: FocusSession,
    action: str,
    occurred_at: datetime,
) -> str:
    if not _allowed_transition(focus.status, action):
        raise AppError(
            "invalid_focus_transition",
            "当前专注状态不能执行此操作。",
            status=409,
            details={"status": focus.status, "action": action},
        )
    if action == "pause":
        if focus.active_started_at is None:
            raise AppError("invalid_focus_state", "专注计时状态不完整。", status=500)
        focus.accumulated_seconds += max(
            0,
            int((occurred_at - focus.active_started_at).total_seconds()),
        )
        focus.active_started_at = None
        focus.status = "paused"
        return "paused"
    if action == "resume":
        focus.active_started_at = occurred_at
        focus.status = "running"
        return "resumed"
    if focus.status == "running":
        if focus.active_started_at is None:
            raise AppError(
                "invalid_focus_state",
                "专注计时状态不完整。",
                status=500,
            )
        focus.accumulated_seconds += max(
            0,
            int((occurred_at - focus.active_started_at).total_seconds()),
        )
    focus.active_started_at = None
    focus.status = "finished" if action == "finish" else "abandoned"
    focus.ended_at = occurred_at
    focus.effective_started_at = focus.started_at
    focus.effective_ended_at = occurred_at
    focus.effective_duration_seconds = focus.accumulated_seconds
    return "finished" if action == "finish" else "abandoned"


async def _transition_time(
    session: AsyncSession,
    focus: FocusSession,
    data: FocusTransitionInput,
    current_time: datetime,
) -> tuple[datetime, str]:
    if data.client_recorded_at is None:
        return current_time, "online"
    occurred_at = data.client_recorded_at.astimezone(UTC)
    if occurred_at > current_time + FUTURE_EVENT_TOLERANCE:
        raise AppError(
            "invalid_focus_event_time",
            "离线操作时间晚于服务端时间过多，请核对设备时间。",
            status=422,
        )
    last_event_at = await session.scalar(
        select(FocusEvent.occurred_at)
        .where(FocusEvent.focus_session_id == focus.id)
        .order_by(FocusEvent.sequence.desc())
        .limit(1)
    )
    if last_event_at is not None and occurred_at < last_event_at:
        raise AppError(
            "focus_event_out_of_order",
            "此离线操作早于已保存状态，请核对后重试。",
            status=409,
            details={"last_event_at": last_event_at.isoformat()},
        )
    return occurred_at, "offline"


async def transition_focus(
    session: AsyncSession,
    owner_id: UUID,
    focus_id: UUID,
    data: FocusTransitionInput,
    *,
    current_time: datetime,
) -> dict:
    scope = f"focus.transition:{focus_id}"
    replay = await replay_operation(
        session,
        owner_id,
        data.operation_id,
        scope,
        data,
    )
    if replay is not None:
        return replay
    focus = await _owned_focus(session, owner_id, focus_id, lock=True)
    if focus.version != data.expected_version:
        current = await focus_result(session, focus, current_time)
        raise AppError(
            "focus_version_conflict",
            "此专注已在其他页面或设备更新，请比较后再继续。",
            status=409,
            details={"current_focus": jsonable_encoder(current)},
        )
    sequence = await _last_event_sequence(session, focus.id)
    if sequence >= MAX_FOCUS_EVENTS:
        raise AppError(
            "focus_event_limit_reached",
            "此专注的操作记录已达到上限，请结束后新建专注。",
            status=409,
        )
    occurred_at, source = await _transition_time(
        session,
        focus,
        data,
        current_time,
    )
    change_action = apply_transition(focus, data.action, occurred_at)
    focus.version += 1
    focus.updated_at = current_time
    event = FocusEvent(
        owner_id=owner_id,
        focus_session_id=focus.id,
        sequence=sequence + 1,
        kind=data.action,
        occurred_at=occurred_at,
        client_recorded_at=(
            data.client_recorded_at.astimezone(UTC)
            if data.client_recorded_at is not None
            else None
        ),
        source=source,
        details={},
    )
    session.add(event)
    emit_change(
        session,
        owner_id,
        "focus_session",
        focus.id,
        change_action,
        focus.version,
    )
    await session.flush()
    result = {
        "operation_id": data.operation_id,
        "focus": await focus_result(session, focus, current_time),
        "server_now": current_time,
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


async def current_focus(
    session: AsyncSession,
    owner_id: UUID,
    *,
    current_time: datetime,
) -> dict | None:
    focus = await session.scalar(
        select(FocusSession).where(
            FocusSession.owner_id == owner_id,
            FocusSession.status.in_(OPEN_FOCUS_STATUSES),
        )
    )
    if focus is None:
        return None
    return await focus_result(session, focus, current_time)


async def focus_detail(
    session: AsyncSession,
    owner_id: UUID,
    focus_id: UUID,
    *,
    current_time: datetime,
) -> dict:
    focus = await _owned_focus(session, owner_id, focus_id)
    events = list(
        await session.scalars(
            select(FocusEvent)
            .where(
                FocusEvent.focus_session_id == focus.id,
                FocusEvent.owner_id == owner_id,
            )
            .order_by(FocusEvent.sequence)
        )
    )
    revisions = list(
        await session.scalars(
            select(ExecutionRevision)
            .where(
                ExecutionRevision.focus_session_id == focus.id,
                ExecutionRevision.owner_id == owner_id,
            )
            .order_by(ExecutionRevision.session_version)
        )
    )
    return {
        "focus": await focus_result(session, focus, current_time),
        "events": [_event_result(event) for event in events],
        "revisions": [_revision_result(revision) for revision in revisions],
        "server_now": current_time,
    }


async def list_focus_sessions(
    session: AsyncSession,
    owner_id: UUID,
    *,
    start: datetime,
    end: datetime,
    status: str | None,
    before_time: datetime | None,
    before_id: UUID | None,
    limit: int,
    current_time: datetime,
) -> dict:
    validate_cursor(before_time, before_id)
    if start.tzinfo is None or end.tzinfo is None or end <= start:
        raise AppError(
            "invalid_focus_range",
            "专注查询时间范围无效。",
        )
    if end - start > MAX_FOCUS_RANGE:
        raise AppError(
            "focus_range_too_large",
            "专注查询范围不能超过 93 天。",
        )
    statement = select(FocusSession).where(
        FocusSession.owner_id == owner_id,
        FocusSession.started_at >= start.astimezone(UTC),
        FocusSession.started_at < end.astimezone(UTC),
    )
    if status is not None:
        statement = statement.where(FocusSession.status == status)
    if before_time is not None and before_id is not None:
        statement = statement.where(
            or_(
                FocusSession.started_at < before_time,
                and_(
                    FocusSession.started_at == before_time,
                    FocusSession.id < before_id,
                ),
            )
        )
    rows = list(
        await session.scalars(
            statement.order_by(
                FocusSession.started_at.desc(),
                FocusSession.id.desc(),
            ).limit(limit + 1)
        )
    )
    page = rows[:limit]
    return {
        "items": [
            await focus_result(
                session,
                focus,
                current_time,
                include_task_state=False,
            )
            for focus in page
        ],
        "next_cursor": (
            {
                "before_time": page[-1].started_at,
                "before_id": page[-1].id,
            }
            if len(rows) > limit
            else None
        ),
        "server_now": current_time,
    }


async def _check_focus_version(
    session: AsyncSession,
    focus: FocusSession,
    expected_version: int,
    current_time: datetime,
) -> None:
    if focus.version == expected_version:
        return
    current = await focus_result(session, focus, current_time)
    raise AppError(
        "focus_version_conflict",
        "此专注已在其他页面或设备更新，请比较后再继续。",
        status=409,
        details={"current_focus": jsonable_encoder(current)},
    )


async def update_focus_progress(
    session: AsyncSession,
    owner_id: UUID,
    focus_id: UUID,
    data: FocusProgressInput,
    *,
    current_time: datetime,
) -> dict:
    scope = f"focus.progress:{focus_id}"
    replay = await replay_operation(
        session,
        owner_id,
        data.operation_id,
        scope,
        data,
    )
    if replay is not None:
        return replay
    focus = await _owned_focus(session, owner_id, focus_id, lock=True)
    await _check_focus_version(
        session,
        focus,
        data.expected_version,
        current_time,
    )
    if focus.status not in OPEN_FOCUS_STATUSES:
        raise AppError(
            "focus_not_open",
            "只有进行中或暂停的专注可以更新步骤。",
            status=409,
        )
    if focus.task_id is None:
        raise AppError(
            "focus_task_required",
            "此专注未关联任务，不能更新任务步骤。",
            status=409,
        )
    task = await locked_task(session, owner_id, focus.task_id)
    if task.version != data.expected_task_version:
        raise task_version_conflict(task)
    if task.status == "completed":
        raise AppError(
            "task_already_completed",
            "此任务已经完成，不能继续更新步骤。",
            status=409,
        )
    if focus.occurrence_key is None:
        if data.expected_occurrence_version is not None:
            raise AppError(
                "task_not_recurring",
                "非重复任务不能提交任务实例版本。",
                status=409,
            )
        task_result, changed = await set_template_step_progress(
            session,
            owner_id,
            task,
            data.completed_step_ids,
            changed_at=current_time,
        )
        occurrence_result = None
    else:
        if data.expected_occurrence_version is None:
            raise AppError(
                "task_occurrence_version_required",
                "请提供任务实例版本后重试。",
                status=409,
            )
        task_result, occurrence_result, changed = await set_occurrence_progress(
            session,
            owner_id,
            task,
            focus.occurrence_key,
            data.expected_occurrence_version,
            data.completed_step_ids,
            status="open",
            require_current_open=True,
            changed_at=current_time,
        )
    if changed:
        sequence = await _last_event_sequence(session, focus.id)
        focus.version += 1
        focus.updated_at = current_time
        session.add(
            FocusEvent(
                owner_id=owner_id,
                focus_session_id=focus.id,
                sequence=sequence + 1,
                kind="progress",
                occurred_at=current_time,
                client_recorded_at=None,
                source="online",
                details={
                    "completed_step_ids": [
                        str(step_id) for step_id in data.completed_step_ids
                    ],
                    "task_version": task.version,
                    "occurrence_version": (
                        occurrence_result["occurrence_version"]
                        if occurrence_result is not None
                        else None
                    ),
                },
            )
        )
        emit_change(
            session,
            owner_id,
            "focus_session",
            focus.id,
            "updated",
            focus.version,
        )
        await session.flush()
    result = {
        "operation_id": data.operation_id,
        "focus": await focus_result(session, focus, current_time),
        "task": task_result,
        "occurrence": occurrence_result,
        "server_now": current_time,
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


async def complete_focus_task(
    session: AsyncSession,
    owner_id: UUID,
    focus_id: UUID,
    data: FocusTaskCompletionInput,
    *,
    current_time: datetime,
) -> dict:
    scope = f"focus.complete_task:{focus_id}"
    replay = await replay_operation(
        session,
        owner_id,
        data.operation_id,
        scope,
        data,
    )
    if replay is not None:
        return replay
    focus = await _owned_focus(session, owner_id, focus_id, lock=True)
    await _check_focus_version(
        session,
        focus,
        data.expected_version,
        current_time,
    )
    if focus.status not in TERMINAL_FOCUS_STATUSES:
        raise AppError(
            "focus_not_terminal",
            "请先结束或放弃当前专注，再确认任务完成。",
            status=409,
        )
    if focus.task_id is None:
        raise AppError(
            "focus_task_required",
            "此专注未关联任务。",
            status=409,
        )
    task = await locked_task(session, owner_id, focus.task_id)
    if task.version != data.expected_task_version:
        raise task_version_conflict(task)
    task_result, occurrence_result, changed = await complete_task(
        session,
        owner_id,
        task,
        focus.occurrence_key,
        data.expected_occurrence_version,
        changed_at=current_time,
    )
    if changed:
        sequence = await _last_event_sequence(session, focus.id)
        focus.version += 1
        focus.updated_at = current_time
        session.add(
            FocusEvent(
                owner_id=owner_id,
                focus_session_id=focus.id,
                sequence=sequence + 1,
                kind="task_completed",
                occurred_at=current_time,
                client_recorded_at=None,
                source="online",
                details={
                    "task_id": str(task.id),
                    "occurrence_key": focus.occurrence_key,
                    "task_version": task.version,
                    "occurrence_version": (
                        occurrence_result["occurrence_version"]
                        if occurrence_result is not None
                        else None
                    ),
                },
            )
        )
        emit_change(
            session,
            owner_id,
            "focus_session",
            focus.id,
            "updated",
            focus.version,
        )
        await session.flush()
    result = {
        "operation_id": data.operation_id,
        "focus": await focus_result(session, focus, current_time),
        "task": task_result,
        "occurrence": occurrence_result,
        "server_now": current_time,
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


async def _ensure_no_focus_overlap(
    session: AsyncSession,
    owner_id: UUID,
    starts_at: datetime,
    ends_at: datetime,
    *,
    exclude_id: UUID | None = None,
) -> None:
    statement = select(FocusSession.id).where(
        FocusSession.owner_id == owner_id,
        or_(
            and_(
                FocusSession.effective_started_at.is_not(None),
                FocusSession.effective_ended_at.is_not(None),
                FocusSession.effective_started_at < ends_at,
                FocusSession.effective_ended_at > starts_at,
            ),
            and_(
                FocusSession.status.in_(OPEN_FOCUS_STATUSES),
                FocusSession.started_at < ends_at,
            ),
        ),
    )
    if exclude_id is not None:
        statement = statement.where(FocusSession.id != exclude_id)
    conflict_id = await session.scalar(statement.limit(1))
    if conflict_id is not None:
        raise AppError(
            "focus_time_conflict",
            "这段时间与另一条专注记录重叠，请调整后保存。",
            status=409,
            details={"conflict_focus_id": str(conflict_id)},
        )


async def _resolve_manual_values(
    session: AsyncSession,
    owner_id: UUID,
    data: FocusManualCreateInput | FocusRevisionInput,
    *,
    current_time: datetime,
    exclude_id: UUID | None = None,
) -> tuple[datetime, datetime, Task | None, list[dict]]:
    starts_at, ends_at = resolve_manual_interval(
        data.local_started_at,
        data.local_ended_at,
        data.time_zone,
        data.start_utc_offset_minutes,
        data.end_utc_offset_minutes,
    )
    if ends_at > current_time + FUTURE_EVENT_TOLERANCE:
        raise AppError(
            "invalid_focus_event_time",
            "补记结束时间晚于服务端时间过多，请核对后重试。",
            status=422,
        )
    interval_seconds = int((ends_at - starts_at).total_seconds())
    if data.effective_duration_seconds > interval_seconds:
        raise AppError(
            "invalid_focus_duration",
            "有效专注时长不能超过开始到结束的时间跨度。",
            status=422,
        )
    await _ensure_no_focus_overlap(
        session,
        owner_id,
        starts_at,
        ends_at,
        exclude_id=exclude_id,
    )
    task = await _resolve_historical_task(
        session,
        owner_id,
        data.task_id,
        data.occurrence_key,
    )
    return starts_at, ends_at, task, await _step_snapshots(session, task)


async def create_manual_focus(
    session: AsyncSession,
    owner_id: UUID,
    data: FocusManualCreateInput,
    *,
    current_time: datetime,
) -> dict:
    scope = "focus.manual"
    replay = await replay_operation(
        session,
        owner_id,
        data.operation_id,
        scope,
        data,
    )
    if replay is not None:
        return replay
    starts_at, ends_at, task, steps = await _resolve_manual_values(
        session,
        owner_id,
        data,
        current_time=current_time,
    )
    focus = FocusSession(
        owner_id=owner_id,
        source="manual",
        task_id=task.id if task is not None else None,
        occurrence_key=data.occurrence_key,
        scheduled_block_id=None,
        title_snapshot=data.title,
        task_title_snapshot=task.title if task is not None else None,
        step_snapshots=steps,
        mode="stopwatch",
        target_seconds=None,
        status="finished",
        started_at=starts_at,
        ended_at=ends_at,
        active_started_at=None,
        accumulated_seconds=data.effective_duration_seconds,
        effective_started_at=starts_at,
        effective_ended_at=ends_at,
        effective_duration_seconds=data.effective_duration_seconds,
        time_zone=data.time_zone,
    )
    session.add(focus)
    await session.flush()
    session.add_all(
        [
            FocusEvent(
                owner_id=owner_id,
                focus_session_id=focus.id,
                sequence=1,
                kind="start",
                occurred_at=starts_at,
                client_recorded_at=None,
                source="online",
                details={"manual": True},
            ),
            FocusEvent(
                owner_id=owner_id,
                focus_session_id=focus.id,
                sequence=2,
                kind="finish",
                occurred_at=ends_at,
                client_recorded_at=None,
                source="online",
                details={
                    "manual": True,
                    "effective_duration_seconds": data.effective_duration_seconds,
                },
            ),
        ]
    )
    emit_change(
        session,
        owner_id,
        "focus_session",
        focus.id,
        "finished",
        focus.version,
    )
    await session.flush()
    result = {
        "operation_id": data.operation_id,
        "focus": await focus_result(session, focus, current_time),
        "server_now": current_time,
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


def _effective_snapshot(focus: FocusSession) -> dict:
    return {
        "title": focus.title_snapshot,
        "task_id": str(focus.task_id) if focus.task_id is not None else None,
        "occurrence_key": focus.occurrence_key,
        "effective_started_at": focus.effective_started_at,
        "effective_ended_at": focus.effective_ended_at,
        "effective_duration_seconds": focus.effective_duration_seconds,
        "time_zone": focus.time_zone,
    }


async def revise_focus(
    session: AsyncSession,
    owner_id: UUID,
    focus_id: UUID,
    data: FocusRevisionInput,
    *,
    current_time: datetime,
) -> dict:
    scope = f"focus.revise:{focus_id}"
    replay = await replay_operation(
        session,
        owner_id,
        data.operation_id,
        scope,
        data,
    )
    if replay is not None:
        return replay
    focus = await _owned_focus(session, owner_id, focus_id, lock=True)
    await _check_focus_version(
        session,
        focus,
        data.expected_version,
        current_time,
    )
    if focus.status not in TERMINAL_FOCUS_STATUSES:
        raise AppError(
            "focus_not_terminal",
            "进行中或暂停的专注不能修改，请先结束或放弃。",
            status=409,
        )
    starts_at, ends_at, task, steps = await _resolve_manual_values(
        session,
        owner_id,
        data,
        current_time=current_time,
        exclude_id=focus.id,
    )
    before = jsonable_encoder(_effective_snapshot(focus))
    keep_block = (
        focus.task_id == data.task_id
        and focus.occurrence_key == data.occurrence_key
    )
    focus.task_id = task.id if task is not None else None
    focus.occurrence_key = data.occurrence_key
    if not keep_block:
        focus.scheduled_block_id = None
    focus.title_snapshot = data.title
    focus.task_title_snapshot = task.title if task is not None else None
    focus.step_snapshots = steps
    focus.effective_started_at = starts_at
    focus.effective_ended_at = ends_at
    focus.effective_duration_seconds = data.effective_duration_seconds
    focus.time_zone = data.time_zone
    focus.version += 1
    focus.updated_at = current_time
    after = jsonable_encoder(_effective_snapshot(focus))
    session.add(
        ExecutionRevision(
            owner_id=owner_id,
            focus_session_id=focus.id,
            session_version=focus.version,
            before_data=before,
            after_data=after,
            reason=data.reason,
        )
    )
    emit_change(
        session,
        owner_id,
        "focus_session",
        focus.id,
        "revised",
        focus.version,
    )
    await session.flush()
    result = {
        "operation_id": data.operation_id,
        "focus": await focus_result(session, focus, current_time),
        "server_now": current_time,
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
