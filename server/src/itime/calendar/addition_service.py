from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from itime.calendar.models import (
    CalendarAddition,
    CalendarSeries,
    UserTimeSettings,
)
from itime.calendar.recurrence import RecurrencePlan, occurrence_dates
from itime.calendar.schemas import AdditionFields
from itime.calendar.service import validate_time_zone
from itime.calendar.time_math import resolve_local_interval, wall_time_duration
from itime.errors import AppError


async def resolve_addition_time_zone(
    session: AsyncSession,
    owner_id: UUID,
    series: CalendarSeries,
    requested_time_zone: str | None,
) -> str:
    if requested_time_zone is not None:
        return validate_time_zone(requested_time_zone)
    current_time_zone = await session.scalar(
        select(UserTimeSettings.time_zone).where(
            UserTimeSettings.owner_id == owner_id
        )
    )
    return validate_time_zone(current_time_zone or series.time_zone)


def validate_addition_fields(
    data: AdditionFields,
    time_zone: str,
) -> None:
    if data.starts_at.tzinfo is not None or data.ends_at.tzinfo is not None:
        raise AppError(
            "invalid_addition_time",
            "补课时刻必须是不带 UTC 偏移的本地墙上时间。",
        )
    wall_time_duration(data.starts_at, data.ends_at, data.end_day_offset)
    interval = resolve_local_interval(
        data.local_start_date,
        data.starts_at,
        data.ends_at,
        data.end_day_offset,
        time_zone,
        preferred_offset_minutes=data.ambiguous_utc_offset_minutes,
        strict_ambiguous=True,
    )
    if interval.warning_codes:
        raise AppError(
            "invalid_local_time",
            "补课时刻位于夏令时跳过区间，请选择其他时刻。",
        )


def addition_values(
    data: AdditionFields,
    time_zone: str,
) -> dict:
    return {
        "status": data.status,
        "local_start_date": data.local_start_date,
        "starts_at": data.starts_at,
        "ends_at": data.ends_at,
        "end_day_offset": data.end_day_offset,
        "time_zone": time_zone,
        "ambiguous_offset_minutes": data.ambiguous_utc_offset_minutes,
        "override_name": data.override_name,
        "override_description": data.override_description,
        "override_location": data.override_location,
        "override_busy": data.override_busy,
    }


def addition_result(addition: CalendarAddition) -> dict:
    return {
        "id": addition.id,
        "series_id": addition.series_id,
        "occurrence_key": addition.occurrence_key,
        "source_occurrence_key": addition.source_occurrence_key,
        "source_original_start_date": addition.source_original_start_date,
        "status": addition.status,
        "local_start_date": addition.local_start_date,
        "starts_at": addition.starts_at,
        "ends_at": addition.ends_at,
        "end_day_offset": addition.end_day_offset,
        "time_zone": addition.time_zone,
        "ambiguous_utc_offset_minutes": addition.ambiguous_offset_minutes,
        "override_name": addition.override_name,
        "override_description": addition.override_description,
        "override_location": addition.override_location,
        "override_busy": addition.override_busy,
        "version": addition.version,
        "created_at": addition.created_at,
        "updated_at": addition.updated_at,
    }


def addition_occurrence_result(
    series: CalendarSeries,
    addition: CalendarAddition,
) -> dict:
    interval = resolve_local_interval(
        addition.local_start_date,
        addition.starts_at,
        addition.ends_at,
        addition.end_day_offset,
        addition.time_zone,
        preferred_offset_minutes=addition.ambiguous_offset_minutes,
        strict_ambiguous=False,
    )
    source_start_time = datetime.fromisoformat(
        addition.source_occurrence_key
    ).time()
    busy = (
        addition.override_busy
        if addition.override_busy is not None
        else series.busy
    )
    if addition.status == "cancelled":
        busy = False
    return {
        "series_id": series.id,
        "series_version": series.version,
        "occurrence_key": addition.occurrence_key,
        "occurrence_type": "addition",
        "kind": series.kind,
        "term_id": series.term_id,
        "name": (
            addition.override_name
            if addition.override_name is not None
            else series.name
        ),
        "description": (
            addition.override_description
            if addition.override_description is not None
            else series.description
        ),
        "location": (
            addition.override_location
            if addition.override_location is not None
            else series.location
        ),
        "busy": busy,
        "status": addition.status,
        "time_zone": addition.time_zone,
        "original_local_start_date": addition.source_original_start_date,
        "original_local_start_time": source_start_time,
        "local_start_date": addition.local_start_date,
        "local_start_time": addition.starts_at,
        "local_end_date": addition.local_start_date
        + timedelta(days=addition.end_day_offset),
        "local_end_time": addition.ends_at,
        "start_at": interval.start_at,
        "end_at": interval.end_at,
        "warning_codes": list(interval.warning_codes),
        "exception_id": None,
        "exception_version": None,
        "exception_action": None,
        "addition_id": addition.id,
        "addition_version": addition.version,
        "source_occurrence_key": addition.source_occurrence_key,
        "conflict_with": [],
    }


async def locked_addition(
    session: AsyncSession,
    owner_id: UUID,
    series_id: UUID,
    addition_id: UUID,
) -> CalendarAddition:
    addition = await session.scalar(
        select(CalendarAddition)
        .where(
            CalendarAddition.id == addition_id,
            CalendarAddition.series_id == series_id,
            CalendarAddition.owner_id == owner_id,
        )
        .with_for_update()
    )
    if addition is None:
        raise AppError(
            "calendar_addition_not_found",
            "未找到此补课安排。",
            status=404,
        )
    return addition


def addition_version_conflict(addition: CalendarAddition) -> AppError:
    return AppError(
        "calendar_addition_version_conflict",
        "此补课安排已在其他页面或设备更新，请刷新后比较再保存。",
        status=409,
        details={"current_version": addition.version},
    )


async def series_additions(
    session: AsyncSession,
    owner_id: UUID,
    series_id: UUID,
    *,
    lock: bool = False,
) -> list[CalendarAddition]:
    statement = (
        select(CalendarAddition)
        .where(
            CalendarAddition.owner_id == owner_id,
            CalendarAddition.series_id == series_id,
        )
        .order_by(
            CalendarAddition.source_original_start_date,
            CalendarAddition.id,
        )
    )
    if lock:
        statement = statement.with_for_update()
    return list(await session.scalars(statement))


async def ensure_additions_remain_valid(
    session: AsyncSession,
    owner_id: UUID,
    series_id: UUID,
    plan: RecurrencePlan,
) -> None:
    additions = await series_additions(session, owner_id, series_id)
    if not additions:
        return
    valid_dates = set(occurrence_dates(plan))
    orphaned = [
        addition
        for addition in additions
        if addition.source_original_start_date not in valid_dates
    ]
    if orphaned:
        raise AppError(
            "series_additions_would_be_orphaned",
            "新的重复规则会移除已有补课来源，请先处理这些补课安排。",
            status=409,
            details={
                "addition_ids": [
                    str(addition.id) for addition in orphaned[:20]
                ],
                "has_more": len(orphaned) > 20,
            },
        )
