from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from itime.calendar.models import UserTimeSettings
from itime.errors import AppError
from itime.execution.models import ExecutionRevision, FocusEvent, FocusSession
from itime.planning.models import Plan, PlanVersion, ScheduledBlock
from itime.statistics.schemas import METHODOLOGY_VERSION, StatisticsScope
from itime.statistics.time_math import (
    ActivityWindow,
    activity_windows,
    allocate_seconds,
    interval_weights,
    resolve_statistics_range,
)
from itime.tasks.models import Task, TaskOccurrenceState

OPEN_FOCUS_STATUSES = {"running", "paused"}
TERMINAL_EVENT_KINDS = {"pause", "finish", "abandon"}


@dataclass
class _PlanningMetrics:
    block_ids: set[UUID] = field(default_factory=set)
    task_ids: set[UUID] = field(default_factory=set)
    planned_seconds: int = 0

    def result(self) -> dict:
        return {
            "block_count": len(self.block_ids),
            "task_count": len(self.task_ids),
            "planned_seconds": self.planned_seconds,
        }


@dataclass
class _TaskMetrics:
    completed_task_ids: set[UUID] = field(default_factory=set)
    completed_occurrence_ids: set[UUID] = field(default_factory=set)
    skipped_occurrence_ids: set[UUID] = field(default_factory=set)

    def result(self) -> dict:
        task_count = len(self.completed_task_ids)
        occurrence_count = len(self.completed_occurrence_ids)
        return {
            "completed_count": task_count + occurrence_count,
            "completed_task_count": task_count,
            "completed_occurrence_count": occurrence_count,
            "skipped_occurrence_count": len(self.skipped_occurrence_ids),
        }


@dataclass
class _FocusMetrics:
    session_ids: set[UUID] = field(default_factory=set)
    finished_ids: set[UUID] = field(default_factory=set)
    abandoned_ids: set[UUID] = field(default_factory=set)
    manual_ids: set[UUID] = field(default_factory=set)
    revised_ids: set[UUID] = field(default_factory=set)
    ongoing_ids: set[UUID] = field(default_factory=set)
    proportional_ids: set[UUID] = field(default_factory=set)
    total_seconds: int = 0
    finished_seconds: int = 0
    abandoned_seconds: int = 0
    manual_seconds: int = 0
    ongoing_seconds: int = 0

    def add(
        self,
        focus: FocusSession,
        seconds: int,
        *,
        revised: bool,
        proportional: bool,
    ) -> None:
        if seconds <= 0:
            return
        self.session_ids.add(focus.id)
        self.total_seconds += seconds
        if focus.status == "finished":
            self.finished_ids.add(focus.id)
            self.finished_seconds += seconds
        elif focus.status == "abandoned":
            self.abandoned_ids.add(focus.id)
            self.abandoned_seconds += seconds
        if focus.source == "manual":
            self.manual_ids.add(focus.id)
            self.manual_seconds += seconds
        if revised:
            self.revised_ids.add(focus.id)
        if focus.status in OPEN_FOCUS_STATUSES:
            self.ongoing_ids.add(focus.id)
            self.ongoing_seconds += seconds
        if proportional:
            self.proportional_ids.add(focus.id)

    def result(self) -> dict:
        return {
            "session_count": len(self.session_ids),
            "total_seconds": self.total_seconds,
            "finished_count": len(self.finished_ids),
            "finished_seconds": self.finished_seconds,
            "abandoned_count": len(self.abandoned_ids),
            "abandoned_seconds": self.abandoned_seconds,
            "manual_count": len(self.manual_ids),
            "manual_seconds": self.manual_seconds,
            "revised_count": len(self.revised_ids),
            "ongoing_count": len(self.ongoing_ids),
            "ongoing_seconds": self.ongoing_seconds,
            "proportional_focus_count": len(self.proportional_ids),
        }


@dataclass
class _DayBucket:
    activity_date: date
    planning: _PlanningMetrics = field(default_factory=_PlanningMetrics)
    tasks: _TaskMetrics = field(default_factory=_TaskMetrics)
    focus: _FocusMetrics = field(default_factory=_FocusMetrics)

    def result(self) -> dict:
        return {
            "activity_date": self.activity_date,
            "planning": self.planning.result(),
            "tasks": self.tasks.result(),
            "focus": self.focus.result(),
        }


def _window_for(
    instant: datetime,
    windows: tuple[ActivityWindow, ...],
) -> ActivityWindow | None:
    for window in windows:
        if window.starts_at <= instant < window.ends_at:
            return window
    return None


def _merge_planning(
    target: _PlanningMetrics,
    source: _PlanningMetrics,
) -> None:
    target.block_ids.update(source.block_ids)
    target.task_ids.update(source.task_ids)
    target.planned_seconds += source.planned_seconds


def _merge_tasks(target: _TaskMetrics, source: _TaskMetrics) -> None:
    target.completed_task_ids.update(source.completed_task_ids)
    target.completed_occurrence_ids.update(source.completed_occurrence_ids)
    target.skipped_occurrence_ids.update(source.skipped_occurrence_ids)


def _merge_focus(target: _FocusMetrics, source: _FocusMetrics) -> None:
    target.session_ids.update(source.session_ids)
    target.finished_ids.update(source.finished_ids)
    target.abandoned_ids.update(source.abandoned_ids)
    target.manual_ids.update(source.manual_ids)
    target.revised_ids.update(source.revised_ids)
    target.ongoing_ids.update(source.ongoing_ids)
    target.proportional_ids.update(source.proportional_ids)
    target.total_seconds += source.total_seconds
    target.finished_seconds += source.finished_seconds
    target.abandoned_seconds += source.abandoned_seconds
    target.manual_seconds += source.manual_seconds
    target.ongoing_seconds += source.ongoing_seconds


async def _collect_planning(
    session: AsyncSession,
    owner_id: UUID,
    windows: tuple[ActivityWindow, ...],
    buckets: dict[date, _DayBucket],
) -> None:
    overall_start = windows[0].starts_at
    overall_end = windows[-1].ends_at
    blocks = list(
        await session.scalars(
            select(ScheduledBlock)
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
                    Plan.current_version_id == PlanVersion.id,
                ),
            )
            .where(
                ScheduledBlock.owner_id == owner_id,
                ScheduledBlock.starts_at < overall_end,
                ScheduledBlock.ends_at > overall_start,
            )
            .order_by(ScheduledBlock.starts_at, ScheduledBlock.id)
        )
    )
    for block in blocks:
        for activity_date, seconds in interval_weights(
            block.starts_at,
            block.ends_at,
            windows,
        ).items():
            metrics = buckets[activity_date].planning
            metrics.block_ids.add(block.id)
            metrics.task_ids.add(block.task_id)
            metrics.planned_seconds += seconds


async def _collect_tasks(
    session: AsyncSession,
    owner_id: UUID,
    windows: tuple[ActivityWindow, ...],
    buckets: dict[date, _DayBucket],
) -> None:
    overall_start = windows[0].starts_at
    overall_end = windows[-1].ends_at
    completed_tasks = list(
        await session.scalars(
            select(Task)
            .where(
                Task.owner_id == owner_id,
                Task.status == "completed",
                Task.completed_at >= overall_start,
                Task.completed_at < overall_end,
                Task.purged_at.is_(None),
            )
            .order_by(Task.completed_at, Task.id)
        )
    )
    for task in completed_tasks:
        window = _window_for(task.completed_at, windows)
        if window is not None:
            buckets[window.activity_date].tasks.completed_task_ids.add(task.id)

    completed_occurrences = list(
        await session.scalars(
            select(TaskOccurrenceState)
            .join(
                Task,
                and_(
                    Task.id == TaskOccurrenceState.task_id,
                    Task.owner_id == TaskOccurrenceState.owner_id,
                ),
            )
            .where(
                TaskOccurrenceState.owner_id == owner_id,
                TaskOccurrenceState.status == "completed",
                TaskOccurrenceState.completed_at >= overall_start,
                TaskOccurrenceState.completed_at < overall_end,
                Task.purged_at.is_(None),
            )
            .order_by(
                TaskOccurrenceState.completed_at,
                TaskOccurrenceState.id,
            )
        )
    )
    for occurrence in completed_occurrences:
        window = _window_for(occurrence.completed_at, windows)
        if window is not None:
            buckets[
                window.activity_date
            ].tasks.completed_occurrence_ids.add(occurrence.id)

    skipped_occurrences = list(
        await session.scalars(
            select(TaskOccurrenceState)
            .join(
                Task,
                and_(
                    Task.id == TaskOccurrenceState.task_id,
                    Task.owner_id == TaskOccurrenceState.owner_id,
                ),
            )
            .where(
                TaskOccurrenceState.owner_id == owner_id,
                TaskOccurrenceState.status == "skipped",
                TaskOccurrenceState.updated_at >= overall_start,
                TaskOccurrenceState.updated_at < overall_end,
                Task.purged_at.is_(None),
            )
            .order_by(
                TaskOccurrenceState.updated_at,
                TaskOccurrenceState.id,
            )
        )
    )
    for occurrence in skipped_occurrences:
        window = _window_for(occurrence.updated_at, windows)
        if window is not None:
            buckets[
                window.activity_date
            ].tasks.skipped_occurrence_ids.add(occurrence.id)


def _focus_duration(focus: FocusSession, current_time: datetime) -> int:
    if focus.status in OPEN_FOCUS_STATUSES:
        active_seconds = (
            max(0, int((current_time - focus.active_started_at).total_seconds()))
            if focus.status == "running" and focus.active_started_at is not None
            else 0
        )
        return focus.accumulated_seconds + active_seconds
    return focus.effective_duration_seconds or 0


def _focus_interval(
    focus: FocusSession,
    current_time: datetime,
) -> tuple[datetime, datetime]:
    if focus.status in OPEN_FOCUS_STATUSES:
        return focus.started_at, current_time
    if (
        focus.effective_started_at is None
        or focus.effective_ended_at is None
    ):
        raise AppError(
            "statistics_focus_inconsistent",
            "专注记录暂时无法统计，请稍后重试。",
            status=409,
            retryable=True,
        )
    return focus.effective_started_at, focus.effective_ended_at


def _segment_weights(
    events: list[FocusEvent],
    windows: tuple[ActivityWindow, ...],
    *,
    current_time: datetime,
) -> tuple[dict[date, int], int]:
    selected: defaultdict[date, int] = defaultdict(int)
    total_weight = 0
    active_start: datetime | None = None
    for event in events:
        if event.kind in {"start", "resume"}:
            active_start = event.occurred_at
        elif event.kind in TERMINAL_EVENT_KINDS and active_start is not None:
            segment_end = max(active_start, event.occurred_at)
            total_weight += int((segment_end - active_start).total_seconds())
            for activity_date, seconds in interval_weights(
                active_start,
                segment_end,
                windows,
            ).items():
                selected[activity_date] += seconds
            active_start = None
    if active_start is not None and current_time > active_start:
        total_weight += int((current_time - active_start).total_seconds())
        for activity_date, seconds in interval_weights(
            active_start,
            current_time,
            windows,
        ).items():
            selected[activity_date] += seconds
    return dict(selected), total_weight


async def _collect_focus(
    session: AsyncSession,
    owner_id: UUID,
    windows: tuple[ActivityWindow, ...],
    buckets: dict[date, _DayBucket],
    *,
    current_time: datetime,
) -> None:
    overall_start = windows[0].starts_at
    overall_end = windows[-1].ends_at
    effective_start = func.coalesce(
        FocusSession.effective_started_at,
        FocusSession.started_at,
    )
    focuses = list(
        await session.scalars(
            select(FocusSession)
            .where(
                FocusSession.owner_id == owner_id,
                effective_start < overall_end,
                or_(
                    and_(
                        FocusSession.status.in_(OPEN_FOCUS_STATUSES),
                        current_time > overall_start,
                    ),
                    FocusSession.effective_ended_at > overall_start,
                ),
            )
            .order_by(effective_start, FocusSession.id)
        )
    )
    if not focuses:
        return

    focus_ids = [focus.id for focus in focuses]
    revised_ids = set(
        await session.scalars(
            select(ExecutionRevision.focus_session_id)
            .where(
                ExecutionRevision.owner_id == owner_id,
                ExecutionRevision.focus_session_id.in_(focus_ids),
            )
            .distinct()
        )
    )
    event_rows = list(
        await session.scalars(
            select(FocusEvent)
            .where(
                FocusEvent.owner_id == owner_id,
                FocusEvent.focus_session_id.in_(focus_ids),
            )
            .order_by(FocusEvent.focus_session_id, FocusEvent.sequence)
        )
    )
    events_by_focus: defaultdict[UUID, list[FocusEvent]] = defaultdict(list)
    for event in event_rows:
        events_by_focus[event.focus_session_id].append(event)

    for focus in focuses:
        duration = _focus_duration(focus, current_time)
        if duration <= 0:
            continue
        revised = focus.id in revised_ids
        proportional = focus.source == "manual" or revised
        if proportional:
            starts_at, ends_at = _focus_interval(focus, current_time)
            weights = interval_weights(starts_at, ends_at, windows)
            total_weight = int((ends_at - starts_at).total_seconds())
        else:
            weights, total_weight = _segment_weights(
                events_by_focus[focus.id],
                windows,
                current_time=current_time,
            )
        if total_weight <= 0:
            continue
        allocated = allocate_seconds(
            duration,
            weights,
            total_weight=total_weight,
        )
        for activity_date, seconds in allocated.items():
            buckets[activity_date].focus.add(
                focus,
                seconds,
                revised=revised,
                proportional=proportional,
            )


async def read_statistics(
    session: AsyncSession,
    owner_id: UUID,
    *,
    scope: StatisticsScope,
    anchor_date: date,
    current_time: datetime,
) -> dict:
    settings = await session.get(UserTimeSettings, owner_id)
    if settings is None:
        raise AppError(
            "time_settings_required",
            "请先保存当前使用的时区，再查看统计。",
            status=409,
        )
    selected_range = resolve_statistics_range(
        scope,
        anchor_date,
        current_time=current_time,
        time_zone=settings.time_zone,
    )
    windows = activity_windows(
        selected_range.period_start_date,
        selected_range.data_end_date,
        settings.time_zone,
    )
    buckets = {
        window.activity_date: _DayBucket(window.activity_date)
        for window in windows
    }
    await _collect_planning(session, owner_id, windows, buckets)
    await _collect_tasks(session, owner_id, windows, buckets)
    await _collect_focus(
        session,
        owner_id,
        windows,
        buckets,
        current_time=current_time,
    )

    summary_planning = _PlanningMetrics()
    summary_tasks = _TaskMetrics()
    summary_focus = _FocusMetrics()
    for bucket in buckets.values():
        _merge_planning(summary_planning, bucket.planning)
        _merge_tasks(summary_tasks, bucket.tasks)
        _merge_focus(summary_focus, bucket.focus)

    return {
        "scope": selected_range.scope,
        "anchor_date": selected_range.anchor_date,
        "period_start_date": selected_range.period_start_date,
        "period_end_date": selected_range.period_end_date,
        "data_end_date": selected_range.data_end_date,
        "current_activity_date": selected_range.current_activity_date,
        "time_zone": settings.time_zone,
        "activity_day_start_minute": settings.activity_day_start_minute,
        "calculated_at": current_time,
        "methodology_version": METHODOLOGY_VERSION,
        "summary": {
            "planning": summary_planning.result(),
            "tasks": summary_tasks.result(),
            "focus": summary_focus.result(),
        },
        "days": [bucket.result() for bucket in buckets.values()],
    }
