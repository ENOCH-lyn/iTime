from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request
from sqlalchemy import and_, func, or_, select

from itime.auth.security import now
from itime.auth.session import authorized
from itime.calendar.models import (
    AcademicTerm,
    CalendarSeries,
    TermPeriod,
    UserTimeSettings,
)
from itime.calendar.schemas import (
    PeriodsReplace,
    SeriesCreate,
    SeriesUpdate,
    TermCreate,
    TermUpdate,
    TimeSettingsUpdate,
)
from itime.calendar.series_service import (
    find_series_conflicts,
    locked_series,
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
            or settings.week_starts_on != data.week_starts_on
            or settings.activity_day_start_minute != data.activity_day_start_minute
        ):
            settings.time_zone = time_zone
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
            source_type="manual",
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
        return {"series": series_result(series)}


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
