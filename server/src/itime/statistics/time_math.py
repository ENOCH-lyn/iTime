from calendar import monthrange
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from math import floor
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from itime.errors import AppError
from itime.statistics.schemas import ACTIVITY_DAY_START_MINUTE, StatisticsScope


@dataclass(frozen=True)
class StatisticsRange:
    scope: StatisticsScope
    anchor_date: date
    period_start_date: date
    period_end_date: date
    data_end_date: date
    current_activity_date: date


@dataclass(frozen=True)
class ActivityWindow:
    activity_date: date
    starts_at: datetime
    ends_at: datetime

    @property
    def duration_seconds(self) -> int:
        return int((self.ends_at - self.starts_at).total_seconds())


def _zone(time_zone: str) -> ZoneInfo:
    try:
        return ZoneInfo(time_zone)
    except ZoneInfoNotFoundError as exc:
        raise AppError("invalid_time_zone", "时区名称无效。") from exc


def activity_date_at(instant: datetime, time_zone: str) -> date:
    if instant.tzinfo is None:
        raise ValueError("instant must include a UTC offset")
    local = instant.astimezone(_zone(time_zone))
    activity_date = local.date()
    local_minutes = local.hour * 60 + local.minute
    if local_minutes < ACTIVITY_DAY_START_MINUTE:
        activity_date -= timedelta(days=1)
    return activity_date


def resolve_statistics_range(
    scope: StatisticsScope,
    anchor_date: date,
    *,
    current_time: datetime,
    time_zone: str,
) -> StatisticsRange:
    current_activity_date = activity_date_at(current_time, time_zone)
    if anchor_date > current_activity_date:
        raise AppError(
            "future_statistics_range",
            "不能查看未来活动日的统计。",
            status=422,
        )
    if scope == "day":
        period_start = anchor_date
        period_end = anchor_date
    elif scope == "week":
        period_start = anchor_date - timedelta(days=anchor_date.weekday())
        period_end = period_start + timedelta(days=6)
    else:
        period_start = anchor_date.replace(day=1)
        period_end = anchor_date.replace(
            day=monthrange(anchor_date.year, anchor_date.month)[1]
        )
    return StatisticsRange(
        scope=scope,
        anchor_date=anchor_date,
        period_start_date=period_start,
        period_end_date=period_end,
        data_end_date=min(period_end, current_activity_date),
        current_activity_date=current_activity_date,
    )


def activity_windows(
    start_date: date,
    end_date: date,
    time_zone: str,
) -> tuple[ActivityWindow, ...]:
    if end_date < start_date:
        return ()
    zone = _zone(time_zone)
    boundary = time(
        hour=ACTIVITY_DAY_START_MINUTE // 60,
        minute=ACTIVITY_DAY_START_MINUTE % 60,
    )
    windows: list[ActivityWindow] = []
    current = start_date
    while current <= end_date:
        starts_at = datetime.combine(current, boundary, tzinfo=zone).astimezone(UTC)
        ends_at = datetime.combine(
            current + timedelta(days=1),
            boundary,
            tzinfo=zone,
        ).astimezone(UTC)
        windows.append(
            ActivityWindow(
                activity_date=current,
                starts_at=starts_at,
                ends_at=ends_at,
            )
        )
        current += timedelta(days=1)
    return tuple(windows)


def interval_weights(
    starts_at: datetime,
    ends_at: datetime,
    windows: tuple[ActivityWindow, ...],
) -> dict[date, int]:
    if starts_at.tzinfo is None or ends_at.tzinfo is None:
        raise ValueError("interval endpoints must include UTC offsets")
    if ends_at <= starts_at:
        return {}
    weights: dict[date, int] = {}
    for window in windows:
        overlap_start = max(starts_at, window.starts_at)
        overlap_end = min(ends_at, window.ends_at)
        seconds = max(0, int((overlap_end - overlap_start).total_seconds()))
        if seconds:
            weights[window.activity_date] = seconds
    return weights


def allocate_seconds(
    total_seconds: int,
    weights: Mapping[date, int],
    *,
    total_weight: int | None = None,
) -> dict[date, int]:
    if total_seconds < 0:
        raise ValueError("total_seconds must be non-negative")
    if any(weight < 0 for weight in weights.values()):
        raise ValueError("weights must be non-negative")
    selected_weight = sum(weights.values())
    denominator = selected_weight if total_weight is None else total_weight
    if denominator < selected_weight or denominator < 0:
        raise ValueError("total_weight must cover all selected weights")
    if not weights:
        return {}
    if total_seconds == 0 or selected_weight == 0 or denominator == 0:
        return {key: 0 for key in weights}

    raw = {
        key: total_seconds * weight / denominator
        for key, weight in weights.items()
    }
    allocated = {key: floor(value) for key, value in raw.items()}
    target = floor(total_seconds * selected_weight / denominator)
    remainder = target - sum(allocated.values())
    order = sorted(
        raw,
        key=lambda key: (-(raw[key] - allocated[key]), key),
    )
    for key in order[:remainder]:
        allocated[key] += 1
    return allocated
