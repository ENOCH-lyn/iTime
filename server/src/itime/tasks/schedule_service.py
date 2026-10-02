from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from itime.calendar.models import UserTimeSettings
from itime.calendar.recurrence import (
    RecurrencePlan,
    build_recurrence_plan,
    occurrence_dates,
)
from itime.calendar.schemas import RecurrenceInput
from itime.calendar.service import validate_time_zone
from itime.calendar.time_math import (
    earliest_valid_local_datetime,
    occurrence_key,
    resolve_local_datetime,
)
from itime.errors import AppError
from itime.tasks.models import (
    Task,
    TaskLabel,
    TaskLink,
    TaskOccurrenceState,
    TaskSchedule,
    TaskStep,
)
from itime.tasks.schemas import TaskScheduleFields

MAX_TASK_OCCURRENCES = 2000
MAX_TASK_SCHEDULES = 1000
MAX_MAKEUP_OCCURRENCES = 366


@dataclass(frozen=True)
class ResolvedTaskSchedule:
    due_kind: str
    plan: RecurrencePlan | None
    last_occurrence_date: date | None
    due_local_time: time | None
    due_at: datetime | None
    time_zone: str | None
    warning_occurrence_keys: tuple[str, ...]


def _shift_date_clamped(value: date, days: int) -> date:
    try:
        return value + timedelta(days=days)
    except OverflowError:
        return date.min if days < 0 else date.max


def _default_recurrence(recurrence: RecurrenceInput) -> bool:
    return recurrence == RecurrenceInput()


async def resolve_task_schedule(
    session: AsyncSession,
    owner_id: UUID,
    task: Task,
    data: TaskScheduleFields,
) -> ResolvedTaskSchedule:
    if data.due_kind == "none":
        if (
            data.due_date is not None
            or data.due_time is not None
            or data.time_zone is not None
            or data.ambiguous_utc_offset_minutes is not None
            or not _default_recurrence(data.recurrence)
        ):
            raise AppError(
                "invalid_task_schedule",
                "无截止时间的任务不能设置日期、时刻、时区或重复规则。",
            )
        return ResolvedTaskSchedule(
            due_kind="none",
            plan=None,
            last_occurrence_date=None,
            due_local_time=None,
            due_at=None,
            time_zone=None,
            warning_occurrence_keys=(),
        )

    if data.due_date is None:
        raise AppError(
            "task_due_date_required",
            "日期型和定时截止都必须提供截止日期。",
        )
    if data.recurrence.week_numbers:
        raise AppError(
            "invalid_task_recurrence",
            "任务重复不使用学期周次。",
        )
    time_zone = data.time_zone
    if time_zone is None:
        time_zone = await session.scalar(
            select(UserTimeSettings.time_zone).where(
                UserTimeSettings.owner_id == owner_id
            )
        )
    if time_zone is None:
        raise AppError(
            "time_settings_required",
            "请先保存当前使用的时区，再设置任务截止时间。",
            status=409,
        )
    time_zone = validate_time_zone(time_zone)
    plan = build_recurrence_plan(
        data.recurrence,
        kind="fixed",
        requested_start_date=data.due_date,
        term=None,
    )
    dates = occurrence_dates(plan)
    if plan.frequency != "none" and task.status == "completed":
        raise AppError(
            "completed_task_cannot_repeat",
            "已完成任务不能直接改为重复任务，请先重新打开。",
            status=409,
        )

    if data.due_kind == "date":
        if data.due_time is not None or data.ambiguous_utc_offset_minutes is not None:
            raise AppError(
                "invalid_task_due_time",
                "日期型截止不接受具体时刻或 UTC 偏移。",
            )
        return ResolvedTaskSchedule(
            due_kind="date",
            plan=plan,
            last_occurrence_date=dates[-1],
            due_local_time=None,
            due_at=None,
            time_zone=time_zone,
            warning_occurrence_keys=(),
        )

    if data.due_time is None:
        raise AppError(
            "task_due_time_required",
            "定时截止必须提供本地截止时刻。",
        )
    if data.due_time.tzinfo is not None:
        raise AppError(
            "invalid_task_due_time",
            "任务截止时刻必须是不带 UTC 偏移的本地墙上时间。",
        )
    warning_keys: list[str] = []
    first_due_at: datetime | None = None
    for index, occurrence_date in enumerate(dates):
        due_at, warning = resolve_local_datetime(
            datetime.combine(occurrence_date, data.due_time),
            time_zone,
            preferred_offset_minutes=data.ambiguous_utc_offset_minutes,
            strict_ambiguous=True,
        )
        if warning is not None:
            warning_keys.append(occurrence_key(occurrence_date, data.due_time))
        if index == 0:
            first_due_at = due_at
    if first_due_at is None:
        raise AppError(
            "invalid_local_time",
            "首个任务截止位于夏令时跳过的本地时间，请选择其他时刻。",
        )
    return ResolvedTaskSchedule(
        due_kind="datetime",
        plan=plan,
        last_occurrence_date=dates[-1],
        due_local_time=data.due_time,
        due_at=first_due_at,
        time_zone=time_zone,
        warning_occurrence_keys=tuple(warning_keys),
    )


def task_schedule_values(
    data: TaskScheduleFields,
    resolved: ResolvedTaskSchedule,
) -> dict:
    plan = resolved.plan
    return {
        "due_kind": resolved.due_kind,
        "start_date": plan.start_date if plan is not None else None,
        "last_occurrence_date": resolved.last_occurrence_date,
        "due_local_time": resolved.due_local_time,
        "due_at": resolved.due_at,
        "time_zone": resolved.time_zone,
        "ambiguous_offset_minutes": data.ambiguous_utc_offset_minutes,
        "recurrence_frequency": plan.frequency if plan is not None else "none",
        "recurrence_interval": plan.interval if plan is not None else 1,
        "recurrence_weekdays": list(plan.weekdays) if plan is not None else [],
        "recurrence_month": plan.month if plan is not None else None,
        "recurrence_day_of_month": (
            plan.day_of_month if plan is not None else None
        ),
        "recurrence_until": plan.until if plan is not None else None,
        "recurrence_count": plan.count if plan is not None else None,
        "splittable": data.splittable,
        "minimum_block_minutes": data.minimum_block_minutes,
    }


def task_schedule_result(schedule: TaskSchedule | None) -> dict:
    if schedule is None:
        return {
            "configured": False,
            "preferences_saved": False,
            "predecessor_task_id": None,
            "due_kind": "none",
            "due_date": None,
            "last_occurrence_date": None,
            "due_time": None,
            "due_at": None,
            "time_zone": None,
            "ambiguous_utc_offset_minutes": None,
            "splittable": True,
            "minimum_block_minutes": 15,
            "recurrence": RecurrenceInput().model_dump(mode="json"),
        }
    return {
        "configured": schedule.due_kind != "none",
        "preferences_saved": True,
        "predecessor_task_id": schedule.predecessor_task_id,
        "due_kind": schedule.due_kind,
        "due_date": schedule.start_date,
        "last_occurrence_date": schedule.last_occurrence_date,
        "due_time": schedule.due_local_time,
        "due_at": schedule.due_at,
        "time_zone": schedule.time_zone,
        "ambiguous_utc_offset_minutes": schedule.ambiguous_offset_minutes,
        "splittable": schedule.splittable,
        "minimum_block_minutes": schedule.minimum_block_minutes,
        "recurrence": {
            "frequency": schedule.recurrence_frequency,
            "interval": schedule.recurrence_interval,
            "weekdays": schedule.recurrence_weekdays,
            "month": schedule.recurrence_month,
            "day_of_month": schedule.recurrence_day_of_month,
            "until": schedule.recurrence_until,
            "count": schedule.recurrence_count,
            "week_numbers": [],
        },
    }


def task_plan(schedule: TaskSchedule) -> RecurrencePlan:
    if schedule.start_date is None:
        raise AppError("task_has_no_due_date", "此任务没有可展开的截止日期。")
    return RecurrencePlan(
        start_date=schedule.start_date,
        frequency=schedule.recurrence_frequency,
        interval=schedule.recurrence_interval,
        weekdays=tuple(schedule.recurrence_weekdays),
        month=schedule.recurrence_month,
        day_of_month=schedule.recurrence_day_of_month,
        until=schedule.recurrence_until,
        count=schedule.recurrence_count,
        week_numbers=(),
        course_first_week_monday=None,
    )


def task_occurrence_key(schedule: TaskSchedule, occurrence_date: date) -> str:
    return (
        occurrence_date.isoformat()
        if schedule.due_kind == "date"
        else occurrence_key(occurrence_date, schedule.due_local_time)
    )


async def find_task_occurrence_date(
    session: AsyncSession,
    owner_id: UUID,
    schedule: TaskSchedule,
    key: str,
) -> date:
    valid_dates = occurrence_dates(task_plan(schedule))
    for occurrence_date in valid_dates:
        if task_occurrence_key(schedule, occurrence_date) == key:
            return occurrence_date
    state = await session.scalar(
        select(TaskOccurrenceState).where(
            TaskOccurrenceState.owner_id == owner_id,
            TaskOccurrenceState.task_id == schedule.task_id,
            TaskOccurrenceState.kind == "base",
            TaskOccurrenceState.occurrence_key == key,
        )
    )
    if state is not None and state.original_date in valid_dates:
        return state.original_date
    raise AppError(
        "task_occurrence_not_found",
        "未找到此重复任务实例。",
        status=404,
    )


def resolve_effective_due(
    schedule: TaskSchedule,
    due_date: date,
    due_time: time | None,
    ambiguous_offset_minutes: int | None,
) -> tuple[datetime | None, tuple[str, ...]]:
    if schedule.due_kind == "date":
        if due_time is not None or ambiguous_offset_minutes is not None:
            raise AppError(
                "invalid_task_due_time",
                "日期型任务实例不接受具体时刻或 UTC 偏移。",
            )
        return None, ()
    if due_time is None or due_time.tzinfo is not None:
        raise AppError(
            "task_due_time_required",
            "定时任务实例必须提供不带 UTC 偏移的本地时刻。",
        )
    due_at, warning = resolve_local_datetime(
        datetime.combine(due_date, due_time),
        schedule.time_zone,
        preferred_offset_minutes=ambiguous_offset_minutes,
        strict_ambiguous=True,
    )
    if warning is not None or due_at is None:
        raise AppError(
            "invalid_local_time",
            "任务实例截止位于夏令时跳过的本地时间，请选择其他时刻。",
        )
    return due_at, ()


def _date_due_boundary(due_date: date, time_zone: str) -> datetime | None:
    try:
        boundary_date = due_date + timedelta(days=1)
    except OverflowError:
        return None
    local_boundary = datetime.combine(boundary_date, time.min)
    for minutes in range(24 * 60 + 1):
        resolved = earliest_valid_local_datetime(
            local_boundary + timedelta(minutes=minutes),
            time_zone,
        )
        if resolved is not None:
            return resolved
    return None


def task_occurrence_cutoff(
    schedule: TaskSchedule,
    occurrence_date: date,
) -> datetime | None:
    if schedule.due_kind == "date":
        return _date_due_boundary(occurrence_date, schedule.time_zone)
    due_at, _ = resolve_local_datetime(
        datetime.combine(occurrence_date, schedule.due_local_time),
        schedule.time_zone,
        preferred_offset_minutes=schedule.ambiguous_offset_minutes,
        strict_ambiguous=False,
    )
    return due_at


def occurrence_state_result(state: TaskOccurrenceState) -> dict:
    return {
        "id": state.id,
        "occurrence_key": state.occurrence_key,
        "original_date": state.original_date,
        "kind": state.kind,
        "effective_date": state.effective_date,
        "effective_time": state.effective_local_time,
        "effective_at": state.effective_at,
        "effective_time_zone": state.effective_time_zone,
        "ambiguous_utc_offset_minutes": state.ambiguous_offset_minutes,
        "rescheduled": state.rescheduled,
        "status": state.status,
        "completed_at": state.completed_at,
        "completed_step_ids": state.completed_step_ids,
        "makeup_for_key": state.makeup_for_key,
        "makeup_for_date": state.makeup_for_date,
        "version": state.version,
        "created_at": state.created_at,
        "updated_at": state.updated_at,
    }


def project_task_occurrence(
    task: Task,
    schedule: TaskSchedule,
    occurrence_date: date,
    state: TaskOccurrenceState | None,
    *,
    step_count: int,
) -> dict:
    has_schedule_override = state is not None and (
        state.kind == "makeup" or state.rescheduled
    )
    effective_date = (
        state.effective_date if has_schedule_override else occurrence_date
    )
    effective_time = (
        state.effective_local_time
        if has_schedule_override
        else schedule.due_local_time
    )
    effective_at = state.effective_at if has_schedule_override else None
    effective_time_zone = (
        state.effective_time_zone if has_schedule_override else schedule.time_zone
    )
    warning_codes: list[str] = []
    if schedule.due_kind == "datetime" and not has_schedule_override:
        effective_at, warning = resolve_local_datetime(
            datetime.combine(effective_date, effective_time),
            effective_time_zone,
            preferred_offset_minutes=schedule.ambiguous_offset_minutes,
            strict_ambiguous=False,
        )
        if warning is not None:
            warning_codes.append(warning)
    status = (
        state.status
        if state is not None
        else (
            task.status
            if schedule.recurrence_frequency == "none"
            else "open"
        )
    )
    completed_steps = state.completed_step_ids if state is not None else []
    effective_step_count = (
        state.step_count_snapshot if state is not None else step_count
    )
    return {
        "task_id": task.id,
        "task_version": task.version,
        "occurrence_state_id": state.id if state is not None else None,
        "occurrence_version": state.version if state is not None else 0,
        "occurrence_key": (
            state.occurrence_key
            if state is not None
            else task_occurrence_key(schedule, occurrence_date)
        ),
        "kind": state.kind if state is not None else "base",
        "makeup_for_key": state.makeup_for_key if state is not None else None,
        "title": task.title,
        "description": task.description,
        "project_id": task.project_id,
        "estimated_minutes": task.estimated_minutes,
        "priority": task.priority,
        "splittable": schedule.splittable,
        "minimum_block_minutes": schedule.minimum_block_minutes,
        "due_kind": schedule.due_kind,
        "original_due_date": occurrence_date,
        "due_date": effective_date,
        "due_time": effective_time,
        "due_at": effective_at,
        "overdue_after": (
            _date_due_boundary(effective_date, effective_time_zone)
            if schedule.due_kind == "date"
            else effective_at
        ),
        "time_zone": effective_time_zone,
        "status": status,
        "completed_at": state.completed_at if state is not None else task.completed_at,
        "step_progress": {
            "total": effective_step_count,
            "completed": len(completed_steps),
        },
        "completed_step_ids": completed_steps,
        "warning_codes": warning_codes,
    }


async def validate_completed_steps(
    session: AsyncSession,
    task_id: UUID,
    completed_step_ids: list[UUID],
) -> list[str]:
    available = set(
        await session.scalars(select(TaskStep.id).where(TaskStep.task_id == task_id))
    )
    requested = set(completed_step_ids)
    if not requested.issubset(available):
        raise AppError(
            "task_steps_changed",
            "任务步骤已变化，请刷新后重新提交实例进度。",
            status=409,
        )
    return [str(step_id) for step_id in completed_step_ids]


async def clone_task_template(
    session: AsyncSession,
    task: Task,
) -> tuple[Task, dict[str, str]]:
    successor = Task(
        owner_id=task.owner_id,
        project_id=task.project_id,
        title=task.title,
        description=task.description,
        estimated_minutes=task.estimated_minutes,
        estimate_source=task.estimate_source,
        priority=task.priority,
        status="open",
        completed_at=None,
    )
    session.add(successor)
    await session.flush()

    steps = list(
        await session.scalars(
            select(TaskStep)
            .where(TaskStep.task_id == task.id)
            .order_by(TaskStep.position)
        )
    )
    step_map: dict[str, str] = {}
    copied_steps: list[TaskStep] = []
    for step in steps:
        copied = TaskStep(
            id=uuid4(),
            task_id=successor.id,
            owner_id=task.owner_id,
            title=step.title,
            position=step.position,
            completed=False,
            completed_at=None,
        )
        copied_steps.append(copied)
        step_map[str(step.id)] = str(copied.id)
    session.add_all(copied_steps)

    links = list(
        await session.scalars(
            select(TaskLink)
            .where(TaskLink.task_id == task.id)
            .order_by(TaskLink.position)
        )
    )
    session.add_all(
        TaskLink(
            task_id=successor.id,
            owner_id=task.owner_id,
            title=link.title,
            url=link.url,
            position=link.position,
        )
        for link in links
    )
    label_ids = list(
        await session.scalars(
            select(TaskLabel.label_id).where(TaskLabel.task_id == task.id)
        )
    )
    session.add_all(
        TaskLabel(
            task_id=successor.id,
            label_id=label_id,
            owner_id=task.owner_id,
        )
        for label_id in label_ids
    )
    return successor, step_map


async def query_task_occurrences(
    session: AsyncSession,
    owner_id: UUID,
    start_date: date,
    end_date: date,
) -> tuple[list[dict], str]:
    days = (end_date - start_date).days
    if days <= 0 or days > 93:
        raise AppError(
            "task_occurrence_range_too_large",
            "任务实例查询必须使用不超过 93 天的非空日期范围。",
        )
    user_time_zone = await session.scalar(
        select(UserTimeSettings.time_zone).where(UserTimeSettings.owner_id == owner_id)
    )
    if user_time_zone is None:
        raise AppError(
            "time_settings_required",
            "请先保存当前使用的时区，再查看任务日期。",
            status=409,
        )
    query_start = datetime.combine(
        start_date,
        time.min,
        tzinfo=ZoneInfo(user_time_zone),
    ).astimezone(UTC)
    query_end = datetime.combine(
        end_date,
        time.min,
        tzinfo=ZoneInfo(user_time_zone),
    ).astimezone(UTC)
    coarse_start = _shift_date_clamped(start_date, -2)
    coarse_end = _shift_date_clamped(end_date, 2)
    rows = list(
        (
            await session.execute(
                select(Task, TaskSchedule)
                .join(
                    TaskSchedule,
                    TaskSchedule.task_id == Task.id,
                )
                .where(
                    Task.owner_id == owner_id,
                    TaskSchedule.due_kind != "none",
                    TaskSchedule.start_date < coarse_end,
                    TaskSchedule.last_occurrence_date >= coarse_start,
                )
                .order_by(TaskSchedule.start_date, Task.id)
                .limit(MAX_TASK_SCHEDULES + 1)
            )
        ).all()
    )
    moved_task_ids = set(
        await session.scalars(
            select(TaskOccurrenceState.task_id).where(
                TaskOccurrenceState.owner_id == owner_id,
                TaskOccurrenceState.effective_date >= coarse_start,
                TaskOccurrenceState.effective_date < coarse_end,
                or_(
                    TaskOccurrenceState.kind == "makeup",
                    TaskOccurrenceState.rescheduled.is_(True),
                ),
            )
        )
    )
    loaded_ids = {task.id for task, _ in rows}
    missing_ids = moved_task_ids - loaded_ids
    if missing_ids:
        rows.extend(
            (
                await session.execute(
                    select(Task, TaskSchedule)
                    .join(
                        TaskSchedule,
                        TaskSchedule.task_id == Task.id,
                    )
                    .where(
                        Task.owner_id == owner_id,
                        Task.id.in_(missing_ids),
                    )
                )
            ).all()
        )
    if len(rows) > MAX_TASK_SCHEDULES:
        raise AppError(
            "task_occurrence_result_too_large",
            "带日期任务过多，请缩小查询范围。",
            status=422,
        )
    task_ids = [task.id for task, _ in rows]
    state_map: defaultdict[UUID, dict[date, TaskOccurrenceState]] = defaultdict(dict)
    makeup_states: defaultdict[UUID, list[TaskOccurrenceState]] = defaultdict(list)
    if task_ids:
        states = await session.scalars(
            select(TaskOccurrenceState).where(
                TaskOccurrenceState.owner_id == owner_id,
                TaskOccurrenceState.task_id.in_(task_ids),
                or_(
                    and_(
                        TaskOccurrenceState.kind == "base",
                        TaskOccurrenceState.original_date >= coarse_start,
                        TaskOccurrenceState.original_date < coarse_end,
                    ),
                    and_(
                        TaskOccurrenceState.effective_date >= coarse_start,
                        TaskOccurrenceState.effective_date < coarse_end,
                    ),
                ),
            )
        )
        for state in states:
            if state.kind == "base":
                state_map[state.task_id][state.original_date] = state
            else:
                makeup_states[state.task_id].append(state)
    step_counts = (
        {
            task_id: count
            for task_id, count in await session.execute(
                select(TaskStep.task_id, func.count(TaskStep.id))
                .where(TaskStep.task_id.in_(task_ids))
                .group_by(TaskStep.task_id)
            )
        }
        if task_ids
        else {}
    )

    results: list[dict] = []
    for task, schedule in rows:
        for occurrence_date in occurrence_dates(task_plan(schedule)):
            state = state_map[task.id].get(occurrence_date)
            projected = project_task_occurrence(
                task,
                schedule,
                occurrence_date,
                state,
                step_count=step_counts.get(task.id, 0),
            )
            if (
                schedule.due_kind == "date"
                and start_date <= projected["due_date"] < end_date
            ) or (
                schedule.due_kind == "datetime"
                and (
                    (
                        projected["due_at"] is not None
                        and query_start <= projected["due_at"] < query_end
                    )
                    or (
                        projected["due_at"] is None
                        and start_date <= projected["due_date"] < end_date
                    )
                )
            ):
                results.append(projected)
        for state in makeup_states[task.id]:
            projected = project_task_occurrence(
                task,
                schedule,
                state.original_date,
                state,
                step_count=step_counts.get(task.id, 0),
            )
            if (
                schedule.due_kind == "date"
                and start_date <= projected["due_date"] < end_date
            ) or (
                schedule.due_kind == "datetime"
                and projected["due_at"] is not None
                and query_start <= projected["due_at"] < query_end
            ):
                results.append(projected)
        if len(results) > MAX_TASK_OCCURRENCES:
            raise AppError(
                "task_occurrence_result_too_large",
                "该范围内超过 2000 个任务实例，请缩小查询范围。",
                status=422,
            )
    results.sort(
        key=lambda item: (
            item["due_date"],
            item["due_time"] or time.max,
            str(item["task_id"]),
            item["occurrence_key"],
        )
    )
    return results, user_time_zone
