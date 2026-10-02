from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from itime.errors import AppError


@dataclass(frozen=True)
class LocalInterval:
    start_at: datetime | None
    end_at: datetime | None
    warning_codes: tuple[str, ...]


def occurrence_key(local_date: date, starts_at: time) -> str:
    return f"{local_date.isoformat()}T{starts_at.isoformat()}"


def wall_time_duration(
    starts_at: time,
    ends_at: time,
    end_day_offset: int,
) -> timedelta:
    anchor = date(2000, 1, 1)
    start = datetime.combine(anchor, starts_at)
    end = datetime.combine(anchor + timedelta(days=end_day_offset), ends_at)
    duration = end - start
    if duration <= timedelta(0) or duration > timedelta(days=1):
        raise AppError(
            "invalid_series_time",
            "安排结束时间必须晚于开始时间，且单次时长不能超过 24 小时。",
        )
    return duration


def _local_candidates(
    local_value: datetime,
    zone: ZoneInfo,
) -> list[datetime]:
    candidates: list[datetime] = []
    seen: set[datetime] = set()
    for fold in (0, 1):
        aware = local_value.replace(tzinfo=zone, fold=fold)
        utc_value = aware.astimezone(UTC)
        round_trip = utc_value.astimezone(zone).replace(tzinfo=None)
        if round_trip == local_value and utc_value not in seen:
            candidates.append(aware)
            seen.add(utc_value)
    return candidates


def _offset_minutes(value: datetime) -> int:
    offset = value.utcoffset()
    if offset is None:
        raise ValueError("resolved local date-time has no UTC offset")
    return int(offset.total_seconds() // 60)


def _resolve_local(
    local_value: datetime,
    zone: ZoneInfo,
    *,
    preferred_offset_minutes: int | None,
    strict_ambiguous: bool,
) -> tuple[datetime | None, str | None]:
    candidates = _local_candidates(local_value, zone)
    if not candidates:
        return None, "invalid_local_time"
    if len(candidates) == 1:
        return candidates[0].astimezone(UTC), None

    offsets = [_offset_minutes(candidate) for candidate in candidates]
    if preferred_offset_minutes is None:
        if strict_ambiguous:
            raise AppError(
                "ambiguous_local_time",
                "该本地时间在夏令时切换时出现两次，请选择明确的 UTC 偏移。",
                details={
                    "local_time": local_value.isoformat(),
                    "candidate_offsets_minutes": offsets,
                },
            )
        return None, "ambiguous_local_time"
    matches = [
        candidate
        for candidate in candidates
        if _offset_minutes(candidate) == preferred_offset_minutes
    ]
    if len(matches) != 1:
        raise AppError(
            "invalid_ambiguous_offset",
            "所选 UTC 偏移不适用于该重复的本地时间。",
            details={
                "local_time": local_value.isoformat(),
                "candidate_offsets_minutes": offsets,
            },
        )
    return matches[0].astimezone(UTC), None


def resolve_local_interval(
    local_date: date,
    starts_at: time,
    ends_at: time,
    end_day_offset: int,
    time_zone: str,
    *,
    preferred_offset_minutes: int | None,
    strict_ambiguous: bool,
) -> LocalInterval:
    wall_time_duration(starts_at, ends_at, end_day_offset)
    zone = ZoneInfo(time_zone)
    local_start = datetime.combine(local_date, starts_at)
    try:
        end_date = local_date + timedelta(days=end_day_offset)
    except OverflowError as exc:
        raise AppError(
            "invalid_series_time",
            "跨日安排超出支持的日历日期范围。",
        ) from exc
    local_end = datetime.combine(end_date, ends_at)
    start_at, start_warning = _resolve_local(
        local_start,
        zone,
        preferred_offset_minutes=preferred_offset_minutes,
        strict_ambiguous=strict_ambiguous,
    )
    end_at, end_warning = _resolve_local(
        local_end,
        zone,
        preferred_offset_minutes=preferred_offset_minutes,
        strict_ambiguous=strict_ambiguous,
    )
    warnings = tuple(
        dict.fromkeys(
            warning
            for warning in (start_warning, end_warning)
            if warning is not None
        )
    )
    if start_at is not None and end_at is not None and end_at <= start_at:
        raise AppError(
            "invalid_series_time",
            "安排在所选时区中的实际结束时间必须晚于开始时间。",
        )
    return LocalInterval(start_at=start_at, end_at=end_at, warning_codes=warnings)
