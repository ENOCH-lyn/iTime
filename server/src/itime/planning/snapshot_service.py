from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from hashlib import sha256
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from itime.calendar.models import UserTimeSettings
from itime.calendar.series_service import query_occurrences
from itime.errors import AppError
from itime.planning.models import (
    Plan,
    PlanningInputRevision,
    PlanningInputSnapshot,
    PlanUnscheduledItem,
    PlanVersion,
    ScheduledBlock,
)
from itime.planning.schemas import (
    DEFAULT_AVAILABILITY_END,
    DEFAULT_AVAILABILITY_START,
    DEFAULT_BUFFER_MINUTES,
    MAX_SELECTED_TASKS,
    PlanningRequestFields,
)
from itime.public_links.service import planning_public_sources
from itime.tasks.models import Task, TaskSchedule, TaskStep
from itime.tasks.schedule_service import query_task_occurrences

MAX_FIXED_OCCURRENCES = 2000
MIN_TARGET_DATE_OFFSET = -31
MAX_TARGET_DATE_OFFSET = 366


async def lock_planning_revision(
    session: AsyncSession,
    owner_id: UUID,
    *,
    write: bool,
) -> PlanningInputRevision:
    """Lock the per-owner serialization row used by planning readers and writers."""
    await session.execute(
        postgresql_insert(PlanningInputRevision)
        .values(owner_id=owner_id, revision=0)
        .on_conflict_do_nothing(index_elements=[PlanningInputRevision.owner_id])
    )
    statement = select(PlanningInputRevision).where(
        PlanningInputRevision.owner_id == owner_id
    )
    statement = statement.with_for_update() if write else statement.with_for_update(read=True)
    revision = await session.scalar(statement)
    if revision is None:
        raise AppError(
            "planning_revision_unavailable",
            "规划输入暂时无法读取，请稍后重试。",
            status=503,
            retryable=True,
        )
    return revision


async def build_planning_snapshot(
    session: AsyncSession,
    owner_id: UUID,
    request: PlanningRequestFields,
) -> PlanningInputSnapshot:
    """Persist an immutable snapshot while holding the owner's shared revision lock."""
    context = await _collect_context(session, owner_id, request)
    current_plan = context["current_plan"]
    snapshot = PlanningInputSnapshot(
        owner_id=owner_id,
        revision=context["revision"],
        scope=context["scope"],
        target_date=request.target_date,
        week_start_date=date.fromisoformat(context["week_start_date"]),
        week_end_date=date.fromisoformat(context["week_end_date"]),
        time_zone=context["time_zone"],
        density=context["density"],
        buffer_minutes=context["buffer_minutes"],
        availability_start=request.availability_start,
        availability_end=request.availability_end,
        user_instructions=context["instruction"],
        selected_task_ids=context["selected_task_ids"],
        task_candidates=context["task_candidates"],
        fixed_occurrences=context["fixed_occurrences"],
        current_plan_id=UUID(current_plan["id"]) if current_plan is not None else None,
        current_plan_version=(
            current_plan["version"] if current_plan is not None else None
        ),
        preserved_dates=context["preserved_dates"],
        content_sha256=context["content_sha256"],
    )
    session.add(snapshot)
    await session.flush()
    return snapshot


async def planning_context(
    session: AsyncSession,
    owner_id: UUID,
    request: PlanningRequestFields,
) -> dict:
    """Return the same normalized input used to create a persisted snapshot."""
    context = await _collect_context(session, owner_id, request)
    return {
        **context,
        "defaults": {
            "density": "balanced",
            "buffer_minutes": DEFAULT_BUFFER_MINUTES,
            "availability_start": DEFAULT_AVAILABILITY_START.isoformat(),
            "availability_end": DEFAULT_AVAILABILITY_END.isoformat(),
        },
    }


async def _collect_context(
    session: AsyncSession,
    owner_id: UUID,
    request: PlanningRequestFields,
) -> dict:
    revision = await lock_planning_revision(session, owner_id, write=False)
    settings = await session.get(UserTimeSettings, owner_id)
    if settings is None:
        raise AppError(
            "time_settings_required",
            "请先保存当前使用的时区，再查看规划上下文。",
            status=409,
        )
    _validate_target_date(request.target_date, settings.time_zone)
    week_start = request.target_date - timedelta(days=request.target_date.weekday())
    week_end = week_start + timedelta(days=6)
    range_start = request.target_date if request.scope == "day" else week_start
    range_end = request.target_date if request.scope == "day" else week_end
    query_end = range_end + timedelta(days=1)

    task_occurrences, task_time_zone = await query_task_occurrences(
        session,
        owner_id,
        range_start,
        query_end,
    )
    calendar_occurrences, calendar_time_zone = await query_occurrences(
        session,
        owner_id,
        range_start,
        query_end,
    )
    if task_time_zone != settings.time_zone or calendar_time_zone != settings.time_zone:
        raise AppError(
            "planning_time_zone_changed",
            "规划读取期间时区发生变化，请刷新后重试。",
            status=409,
            retryable=True,
        )

    task_candidates = await _task_candidates(
        session,
        owner_id,
        request.task_ids,
        task_occurrences,
    )
    fixed_occurrences = _fixed_occurrences(calendar_occurrences)
    current_plan = await _current_plan_context(session, owner_id, week_start)
    preserved_dates = _preserved_dates(
        request.scope,
        request.target_date,
        current_plan,
    )
    normalized = {
        "revision": revision.revision,
        "scope": request.scope,
        "target_date": request.target_date.isoformat(),
        "range_start_date": range_start.isoformat(),
        "range_end_date": range_end.isoformat(),
        "week_start_date": week_start.isoformat(),
        "week_end_date": week_end.isoformat(),
        "time_zone": settings.time_zone,
        "density": request.density,
        "buffer_minutes": request.buffer_minutes,
        "availability_start": request.availability_start.isoformat(),
        "availability_end": request.availability_end.isoformat(),
        "instruction": request.instruction,
        "selected_task_ids": sorted(str(task_id) for task_id in request.task_ids),
        "task_candidates": task_candidates,
        "fixed_occurrences": fixed_occurrences,
        "current_plan": current_plan,
        "preserved_dates": preserved_dates,
    }
    return {
        **normalized,
        "content_sha256": _content_sha256(normalized),
    }


def _validate_target_date(target_date: date, time_zone: str) -> None:
    today = datetime.now(UTC).astimezone(ZoneInfo(time_zone)).date()
    minimum = today + timedelta(days=MIN_TARGET_DATE_OFFSET)
    maximum = today + timedelta(days=MAX_TARGET_DATE_OFFSET)
    if not minimum <= target_date <= maximum:
        raise AppError(
            "planning_date_out_of_range",
            "规划目标日期必须在当前日期前 31 天至后 366 天内。",
            status=422,
            details={
                "minimum_date": minimum.isoformat(),
                "maximum_date": maximum.isoformat(),
            },
        )


async def _task_candidates(
    session: AsyncSession,
    owner_id: UUID,
    selected_task_ids: list[UUID],
    occurrences: list[dict],
) -> list[dict]:
    selected = set(selected_task_ids)
    eligible_conditions = (
        Task.owner_id == owner_id,
        Task.status == "open",
        Task.archived_at.is_(None),
        Task.deleted_at.is_(None),
        Task.purged_at.is_(None),
    )
    selected_tasks: dict[UUID, Task] = {}
    if selected:
        selected_tasks = {
            task.id: task
            for task in await session.scalars(
                select(Task).where(*eligible_conditions, Task.id.in_(selected))
            )
        }
        if set(selected_tasks) != selected:
            raise AppError("task_not_found", "未找到此任务。", status=404)

    base_statement = (
        select(Task, TaskSchedule)
        .outerjoin(
            TaskSchedule,
            and_(
                TaskSchedule.task_id == Task.id,
                TaskSchedule.owner_id == Task.owner_id,
            ),
        )
        .where(
            *eligible_conditions,
            or_(
                TaskSchedule.task_id.is_(None),
                TaskSchedule.due_kind == "none",
                TaskSchedule.recurrence_frequency == "none",
            ),
        )
        .order_by(Task.id)
        .limit(MAX_SELECTED_TASKS + 1)
    )
    if selected:
        base_statement = base_statement.where(Task.id.in_(selected))
    base_rows = list((await session.execute(base_statement)).all())
    if len(base_rows) > MAX_SELECTED_TASKS:
        raise _too_many_candidates()

    open_occurrences = [
        occurrence
        for occurrence in occurrences
        if occurrence["status"] == "open"
        and (not selected or occurrence["task_id"] in selected)
    ]
    occurrence_task_ids = {item["task_id"] for item in open_occurrences}
    occurrence_tasks = (
        {
            task.id: task
            for task in await session.scalars(
                select(Task).where(
                    *eligible_conditions,
                    Task.id.in_(occurrence_task_ids),
                )
            )
        }
        if occurrence_task_ids
        else {}
    )
    all_task_ids = {
        task.id for task, _ in base_rows
    } | set(occurrence_tasks) | set(selected_tasks)
    schedules = (
        {
            schedule.task_id: schedule
            for schedule in await session.scalars(
                select(TaskSchedule).where(
                    TaskSchedule.owner_id == owner_id,
                    TaskSchedule.task_id.in_(all_task_ids),
                )
            )
        }
        if all_task_ids
        else {}
    )
    occurrence_by_task: defaultdict[UUID, list[dict]] = defaultdict(list)
    for occurrence in open_occurrences:
        if occurrence["task_id"] in occurrence_tasks:
            occurrence_by_task[occurrence["task_id"]].append(occurrence)

    drafts: list[tuple[Task, TaskSchedule | None, dict | None]] = []
    for task, schedule in base_rows:
        effective = occurrence_by_task[task.id][0] if occurrence_by_task[task.id] else None
        drafts.append((task, schedule, effective))
    for task_id, items in occurrence_by_task.items():
        schedule = schedules.get(task_id)
        if schedule is None or schedule.recurrence_frequency == "none":
            continue
        task = occurrence_tasks[task_id]
        drafts.extend((task, schedule, occurrence) for occurrence in items)
    if len(drafts) > MAX_SELECTED_TASKS:
        raise _too_many_candidates()

    step_rows = list(
        await session.scalars(
            select(TaskStep)
            .where(
                TaskStep.owner_id == owner_id,
                TaskStep.task_id.in_({task.id for task, _, _ in drafts}),
            )
            .order_by(TaskStep.task_id, TaskStep.position, TaskStep.id)
        )
    ) if drafts else []
    steps_by_task: defaultdict[UUID, list[TaskStep]] = defaultdict(list)
    for step in step_rows:
        steps_by_task[step.task_id].append(step)

    candidates = [
        _task_candidate(task, schedule, occurrence, steps_by_task[task.id])
        for task, schedule, occurrence in drafts
    ]
    candidates.sort(key=lambda item: item["candidate_key"])
    source_contexts = await planning_public_sources(
        session,
        owner_id,
        [UUID(item["task_id"]) for item in candidates],
    )
    for candidate, source_context in zip(
        candidates,
        source_contexts,
        strict=True,
    ):
        candidate.update(source_context)
    return candidates


def _task_candidate(
    task: Task,
    schedule: TaskSchedule | None,
    occurrence: dict | None,
    steps: list[TaskStep],
) -> dict:
    repeating = schedule is not None and schedule.recurrence_frequency != "none"
    occurrence_key = occurrence["occurrence_key"] if repeating and occurrence else None
    completed_step_ids = (
        {str(step_id) for step_id in occurrence["completed_step_ids"]}
        if repeating and occurrence is not None
        else {str(step.id) for step in steps if step.completed}
    )
    candidate_steps = [
        {
            "id": str(step.id),
            "title": step.title,
            "position": step.position,
            "completed": str(step.id) in completed_step_ids,
        }
        for step in steps
    ]
    due_kind = schedule.due_kind if schedule is not None else "none"
    due_date = (
        occurrence["due_date"]
        if occurrence is not None
        else schedule.start_date if schedule is not None else None
    )
    due_at = (
        occurrence["due_at"]
        if occurrence is not None
        else schedule.due_at if schedule is not None else None
    )
    return {
        "candidate_key": (
            f"task:{task.id}:occurrence:{occurrence_key}"
            if occurrence_key is not None
            else f"task:{task.id}"
        ),
        "task_id": str(task.id),
        "task_version": task.version,
        "occurrence_key": occurrence_key,
        "occurrence_version": (
            occurrence["occurrence_version"] if occurrence is not None else 0
        ),
        "title": task.title,
        "description": task.description,
        "project_id": str(task.project_id) if task.project_id is not None else None,
        "priority": task.priority,
        "status": "open",
        "estimated_minutes": task.estimated_minutes,
        "estimate_source": task.estimate_source,
        "splittable": schedule.splittable if schedule is not None else True,
        "minimum_block_minutes": (
            schedule.minimum_block_minutes if schedule is not None else 15
        ),
        "due_kind": due_kind,
        "due_date": due_date.isoformat() if due_date is not None else None,
        "due_at": _datetime_text(due_at),
        "time_zone": schedule.time_zone if schedule is not None else None,
        "step_progress": {
            "total": len(candidate_steps),
            "completed": len(completed_step_ids),
        },
        "completed_step_ids": sorted(completed_step_ids),
        "steps": candidate_steps,
    }


def _too_many_candidates() -> AppError:
    return AppError(
        "planning_candidates_too_large",
        "本次规划候选超过 100 个，请选择任务或缩小日期范围。",
        status=422,
        details={"maximum": MAX_SELECTED_TASKS},
    )


def _fixed_occurrences(occurrences: list[dict]) -> list[dict]:
    fixed = [
        {
            "series_id": _optional_uuid_text(item.get("series_id")),
            "series_version": item.get("series_version"),
            "subscription_event_id": _optional_uuid_text(
                item.get("subscription_event_id")
            ),
            "source_event_version": item.get("source_event_version"),
            "occurrence_key": item["occurrence_key"],
            "occurrence_type": item["occurrence_type"],
            "kind": item["kind"],
            "source_id": _optional_uuid_text(item.get("source_id")),
            "source_type": item["source_type"],
            "name": item["name"],
            "busy": item["busy"],
            "status": item["status"],
            "time_zone": item["time_zone"],
            "local_start_date": item["local_start_date"].isoformat(),
            "local_start_time": _time_text(item.get("local_start_time")),
            "local_end_date": item["local_end_date"].isoformat(),
            "local_end_time": _time_text(item.get("local_end_time")),
            "start_at": _datetime_text(item.get("start_at")),
            "end_at": _datetime_text(item.get("end_at")),
            "warning_codes": sorted(item.get("warning_codes", [])),
        }
        for item in occurrences
        if item["busy"] is True and item["status"] == "scheduled"
    ]
    if len(fixed) > MAX_FIXED_OCCURRENCES:
        raise AppError(
            "planning_fixed_occurrences_too_large",
            "本次规划固定安排超过 2000 个，请缩小日期范围。",
            status=422,
            details={"maximum": MAX_FIXED_OCCURRENCES},
        )
    fixed.sort(
        key=lambda item: (
            item["start_at"] is None,
            item["start_at"] or "",
            item["occurrence_key"],
        )
    )
    return fixed


async def _current_plan_context(
    session: AsyncSession,
    owner_id: UUID,
    week_start: date,
) -> dict | None:
    from itime.planning.source_conflict_service import (
        open_source_conflict_summary,
    )

    plan = await session.scalar(
        select(Plan).where(
            Plan.owner_id == owner_id,
            Plan.week_start_date == week_start,
        )
    )
    if plan is None:
        return None
    version = await session.scalar(
        select(PlanVersion).where(
            PlanVersion.id == plan.current_version_id,
            PlanVersion.plan_id == plan.id,
            PlanVersion.owner_id == owner_id,
        )
    )
    if version is None:
        raise AppError(
            "current_plan_unavailable",
            "当前计划版本暂时无法读取，请刷新后重试。",
            status=409,
            retryable=True,
        )
    blocks = list(
        await session.scalars(
            select(ScheduledBlock)
            .where(
                ScheduledBlock.owner_id == owner_id,
                ScheduledBlock.plan_version_id == version.id,
            )
            .order_by(ScheduledBlock.position)
        )
    )
    unscheduled = list(
        await session.scalars(
            select(PlanUnscheduledItem)
            .where(
                PlanUnscheduledItem.owner_id == owner_id,
                PlanUnscheduledItem.plan_version_id == version.id,
            )
            .order_by(PlanUnscheduledItem.position)
        )
    )
    source_conflicts = await open_source_conflict_summary(
        session,
        owner_id,
        version.id,
        version.version,
    )
    return {
        "id": str(plan.id),
        "version_id": str(version.id),
        "version": version.version,
        "week_start_date": plan.week_start_date.isoformat(),
        "time_zone": plan.time_zone,
        "source": version.source,
        "scope": version.scope,
        "target_date": version.target_date.isoformat(),
        "density": version.density,
        "buffer_minutes": version.buffer_minutes,
        "availability_start": version.availability_start.isoformat(),
        "availability_end": version.availability_end.isoformat(),
        "summary": version.summary,
        "assumptions": list(version.assumptions),
        "input_revision": version.input_revision,
        "blocks": [_scheduled_block_result(block) for block in blocks],
        "unscheduled_items": [
            _unscheduled_item_result(item) for item in unscheduled
        ],
        "source_conflicts": source_conflicts,
    }


def _scheduled_block_result(block: ScheduledBlock) -> dict:
    return {
        "id": str(block.id),
        "position": block.position,
        "task_id": str(block.task_id),
        "occurrence_key": block.occurrence_key,
        "task_title": block.task_title_snapshot,
        "step_ids": list(block.step_ids),
        "step_titles": list(block.step_titles_snapshot),
        "local_date": block.local_date.isoformat(),
        "local_starts_at": block.local_starts_at.isoformat(),
        "local_ends_at": block.local_ends_at.isoformat(),
        "time_zone": block.time_zone,
        "starts_at": _datetime_text(block.starts_at),
        "ends_at": _datetime_text(block.ends_at),
        "estimate_source": block.estimate_source,
        "estimated_minutes": block.estimated_minutes,
    }


def _unscheduled_item_result(item: PlanUnscheduledItem) -> dict:
    return {
        "id": str(item.id),
        "position": item.position,
        "task_id": str(item.task_id),
        "occurrence_key": item.occurrence_key,
        "task_title": item.task_title_snapshot,
        "remaining_minutes": item.remaining_minutes,
        "reason": item.reason,
        "explanation": item.explanation,
    }


def _preserved_dates(
    scope: str,
    target_date: date,
    current_plan: dict | None,
) -> list[str]:
    if scope == "week" or current_plan is None:
        return []
    return sorted(
        {
            block["local_date"]
            for block in current_plan["blocks"]
            if block["local_date"] != target_date.isoformat()
        }
    )


def _content_sha256(content: dict) -> str:
    serialized = json.dumps(
        content,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return sha256(serialized).hexdigest()


def _optional_uuid_text(value: Any) -> str | None:
    return str(value) if value is not None else None


def _datetime_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _time_text(value: time | None) -> str | None:
    return value.isoformat() if value is not None else None
