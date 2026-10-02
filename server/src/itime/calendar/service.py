from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from itime.calendar.models import AcademicTerm, TermPeriod, UserTimeSettings
from itime.calendar.schemas import PeriodInput
from itime.errors import AppError


def validate_time_zone(time_zone: str) -> str:
    try:
        ZoneInfo(time_zone)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise AppError(
            "invalid_time_zone",
            "时区无效，请使用浏览器提供的 IANA 时区名称。",
            details={"field": "time_zone"},
        ) from exc
    return time_zone


def activity_day_at(
    instant: datetime,
    time_zone: str,
    *,
    start_minute: int = 240,
) -> date:
    if instant.tzinfo is None:
        raise ValueError("instant must include a UTC offset")
    local = instant.astimezone(ZoneInfo(validate_time_zone(time_zone)))
    return (local - timedelta(minutes=start_minute)).date()


def time_settings_result(settings: UserTimeSettings | None) -> dict:
    if settings is None:
        return {
            "initialized": False,
            "time_zone": None,
            "week_starts_on": 1,
            "activity_day_start_minute": 240,
            "version": 0,
            "created_at": None,
            "updated_at": None,
        }
    return {
        "initialized": True,
        "time_zone": settings.time_zone,
        "week_starts_on": settings.week_starts_on,
        "activity_day_start_minute": settings.activity_day_start_minute,
        "version": settings.version,
        "created_at": settings.created_at,
        "updated_at": settings.updated_at,
    }


def validate_term_dates(
    start_date: date,
    end_date: date,
    first_week_monday: date,
) -> None:
    duration = (end_date - start_date).days
    first_week_offset = (start_date - first_week_monday).days
    if duration < 0 or duration > 399:
        raise AppError(
            "invalid_term_dates",
            "学期结束日期不得早于开始日期，且完整日期范围不能超过 400 天。",
            details={"field": "end_date"},
        )
    if first_week_monday.weekday() != 0 or not 0 <= first_week_offset <= 6:
        raise AppError(
            "invalid_first_week",
            "第一周周一必须是学期开始日当天或之前六天内的周一。",
            details={"field": "first_week_monday"},
        )


async def default_term_time_zone(session: AsyncSession, owner_id: UUID) -> str:
    time_zone = await session.scalar(
        select(UserTimeSettings.time_zone).where(UserTimeSettings.owner_id == owner_id)
    )
    if time_zone is None:
        raise AppError(
            "time_settings_required",
            "请先保存当前使用的时区，再创建学期。",
            status=409,
        )
    return time_zone


def period_result(period: TermPeriod) -> dict:
    return {
        "period_number": period.period_number,
        "name": period.name,
        "starts_at": period.starts_at,
        "ends_at": period.ends_at,
    }


def term_result(
    term: AcademicTerm,
    *,
    periods: list[TermPeriod] | None = None,
    period_count: int | None = None,
) -> dict:
    result = {
        "id": term.id,
        "name": term.name,
        "start_date": term.start_date,
        "end_date": term.end_date,
        "first_week_monday": term.first_week_monday,
        "time_zone": term.time_zone,
        "version": term.version,
        "created_at": term.created_at,
        "updated_at": term.updated_at,
    }
    if periods is not None:
        result["periods"] = [period_result(period) for period in periods]
        result["period_count"] = len(periods)
    elif period_count is not None:
        result["period_count"] = period_count
    return result


async def term_detail(session: AsyncSession, term: AcademicTerm) -> dict:
    periods = list(
        await session.scalars(
            select(TermPeriod)
            .where(TermPeriod.term_id == term.id)
            .order_by(TermPeriod.period_number)
        )
    )
    return term_result(term, periods=periods)


async def locked_term(
    session: AsyncSession,
    owner_id: UUID,
    term_id: UUID,
) -> AcademicTerm:
    term = await session.scalar(
        select(AcademicTerm)
        .where(AcademicTerm.id == term_id, AcademicTerm.owner_id == owner_id)
        .with_for_update()
    )
    if term is None:
        raise AppError("term_not_found", "未找到此学期。", status=404)
    return term


def term_version_conflict(term: AcademicTerm) -> AppError:
    return AppError(
        "term_version_conflict",
        "此学期已在其他页面或设备更新，请刷新后比较再保存。",
        status=409,
        details={"current_version": term.version},
    )


def normalized_period_name(name: str) -> str:
    return name.lower()


def validate_periods(periods: list[PeriodInput]) -> None:
    if len(periods) > 30:
        raise AppError("too_many_periods", "一个学期最多设置 30 个节次。")
    numbers: set[int] = set()
    names: dict[str, int] = {}
    for period in periods:
        if period.starts_at.tzinfo is not None or period.ends_at.tzinfo is not None:
            raise AppError(
                "invalid_period_time",
                "节次时间必须是不带 UTC 偏移的本地时间。",
            )
        if period.starts_at >= period.ends_at:
            raise AppError(
                "invalid_period_time",
                "每个节次的结束时间必须晚于开始时间。",
                details={"period_number": period.period_number},
            )
        normalized_name = normalized_period_name(period.name)
        if period.period_number in numbers:
            raise AppError(
                "duplicate_period_number",
                "节次编号不能重复。",
                details={"period_number": period.period_number},
            )
        if normalized_name in names:
            raise AppError(
                "duplicate_period_name",
                "节次名称不能重复。",
                details={
                    "period_numbers": [
                        names[normalized_name],
                        period.period_number,
                    ]
                },
            )
        numbers.add(period.period_number)
        names[normalized_name] = period.period_number

    by_time = sorted(periods, key=lambda item: (item.starts_at, item.ends_at))
    for previous, current in zip(by_time, by_time[1:], strict=False):
        if current.starts_at < previous.ends_at:
            raise AppError(
                "periods_overlap",
                "节次时间发生重叠，本次节次表未保存。",
                details={
                    "period_numbers": [
                        previous.period_number,
                        current.period_number,
                    ]
                },
            )


def period_rows_equal(current: list[TermPeriod], requested: list[PeriodInput]) -> bool:
    existing = [
        (period.period_number, period.name, period.starts_at, period.ends_at)
        for period in sorted(current, key=lambda item: item.period_number)
    ]
    desired = [
        (period.period_number, period.name, period.starts_at, period.ends_at)
        for period in sorted(requested, key=lambda item: item.period_number)
    ]
    return existing == desired
