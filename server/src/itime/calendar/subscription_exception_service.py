from datetime import UTC, datetime, time
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from itime.calendar.models import CalendarSubscriptionException
from itime.calendar.service import validate_time_zone
from itime.calendar.subscription_schemas import SubscriptionExceptionFields
from itime.calendar.time_math import resolve_local_interval
from itime.errors import AppError


def subscription_exception_result(
    exception: CalendarSubscriptionException,
) -> dict:
    return {
        "id": exception.id,
        "master_event_id": exception.master_event_id,
        "source_occurrence_key": exception.source_occurrence_key,
        "original_start_at": exception.original_start_at,
        "original_end_at": exception.original_end_at,
        "original_all_day": exception.original_all_day,
        "action": exception.action,
        "moved_all_day": exception.moved_all_day,
        "moved_start_date": exception.moved_start_date,
        "moved_end_date": exception.moved_end_date,
        "moved_starts_at": exception.moved_starts_at,
        "moved_ends_at": exception.moved_ends_at,
        "moved_time_zone": exception.moved_time_zone,
        "moved_ambiguous_utc_offset_minutes": (
            exception.moved_ambiguous_offset_minutes
        ),
        "moved_start_at": exception.moved_start_at,
        "moved_end_at": exception.moved_end_at,
        "override_name": exception.override_name,
        "override_description": exception.override_description,
        "override_location": exception.override_location,
        "override_busy": exception.override_busy,
        "version": exception.version,
        "created_at": exception.created_at,
        "updated_at": exception.updated_at,
    }


def subscription_exception_version_conflict(
    exception: CalendarSubscriptionException,
) -> AppError:
    return AppError(
        "calendar_subscription_exception_version_conflict",
        "此单次例外已在其他页面或设备修改，请刷新后比较再保存。",
        status=409,
        details={"current_version": exception.version},
    )


async def locked_subscription_exception(
    session: AsyncSession,
    owner_id: UUID,
    exception_id: UUID,
) -> CalendarSubscriptionException:
    exception = await session.scalar(
        select(CalendarSubscriptionException)
        .where(
            CalendarSubscriptionException.id == exception_id,
            CalendarSubscriptionException.owner_id == owner_id,
        )
        .with_for_update()
    )
    if exception is None:
        raise AppError(
            "calendar_subscription_exception_not_found",
            "未找到此订阅单次例外。",
            status=404,
        )
    return exception


def validate_subscription_exception(
    data: SubscriptionExceptionFields,
    occurrence: dict,
    default_time_zone: str,
) -> dict:
    start_at = occurrence.get("start_at")
    end_at = occurrence.get("end_at")
    if start_at is None or end_at is None or end_at <= start_at:
        raise AppError(
            "calendar_subscription_occurrence_invalid",
            "此订阅实例的时间无效，暂时不能创建本地例外。",
            status=409,
        )
    values = {
        "action": data.action,
        "moved_all_day": None,
        "moved_start_date": None,
        "moved_end_date": None,
        "moved_starts_at": None,
        "moved_ends_at": None,
        "moved_time_zone": None,
        "moved_ambiguous_offset_minutes": None,
        "moved_start_at": None,
        "moved_end_at": None,
        "override_name": data.override_name,
        "override_description": data.override_description,
        "override_location": data.override_location,
        "override_busy": data.override_busy,
    }
    if data.action != "moved":
        return values

    time_zone = validate_time_zone(data.moved_time_zone or default_time_zone)
    if data.moved_start_date is None or data.moved_end_date is None:
        raise RuntimeError("validated moved exception dates are missing")
    if data.moved_all_day:
        zone = ZoneInfo(time_zone)
        moved_start_at = datetime.combine(
            data.moved_start_date,
            time.min,
            tzinfo=zone,
        ).astimezone(UTC)
        moved_end_at = datetime.combine(
            data.moved_end_date,
            time.min,
            tzinfo=zone,
        ).astimezone(UTC)
    else:
        if data.moved_starts_at is None or data.moved_ends_at is None:
            raise RuntimeError("validated timed exception values are missing")
        interval = resolve_local_interval(
            data.moved_start_date,
            data.moved_starts_at,
            data.moved_ends_at,
            (data.moved_end_date - data.moved_start_date).days,
            time_zone,
            preferred_offset_minutes=data.moved_ambiguous_utc_offset_minutes,
            strict_ambiguous=True,
        )
        if interval.warning_codes or interval.start_at is None or interval.end_at is None:
            raise AppError(
                "invalid_local_time",
                "移动后的时刻位于夏令时跳过区间，请选择其他时刻。",
            )
        moved_start_at = interval.start_at
        moved_end_at = interval.end_at
    values.update(
        {
            "moved_all_day": data.moved_all_day,
            "moved_start_date": data.moved_start_date,
            "moved_end_date": data.moved_end_date,
            "moved_starts_at": data.moved_starts_at,
            "moved_ends_at": data.moved_ends_at,
            "moved_time_zone": time_zone,
            "moved_ambiguous_offset_minutes": (
                data.moved_ambiguous_utc_offset_minutes
            ),
            "moved_start_at": moved_start_at,
            "moved_end_at": moved_end_at,
        }
    )
    return values


def apply_subscription_exception(
    occurrence: dict,
    exception: CalendarSubscriptionException,
) -> dict:
    occurrence["exception_id"] = exception.id
    occurrence["exception_version"] = exception.version
    occurrence["exception_action"] = exception.action
    if exception.action == "cancelled":
        occurrence["status"] = "cancelled"
        occurrence["busy"] = False
        return occurrence
    if exception.action == "moved":
        occurrence["status"] = "scheduled"
        occurrence["all_day"] = bool(exception.moved_all_day)
        occurrence["time_zone"] = exception.moved_time_zone
        occurrence["local_start_date"] = exception.moved_start_date
        occurrence["local_start_time"] = (
            None if exception.moved_all_day else exception.moved_starts_at
        )
        occurrence["local_end_date"] = exception.moved_end_date
        occurrence["local_end_time"] = (
            None if exception.moved_all_day else exception.moved_ends_at
        )
        occurrence["start_at"] = exception.moved_start_at
        occurrence["end_at"] = exception.moved_end_at
        occurrence["warning_codes"] = []
        return occurrence
    if exception.override_name is not None:
        occurrence["name"] = exception.override_name
    if exception.override_description is not None:
        occurrence["description"] = exception.override_description
    if exception.override_location is not None:
        occurrence["location"] = exception.override_location
    if exception.override_busy is not None:
        occurrence["busy"] = (
            exception.override_busy
            and occurrence["status"] == "scheduled"
        )
    return occurrence
