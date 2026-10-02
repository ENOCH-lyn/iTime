from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from itime.calendar.models import CalendarException, CalendarSeries
from itime.calendar.recurrence import RecurrencePlan, occurrence_dates, plan_from_series
from itime.calendar.schemas import ExceptionFields
from itime.calendar.time_math import (
    occurrence_key,
    resolve_local_interval,
    wall_time_duration,
)
from itime.errors import AppError


async def find_occurrence_date(
    session: AsyncSession,
    owner_id: UUID,
    series: CalendarSeries,
    key: str,
) -> date:
    valid_dates = occurrence_dates(plan_from_series(series))
    for occurrence_date in valid_dates:
        if occurrence_key(occurrence_date, series.starts_at) == key:
            return occurrence_date
    existing = await session.scalar(
        select(CalendarException).where(
            CalendarException.owner_id == owner_id,
            CalendarException.series_id == series.id,
            CalendarException.occurrence_key == key,
        )
    )
    if existing is not None and existing.original_start_date in valid_dates:
        return existing.original_start_date
    raise AppError(
        "occurrence_not_found",
        "未找到此系列中的原始日程实例。",
        status=404,
    )


def validate_exception_fields(
    series: CalendarSeries,
    data: ExceptionFields,
) -> None:
    if data.action != "moved":
        return
    if data.moved_starts_at.tzinfo is not None or data.moved_ends_at.tzinfo is not None:
        raise AppError(
            "invalid_exception_time",
            "调课时刻必须是不带 UTC 偏移的本地墙上时间。",
        )
    wall_time_duration(
        data.moved_starts_at,
        data.moved_ends_at,
        data.moved_end_day_offset,
    )
    interval = resolve_local_interval(
        data.moved_start_date,
        data.moved_starts_at,
        data.moved_ends_at,
        data.moved_end_day_offset,
        series.time_zone,
        preferred_offset_minutes=data.moved_ambiguous_utc_offset_minutes,
        strict_ambiguous=True,
    )
    if interval.warning_codes:
        raise AppError(
            "invalid_local_time",
            "调课后的时刻位于夏令时跳过区间，请选择其他时刻。",
        )


def exception_values(data: ExceptionFields) -> dict:
    return {
        "action": data.action,
        "moved_start_date": data.moved_start_date,
        "moved_starts_at": data.moved_starts_at,
        "moved_ends_at": data.moved_ends_at,
        "moved_end_day_offset": data.moved_end_day_offset,
        "moved_ambiguous_offset_minutes": (
            data.moved_ambiguous_utc_offset_minutes
        ),
        "override_name": data.override_name,
        "override_description": data.override_description,
        "override_location": data.override_location,
        "override_busy": data.override_busy,
    }


def exception_result(exception: CalendarException) -> dict:
    return {
        "id": exception.id,
        "series_id": exception.series_id,
        "occurrence_key": exception.occurrence_key,
        "original_start_date": exception.original_start_date,
        "action": exception.action,
        "moved_start_date": exception.moved_start_date,
        "moved_starts_at": exception.moved_starts_at,
        "moved_ends_at": exception.moved_ends_at,
        "moved_end_day_offset": exception.moved_end_day_offset,
        "moved_ambiguous_utc_offset_minutes": (
            exception.moved_ambiguous_offset_minutes
        ),
        "override_name": exception.override_name,
        "override_description": exception.override_description,
        "override_location": exception.override_location,
        "override_busy": exception.override_busy,
        "version": exception.version,
        "created_at": exception.created_at,
        "updated_at": exception.updated_at,
    }


async def locked_exception(
    session: AsyncSession,
    owner_id: UUID,
    series_id: UUID,
    exception_id: UUID,
) -> CalendarException:
    exception = await session.scalar(
        select(CalendarException)
        .where(
            CalendarException.id == exception_id,
            CalendarException.series_id == series_id,
            CalendarException.owner_id == owner_id,
        )
        .with_for_update()
    )
    if exception is None:
        raise AppError(
            "calendar_exception_not_found",
            "未找到此单次例外。",
            status=404,
        )
    return exception


def exception_version_conflict(exception: CalendarException) -> AppError:
    return AppError(
        "calendar_exception_version_conflict",
        "此单次例外已在其他页面或设备更新，请刷新后比较再保存。",
        status=409,
        details={"current_version": exception.version},
    )


async def series_exceptions(
    session: AsyncSession,
    owner_id: UUID,
    series_id: UUID,
    *,
    lock: bool = False,
) -> list[CalendarException]:
    statement = (
        select(CalendarException)
        .where(
            CalendarException.owner_id == owner_id,
            CalendarException.series_id == series_id,
        )
        .order_by(CalendarException.original_start_date, CalendarException.id)
    )
    if lock:
        statement = statement.with_for_update()
    return list(await session.scalars(statement))


async def ensure_exceptions_remain_valid(
    session: AsyncSession,
    owner_id: UUID,
    series_id: UUID,
    plan: RecurrencePlan,
) -> None:
    exceptions = await series_exceptions(session, owner_id, series_id)
    if not exceptions:
        return
    valid_dates = set(occurrence_dates(plan))
    orphaned = [
        exception
        for exception in exceptions
        if exception.original_start_date not in valid_dates
    ]
    if orphaned:
        raise AppError(
            "series_exceptions_would_be_orphaned",
            "新的重复规则会移除已有单次修改，请先处理这些例外。",
            status=409,
            details={
                "exception_ids": [str(exception.id) for exception in orphaned[:20]],
                "has_more": len(orphaned) > 20,
            },
        )


def apply_exception(
    series: CalendarSeries,
    occurrence: dict,
    exception: CalendarException,
) -> dict:
    occurrence["exception_id"] = exception.id
    occurrence["exception_version"] = exception.version
    occurrence["exception_action"] = exception.action
    occurrence["occurrence_key"] = exception.occurrence_key
    if exception.action == "cancelled":
        occurrence["status"] = "cancelled"
        occurrence["busy"] = False
    elif exception.action == "moved":
        interval = resolve_local_interval(
            exception.moved_start_date,
            exception.moved_starts_at,
            exception.moved_ends_at,
            exception.moved_end_day_offset,
            series.time_zone,
            preferred_offset_minutes=exception.moved_ambiguous_offset_minutes,
            strict_ambiguous=False,
        )
        occurrence["local_start_date"] = exception.moved_start_date
        occurrence["local_start_time"] = exception.moved_starts_at
        occurrence["local_end_date"] = exception.moved_start_date + timedelta(
            days=exception.moved_end_day_offset
        )
        occurrence["local_end_time"] = exception.moved_ends_at
        occurrence["start_at"] = interval.start_at
        occurrence["end_at"] = interval.end_at
        occurrence["warning_codes"] = list(interval.warning_codes)
    else:
        if exception.override_name is not None:
            occurrence["name"] = exception.override_name
        if exception.override_description is not None:
            occurrence["description"] = exception.override_description
        if exception.override_location is not None:
            occurrence["location"] = exception.override_location
        if exception.override_busy is not None:
            occurrence["busy"] = exception.override_busy
    return occurrence
