from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from itime.errors import AppError
from itime.statistics.time_math import (
    activity_windows,
    allocate_seconds,
    interval_weights,
    resolve_statistics_range,
)


def test_day_week_and_month_ranges_stop_at_the_current_activity_day():
    current = datetime(2026, 10, 3, 20, 30, tzinfo=UTC)

    day = resolve_statistics_range(
        "day",
        date(2026, 10, 4),
        current_time=current,
        time_zone="Asia/Shanghai",
    )
    assert day.current_activity_date == date(2026, 10, 4)
    assert day.period_start_date == date(2026, 10, 4)
    assert day.period_end_date == date(2026, 10, 4)
    assert day.data_end_date == date(2026, 10, 4)

    week = resolve_statistics_range(
        "week",
        date(2026, 10, 2),
        current_time=current,
        time_zone="Asia/Shanghai",
    )
    assert week.period_start_date == date(2026, 9, 28)
    assert week.period_end_date == date(2026, 10, 4)
    assert week.data_end_date == date(2026, 10, 4)

    month = resolve_statistics_range(
        "month",
        date(2026, 10, 3),
        current_time=current,
        time_zone="Asia/Shanghai",
    )
    assert month.period_start_date == date(2026, 10, 1)
    assert month.period_end_date == date(2026, 10, 31)
    assert month.data_end_date == date(2026, 10, 4)


def test_historical_ranges_are_complete_and_future_anchors_are_rejected():
    current = datetime(2026, 10, 3, 20, 30, tzinfo=UTC)
    historical = resolve_statistics_range(
        "month",
        date(2026, 9, 20),
        current_time=current,
        time_zone="Asia/Shanghai",
    )
    assert historical.period_start_date == date(2026, 9, 1)
    assert historical.period_end_date == date(2026, 9, 30)
    assert historical.data_end_date == date(2026, 9, 30)

    with pytest.raises(AppError) as error:
        resolve_statistics_range(
            "day",
            date(2026, 10, 5),
            current_time=current,
            time_zone="Asia/Shanghai",
        )
    assert error.value.code == "future_statistics_range"
    assert error.value.status == 422


def test_activity_windows_follow_local_four_am_across_dst():
    spring = activity_windows(
        date(2025, 3, 8),
        date(2025, 3, 9),
        "America/New_York",
    )
    assert spring[0].starts_at == datetime(2025, 3, 8, 9, tzinfo=UTC)
    assert spring[0].ends_at == datetime(2025, 3, 9, 8, tzinfo=UTC)
    assert spring[0].duration_seconds == 23 * 60 * 60
    assert spring[1].duration_seconds == 24 * 60 * 60

    autumn = activity_windows(
        date(2025, 11, 1),
        date(2025, 11, 2),
        "America/New_York",
    )
    assert autumn[0].duration_seconds == 25 * 60 * 60
    assert autumn[1].duration_seconds == 24 * 60 * 60


def test_interval_weights_split_at_activity_boundary():
    windows = activity_windows(
        date(2026, 10, 2),
        date(2026, 10, 3),
        "Asia/Shanghai",
    )
    zone = ZoneInfo("Asia/Shanghai")
    weights = interval_weights(
        datetime(2026, 10, 3, 3, 30, tzinfo=zone).astimezone(UTC),
        datetime(2026, 10, 3, 4, 30, tzinfo=zone).astimezone(UTC),
        windows,
    )
    assert weights == {
        date(2026, 10, 2): 1800,
        date(2026, 10, 3): 1800,
    }


def test_allocate_seconds_is_stable_and_conserves_full_or_partial_duration():
    weights = {
        date(2026, 10, 2): 1800,
        date(2026, 10, 3): 1800,
    }
    assert allocate_seconds(1801, weights) == {
        date(2026, 10, 2): 901,
        date(2026, 10, 3): 900,
    }
    assert sum(allocate_seconds(1801, weights).values()) == 1801

    partial = allocate_seconds(
        3600,
        {date(2026, 10, 3): 1800},
        total_weight=3600,
    )
    assert partial == {date(2026, 10, 3): 1800}
    assert allocate_seconds(0, weights) == {
        date(2026, 10, 2): 0,
        date(2026, 10, 3): 0,
    }


def test_allocate_seconds_rejects_invalid_totals_and_weights():
    with pytest.raises(ValueError, match="total_seconds"):
        allocate_seconds(-1, {date(2026, 10, 2): 1})
    with pytest.raises(ValueError, match="weights"):
        allocate_seconds(10, {date(2026, 10, 2): -1})
    with pytest.raises(ValueError, match="total_weight"):
        allocate_seconds(
            10,
            {date(2026, 10, 2): 2},
            total_weight=1,
        )
