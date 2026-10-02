from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from itime.calendar.addition_service import addition_occurrence_result
from itime.calendar.exception_service import apply_exception
from itime.calendar.models import (
    AcademicTerm,
    CalendarAddition,
    CalendarException,
    CalendarSeries,
    TermPeriod,
    UserTimeSettings,
)
from itime.calendar.recurrence import (
    RecurrencePlan,
    build_recurrence_plan,
    occurrence_dates,
    plan_from_series,
)
from itime.calendar.schemas import SeriesFields
from itime.calendar.service import validate_time_zone
from itime.calendar.subscription_projection import query_subscription_occurrences
from itime.calendar.time_math import (
    occurrence_key,
    resolve_local_interval,
    wall_time_duration,
)
from itime.errors import AppError

MAX_QUERY_DAYS = 93
MAX_QUERY_OCCURRENCES = 2000
MAX_CONFLICT_ITEMS = 100
MAX_CANDIDATE_SERIES = 1000


@dataclass(frozen=True)
class ResolvedSeriesDraft:
    term: AcademicTerm | None
    plan: RecurrencePlan
    time_zone: str
    starts_at: time
    ends_at: time
    end_day_offset: int
    start_period_number: int | None
    end_period_number: int | None
    warning_occurrence_keys: tuple[str, ...]


def _shift_date_clamped(value: date, days: int) -> date:
    try:
        return value + timedelta(days=days)
    except OverflowError:
        return date.min if days < 0 else date.max


async def _owned_term(
    session: AsyncSession,
    owner_id: UUID,
    term_id: UUID | None,
) -> AcademicTerm | None:
    if term_id is None:
        return None
    term = await session.scalar(
        select(AcademicTerm).where(
            AcademicTerm.id == term_id,
            AcademicTerm.owner_id == owner_id,
        )
    )
    if term is None:
        raise AppError("term_not_found", "未找到此学期。", status=404)
    return term


async def _default_time_zone(
    session: AsyncSession,
    owner_id: UUID,
    term: AcademicTerm | None,
) -> str:
    if term is not None:
        return term.time_zone
    time_zone = await session.scalar(
        select(UserTimeSettings.time_zone).where(UserTimeSettings.owner_id == owner_id)
    )
    if time_zone is None:
        raise AppError(
            "time_settings_required",
            "请先保存当前使用的时区，再创建固定安排。",
            status=409,
        )
    return time_zone


async def _resolve_series_times(
    session: AsyncSession,
    owner_id: UUID,
    data: SeriesFields,
    term: AcademicTerm | None,
) -> tuple[time, time, int, int | None, int | None]:
    period_values = (data.start_period_number, data.end_period_number)
    if (period_values[0] is None) != (period_values[1] is None):
        raise AppError(
            "invalid_period_reference",
            "开始节次和结束节次必须同时提供。",
        )
    if period_values[0] is not None:
        if term is None:
            raise AppError(
                "period_term_required",
                "按节次设置时间时必须选择学期。",
            )
        if (
            data.starts_at is not None
            or data.ends_at is not None
            or data.end_day_offset != 0
        ):
            raise AppError(
                "period_time_conflict",
                "按节次设置时间时不能同时提交显式时刻或跨日偏移。",
            )
        start_number, end_number = period_values
        if start_number > end_number:
            raise AppError(
                "invalid_period_reference",
                "结束节次编号不能早于开始节次编号。",
            )
        periods = list(
            await session.scalars(
                select(TermPeriod)
                .where(
                    TermPeriod.owner_id == owner_id,
                    TermPeriod.term_id == term.id,
                    TermPeriod.period_number.between(start_number, end_number),
                )
                .order_by(TermPeriod.period_number)
            )
        )
        expected_numbers = list(range(start_number, end_number + 1))
        if [period.period_number for period in periods] != expected_numbers:
            raise AppError(
                "period_not_found",
                "所选节次不存在或编号不连续，请刷新学期设置。",
                status=409,
            )
        return (
            periods[0].starts_at,
            periods[-1].ends_at,
            0,
            start_number,
            end_number,
        )

    if data.starts_at is None or data.ends_at is None:
        raise AppError(
            "series_time_required",
            "必须提供明确的开始和结束时刻，或选择连续节次。",
        )
    if data.starts_at.tzinfo is not None or data.ends_at.tzinfo is not None:
        raise AppError(
            "invalid_series_time",
            "安排时刻必须是不带 UTC 偏移的本地墙上时间。",
        )
    wall_time_duration(data.starts_at, data.ends_at, data.end_day_offset)
    return (
        data.starts_at,
        data.ends_at,
        data.end_day_offset,
        None,
        None,
    )


async def resolve_series_draft(
    session: AsyncSession,
    owner_id: UUID,
    data: SeriesFields,
) -> ResolvedSeriesDraft:
    term = await _owned_term(session, owner_id, data.term_id)
    plan = build_recurrence_plan(
        data.recurrence,
        kind=data.kind,
        requested_start_date=data.start_date,
        term=term,
    )
    dates = occurrence_dates(plan)
    if term is not None and data.kind == "fixed":
        if any(value < term.start_date or value > term.end_date for value in dates):
            raise AppError(
                "series_outside_term",
                "关联学期的固定安排必须全部位于学期日期范围内。",
            )
    time_zone = validate_time_zone(
        data.time_zone
        if data.time_zone is not None
        else await _default_time_zone(session, owner_id, term)
    )
    (
        starts_at,
        ends_at,
        end_day_offset,
        start_period_number,
        end_period_number,
    ) = await _resolve_series_times(session, owner_id, data, term)

    warning_keys: list[str] = []
    for occurrence_date in dates:
        interval = resolve_local_interval(
            occurrence_date,
            starts_at,
            ends_at,
            end_day_offset,
            time_zone,
            preferred_offset_minutes=data.ambiguous_utc_offset_minutes,
            strict_ambiguous=True,
        )
        if interval.warning_codes:
            warning_keys.append(occurrence_key(occurrence_date, starts_at))
    if warning_keys and len(dates) == 1:
        raise AppError(
            "invalid_local_time",
            "该安排位于夏令时跳过的本地时间，请选择其他时刻。",
            details={"occurrence_key": warning_keys[0]},
        )
    return ResolvedSeriesDraft(
        term=term,
        plan=plan,
        time_zone=time_zone,
        starts_at=starts_at,
        ends_at=ends_at,
        end_day_offset=end_day_offset,
        start_period_number=start_period_number,
        end_period_number=end_period_number,
        warning_occurrence_keys=tuple(warning_keys),
    )


def series_values(data: SeriesFields, resolved: ResolvedSeriesDraft) -> dict:
    plan = resolved.plan
    return {
        "term_id": resolved.term.id if resolved.term is not None else None,
        "kind": data.kind,
        "name": data.name,
        "description": data.description,
        "location": data.location,
        "busy": data.busy,
        "time_zone": resolved.time_zone,
        "start_date": plan.start_date,
        "starts_at": resolved.starts_at,
        "ends_at": resolved.ends_at,
        "end_day_offset": resolved.end_day_offset,
        "start_period_number": resolved.start_period_number,
        "end_period_number": resolved.end_period_number,
        "recurrence_frequency": plan.frequency,
        "recurrence_interval": plan.interval,
        "recurrence_weekdays": list(plan.weekdays),
        "recurrence_month": plan.month,
        "recurrence_day_of_month": plan.day_of_month,
        "recurrence_until": plan.until,
        "recurrence_count": plan.count,
        "course_first_week_monday": plan.course_first_week_monday,
        "course_week_numbers": list(plan.week_numbers),
        "ambiguous_offset_minutes": data.ambiguous_utc_offset_minutes,
    }


def recurrence_result(series: CalendarSeries) -> dict:
    return {
        "frequency": series.recurrence_frequency,
        "interval": series.recurrence_interval,
        "weekdays": series.recurrence_weekdays,
        "month": series.recurrence_month,
        "day_of_month": series.recurrence_day_of_month,
        "until": series.recurrence_until,
        "count": series.recurrence_count,
        "week_numbers": series.course_week_numbers,
    }


def series_result(series: CalendarSeries) -> dict:
    return {
        "id": series.id,
        "kind": series.kind,
        "term_id": series.term_id,
        "predecessor_id": series.predecessor_id,
        "source_id": series.source_id,
        "name": series.name,
        "description": series.description,
        "location": series.location,
        "busy": series.busy,
        "source_type": series.source_type,
        "time_zone": series.time_zone,
        "start_date": series.start_date,
        "starts_at": series.starts_at,
        "ends_at": series.ends_at,
        "end_day_offset": series.end_day_offset,
        "start_period_number": series.start_period_number,
        "end_period_number": series.end_period_number,
        "ambiguous_utc_offset_minutes": series.ambiguous_offset_minutes,
        "recurrence": recurrence_result(series),
        "version": series.version,
        "created_at": series.created_at,
        "updated_at": series.updated_at,
    }


async def locked_series(
    session: AsyncSession,
    owner_id: UUID,
    series_id: UUID,
) -> CalendarSeries:
    series = await session.scalar(
        select(CalendarSeries)
        .where(
            CalendarSeries.id == series_id,
            CalendarSeries.owner_id == owner_id,
        )
        .with_for_update()
    )
    if series is None:
        raise AppError("calendar_series_not_found", "未找到此日历系列。", status=404)
    return series


def series_version_conflict(series: CalendarSeries) -> AppError:
    return AppError(
        "calendar_series_version_conflict",
        "此日历系列已在其他页面或设备更新，请刷新后比较再保存。",
        status=409,
        details={"current_version": series.version},
    )


def occurrence_result(series: CalendarSeries, occurrence_date: date) -> dict:
    interval = resolve_local_interval(
        occurrence_date,
        series.starts_at,
        series.ends_at,
        series.end_day_offset,
        series.time_zone,
        preferred_offset_minutes=series.ambiguous_offset_minutes,
        strict_ambiguous=False,
    )
    return {
        "series_id": series.id,
        "series_version": series.version,
        "occurrence_key": occurrence_key(occurrence_date, series.starts_at),
        "occurrence_type": "base",
        "kind": series.kind,
        "term_id": series.term_id,
        "source_id": series.source_id,
        "source_type": series.source_type,
        "name": series.name,
        "description": series.description,
        "location": series.location,
        "busy": series.busy,
        "status": "scheduled",
        "time_zone": series.time_zone,
        "original_local_start_date": occurrence_date,
        "original_local_start_time": series.starts_at,
        "local_start_date": occurrence_date,
        "local_start_time": series.starts_at,
        "local_end_date": occurrence_date + timedelta(days=series.end_day_offset),
        "local_end_time": series.ends_at,
        "start_at": interval.start_at,
        "end_at": interval.end_at,
        "warning_codes": list(interval.warning_codes),
        "exception_id": None,
        "exception_version": None,
        "exception_action": None,
        "addition_id": None,
        "addition_version": None,
        "source_occurrence_key": None,
        "conflict_with": [],
    }


def _query_utc_bounds(
    start_date: date,
    end_date: date,
    time_zone: str,
) -> tuple[datetime, datetime]:
    zone = ZoneInfo(time_zone)
    return (
        datetime.combine(start_date, time.min, tzinfo=zone).astimezone(UTC),
        datetime.combine(end_date, time.min, tzinfo=zone).astimezone(UTC),
    )


def _filter_occurrences(
    series: CalendarSeries,
    exceptions: dict[date, CalendarException],
    additions: list[CalendarAddition],
    *,
    query_start: datetime,
    query_end: datetime,
    local_start: date,
    local_end: date,
) -> list[dict]:
    results: list[dict] = []
    for occurrence in all_series_occurrences(series, exceptions, additions):
        if occurrence["start_at"] is None or occurrence["end_at"] is None:
            if local_start <= occurrence["local_start_date"] < local_end:
                results.append(occurrence)
        elif (
            occurrence["start_at"] < query_end
            and occurrence["end_at"] > query_start
        ):
            results.append(occurrence)
    return results


async def load_exception_maps(
    session: AsyncSession,
    owner_id: UUID,
    series_ids: list[UUID],
) -> dict[UUID, dict[date, CalendarException]]:
    result: defaultdict[UUID, dict[date, CalendarException]] = defaultdict(dict)
    if not series_ids:
        return result
    exceptions = await session.scalars(
        select(CalendarException).where(
            CalendarException.owner_id == owner_id,
            CalendarException.series_id.in_(series_ids),
        )
    )
    for exception in exceptions:
        result[exception.series_id][exception.original_start_date] = exception
    return result


async def load_addition_maps(
    session: AsyncSession,
    owner_id: UUID,
    series_ids: list[UUID],
) -> dict[UUID, list[CalendarAddition]]:
    result: defaultdict[UUID, list[CalendarAddition]] = defaultdict(list)
    if not series_ids:
        return result
    additions = await session.scalars(
        select(CalendarAddition)
        .where(
            CalendarAddition.owner_id == owner_id,
            CalendarAddition.series_id.in_(series_ids),
        )
        .order_by(CalendarAddition.local_start_date, CalendarAddition.id)
    )
    for addition in additions:
        result[addition.series_id].append(addition)
    return result


def all_series_occurrences(
    series: CalendarSeries,
    exceptions: dict[date, CalendarException],
    additions: list[CalendarAddition] | None = None,
) -> list[dict]:
    results: list[dict] = []
    for occurrence_date in occurrence_dates(plan_from_series(series)):
        occurrence = occurrence_result(series, occurrence_date)
        exception = exceptions.get(occurrence_date)
        results.append(
            apply_exception(series, occurrence, exception)
            if exception is not None
            else occurrence
        )
    results.extend(
        addition_occurrence_result(series, addition)
        for addition in additions or []
    )
    return results


def apply_conflict_links(occurrences: list[dict]) -> None:
    timed = sorted(
        (
            occurrence
            for occurrence in occurrences
            if occurrence["busy"]
            and occurrence["status"] == "scheduled"
            and occurrence["start_at"] is not None
            and occurrence["end_at"] is not None
        ),
        key=lambda item: (item["start_at"], item["end_at"]),
    )
    for index, current in enumerate(timed):
        for other in timed[index + 1 :]:
            if other["start_at"] >= current["end_at"]:
                break
            current["conflict_with"].append(
                {
                    "series_id": other["series_id"],
                    "subscription_event_id": other.get("subscription_event_id"),
                    "occurrence_key": other["occurrence_key"],
                    "occurrence_type": other["occurrence_type"],
                    "addition_id": other["addition_id"],
                    "overlap_start": max(current["start_at"], other["start_at"]),
                    "overlap_end": min(current["end_at"], other["end_at"]),
                }
            )
            other["conflict_with"].append(
                {
                    "series_id": current["series_id"],
                    "subscription_event_id": current.get("subscription_event_id"),
                    "occurrence_key": current["occurrence_key"],
                    "occurrence_type": current["occurrence_type"],
                    "addition_id": current["addition_id"],
                    "overlap_start": max(current["start_at"], other["start_at"]),
                    "overlap_end": min(current["end_at"], other["end_at"]),
                }
            )


async def query_occurrences(
    session: AsyncSession,
    owner_id: UUID,
    start_date: date,
    end_date: date,
) -> tuple[list[dict], str]:
    days = (end_date - start_date).days
    if days <= 0 or days > MAX_QUERY_DAYS:
        raise AppError(
            "calendar_range_too_large",
            "日历查询必须使用不超过 93 天的非空日期范围。",
        )
    user_time_zone = await session.scalar(
        select(UserTimeSettings.time_zone).where(UserTimeSettings.owner_id == owner_id)
    )
    if user_time_zone is None:
        raise AppError(
            "time_settings_required",
            "请先保存当前使用的时区，再查看日历。",
            status=409,
        )
    query_start, query_end = _query_utc_bounds(
        start_date,
        end_date,
        user_time_zone,
    )
    coarse_start = _shift_date_clamped(start_date, -2)
    coarse_end = _shift_date_clamped(end_date, 2)
    statement = (
        select(CalendarSeries)
        .where(
            CalendarSeries.owner_id == owner_id,
            CalendarSeries.start_date < coarse_end,
            or_(
                and_(
                    CalendarSeries.recurrence_frequency == "none",
                    CalendarSeries.start_date >= coarse_start,
                ),
                CalendarSeries.recurrence_count.is_not(None),
                CalendarSeries.recurrence_until >= coarse_start,
            ),
        )
        .order_by(CalendarSeries.start_date, CalendarSeries.id)
        .limit(MAX_CANDIDATE_SERIES + 1)
    )
    series_rows = list(await session.scalars(statement))
    moved_series_ids = set(
        await session.scalars(
            select(CalendarException.series_id).where(
                CalendarException.owner_id == owner_id,
                CalendarException.action == "moved",
                CalendarException.moved_start_date >= coarse_start,
                CalendarException.moved_start_date < coarse_end,
            )
        )
    )
    addition_series_ids = set(
        await session.scalars(
            select(CalendarAddition.series_id).where(
                CalendarAddition.owner_id == owner_id,
                CalendarAddition.local_start_date >= coarse_start,
                CalendarAddition.local_start_date < coarse_end,
            )
        )
    )
    loaded_ids = {series.id for series in series_rows}
    missing_ids = (moved_series_ids | addition_series_ids) - loaded_ids
    if missing_ids:
        series_rows.extend(
            await session.scalars(
                select(CalendarSeries).where(
                    CalendarSeries.owner_id == owner_id,
                    CalendarSeries.id.in_(missing_ids),
                )
            )
        )
    if len(series_rows) > MAX_CANDIDATE_SERIES:
        raise AppError(
            "calendar_result_too_large",
            "该范围内的日历系列过多，请缩小查询范围。",
            status=422,
        )
    exception_maps = await load_exception_maps(
        session,
        owner_id,
        [series.id for series in series_rows],
    )
    addition_maps = await load_addition_maps(
        session,
        owner_id,
        [series.id for series in series_rows],
    )
    occurrences: list[dict] = []
    for series in series_rows:
        occurrences.extend(
            _filter_occurrences(
                series,
                exception_maps[series.id],
                addition_maps[series.id],
                query_start=query_start,
                query_end=query_end,
                local_start=start_date,
                local_end=end_date,
            )
        )
        if len(occurrences) > MAX_QUERY_OCCURRENCES:
            raise AppError(
                "calendar_result_too_large",
                "该范围内超过 2000 条安排，请缩小查询范围。",
                status=422,
            )
    occurrences.extend(
        await query_subscription_occurrences(
            session,
            owner_id,
            query_start=query_start,
            query_end=query_end,
            remaining_limit=MAX_QUERY_OCCURRENCES - len(occurrences),
        )
    )
    if len(occurrences) > MAX_QUERY_OCCURRENCES:
        raise AppError(
            "calendar_result_too_large",
            "该范围内超过 2000 条安排，请缩小查询范围。",
            status=422,
        )
    apply_conflict_links(occurrences)
    occurrences.sort(
        key=lambda item: (
            item["start_at"] is None,
            item["start_at"] or datetime.max.replace(tzinfo=UTC),
            str(item["series_id"]),
            item["occurrence_type"],
            item["occurrence_key"],
        )
    )
    return occurrences, user_time_zone


async def find_series_conflicts(
    session: AsyncSession,
    owner_id: UUID,
    candidate: CalendarSeries,
) -> dict:
    candidate_exception_maps = await load_exception_maps(
        session,
        owner_id,
        [candidate.id],
    )
    candidate_addition_maps = await load_addition_maps(
        session,
        owner_id,
        [candidate.id],
    )
    candidate_occurrences = all_series_occurrences(
        candidate,
        candidate_exception_maps[candidate.id],
        candidate_addition_maps[candidate.id],
    )
    candidate_timed = [
        occurrence
        for occurrence in candidate_occurrences
        if occurrence["busy"]
        and occurrence["status"] == "scheduled"
        and occurrence["start_at"] is not None
        and occurrence["end_at"] is not None
    ]
    if not candidate_timed:
        return {"items": [], "has_more": False}
    first_date = min(
        occurrence["local_start_date"] for occurrence in candidate_timed
    )
    last_date = max(
        occurrence["local_end_date"] for occurrence in candidate_timed
    )
    query_start = min(occurrence["start_at"] for occurrence in candidate_timed)
    query_end = max(occurrence["end_at"] for occurrence in candidate_timed)
    coarse_start = _shift_date_clamped(first_date, -2)
    coarse_end = _shift_date_clamped(last_date, 2)
    existing = list(
        await session.scalars(
            select(CalendarSeries)
            .where(
                CalendarSeries.owner_id == owner_id,
                CalendarSeries.id != candidate.id,
                CalendarSeries.start_date <= coarse_end,
                or_(
                    and_(
                        CalendarSeries.recurrence_frequency == "none",
                        CalendarSeries.start_date >= coarse_start,
                    ),
                    CalendarSeries.recurrence_count.is_not(None),
                    CalendarSeries.recurrence_until >= coarse_start,
                ),
            )
            .order_by(CalendarSeries.id)
            .limit(MAX_CANDIDATE_SERIES + 1)
        )
    )
    moved_series_ids = set(
        await session.scalars(
            select(CalendarException.series_id).where(
                CalendarException.owner_id == owner_id,
                CalendarException.action == "moved",
                CalendarException.moved_start_date >= coarse_start,
                CalendarException.moved_start_date <= coarse_end,
                CalendarException.series_id != candidate.id,
            )
        )
    )
    addition_series_ids = set(
        await session.scalars(
            select(CalendarAddition.series_id).where(
                CalendarAddition.owner_id == owner_id,
                CalendarAddition.local_start_date >= coarse_start,
                CalendarAddition.local_start_date <= coarse_end,
                CalendarAddition.series_id != candidate.id,
            )
        )
    )
    loaded_ids = {series.id for series in existing}
    missing_ids = (moved_series_ids | addition_series_ids) - loaded_ids
    if missing_ids:
        existing.extend(
            await session.scalars(
                select(CalendarSeries).where(
                    CalendarSeries.owner_id == owner_id,
                    CalendarSeries.id.in_(missing_ids),
                )
            )
        )
    if len(existing) > MAX_CANDIDATE_SERIES:
        raise AppError(
            "conflict_check_too_large",
            "现有固定安排过多，暂时无法完成完整冲突检查。",
            status=422,
        )
    existing_exception_maps = await load_exception_maps(
        session,
        owner_id,
        [series.id for series in existing],
    )
    existing_addition_maps = await load_addition_maps(
        session,
        owner_id,
        [series.id for series in existing],
    )
    existing_occurrences: list[dict] = []
    for series in existing:
        existing_occurrences.extend(
            _filter_occurrences(
                series,
                existing_exception_maps[series.id],
                existing_addition_maps[series.id],
                query_start=query_start,
                query_end=query_end,
                local_start=first_date,
                local_end=_shift_date_clamped(last_date, 1),
            )
        )
        if len(existing_occurrences) > MAX_QUERY_OCCURRENCES:
            raise AppError(
                "conflict_check_too_large",
                "冲突检查涉及超过 2000 条既有安排，请缩短系列范围。",
                status=422,
            )
    existing_occurrences.extend(
        await query_subscription_occurrences(
            session,
            owner_id,
            query_start=query_start,
            query_end=query_end,
            remaining_limit=MAX_QUERY_OCCURRENCES - len(existing_occurrences),
        )
    )

    timeline = sorted(
        [
            (
                occurrence["start_at"],
                occurrence["end_at"],
                True,
                occurrence,
            )
            for occurrence in candidate_timed
        ]
        + [
            (
                occurrence["start_at"],
                occurrence["end_at"],
                False,
                occurrence,
            )
            for occurrence in existing_occurrences
            if occurrence["busy"]
            and occurrence["status"] == "scheduled"
            and occurrence["start_at"] is not None
            and occurrence["end_at"] is not None
        ],
        key=lambda item: (item[0], item[1]),
    )
    conflicts: list[dict] = []
    active: list[tuple[datetime, bool, dict]] = []
    for start_at, end_at, is_candidate, occurrence in timeline:
        active = [item for item in active if item[0] > start_at]
        for _, active_is_candidate, active_occurrence in active:
            if not (active_is_candidate or is_candidate):
                continue
            current = occurrence if is_candidate else active_occurrence
            other = active_occurrence if is_candidate else occurrence
            conflicts.append(
                {
                    "occurrence_key": current["occurrence_key"],
                    "occurrence_type": current["occurrence_type"],
                    "addition_id": current["addition_id"],
                    "conflicting_series_id": other["series_id"],
                    "conflicting_subscription_event_id": other.get(
                        "subscription_event_id"
                    ),
                    "conflicting_occurrence_key": other["occurrence_key"],
                    "conflicting_occurrence_type": other["occurrence_type"],
                    "conflicting_addition_id": other["addition_id"],
                    "overlap_start": max(
                        current["start_at"],
                        other["start_at"],
                    ),
                    "overlap_end": min(
                        current["end_at"],
                        other["end_at"],
                    ),
                }
            )
            if len(conflicts) > MAX_CONFLICT_ITEMS:
                return {
                    "items": conflicts[:MAX_CONFLICT_ITEMS],
                    "has_more": True,
                }
        active.append((end_at, is_candidate, occurrence))
    return {"items": conflicts, "has_more": False}
