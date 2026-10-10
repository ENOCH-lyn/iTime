"""Read-only AI tool handlers.

Each handler receives (session, owner_id, args: dict, current_time) and returns
a plain dict shaped for the model. Handlers must:
- never mutate business data beyond `last_used_at` on memory search (treated as
  a read side-effect per the spec),
- always scope queries to `owner_id`,
- surface missing resources via `{"missing": [...]}` rather than silent empties.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from itime.calendar import service as calendar_service
from itime.calendar.models import UserTimeSettings
from itime.calendar.series_service import query_occurrences, series_result
from itime.errors import AppError
from itime.execution import difficulty_service
from itime.planning import plan_service
from itime.review import memory_service
from itime.review import service as review_service
from itime.tasks.models import Task
from itime.tasks.service import task_detail, task_summaries

from .schemas import (
    CalendarRangeInput,
    DifficultyRangeInput,
    EmptyInput,
    EventIdInput,
    ListMemoriesInput,
    ListPlansInput,
    ListTasksInput,
    PlanIdInput,
    ReviewIdInput,
    ReviewRangeInput,
    SearchMemoryInput,
    SearchTasksInput,
    TaskIdInput,
)


def _active_task_conditions() -> tuple:
    return (
        Task.archived_at.is_(None),
        Task.deleted_at.is_(None),
        Task.purged_at.is_(None),
    )


def _missing(key: str, value: str | int | UUID) -> dict:
    return {"missing": [{"field": key, "value": str(value)}]}


async def handle_get_today(
    session: AsyncSession,
    owner_id: UUID,
    args: dict,
    current_time: datetime,
) -> dict:
    EmptyInput.model_validate(args)
    settings = await session.get(UserTimeSettings, owner_id)
    if settings is None:
        return _missing("time_settings", owner_id)
    activity_date = calendar_service.activity_day_at(
        current_time,
        settings.time_zone,
        start_minute=settings.activity_day_start_minute,
    )
    return {
        "activity_date": activity_date.isoformat(),
        "iana_tz": settings.time_zone,
        "current_time": current_time.isoformat(),
    }


async def handle_get_time_settings(
    session: AsyncSession,
    owner_id: UUID,
    args: dict,
    current_time: datetime,
) -> dict:
    EmptyInput.model_validate(args)
    settings = await session.get(UserTimeSettings, owner_id)
    return calendar_service.time_settings_result(settings)


async def handle_get_calendar_events(
    session: AsyncSession,
    owner_id: UUID,
    args: dict,
    current_time: datetime,
) -> dict:
    payload = CalendarRangeInput.model_validate(args)
    try:
        occurrences, tz = await query_occurrences(
            session,
            owner_id,
            payload.date_from,
            payload.date_to,
        )
    except AppError as exc:
        if exc.code == "time_settings_required":
            return _missing("time_settings", owner_id)
        raise
    return {"items": occurrences, "iana_tz": tz}


async def handle_get_event(
    session: AsyncSession,
    owner_id: UUID,
    args: dict,
    current_time: datetime,
) -> dict:
    payload = EventIdInput.model_validate(args)
    from itime.calendar.models import CalendarSeries

    series = await session.scalar(
        select(CalendarSeries).where(
            CalendarSeries.id == payload.event_id,
            CalendarSeries.owner_id == owner_id,
            CalendarSeries.deleted_at.is_(None),
            CalendarSeries.purged_at.is_(None),
        )
    )
    if series is None:
        return _missing("event_id", payload.event_id)
    return series_result(series)


async def handle_list_tasks(
    session: AsyncSession,
    owner_id: UUID,
    args: dict,
    current_time: datetime,
) -> dict:
    payload = ListTasksInput.model_validate(args)
    filt = payload.filter
    statement = select(Task).where(
        Task.owner_id == owner_id,
        *_active_task_conditions(),
    )
    if filt.status in ("open", "completed"):
        statement = statement.where(Task.status == filt.status)
    if filt.due_from is not None:
        statement = statement.where(Task.due_at.is_not(None))
        statement = statement.where(Task.due_at >= filt.due_from)
    if filt.due_to is not None:
        statement = statement.where(Task.due_at <= filt.due_to)
    if filt.keyword:
        term = filt.keyword.strip()
        if term:
            statement = statement.where(
                or_(
                    Task.title.icontains(term, autoescape=True),
                    Task.description.icontains(term, autoescape=True),
                )
            )
    statement = statement.order_by(
        Task.updated_at.desc(), Task.id.desc()
    ).limit(filt.limit + 1)
    rows = list(await session.scalars(statement))
    page = rows[: filt.limit]
    summaries = await task_summaries(session, page)
    return {
        "items": summaries,
        "next_cursor": (
            {
                "before_time": page[-1].updated_at.isoformat(),
                "before_id": str(page[-1].id),
            }
            if len(rows) > filt.limit
            else None
        ),
    }


async def handle_get_task(
    session: AsyncSession,
    owner_id: UUID,
    args: dict,
    current_time: datetime,
) -> dict:
    payload = TaskIdInput.model_validate(args)
    task = await session.scalar(
        select(Task).where(
            Task.id == payload.task_id,
            Task.owner_id == owner_id,
            Task.purged_at.is_(None),
        )
    )
    if task is None:
        return _missing("task_id", payload.task_id)
    return await task_detail(session, task)


async def handle_search_tasks(
    session: AsyncSession,
    owner_id: UUID,
    args: dict,
    current_time: datetime,
) -> dict:
    payload = SearchTasksInput.model_validate(args)
    term = payload.query.strip()
    statement = (
        select(Task)
        .where(
            Task.owner_id == owner_id,
            *_active_task_conditions(),
            or_(
                Task.title.icontains(term, autoescape=True),
                Task.description.icontains(term, autoescape=True),
            ),
        )
        .order_by(Task.updated_at.desc(), Task.id.desc())
        .limit(payload.limit)
    )
    rows = list(await session.scalars(statement))
    summaries = await task_summaries(session, rows)
    return {"items": summaries}


async def handle_list_plans(
    session: AsyncSession,
    owner_id: UUID,
    args: dict,
    current_time: datetime,
) -> dict:
    payload = ListPlansInput.model_validate(args)
    if payload.filter.period_from is None or payload.filter.period_to is None:
        return {"items": []}
    plans = await plan_service.list_week_plans(
        session,
        owner_id,
        payload.filter.period_from,
        payload.filter.period_to,
    )
    return {"items": plans}


async def handle_get_plan(
    session: AsyncSession,
    owner_id: UUID,
    args: dict,
    current_time: datetime,
) -> dict:
    payload = PlanIdInput.model_validate(args)
    try:
        return await plan_service.read_plan(session, owner_id, payload.plan_id)
    except AppError as exc:
        if exc.code == "plan_not_found":
            return _missing("plan_id", payload.plan_id)
        raise


async def handle_list_reviews(
    session: AsyncSession,
    owner_id: UUID,
    args: dict,
    current_time: datetime,
) -> dict:
    payload = ReviewRangeInput.model_validate(args)
    result = await review_service.list_reviews(
        session,
        owner_id,
        before_date=None,
        before_id=None,
        limit=20,
    )
    filtered = [
        item
        for item in result["items"]
        if payload.date_from <= item["activity_date"] <= payload.date_to
    ]
    return {"items": filtered}


async def handle_get_review(
    session: AsyncSession,
    owner_id: UUID,
    args: dict,
    current_time: datetime,
) -> dict:
    payload = ReviewIdInput.model_validate(args)
    try:
        return await review_service.review_detail(
            session,
            owner_id,
            payload.review_id,
            current_time=current_time,
        )
    except AppError as exc:
        if exc.code == "review_not_found":
            return _missing("review_id", payload.review_id)
        raise


async def handle_list_difficulties(
    session: AsyncSession,
    owner_id: UUID,
    args: dict,
    current_time: datetime,
) -> dict:
    payload = DifficultyRangeInput.model_validate(args)
    start = datetime.combine(payload.date_from, datetime.min.time()).replace(
        tzinfo=current_time.tzinfo
    )
    end = datetime.combine(payload.date_to, datetime.max.time()).replace(
        tzinfo=current_time.tzinfo
    )
    return await difficulty_service.list_difficulties(
        session,
        owner_id,
        scope="active",
        start=start,
        end=end,
        task_id=None,
        category=None,
        before_time=None,
        before_id=None,
        limit=50,
        current_time=current_time,
    )


async def handle_search_memory(
    session: AsyncSession,
    owner_id: UUID,
    args: dict,
    current_time: datetime,
) -> dict:
    payload = SearchMemoryInput.model_validate(args)
    return await memory_service.search_memory_by_query(
        session,
        owner_id,
        query=payload.query,
        limit=payload.limit,
        current_time=current_time,
    )


async def handle_list_memories(
    session: AsyncSession,
    owner_id: UUID,
    args: dict,
    current_time: datetime,
) -> dict:
    payload = ListMemoriesInput.model_validate(args)
    result = await memory_service.list_memories(
        session,
        owner_id,
        lifecycle=payload.filter.lifecycle,
        before_time=None,
        before_id=None,
        limit=payload.limit,
    )
    items = result["items"]
    if payload.filter.kind is not None:
        items = [item for item in items if item["kind"] == payload.filter.kind]
    if payload.filter.tags:
        wanted = set(payload.filter.tags)
        items = [
            item
            for item in items
            if wanted.intersection(item.get("tags") or [])
        ]
    return {"items": items, "next_cursor": result.get("next_cursor")}
