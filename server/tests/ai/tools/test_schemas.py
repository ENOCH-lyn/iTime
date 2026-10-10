from datetime import date
from uuid import uuid4

import pytest

from itime.ai.tools.schemas import (
    CalendarRangeInput,
    CreateTaskArgs,
    DifficultyRangeInput,
    ListTasksInput,
    ReviewRangeInput,
    SaveMemoryArgs,
    SearchMemoryInput,
    SearchTasksInput,
    UpdateTaskArgs,
)


def test_calendar_range_rejects_reversed_dates():
    with pytest.raises(ValueError):
        CalendarRangeInput(date_from=date(2026, 10, 10), date_to=date(2026, 10, 9))


def test_calendar_range_rejects_over_93_day_span():
    with pytest.raises(ValueError):
        CalendarRangeInput(date_from=date(2026, 1, 1), date_to=date(2026, 4, 10))


def test_calendar_range_accepts_93_day_span():
    payload = CalendarRangeInput(date_from=date(2026, 1, 1), date_to=date(2026, 4, 3))
    assert (payload.date_to - payload.date_from).days == 92


def test_list_tasks_filter_defaults_limit_20():
    payload = ListTasksInput.model_validate({})
    assert payload.filter.limit == 20


def test_list_tasks_filter_rejects_oversize_limit():
    with pytest.raises(ValueError):
        ListTasksInput.model_validate({"filter": {"limit": 100}})


def test_search_tasks_requires_query_within_200():
    SearchTasksInput(query="x" * 200, limit=10)
    with pytest.raises(ValueError):
        SearchTasksInput(query="x" * 201, limit=10)


def test_search_memory_limit_caps_at_10():
    SearchMemoryInput(query="foo", limit=10)
    with pytest.raises(ValueError):
        SearchMemoryInput(query="foo", limit=11)


def test_review_range_blocks_backwards():
    with pytest.raises(ValueError):
        ReviewRangeInput(date_from=date(2026, 10, 10), date_to=date(2026, 10, 9))


def test_difficulty_range_blocks_oversized_window():
    with pytest.raises(ValueError):
        DifficultyRangeInput(date_from=date(2026, 1, 1), date_to=date(2026, 12, 31))


def test_write_args_require_operation_id():
    with pytest.raises(ValueError):
        CreateTaskArgs(operation_id="", title="t")
    args = CreateTaskArgs(operation_id="op-123", title="t")
    assert args.operation_id == "op-123"


def test_update_task_requires_expected_version_at_least_one():
    with pytest.raises(ValueError):
        UpdateTaskArgs(operation_id="op", task_id=uuid4(), expected_version=0)


def test_save_memory_rejects_tags_over_limit():
    with pytest.raises(ValueError):
        SaveMemoryArgs(
            operation_id="op",
            kind="preference",
            title="t",
            content="c",
            tags=[f"tag{i}" for i in range(11)],
        )
