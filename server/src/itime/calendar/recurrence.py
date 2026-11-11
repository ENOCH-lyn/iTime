import calendar as month_calendar
from dataclasses import dataclass
from datetime import date, timedelta

from itime.calendar.models import AcademicTerm, CalendarSeries
from itime.calendar.schemas import RecurrenceInput
from itime.errors import AppError

MAX_SERIES_OCCURRENCES = 366


@dataclass(frozen=True)
class RecurrencePlan:
    start_date: date
    frequency: str
    interval: int
    weekdays: tuple[int, ...]
    month: int | None
    day_of_month: int | None
    until: date | None
    count: int | None
    week_numbers: tuple[int, ...]
    course_first_week_monday: date | None


def _date_overflow() -> AppError:
    return AppError(
        "recurrence_unreachable",
        "重复规则超出支持的日历日期范围。",
    )


def _add_days(value: date, days: int) -> date:
    try:
        return value + timedelta(days=days)
    except OverflowError as exc:
        raise _date_overflow() from exc


def _unique_numbers(values: list[int], *, low: int, high: int, code: str) -> tuple[int, ...]:
    if any(value < low or value > high for value in values) or len(values) != len(set(values)):
        raise AppError(code, "重复规则中的日期编号无效或重复。")
    return tuple(sorted(values))


def build_recurrence_plan(
    recurrence: RecurrenceInput,
    *,
    kind: str,
    requested_start_date: date | None,
    term: AcademicTerm | None,
) -> RecurrencePlan:
    weekdays = _unique_numbers(
        recurrence.weekdays,
        low=1,
        high=7,
        code="invalid_recurrence_weekdays",
    )
    week_numbers = _unique_numbers(
        recurrence.week_numbers,
        low=1,
        high=60,
        code="invalid_course_weeks",
    )

    if kind == "course":
        if term is None:
            raise AppError("course_term_required", "课程必须关联当前账号中的学期。")
        if (
            recurrence.frequency != "weekly"
            or recurrence.interval != 1
            or not weekdays
            or not week_numbers
            or recurrence.month is not None
            or recurrence.day_of_month is not None
            or recurrence.until is not None
            or recurrence.count is not None
        ):
            raise AppError(
                "invalid_course_recurrence",
                "课程必须提供星期和有效周次，且不能同时设置通用结束条件。",
            )
        term_week_count = (
            (term.end_date - term.first_week_monday).days // 7
        ) + 1
        if any(number > term_week_count for number in week_numbers):
            raise AppError(
                "invalid_course_weeks",
                "有效周次超出所选学期范围。",
                details={"maximum_week": term_week_count},
            )
        dates = [
            _add_days(
                term.first_week_monday,
                (week_number - 1) * 7 + weekday - 1,
            )
            for week_number in week_numbers
            for weekday in weekdays
        ]
        dates = sorted(
            value for value in dates if term.start_date <= value <= term.end_date
        )
        if not dates:
            raise AppError(
                "course_has_no_occurrences",
                "所选星期和周次在学期范围内没有课程日期。",
            )
        if requested_start_date is not None:
            if requested_start_date not in dates:
                raise AppError(
                    "invalid_course_start_date",
                    "课程的第一个上课日必须正好落在所选星期上，并且这个星期"
                    "要在有效周次和学期范围内。比如周三的课，起始日期就填一个周三。",
                )
            dates = [value for value in dates if value >= requested_start_date]
        if len(dates) > MAX_SERIES_OCCURRENCES:
            raise AppError(
                "too_many_occurrences",
                "单个系列最多包含 366 次安排。",
            )
        return RecurrencePlan(
            start_date=dates[0],
            frequency="weekly",
            interval=1,
            weekdays=weekdays,
            month=None,
            day_of_month=None,
            until=dates[-1],
            count=None,
            week_numbers=week_numbers,
            course_first_week_monday=term.first_week_monday,
        )

    if requested_start_date is None:
        raise AppError(
            "series_start_date_required",
            "固定安排必须提供开始日期。",
            details={"field": "start_date"},
        )
    if week_numbers:
        raise AppError(
            "invalid_recurrence",
            "只有课程可以指定学期周次。",
        )
    if recurrence.frequency == "none":
        if (
            recurrence.interval != 1
            or weekdays
            or recurrence.month is not None
            or recurrence.day_of_month is not None
            or recurrence.until is not None
            or recurrence.count is not None
        ):
            raise AppError(
                "invalid_recurrence",
                "不重复安排不能包含间隔、星期或结束条件。",
            )
    else:
        if (recurrence.until is None) == (recurrence.count is None):
            raise AppError(
                "recurrence_end_required",
                "重复安排必须且只能使用结束日期或次数中的一种。",
            )
        if recurrence.until is not None and recurrence.until < requested_start_date:
            raise AppError(
                "invalid_recurrence_end",
                "重复结束日期不能早于开始日期。",
            )
        if recurrence.frequency == "daily":
            if weekdays or recurrence.month is not None or recurrence.day_of_month is not None:
                raise AppError("invalid_recurrence", "每日重复不接受星期或月日参数。")
        elif recurrence.frequency == "weekdays":
            if (
                recurrence.interval != 1
                or weekdays
                or recurrence.month is not None
                or recurrence.day_of_month is not None
            ):
                raise AppError("invalid_recurrence", "工作日重复不接受其他日期参数。")
        elif recurrence.frequency == "weekly":
            if (
                not weekdays
                or recurrence.month is not None
                or recurrence.day_of_month is not None
            ):
                raise AppError("invalid_recurrence", "每周重复必须提供星期。")
        elif recurrence.frequency == "monthly":
            if (
                weekdays
                or recurrence.month is not None
                or recurrence.day_of_month is None
            ):
                raise AppError("invalid_recurrence", "每月重复必须且只能提供日期。")
        elif recurrence.frequency == "yearly" and (
            weekdays
            or recurrence.month is None
            or recurrence.day_of_month is None
        ):
            raise AppError("invalid_recurrence", "每年重复必须提供月份和日期。")

    plan = RecurrencePlan(
        start_date=requested_start_date,
        frequency=recurrence.frequency,
        interval=recurrence.interval,
        weekdays=weekdays,
        month=recurrence.month,
        day_of_month=recurrence.day_of_month,
        until=recurrence.until,
        count=recurrence.count,
        week_numbers=(),
        course_first_week_monday=None,
    )
    occurrence_dates(plan)
    return plan


def plan_from_series(series: CalendarSeries) -> RecurrencePlan:
    return RecurrencePlan(
        start_date=series.start_date,
        frequency=series.recurrence_frequency,
        interval=series.recurrence_interval,
        weekdays=tuple(series.recurrence_weekdays),
        month=series.recurrence_month,
        day_of_month=series.recurrence_day_of_month,
        until=series.recurrence_until,
        count=series.recurrence_count,
        week_numbers=tuple(series.course_week_numbers),
        course_first_week_monday=series.course_first_week_monday,
    )


def occurrence_dates(plan: RecurrencePlan) -> list[date]:
    if plan.course_first_week_monday is not None:
        values = sorted(
            _add_days(
                plan.course_first_week_monday,
                (week_number - 1) * 7 + weekday - 1,
            )
            for week_number in plan.week_numbers
            for weekday in plan.weekdays
        )
        return [
            value
            for value in values
            if plan.start_date <= value and (plan.until is None or value <= plan.until)
        ]
    if plan.frequency == "none":
        return [plan.start_date]

    results: list[date] = []

    def append(candidate: date) -> bool:
        if candidate < plan.start_date:
            return False
        if plan.until is not None and candidate > plan.until:
            return True
        results.append(candidate)
        if len(results) > MAX_SERIES_OCCURRENCES:
            raise AppError(
                "too_many_occurrences",
                "单个系列最多包含 366 次安排，请缩短范围或增大间隔。",
            )
        return plan.count is not None and len(results) >= plan.count

    if plan.frequency in {"daily", "weekdays"}:
        current = plan.start_date
        while True:
            if plan.frequency == "daily" or current.isoweekday() <= 5:
                if append(current):
                    break
            if plan.until is not None and current >= plan.until:
                break
            current = _add_days(
                current,
                plan.interval if plan.frequency == "daily" else 1,
            )
    elif plan.frequency == "weekly":
        week_start = plan.start_date - timedelta(days=plan.start_date.weekday())
        week_index = 0
        while True:
            current_week = _add_days(
                week_start,
                week_index * plan.interval * 7,
            )
            past_end = False
            for weekday in plan.weekdays:
                candidate = _add_days(current_week, weekday - 1)
                if plan.until is not None and candidate > plan.until:
                    past_end = True
                    continue
                if append(candidate):
                    return results
            if past_end or (
                plan.until is not None
                and _add_days(current_week, 6) >= plan.until
            ):
                break
            week_index += 1
    elif plan.frequency == "monthly":
        month_index = plan.start_date.year * 12 + plan.start_date.month - 1
        attempts = 0
        while attempts <= 5000:
            year, month_zero = divmod(month_index, 12)
            month = month_zero + 1
            if year > 9999:
                break
            days = month_calendar.monthrange(year, month)[1]
            if plan.day_of_month <= days:
                candidate = date(year, month, plan.day_of_month)
                if append(candidate):
                    break
                if plan.until is not None and candidate >= plan.until:
                    break
            elif plan.until is not None and date(year, month, days) >= plan.until:
                break
            month_index += plan.interval
            attempts += 1
    elif plan.frequency == "yearly":
        year = plan.start_date.year
        attempts = 0
        while attempts <= 5000 and year <= 9999:
            try:
                candidate = date(year, plan.month, plan.day_of_month)
            except ValueError:
                candidate = None
            if candidate is not None:
                if append(candidate):
                    break
                if plan.until is not None and candidate >= plan.until:
                    break
            elif plan.until is not None and year >= plan.until.year:
                break
            year += plan.interval
            attempts += 1

    if not results:
        raise AppError(
            "recurrence_has_no_occurrences",
            "重复规则在指定范围内不会产生任何安排。",
        )
    if plan.count is not None and len(results) < plan.count:
        raise AppError(
            "recurrence_unreachable",
            "重复规则无法在支持的日期范围内生成指定次数。",
        )
    return results
