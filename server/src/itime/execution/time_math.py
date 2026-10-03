from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from itime.calendar.time_math import resolve_local_datetime
from itime.errors import AppError

ACTIVITY_DAY_START = time(4)


def activity_day_for(instant: datetime, time_zone: str) -> date:
    try:
        local = instant.astimezone(ZoneInfo(time_zone))
    except ZoneInfoNotFoundError as exc:
        raise AppError("invalid_time_zone", "时区名称无效。") from exc
    day = local.date()
    if local.timetz().replace(tzinfo=None) < ACTIVITY_DAY_START:
        day -= timedelta(days=1)
    return day


def resolve_manual_interval(
    local_start: datetime,
    local_end: datetime,
    time_zone: str,
    start_offset_minutes: int | None,
    end_offset_minutes: int | None,
) -> tuple[datetime, datetime]:
    if local_start.tzinfo is not None or local_end.tzinfo is not None:
        raise AppError(
            "invalid_focus_local_time",
            "补记时间应为不带 UTC 偏移的当地时间。",
            status=422,
        )
    try:
        start, start_warning = resolve_local_datetime(
            local_start,
            time_zone,
            preferred_offset_minutes=start_offset_minutes,
            strict_ambiguous=True,
        )
        end, end_warning = resolve_local_datetime(
            local_end,
            time_zone,
            preferred_offset_minutes=end_offset_minutes,
            strict_ambiguous=True,
        )
    except ZoneInfoNotFoundError as exc:
        raise AppError("invalid_time_zone", "时区名称无效。") from exc
    if (
        start_warning is not None
        or end_warning is not None
        or start is None
        or end is None
    ):
        raise AppError(
            "invalid_local_time",
            "补记时间位于夏令时跳过的当地时刻，请选择其他时间。",
            status=422,
        )
    start = start.astimezone(UTC)
    end = end.astimezone(UTC)
    if end <= start:
        raise AppError(
            "invalid_focus_interval",
            "专注结束时间必须晚于开始时间。",
            status=422,
        )
    return start, end
