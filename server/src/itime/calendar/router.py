from datetime import date, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query, Request
from sqlalchemy import and_, delete, func, or_, select

from itime.auth.security import now
from itime.auth.session import authorized
from itime.calendar.addition_service import (
    addition_result,
    addition_values,
    addition_version_conflict,
    ensure_additions_remain_valid,
    locked_addition,
    resolve_addition_time_zone,
    series_additions,
    validate_addition_fields,
)
from itime.calendar.exception_service import (
    ensure_exceptions_remain_valid,
    exception_result,
    exception_values,
    exception_version_conflict,
    find_occurrence_date,
    locked_exception,
    series_exceptions,
    validate_exception_fields,
)
from itime.calendar.lifecycle_service import (
    ensure_calendar_editable,
    ensure_calendar_readable,
)
from itime.calendar.lifecycle_service import (
    restore_calendar_series as restore_calendar_series_item,
)
from itime.calendar.lifecycle_service import (
    trash_calendar_series as trash_calendar_series_item,
)
from itime.calendar.models import (
    AcademicTerm,
    CalendarAddition,
    CalendarException,
    CalendarSeries,
    TermPeriod,
    UserTimeSettings,
)
from itime.calendar.recurrence import occurrence_dates, plan_from_series
from itime.calendar.schemas import (
    AdditionCreate,
    AdditionUpdate,
    ExceptionCreate,
    ExceptionUpdate,
    PeriodsReplace,
    SeriesCreate,
    SeriesLifecycleInput,
    SeriesSplit,
    SeriesUpdate,
    TermCreate,
    TermUpdate,
    TimeSettingsUpdate,
)
from itime.calendar.series_service import (
    find_series_conflicts,
    locked_series,
    occurrence_result,
    query_occurrences,
    resolve_series_draft,
    series_result,
    series_values,
    series_version_conflict,
)
from itime.calendar.service import (
    default_term_time_zone,
    locked_term,
    normalized_period_name,
    period_rows_equal,
    term_detail,
    term_result,
    term_version_conflict,
    time_settings_result,
    validate_periods,
    validate_term_dates,
    validate_time_zone,
)
from itime.errors import AppError
from itime.tasks.service import (
    emit_change,
    replay_operation,
    save_operation,
    validate_cursor,
)

router = APIRouter(prefix="/api/v1", tags=["时间与课表"])


@router.get("/time-settings")
async def get_time_settings(request: Request) -> dict:
    async with authorized(request) as identity:
        settings = await identity.session.get(UserTimeSettings, identity.user.id)
        return {"settings": time_settings_result(settings)}


@router.put("/time-settings")
async def update_time_settings(data: TimeSettingsUpdate, request: Request) -> dict:
    scope = "time_settings.update"
    async with authorized(request, write=True) as identity:
        replay = await replay_operation(
            identity.session,
            identity.user.id,
            data.operation_id,
            scope,
            data,
        )
        if replay is not None:
            return replay
        time_zone = validate_time_zone(data.time_zone)
        settings = await identity.session.scalar(
            select(UserTimeSettings)
            .where(UserTimeSettings.owner_id == identity.user.id)
            .with_for_update()
        )
        current_version = settings.version if settings is not None else 0
        if data.expected_version != current_version:
            raise AppError(
                "time_settings_version_conflict",
                "时间设置已在其他页面或设备更新，请刷新后比较再保存。",
                status=409,
                details={"current_version": current_version},
            )
        if settings is None:
            settings = UserTimeSettings(
                owner_id=identity.user.id,
                time_zone=time_zone,
                weather_location=data.weather_location,
                week_starts_on=data.week_starts_on,
                activity_day_start_minute=data.activity_day_start_minute,
            )
            identity.session.add(settings)
            await identity.session.flush()
            emit_change(
                identity.session,
                identity.user.id,
                "time_settings",
                identity.user.id,
                "created",
                settings.version,
            )
        elif (
            settings.time_zone != time_zone
            or settings.weather_location != data.weather_location
            or settings.week_starts_on != data.week_starts_on
            or settings.activity_day_start_minute != data.activity_day_start_minute
        ):
            settings.time_zone = time_zone
            settings.weather_location = data.weather_location
            settings.week_starts_on = data.week_starts_on
            settings.activity_day_start_minute = data.activity_day_start_minute
            settings.version += 1
            settings.updated_at = now()
            emit_change(
                identity.session,
                identity.user.id,
                "time_settings",
                identity.user.id,
                "updated",
                settings.version,
            )
        await identity.session.flush()
        result = {
            "operation_id": data.operation_id,
            "settings": time_settings_result(settings),
        }
        save_operation(
            identity.session,
            identity.user.id,
            data.operation_id,
            scope,
            data,
            result,
        )
        return result


@router.post("/terms", status_code=201)
async def create_term(data: TermCreate, request: Request) -> dict:
    scope = "terms.create"
    async with authorized(request, write=True) as identity:
        replay = await replay_operation(
            identity.session,
            identity.user.id,
            data.operation_id,
            scope,
            data,
        )
        if replay is not None:
            return replay
        validate_term_dates(
            data.start_date,
            data.end_date,
            data.first_week_monday,
        )
        time_zone = (
            validate_time_zone(data.time_zone)
            if data.time_zone is not None
            else await default_term_time_zone(identity.session, identity.user.id)
        )
        term = AcademicTerm(
            owner_id=identity.user.id,
            name=data.name,
            start_date=data.start_date,
            end_date=data.end_date,
            first_week_monday=data.first_week_monday,
            time_zone=time_zone,
        )
        identity.session.add(term)
        await identity.session.flush()
        emit_change(
            identity.session,
            identity.user.id,
            "term",
            term.id,
            "created",
            term.version,
        )
        result = {
            "operation_id": data.operation_id,
            "term": await term_detail(identity.session, term),
        }
        save_operation(
            identity.session,
            identity.user.id,
            data.operation_id,
            scope,
            data,
            result,
        )
        return result


@router.get("/terms")
async def list_terms(
    request: Request,
    before_time: datetime | None = None,
    before_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict:
    validate_cursor(before_time, before_id)
    async with authorized(request) as identity:
        statement = select(AcademicTerm).where(
            AcademicTerm.owner_id == identity.user.id
        )
        if before_time is not None:
            statement = statement.where(
                or_(
                    AcademicTerm.updated_at < before_time,
                    and_(
                        AcademicTerm.updated_at == before_time,
                        AcademicTerm.id < before_id,
                    ),
                )
            )
        rows = list(
            await identity.session.scalars(
                statement.order_by(
                    AcademicTerm.updated_at.desc(),
                    AcademicTerm.id.desc(),
                ).limit(limit + 1)
            )
        )
        page = rows[:limit]
        term_ids = [term.id for term in page]
        counts = (
            {
                term_id: count
                for term_id, count in await identity.session.execute(
                    select(TermPeriod.term_id, func.count(TermPeriod.period_number))
                    .where(TermPeriod.term_id.in_(term_ids))
                    .group_by(TermPeriod.term_id)
                )
            }
            if term_ids
            else {}
        )
        return {
            "items": [
                term_result(term, period_count=counts.get(term.id, 0))
                for term in page
            ],
            "next_cursor": (
                {
                    "before_time": page[-1].updated_at,
                    "before_id": page[-1].id,
                }
                if len(rows) > limit
                else None
            ),
        }


@router.get("/terms/{term_id}")
async def get_term(term_id: UUID, request: Request) -> dict:
    async with authorized(request) as identity:
        term = await identity.session.scalar(
            select(AcademicTerm).where(
                AcademicTerm.id == term_id,
                AcademicTerm.owner_id == identity.user.id,
            )
        )
        if term is None:
            raise AppError("term_not_found", "未找到此学期。", status=404)
        return {"term": await term_detail(identity.session, term)}


@router.put("/terms/{term_id}")
async def update_term(
    term_id: UUID,
    data: TermUpdate,
    request: Request,
) -> dict:
    scope = f"terms.update:{term_id}"
    async with authorized(request, write=True) as identity:
        replay = await replay_operation(
            identity.session,
            identity.user.id,
            data.operation_id,
            scope,
            data,
        )
        if replay is not None:
            return replay
        term = await locked_term(identity.session, identity.user.id, term_id)
        if term.version != data.expected_version:
            raise term_version_conflict(term)
        validate_term_dates(
            data.start_date,
            data.end_date,
            data.first_week_monday,
        )
        time_zone = (
            validate_time_zone(data.time_zone)
            if data.time_zone is not None
            else term.time_zone
        )
        changed = (
            term.name != data.name
            or term.start_date != data.start_date
            or term.end_date != data.end_date
            or term.first_week_monday != data.first_week_monday
            or term.time_zone != time_zone
        )
        if changed:
            term.name = data.name
            term.start_date = data.start_date
            term.end_date = data.end_date
            term.first_week_monday = data.first_week_monday
            term.time_zone = time_zone
            term.version += 1
            term.updated_at = now()
            emit_change(
                identity.session,
                identity.user.id,
                "term",
                term.id,
                "updated",
                term.version,
            )
        await identity.session.flush()
        result = {
            "operation_id": data.operation_id,
            "term": await term_detail(identity.session, term),
        }
        save_operation(
            identity.session,
            identity.user.id,
            data.operation_id,
            scope,
            data,
            result,
        )
        return result


@router.put("/terms/{term_id}/periods")
async def replace_term_periods(
    term_id: UUID,
    data: PeriodsReplace,
    request: Request,
) -> dict:
    scope = f"terms.periods.replace:{term_id}"
    async with authorized(request, write=True) as identity:
        replay = await replay_operation(
            identity.session,
            identity.user.id,
            data.operation_id,
            scope,
            data,
        )
        if replay is not None:
            return replay
        term = await locked_term(identity.session, identity.user.id, term_id)
        if term.version != data.expected_version:
            raise term_version_conflict(term)
        validate_periods(data.periods)
        current = list(
            await identity.session.scalars(
                select(TermPeriod)
                .where(TermPeriod.term_id == term.id)
                .order_by(TermPeriod.period_number)
                .with_for_update()
            )
        )
        if not period_rows_equal(current, data.periods):
            for existing in current:
                await identity.session.delete(existing)
            await identity.session.flush()
            identity.session.add_all(
                TermPeriod(
                    term_id=term.id,
                    owner_id=identity.user.id,
                    period_number=period.period_number,
                    name=period.name,
                    normalized_name=normalized_period_name(period.name),
                    starts_at=period.starts_at,
                    ends_at=period.ends_at,
                )
                for period in data.periods
            )
            term.version += 1
            term.updated_at = now()
            emit_change(
                identity.session,
                identity.user.id,
                "term",
                term.id,
                "updated",
                term.version,
            )
        await identity.session.flush()
        result = {
            "operation_id": data.operation_id,
            "term": await term_detail(identity.session, term),
        }
        save_operation(
            identity.session,
            identity.user.id,
            data.operation_id,
            scope,
            data,
            result,
        )
        return result


@router.delete("/terms/{term_id}")
async def delete_term(term_id: UUID, request: Request) -> dict:
    async with authorized(request, write=True) as identity:
        term = await locked_term(identity.session, identity.user.id, term_id)
        course_count = await identity.session.scalar(
            select(func.count())
            .select_from(CalendarSeries)
            .where(
                CalendarSeries.owner_id == identity.user.id,
                CalendarSeries.term_id == term.id,
            )
        )
        if course_count:
            await identity.session.execute(
                delete(CalendarSeries).where(
                    CalendarSeries.owner_id == identity.user.id,
                    CalendarSeries.term_id == term.id,
                )
            )
        await identity.session.delete(term)
        emit_change(
            identity.session,
            identity.user.id,
            "term",
            term.id,
            "deleted",
            term.version,
        )
        return {"status": "deleted", "deleted_courses": course_count or 0}


def series_warnings(occurrence_keys: tuple[str, ...]) -> list[dict]:
    return [
        {
            "code": "invalid_local_time",
            "occurrence_key": key,
            "message": "该次安排位于夏令时跳过的本地时间，未自动平移。",
        }
        for key in occurrence_keys
    ]


@router.post("/calendar/series", status_code=201)
async def create_calendar_series(data: SeriesCreate, request: Request) -> dict:
    scope = "calendar_series.create"
    async with authorized(request, write=True) as identity:
        replay = await replay_operation(
            identity.session,
            identity.user.id,
            data.operation_id,
            scope,
            data,
        )
        if replay is not None:
            return replay
        resolved = await resolve_series_draft(
            identity.session,
            identity.user.id,
            data,
        )
        series = CalendarSeries(
            owner_id=identity.user.id,
            predecessor_id=None,
            source_id=None,
            source_type="manual",
            course_group_id=(
                data.course_group_id or uuid4()
                if data.kind == "course"
                else None
            ),
            **series_values(data, resolved),
        )
        identity.session.add(series)
        await identity.session.flush()
        emit_change(
            identity.session,
            identity.user.id,
            "calendar_series",
            series.id,
            "created",
            series.version,
        )
        result = {
            "operation_id": data.operation_id,
            "series": series_result(series),
            "warnings": series_warnings(resolved.warning_occurrence_keys),
            "conflicts": await find_series_conflicts(
                identity.session,
                identity.user.id,
                series,
            ),
        }
        save_operation(
            identity.session,
            identity.user.id,
            data.operation_id,
            scope,
            data,
            result,
        )
        return result


@router.get("/calendar/series")
async def list_calendar_series(
    request: Request,
    kind: Literal["all", "course", "fixed"] = "all",
    lifecycle: Literal["active", "trashed"] = "active",
    term_id: UUID | None = None,
    before_time: datetime | None = None,
    before_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict:
    validate_cursor(before_time, before_id)
    async with authorized(request) as identity:
        if term_id is not None:
            owned = await identity.session.scalar(
                select(AcademicTerm.id).where(
                    AcademicTerm.id == term_id,
                    AcademicTerm.owner_id == identity.user.id,
                )
            )
            if owned is None:
                raise AppError("term_not_found", "未找到此学期。", status=404)
        statement = select(CalendarSeries).where(
            CalendarSeries.owner_id == identity.user.id
        )
        if lifecycle == "active":
            statement = statement.where(
                CalendarSeries.deleted_at.is_(None),
                CalendarSeries.purged_at.is_(None),
            )
        else:
            statement = statement.where(
                CalendarSeries.deleted_at.is_not(None),
                CalendarSeries.purged_at.is_(None),
            )
        if kind != "all":
            statement = statement.where(CalendarSeries.kind == kind)
        if term_id is not None:
            statement = statement.where(CalendarSeries.term_id == term_id)
        if before_time is not None:
            statement = statement.where(
                or_(
                    CalendarSeries.updated_at < before_time,
                    and_(
                        CalendarSeries.updated_at == before_time,
                        CalendarSeries.id < before_id,
                    ),
                )
            )
        rows = list(
            await identity.session.scalars(
                statement.order_by(
                    CalendarSeries.updated_at.desc(),
                    CalendarSeries.id.desc(),
                ).limit(limit + 1)
            )
        )
        page = rows[:limit]
        return {
            "items": [series_result(series) for series in page],
            "next_cursor": (
                {
                    "before_time": page[-1].updated_at,
                    "before_id": page[-1].id,
                }
                if len(rows) > limit
                else None
            ),
        }


@router.get("/calendar/series/{series_id}")
async def get_calendar_series(series_id: UUID, request: Request) -> dict:
    async with authorized(request) as identity:
        series = await identity.session.scalar(
            select(CalendarSeries).where(
                CalendarSeries.id == series_id,
                CalendarSeries.owner_id == identity.user.id,
            )
        )
        if series is None:
            raise AppError(
                "calendar_series_not_found",
                "未找到此日历系列。",
                status=404,
            )
        ensure_calendar_readable(series)
        return {"series": series_result(series)}


@router.delete("/calendar/series/{series_id}")
async def trash_calendar_series(
    series_id: UUID,
    data: SeriesLifecycleInput,
    request: Request,
) -> dict:
    async with authorized(request, write=True) as identity:
        return await trash_calendar_series_item(
            identity.session,
            identity.user.id,
            series_id,
            data,
            current_time=now(),
        )


@router.post("/calendar/series/{series_id}/restore")
async def restore_calendar_series(
    series_id: UUID,
    data: SeriesLifecycleInput,
    request: Request,
) -> dict:
    async with authorized(request, write=True) as identity:
        return await restore_calendar_series_item(
            identity.session,
            identity.user.id,
            series_id,
            data,
            current_time=now(),
        )


@router.put("/calendar/series/{series_id}")
async def update_calendar_series(
    series_id: UUID,
    data: SeriesUpdate,
    request: Request,
) -> dict:
    scope = f"calendar_series.update:{series_id}"
    async with authorized(request, write=True) as identity:
        replay = await replay_operation(
            identity.session,
            identity.user.id,
            data.operation_id,
            scope,
            data,
        )
        if replay is not None:
            return replay
        series = await locked_series(
            identity.session,
            identity.user.id,
            series_id,
        )
        if series.version != data.expected_version:
            raise series_version_conflict(series)
        resolved = await resolve_series_draft(
            identity.session,
            identity.user.id,
            data,
        )
        await ensure_exceptions_remain_valid(
            identity.session,
            identity.user.id,
            series.id,
            resolved.plan,
        )
        await ensure_additions_remain_valid(
            identity.session,
            identity.user.id,
            series.id,
            resolved.plan,
        )
        values = series_values(data, resolved)
        changed = any(getattr(series, field) != value for field, value in values.items())
        if changed:
            for field, value in values.items():
                setattr(series, field, value)
            series.version += 1
            series.updated_at = now()
            emit_change(
                identity.session,
                identity.user.id,
                "calendar_series",
                series.id,
                "updated",
                series.version,
            )
        await identity.session.flush()
        result = {
            "operation_id": data.operation_id,
            "series": series_result(series),
            "warnings": series_warnings(resolved.warning_occurrence_keys),
            "conflicts": await find_series_conflicts(
                identity.session,
                identity.user.id,
                series,
            ),
        }
        save_operation(
            identity.session,
            identity.user.id,
            data.operation_id,
            scope,
            data,
            result,
        )
        return result


@router.get("/calendar/occurrences")
async def get_calendar_occurrences(
    request: Request,
    start: date,
    end: date,
) -> dict:
    async with authorized(request) as identity:
        occurrences, time_zone = await query_occurrences(
            identity.session,
            identity.user.id,
            start,
            end,
        )
        return {
            "start": start,
            "end": end,
            "time_zone": time_zone,
            "items": occurrences,
        }


@router.get("/calendar/series/{series_id}/exceptions")
async def list_calendar_exceptions(series_id: UUID, request: Request) -> dict:
    async with authorized(request) as identity:
        series = await identity.session.scalar(
            select(CalendarSeries).where(
                CalendarSeries.id == series_id,
                CalendarSeries.owner_id == identity.user.id,
            )
        )
        if series is None:
            raise AppError(
                "calendar_series_not_found",
                "未找到此日历系列。",
                status=404,
            )
        ensure_calendar_editable(series)
        exceptions = await series_exceptions(
            identity.session,
            identity.user.id,
            series.id,
        )
        return {
            "series_version": series.version,
            "items": [exception_result(exception) for exception in exceptions],
        }


@router.post("/calendar/series/{series_id}/exceptions", status_code=201)
async def create_calendar_exception(
    series_id: UUID,
    data: ExceptionCreate,
    request: Request,
) -> dict:
    scope = f"calendar_exceptions.create:{series_id}"
    async with authorized(request, write=True) as identity:
        replay = await replay_operation(
            identity.session,
            identity.user.id,
            data.operation_id,
            scope,
            data,
        )
        if replay is not None:
            return replay
        series = await locked_series(
            identity.session,
            identity.user.id,
            series_id,
        )
        if series.version != data.expected_series_version:
            raise series_version_conflict(series)
        original_start_date = await find_occurrence_date(
            identity.session,
            identity.user.id,
            series,
            data.occurrence_key,
        )
        existing = await identity.session.scalar(
            select(CalendarException).where(
                CalendarException.series_id == series.id,
                CalendarException.original_start_date == original_start_date,
            )
        )
        if existing is not None:
            raise AppError(
                "calendar_exception_exists",
                "该次安排已经有单次修改，请编辑现有例外。",
                status=409,
                details={
                    "exception_id": str(existing.id),
                    "current_version": existing.version,
                },
            )
        effective_data = data
        if data.action == "moved" and data.moved_time_zone is None:
            current_time_zone = await identity.session.scalar(
                select(UserTimeSettings.time_zone).where(
                    UserTimeSettings.owner_id == identity.user.id
                )
            )
            effective_data = data.model_copy(
                update={
                    "moved_time_zone": current_time_zone or series.time_zone
                }
            )
        moved_time_zone = validate_exception_fields(series, effective_data)
        exception = CalendarException(
            series_id=series.id,
            owner_id=identity.user.id,
            occurrence_key=data.occurrence_key,
            original_start_date=original_start_date,
            **exception_values(effective_data, moved_time_zone),
        )
        identity.session.add(exception)
        series.version += 1
        series.updated_at = now()
        await identity.session.flush()
        emit_change(
            identity.session,
            identity.user.id,
            "calendar_exception",
            exception.id,
            "created",
            exception.version,
        )
        emit_change(
            identity.session,
            identity.user.id,
            "calendar_series",
            series.id,
            "updated",
            series.version,
        )
        result = {
            "operation_id": data.operation_id,
            "series": series_result(series),
            "exception": exception_result(exception),
            "conflicts": await find_series_conflicts(
                identity.session,
                identity.user.id,
                series,
            ),
        }
        save_operation(
            identity.session,
            identity.user.id,
            data.operation_id,
            scope,
            data,
            result,
        )
        return result


@router.put("/calendar/series/{series_id}/exceptions/{exception_id}")
async def update_calendar_exception(
    series_id: UUID,
    exception_id: UUID,
    data: ExceptionUpdate,
    request: Request,
) -> dict:
    scope = f"calendar_exceptions.update:{series_id}:{exception_id}"
    async with authorized(request, write=True) as identity:
        replay = await replay_operation(
            identity.session,
            identity.user.id,
            data.operation_id,
            scope,
            data,
        )
        if replay is not None:
            return replay
        series = await locked_series(
            identity.session,
            identity.user.id,
            series_id,
        )
        if series.version != data.expected_series_version:
            raise series_version_conflict(series)
        exception = await locked_exception(
            identity.session,
            identity.user.id,
            series.id,
            exception_id,
        )
        if exception.version != data.expected_version:
            raise exception_version_conflict(exception)
        effective_data = data
        if data.action == "moved" and data.moved_time_zone is None:
            default_time_zone = (
                exception.moved_time_zone
                if exception.action == "moved"
                else (
                    await identity.session.scalar(
                        select(UserTimeSettings.time_zone).where(
                            UserTimeSettings.owner_id == identity.user.id
                        )
                    )
                    or series.time_zone
                )
            )
            effective_data = data.model_copy(
                update={"moved_time_zone": default_time_zone}
            )
        moved_time_zone = validate_exception_fields(series, effective_data)
        values = exception_values(effective_data, moved_time_zone)
        changed = any(
            getattr(exception, field) != value for field, value in values.items()
        )
        if changed:
            for field, value in values.items():
                setattr(exception, field, value)
            exception.version += 1
            exception.updated_at = now()
            series.version += 1
            series.updated_at = now()
            emit_change(
                identity.session,
                identity.user.id,
                "calendar_exception",
                exception.id,
                "updated",
                exception.version,
            )
            emit_change(
                identity.session,
                identity.user.id,
                "calendar_series",
                series.id,
                "updated",
                series.version,
            )
        await identity.session.flush()
        result = {
            "operation_id": data.operation_id,
            "series": series_result(series),
            "exception": exception_result(exception),
            "conflicts": await find_series_conflicts(
                identity.session,
                identity.user.id,
                series,
            ),
        }
        save_operation(
            identity.session,
            identity.user.id,
            data.operation_id,
            scope,
            data,
            result,
        )
        return result


@router.get("/calendar/series/{series_id}/additions")
async def list_calendar_additions(series_id: UUID, request: Request) -> dict:
    async with authorized(request) as identity:
        series = await identity.session.scalar(
            select(CalendarSeries).where(
                CalendarSeries.id == series_id,
                CalendarSeries.owner_id == identity.user.id,
            )
        )
        if series is None:
            raise AppError(
                "calendar_series_not_found",
                "未找到此日历系列。",
                status=404,
            )
        ensure_calendar_editable(series)
        additions = await series_additions(
            identity.session,
            identity.user.id,
            series.id,
        )
        return {
            "series_version": series.version,
            "items": [addition_result(addition) for addition in additions],
        }


@router.post("/calendar/series/{series_id}/additions", status_code=201)
async def create_calendar_addition(
    series_id: UUID,
    data: AdditionCreate,
    request: Request,
) -> dict:
    scope = f"calendar_additions.create:{series_id}"
    async with authorized(request, write=True) as identity:
        replay = await replay_operation(
            identity.session,
            identity.user.id,
            data.operation_id,
            scope,
            data,
        )
        if replay is not None:
            return replay
        series = await locked_series(
            identity.session,
            identity.user.id,
            series_id,
        )
        if series.version != data.expected_series_version:
            raise series_version_conflict(series)
        source_date = await find_occurrence_date(
            identity.session,
            identity.user.id,
            series,
            data.source_occurrence_key,
        )
        existing = await identity.session.scalar(
            select(CalendarAddition).where(
                CalendarAddition.series_id == series.id,
                CalendarAddition.source_original_start_date == source_date,
            )
        )
        if existing is not None:
            raise AppError(
                "calendar_addition_exists",
                "该次安排已经有关联补课，请编辑现有补课。",
                status=409,
                details={
                    "addition_id": str(existing.id),
                    "current_version": existing.version,
                },
            )
        time_zone = await resolve_addition_time_zone(
            identity.session,
            identity.user.id,
            series,
            data.time_zone,
        )
        validate_addition_fields(data, time_zone)
        addition_id = uuid4()
        addition = CalendarAddition(
            id=addition_id,
            series_id=series.id,
            owner_id=identity.user.id,
            occurrence_key=f"makeup:{addition_id}",
            source_occurrence_key=data.source_occurrence_key,
            source_original_start_date=source_date,
            **addition_values(data, time_zone),
        )
        identity.session.add(addition)
        series.version += 1
        series.updated_at = now()
        await identity.session.flush()
        emit_change(
            identity.session,
            identity.user.id,
            "calendar_addition",
            addition.id,
            "created",
            addition.version,
        )
        emit_change(
            identity.session,
            identity.user.id,
            "calendar_series",
            series.id,
            "updated",
            series.version,
        )
        result = {
            "operation_id": data.operation_id,
            "series": series_result(series),
            "addition": addition_result(addition),
            "conflicts": await find_series_conflicts(
                identity.session,
                identity.user.id,
                series,
            ),
        }
        save_operation(
            identity.session,
            identity.user.id,
            data.operation_id,
            scope,
            data,
            result,
        )
        return result


@router.put("/calendar/series/{series_id}/additions/{addition_id}")
async def update_calendar_addition(
    series_id: UUID,
    addition_id: UUID,
    data: AdditionUpdate,
    request: Request,
) -> dict:
    scope = f"calendar_additions.update:{series_id}:{addition_id}"
    async with authorized(request, write=True) as identity:
        replay = await replay_operation(
            identity.session,
            identity.user.id,
            data.operation_id,
            scope,
            data,
        )
        if replay is not None:
            return replay
        series = await locked_series(
            identity.session,
            identity.user.id,
            series_id,
        )
        if series.version != data.expected_series_version:
            raise series_version_conflict(series)
        addition = await locked_addition(
            identity.session,
            identity.user.id,
            series.id,
            addition_id,
        )
        if addition.version != data.expected_version:
            raise addition_version_conflict(addition)
        time_zone = await resolve_addition_time_zone(
            identity.session,
            identity.user.id,
            series,
            data.time_zone or addition.time_zone,
        )
        validate_addition_fields(data, time_zone)
        values = addition_values(data, time_zone)
        changed = any(
            getattr(addition, field) != value
            for field, value in values.items()
        )
        if changed:
            for field, value in values.items():
                setattr(addition, field, value)
            addition.version += 1
            addition.updated_at = now()
            series.version += 1
            series.updated_at = now()
            emit_change(
                identity.session,
                identity.user.id,
                "calendar_addition",
                addition.id,
                "updated",
                addition.version,
            )
            emit_change(
                identity.session,
                identity.user.id,
                "calendar_series",
                series.id,
                "updated",
                series.version,
            )
        await identity.session.flush()
        result = {
            "operation_id": data.operation_id,
            "series": series_result(series),
            "addition": addition_result(addition),
            "conflicts": await find_series_conflicts(
                identity.session,
                identity.user.id,
                series,
            ),
        }
        save_operation(
            identity.session,
            identity.user.id,
            data.operation_id,
            scope,
            data,
            result,
        )
        return result


@router.post("/calendar/series/{series_id}/split", status_code=201)
async def split_calendar_series(
    series_id: UUID,
    data: SeriesSplit,
    request: Request,
) -> dict:
    scope = f"calendar_series.split:{series_id}"
    async with authorized(request, write=True) as identity:
        replay = await replay_operation(
            identity.session,
            identity.user.id,
            data.operation_id,
            scope,
            data,
        )
        if replay is not None:
            return replay
        series = await locked_series(
            identity.session,
            identity.user.id,
            series_id,
        )
        if series.version != data.expected_version:
            raise series_version_conflict(series)
        if series.recurrence_frequency == "none":
            raise AppError(
                "series_not_recurring",
                "不重复安排不能按“本次及以后”拆分。",
            )
        existing_successor = await identity.session.scalar(
            select(CalendarSeries.id).where(
                CalendarSeries.owner_id == identity.user.id,
                CalendarSeries.predecessor_id == series.id,
            )
        )
        if existing_successor is not None:
            raise AppError(
                "calendar_series_already_split",
                "此日历系列已经拆分；请从当前有效的后继系列继续修改。",
                status=409,
                details={"successor_series_id": str(existing_successor)},
            )
        split_date = await find_occurrence_date(
            identity.session,
            identity.user.id,
            series,
            data.occurrence_key,
        )
        split_occurrence = occurrence_result(series, split_date)
        current_time = now()
        historical = (
            split_date <= current_time.astimezone(ZoneInfo(series.time_zone)).date()
            if split_occurrence["start_at"] is None
            else split_occurrence["start_at"] <= current_time
        )
        if historical:
            raise AppError(
                "historical_occurrence_immutable",
                "已经发生或时间无效的实例不能作为未来系列分界点。",
                status=409,
            )
        old_dates = occurrence_dates(plan_from_series(series))
        split_index = old_dates.index(split_date)
        if split_index == 0:
            raise AppError(
                "split_at_first_occurrence",
                "从首个实例开始修改等同于修改整个系列，请使用系列编辑。",
            )
        resolved = await resolve_series_draft(
            identity.session,
            identity.user.id,
            data.replacement,
        )
        if (
            data.replacement.kind != series.kind
            or data.replacement.term_id != series.term_id
        ):
            raise AppError(
                "invalid_series_split",
                "拆分后的系列必须保留原类型和学期。",
            )
        if resolved.plan.start_date != split_date:
            raise AppError(
                "invalid_series_split_start",
                "拆分后的系列必须从所选实例日期开始。",
                details={"expected_start_date": split_date.isoformat()},
            )
        successor_dates = set(occurrence_dates(resolved.plan))
        exceptions = await series_exceptions(
            identity.session,
            identity.user.id,
            series.id,
            lock=True,
        )
        future_exceptions = [
            exception
            for exception in exceptions
            if exception.original_start_date >= split_date
        ]
        additions = await series_additions(
            identity.session,
            identity.user.id,
            series.id,
            lock=True,
        )
        future_additions = [
            addition
            for addition in additions
            if addition.source_original_start_date >= split_date
        ]
        orphaned = [
            exception
            for exception in future_exceptions
            if exception.original_start_date not in successor_dates
        ]
        if orphaned:
            raise AppError(
                "series_exceptions_would_be_orphaned",
                "拆分后的重复规则不包含已有的未来单次修改。",
                status=409,
                details={
                    "exception_ids": [
                        str(exception.id) for exception in orphaned[:20]
                    ],
                    "has_more": len(orphaned) > 20,
                },
            )
        orphaned_additions = [
            addition
            for addition in future_additions
            if addition.source_original_start_date not in successor_dates
        ]
        if orphaned_additions:
            raise AppError(
                "series_additions_would_be_orphaned",
                "拆分后的重复规则不包含已有补课的来源实例。",
                status=409,
                details={
                    "addition_ids": [
                        str(addition.id) for addition in orphaned_additions[:20]
                    ],
                    "has_more": len(orphaned_additions) > 20,
                },
            )

        if series.recurrence_count is not None:
            series.recurrence_count = split_index
        else:
            series.recurrence_until = split_date - timedelta(days=1)
        series.version += 1
        series.updated_at = now()
        successor = CalendarSeries(
            owner_id=identity.user.id,
            predecessor_id=series.id,
            source_id=series.source_id,
            source_type=series.source_type,
            **series_values(data.replacement, resolved),
        )
        identity.session.add(successor)
        await identity.session.flush()
        for exception in future_exceptions:
            exception.series_id = successor.id
            exception.version += 1
            exception.updated_at = now()
            emit_change(
                identity.session,
                identity.user.id,
                "calendar_exception",
                exception.id,
                "updated",
                exception.version,
            )
        for addition in future_additions:
            addition.series_id = successor.id
            addition.version += 1
            addition.updated_at = now()
            emit_change(
                identity.session,
                identity.user.id,
                "calendar_addition",
                addition.id,
                "updated",
                addition.version,
            )
        emit_change(
            identity.session,
            identity.user.id,
            "calendar_series",
            series.id,
            "updated",
            series.version,
        )
        emit_change(
            identity.session,
            identity.user.id,
            "calendar_series",
            successor.id,
            "created",
            successor.version,
        )
        await identity.session.flush()
        result = {
            "operation_id": data.operation_id,
            "predecessor": series_result(series),
            "successor": series_result(successor),
            "moved_exception_count": len(future_exceptions),
            "moved_addition_count": len(future_additions),
            "warnings": series_warnings(resolved.warning_occurrence_keys),
            "conflicts": await find_series_conflicts(
                identity.session,
                identity.user.id,
                successor,
            ),
        }
        save_operation(
            identity.session,
            identity.user.id,
            data.operation_id,
            scope,
            data,
            result,
        )
        return result
