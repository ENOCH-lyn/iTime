from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request
from sqlalchemy import and_, func, or_, select

from itime.auth.security import now
from itime.auth.session import authorized
from itime.calendar.models import AcademicTerm, TermPeriod, UserTimeSettings
from itime.calendar.schemas import (
    PeriodsReplace,
    TermCreate,
    TermUpdate,
    TimeSettingsUpdate,
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
