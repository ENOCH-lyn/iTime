"""Bulk reads that let an offline client bootstrap and repair its cache.

The change feed (``/api/v1/sync/changes``) only carries entity identity and
version; it deliberately does not carry payloads.  A client therefore needs
two reads:

* ``snapshot`` - one consistent projection of every syncable entity, plus the
  cursor that matches it.  Used for the first sync and whenever the server
  asks the client to reset its baseline.
* ``entity`` - one entity projection by type and id.  Used to materialise the
  entities named by a change feed page.

Both reads are owner scoped and reuse the same serialisers as the user facing
API, so Web and App always observe identical business shapes.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from itime.auth.security import Security, now
from itime.calendar.addition_service import addition_result
from itime.calendar.exception_service import exception_result
from itime.calendar.models import (
    AcademicTerm,
    CalendarAddition,
    CalendarException,
    CalendarSeries,
    TermPeriod,
    UserTimeSettings,
)
from itime.calendar.series_service import series_result
from itime.calendar.service import term_result, time_settings_result
from itime.errors import AppError
from itime.sync.cursor import SyncCursor, encode_sync_cursor
from itime.sync.service import latest_sequence, require_sync_state
from itime.tasks.models import Project, Task, TaskOccurrenceState, TaskStep
from itime.tasks.schedule_service import occurrence_state_result
from itime.tasks.service import project_result, task_summaries

SUPPORTED_ENTITY_TYPES = frozenset(
    {
        "project",
        "task",
        "step",
        "term",
        "calendar_series",
        "calendar_exception",
        "calendar_addition",
        "task_occurrence",
        "time_settings",
    }
)


def _step_result(step: TaskStep) -> dict:
    return {
        "id": step.id,
        "task_id": step.task_id,
        "title": step.title,
        "position": step.position,
        "completed": step.completed,
        "completed_at": step.completed_at,
        "created_at": step.created_at,
        "updated_at": step.updated_at,
    }


def _steps_by_task(steps: list[TaskStep]) -> dict[UUID, list[dict]]:
    grouped: dict[UUID, list[dict]] = {}
    for step in steps:
        grouped.setdefault(step.task_id, []).append(_step_result(step))
    return grouped


async def build_snapshot(
    session: AsyncSession,
    security: Security,
    owner_id: UUID,
) -> dict:
    """Project every syncable entity for one owner.

    The cursor is captured *before* the entity reads.  A write that lands
    between the cursor read and the entity reads is therefore either visible
    in the snapshot or replayed by the next change-feed page (usually both,
    and replaying it is idempotent), so a client can never skip a change.
    """
    state = await require_sync_state(session, owner_id)
    head = max(
        await latest_sequence(session, owner_id),
        state.discarded_through_sequence,
    )
    cursor = encode_sync_cursor(security, SyncCursor(owner_id, head))
    captured_at: datetime = now()

    tasks = list(
        await session.scalars(
            select(Task)
            .where(Task.owner_id == owner_id)
            .order_by(Task.updated_at, Task.id)
        )
    )
    summaries = (
        await task_summaries(session, tasks) if tasks else []
    )
    steps = list(
        await session.scalars(
            select(TaskStep)
            .where(TaskStep.owner_id == owner_id)
            .order_by(TaskStep.task_id, TaskStep.position)
        )
    )
    grouped_steps = _steps_by_task(steps)
    for summary in summaries:
        summary["steps"] = grouped_steps.get(summary["id"], [])

    projects = list(
        await session.scalars(
            select(Project).where(Project.owner_id == owner_id)
        )
    )
    terms = list(
        await session.scalars(
            select(AcademicTerm).where(AcademicTerm.owner_id == owner_id)
        )
    )
    periods = list(
        await session.scalars(
            select(TermPeriod)
            .where(TermPeriod.owner_id == owner_id)
            .order_by(TermPeriod.term_id, TermPeriod.period_number)
        )
    )
    periods_by_term: dict[UUID, list[TermPeriod]] = {}
    for period in periods:
        periods_by_term.setdefault(period.term_id, []).append(period)

    series = list(
        await session.scalars(
            select(CalendarSeries)
            .where(CalendarSeries.owner_id == owner_id)
            .order_by(CalendarSeries.updated_at, CalendarSeries.id)
        )
    )
    exceptions = list(
        await session.scalars(
            select(CalendarException).where(
                CalendarException.owner_id == owner_id
            )
        )
    )
    additions = list(
        await session.scalars(
            select(CalendarAddition).where(
                CalendarAddition.owner_id == owner_id
            )
        )
    )
    occurrences = list(
        await session.scalars(
            select(TaskOccurrenceState).where(
                TaskOccurrenceState.owner_id == owner_id
            )
        )
    )
    time_settings = await session.get(UserTimeSettings, owner_id)

    return {
        "cursor": cursor,
        "server_time": captured_at,
        "time_settings": time_settings_result(time_settings),
        "projects": [project_result(project) for project in projects],
        "tasks": summaries,
        "terms": [
            term_result(
                term,
                periods=periods_by_term.get(term.id, []),
            )
            for term in terms
        ],
        "series": [series_result(item) for item in series],
        "calendar_exceptions": [
            exception_result(item) for item in exceptions
        ],
        "calendar_additions": [
            addition_result(item) for item in additions
        ],
        "task_occurrences": [
            {**occurrence_state_result(item), "task_id": str(item.task_id)}
            for item in occurrences
        ],
    }


async def read_entity(
    session: AsyncSession,
    owner_id: UUID,
    entity_type: str,
    entity_id: UUID,
) -> dict:
    """Project one entity for delta materialisation."""
    if entity_type not in SUPPORTED_ENTITY_TYPES:
        raise AppError(
            "sync_entity_unsupported",
            "此同步实体需要重新做全量同步。",
            status=400,
            details={"entity_type": entity_type},
        )

    if entity_type == "task":
        task = await session.scalar(
            select(Task).where(
                Task.id == entity_id,
                Task.owner_id == owner_id,
            )
        )
        if task is None:
            return _missing(entity_type, entity_id)
        detail = (await task_summaries(session, [task]))[0]
        steps = list(
            await session.scalars(
                select(TaskStep)
                .where(TaskStep.task_id == task.id)
                .order_by(TaskStep.position)
            )
        )
        detail["steps"] = [_step_result(step) for step in steps]
        return {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "deleted": task.purged_at is not None,
            "data": detail,
        }

    if entity_type == "step":
        step = await session.scalar(
            select(TaskStep).where(
                TaskStep.id == entity_id,
                TaskStep.owner_id == owner_id,
            )
        )
        if step is None:
            return _missing(entity_type, entity_id)
        return {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "deleted": False,
            "task_id": step.task_id,
            "data": _step_result(step),
        }

    if entity_type == "project":
        project = await session.scalar(
            select(Project).where(
                Project.id == entity_id,
                Project.owner_id == owner_id,
            )
        )
        if project is None:
            return _missing(entity_type, entity_id)
        return _found(entity_type, entity_id, project_result(project))

    if entity_type == "term":
        term = await session.scalar(
            select(AcademicTerm).where(
                AcademicTerm.id == entity_id,
                AcademicTerm.owner_id == owner_id,
            )
        )
        if term is None:
            return _missing(entity_type, entity_id)
        periods = list(
            await session.scalars(
                select(TermPeriod)
                .where(TermPeriod.term_id == term.id)
                .order_by(TermPeriod.period_number)
            )
        )
        return _found(
            entity_type,
            entity_id,
            term_result(term, periods=periods),
        )

    if entity_type == "calendar_series":
        series = await session.scalar(
            select(CalendarSeries).where(
                CalendarSeries.id == entity_id,
                CalendarSeries.owner_id == owner_id,
            )
        )
        if series is None:
            return _missing(entity_type, entity_id)
        return _found(entity_type, entity_id, series_result(series))

    if entity_type == "calendar_exception":
        exception = await session.scalar(
            select(CalendarException).where(
                CalendarException.id == entity_id,
                CalendarException.owner_id == owner_id,
            )
        )
        if exception is None:
            return _missing(entity_type, entity_id)
        return _found(
            entity_type,
            entity_id,
            exception_result(exception),
            series_id=exception.series_id,
        )

    if entity_type == "calendar_addition":
        addition = await session.scalar(
            select(CalendarAddition).where(
                CalendarAddition.id == entity_id,
                CalendarAddition.owner_id == owner_id,
            )
        )
        if addition is None:
            return _missing(entity_type, entity_id)
        return _found(
            entity_type,
            entity_id,
            addition_result(addition),
            series_id=addition.series_id,
        )

    if entity_type == "task_occurrence":
        occurrence = await session.scalar(
            select(TaskOccurrenceState).where(
                TaskOccurrenceState.id == entity_id,
                TaskOccurrenceState.owner_id == owner_id,
            )
        )
        if occurrence is None:
            return _missing(entity_type, entity_id)
        return _found(
            entity_type,
            entity_id,
            occurrence_state_result(occurrence),
            task_id=occurrence.task_id,
        )

    # time_settings
    if entity_id != owner_id:
        return _missing(entity_type, entity_id)
    settings = await session.get(UserTimeSettings, owner_id)
    return _found(
        entity_type,
        owner_id,
        time_settings_result(settings),
    )


def _found(
    entity_type: str,
    entity_id: UUID,
    data: dict,
    *,
    series_id: UUID | None = None,
    task_id: UUID | None = None,
) -> dict:
    result = {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "deleted": False,
        "data": data,
    }
    if series_id is not None:
        result["series_id"] = series_id
    if task_id is not None:
        result["task_id"] = task_id
    return result


def _missing(entity_type: str, entity_id: UUID) -> dict:
    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "deleted": True,
        "data": None,
    }
