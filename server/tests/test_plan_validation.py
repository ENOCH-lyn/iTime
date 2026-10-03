from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from itime.errors import AppError
from itime.planning.validation import (
    PlanningToolOutput,
    normalize_plan_payload,
    validate_plan_blocks,
)

TASK_ID = uuid4()
STEP_ID = uuid4()


def candidate(**updates: object) -> dict:
    value = {
        "candidate_key": "task-a",
        "task_id": str(TASK_ID),
        "occurrence_key": None,
        "estimated_minutes": 60,
        "estimate_source": "user",
        "splittable": True,
        "minimum_block_minutes": 15,
        "due_kind": "none",
        "due_date": None,
        "due_at": None,
        "steps": [{"id": str(STEP_ID), "title": "第一步"}],
    }
    value.update(updates)
    return value


def snapshot(
    *,
    task_candidates: list[dict] | None = None,
    fixed_occurrences: list[dict] | None = None,
    **updates: object,
) -> dict:
    value = {
        "scope": "week",
        "target_date": "2026-10-05",
        "week_start_date": "2026-10-05",
        "week_end_date": "2026-10-11",
        "time_zone": "Asia/Shanghai",
        "density": "balanced",
        "buffer_minutes": 15,
        "available_starts_at": "08:00:00",
        "available_ends_at": "22:00:00",
        "task_candidates": task_candidates or [candidate()],
        "fixed_occurrences": fixed_occurrences or [],
    }
    value.update(updates)
    return value


def block(
    *,
    candidate_key: str = "task-a",
    local_date: str = "2026-10-05",
    starts_at: str = "09:00:00",
    ends_at: str = "10:00:00",
    step_ids: list[str] | None = None,
) -> dict:
    return {
        "candidate_key": candidate_key,
        "local_date": local_date,
        "starts_at": starts_at,
        "ends_at": ends_at,
        "step_ids": [str(STEP_ID)] if step_ids is None else step_ids,
    }


def output(
    *,
    blocks: list[dict] | None = None,
    estimates: list[dict] | None = None,
    unscheduled_items: list[dict] | None = None,
    **updates: object,
) -> PlanningToolOutput:
    value = {
        "summary": "按输入快照生成计划",
        "assumptions": [],
        "estimates": estimates or [],
        "blocks": [block()] if blocks is None else blocks,
        "unscheduled_items": unscheduled_items or [],
    }
    value.update(updates)
    return PlanningToolOutput.model_validate(value)


def assert_plan_error(
    expected_code: str,
    plan_snapshot: dict,
    tool_output: PlanningToolOutput,
) -> AppError:
    with pytest.raises(AppError) as caught:
        normalize_plan_payload(plan_snapshot, tool_output)
    assert caught.value.code == expected_code
    assert caught.value.saved == "not_saved"
    return caught.value


def test_tool_output_schema_forbids_unknown_fields_and_blank_text() -> None:
    with pytest.raises(ValidationError):
        output(unexpected=True)

    with pytest.raises(ValidationError):
        output(summary="   ")

    with pytest.raises(ValidationError):
        output(assumptions=["正常", "   "])


def test_normalizes_valid_output_to_frozen_utc_intervals() -> None:
    normalized = normalize_plan_payload(snapshot(buffer_minutes=0), output())

    assert normalized.summary == "按输入快照生成计划"
    assert normalized.assumptions == ()
    assert len(normalized.blocks) == 1
    normalized_block = normalized.blocks[0]
    assert normalized_block.task_id == TASK_ID
    assert normalized_block.step_ids == (STEP_ID,)
    assert normalized_block.starts_at == datetime(2026, 10, 5, 1, tzinfo=UTC)
    assert normalized_block.ends_at == datetime(2026, 10, 5, 2, tzinfo=UTC)
    assert normalized_block.estimate_source == "user"
    assert normalized_block.estimated_minutes == 60
    assert normalized.unscheduled_items == ()

    with pytest.raises(ValidationError):
        normalized_block.estimated_minutes = 90

    validate_plan_blocks(
        snapshot(buffer_minutes=0),
        list(normalized.blocks),
        list(normalized.unscheduled_items),
    )


def test_normalizes_ai_estimate_and_partial_unscheduled_minutes() -> None:
    plan_snapshot = snapshot(
        task_candidates=[
            candidate(
                estimated_minutes=None,
                estimate_source=None,
                splittable=True,
            )
        ],
        buffer_minutes=0,
    )
    tool_output = output(
        estimates=[
            {
                "candidate_key": "task-a",
                "estimated_minutes": 90,
                "reason": "按两个步骤估算",
            }
        ],
        unscheduled_items=[
            {
                "candidate_key": "task-a",
                "remaining_minutes": 30,
                "reason": "no_capacity",
                "explanation": "当天剩余时间不足",
            }
        ],
    )

    normalized = normalize_plan_payload(plan_snapshot, tool_output)

    assert normalized.blocks[0].estimate_source == "ai"
    assert normalized.blocks[0].estimated_minutes == 90
    assert normalized.unscheduled_items[0].remaining_minutes == 30
    assert normalized.unscheduled_items[0].estimate_source == "ai"


@pytest.mark.parametrize(
    ("tool_output", "expected_code"),
    [
        (
            output(
                blocks=[
                    block(
                        candidate_key="unknown",
                        step_ids=[],
                    )
                ]
            ),
            "unknown_plan_candidate",
        ),
        (
            output(blocks=[block(step_ids=[str(uuid4())])]),
            "unknown_plan_step",
        ),
    ],
)
def test_rejects_unknown_candidates_and_steps(
    tool_output: PlanningToolOutput,
    expected_code: str,
) -> None:
    assert_plan_error(expected_code, snapshot(buffer_minutes=0), tool_output)


def test_rejects_duplicate_step_references_across_blocks() -> None:
    plan_snapshot = snapshot(
        task_candidates=[candidate(estimated_minutes=60)],
        buffer_minutes=0,
    )
    tool_output = output(
        blocks=[
            block(starts_at="09:00:00", ends_at="09:30:00"),
            block(starts_at="09:30:00", ends_at="10:00:00"),
        ]
    )

    assert_plan_error("duplicate_plan_step", plan_snapshot, tool_output)


@pytest.mark.parametrize(
    ("plan_snapshot", "tool_output", "expected_code"),
    [
        (
            snapshot(
                scope="day",
                target_date="2026-10-05",
                buffer_minutes=0,
            ),
            output(blocks=[block(local_date="2026-10-06")]),
            "plan_date_out_of_range",
        ),
        (
            snapshot(buffer_minutes=0),
            output(
                blocks=[
                    block(
                        starts_at="09:10:00",
                        ends_at="10:10:00",
                    )
                ]
            ),
            "plan_time_not_aligned",
        ),
        (
            snapshot(buffer_minutes=0),
            output(
                blocks=[
                    block(
                        starts_at="07:45:00",
                        ends_at="08:45:00",
                    )
                ]
            ),
            "plan_outside_availability",
        ),
        (
            snapshot(
                task_candidates=[candidate(estimated_minutes=195)],
                buffer_minutes=0,
            ),
            output(
                blocks=[
                    block(
                        starts_at="09:00:00",
                        ends_at="12:15:00",
                        step_ids=[],
                    )
                ]
            ),
            "plan_block_too_long",
        ),
    ],
)
def test_rejects_range_alignment_window_and_max_duration(
    plan_snapshot: dict,
    tool_output: PlanningToolOutput,
    expected_code: str,
) -> None:
    assert_plan_error(expected_code, plan_snapshot, tool_output)


@pytest.mark.parametrize(
    ("time_zone", "target_date", "starts_at", "ends_at", "expected_code"),
    [
        (
            "America/New_York",
            "2026-03-08",
            "02:15:00",
            "03:15:00",
            "invalid_local_time",
        ),
        (
            "America/New_York",
            "2026-11-01",
            "01:15:00",
            "02:15:00",
            "ambiguous_local_time",
        ),
    ],
)
def test_rejects_dst_missing_and_ambiguous_local_times(
    time_zone: str,
    target_date: str,
    starts_at: str,
    ends_at: str,
    expected_code: str,
) -> None:
    plan_snapshot = snapshot(
        scope="day",
        target_date=target_date,
        week_start_date=target_date,
        week_end_date=target_date,
        time_zone=time_zone,
        buffer_minutes=0,
        available_starts_at="00:00:00",
        available_ends_at="04:00:00",
    )
    tool_output = output(
        blocks=[
            block(
                local_date=target_date,
                starts_at=starts_at,
                ends_at=ends_at,
                step_ids=[],
            )
        ]
    )

    assert_plan_error(expected_code, plan_snapshot, tool_output)


@pytest.mark.parametrize(
    ("busy", "status", "should_conflict"),
    [
        (True, "scheduled", True),
        (False, "scheduled", False),
        (True, "cancelled", False),
        (True, "source_cancelled", False),
    ],
)
def test_only_busy_scheduled_fixed_occurrences_block_time(
    busy: bool,
    status: str,
    should_conflict: bool,
) -> None:
    fixed = {
        "busy": busy,
        "status": status,
        "start_at": "2026-10-05T01:30:00Z",
        "end_at": "2026-10-05T02:00:00Z",
    }
    plan_snapshot = snapshot(
        fixed_occurrences=[fixed],
        buffer_minutes=0,
    )

    if should_conflict:
        assert_plan_error("plan_fixed_conflict", plan_snapshot, output())
    else:
        normalize_plan_payload(plan_snapshot, output())


@pytest.mark.parametrize(
    "fixed",
    [
        {
            "busy": True,
            "status": "scheduled",
            "start_at": None,
            "end_at": None,
            "warning_codes": ["invalid_local_time"],
        },
        {
            "busy": True,
            "status": "scheduled",
            "start_at": "2026-10-05T02:00:00",
            "end_at": "2026-10-05T03:00:00",
            "warning_codes": [],
        },
        {
            "busy": True,
            "status": "scheduled",
            "start_at": "2026-10-05T03:00:00Z",
            "end_at": "2026-10-05T02:00:00Z",
            "warning_codes": [],
        },
    ],
)
def test_rejects_busy_fixed_occurrences_with_unresolved_time(fixed: dict) -> None:
    assert_plan_error(
        "plan_fixed_time_unresolved",
        snapshot(fixed_occurrences=[fixed], buffer_minutes=0),
        output(),
    )


def test_fixed_intervals_are_half_open_and_apply_exact_buffer() -> None:
    fixed = {
        "busy": True,
        "status": "scheduled",
        "start_at": "2026-10-05T02:00:00Z",
        "end_at": "2026-10-05T03:00:00Z",
    }

    normalize_plan_payload(
        snapshot(fixed_occurrences=[fixed], buffer_minutes=0),
        output(
            blocks=[
                block(
                    starts_at="11:00:00",
                    ends_at="12:00:00",
                    step_ids=[],
                )
            ]
        ),
    )
    assert_plan_error(
        "plan_fixed_conflict",
        snapshot(fixed_occurrences=[fixed], buffer_minutes=15),
        output(
            blocks=[
                block(
                    starts_at="11:00:00",
                    ends_at="12:00:00",
                    step_ids=[],
                )
            ]
        ),
    )
    normalize_plan_payload(
        snapshot(fixed_occurrences=[fixed], buffer_minutes=15),
        output(
            blocks=[
                block(
                    starts_at="11:15:00",
                    ends_at="12:15:00",
                    step_ids=[],
                )
            ]
        ),
    )


def test_revalidation_uses_fixed_intervals_from_current_snapshot() -> None:
    original_snapshot = snapshot(buffer_minutes=0)
    normalized = normalize_plan_payload(original_snapshot, output())
    changed_snapshot = snapshot(
        buffer_minutes=0,
        fixed_occurrences=[
            {
                "busy": True,
                "status": "scheduled",
                "start_at": "2026-10-05T01:30:00Z",
                "end_at": "2026-10-05T02:30:00Z",
            }
        ],
    )

    with pytest.raises(AppError) as caught:
        validate_plan_blocks(
            changed_snapshot,
            list(normalized.blocks),
            list(normalized.unscheduled_items),
        )

    assert caught.value.code == "plan_fixed_conflict"


def test_cross_midnight_fixed_interval_uses_utc_overlap() -> None:
    fixed = {
        "busy": True,
        "status": "scheduled",
        "start_at": "2026-10-04T15:30:00Z",
        "end_at": "2026-10-05T00:30:00Z",
    }
    plan_snapshot = snapshot(fixed_occurrences=[fixed], buffer_minutes=0)

    assert_plan_error(
        "plan_fixed_conflict",
        plan_snapshot,
        output(
            blocks=[
                block(
                    starts_at="08:15:00",
                    ends_at="09:15:00",
                    step_ids=[],
                )
            ]
        ),
    )
    normalize_plan_payload(
        plan_snapshot,
        output(
            blocks=[
                block(
                    starts_at="08:30:00",
                    ends_at="09:30:00",
                    step_ids=[],
                )
            ]
        ),
    )


def test_plan_blocks_are_half_open_and_apply_buffer() -> None:
    blocks = [
        block(
            starts_at="09:00:00",
            ends_at="09:30:00",
            step_ids=[],
        ),
        block(
            starts_at="09:30:00",
            ends_at="10:00:00",
            step_ids=[],
        ),
    ]
    normalize_plan_payload(snapshot(buffer_minutes=0), output(blocks=blocks))
    assert_plan_error(
        "plan_block_conflict",
        snapshot(buffer_minutes=15),
        output(blocks=blocks),
    )

    exact_buffer = [
        block(
            starts_at="09:00:00",
            ends_at="09:30:00",
            step_ids=[],
        ),
        block(
            starts_at="09:45:00",
            ends_at="10:15:00",
            step_ids=[],
        ),
    ]
    normalize_plan_payload(
        snapshot(buffer_minutes=15),
        output(blocks=exact_buffer),
    )


@pytest.mark.parametrize(
    ("candidate_updates", "planned_block"),
    [
        (
            {
                "due_kind": "date",
                "due_date": "2026-10-05",
            },
            block(
                local_date="2026-10-06",
                starts_at="09:00:00",
                ends_at="10:00:00",
                step_ids=[],
            ),
        ),
        (
            {
                "due_kind": "datetime",
                "due_at": "2026-10-05T02:00:00Z",
            },
            block(
                starts_at="09:15:00",
                ends_at="10:15:00",
                step_ids=[],
            ),
        ),
    ],
)
def test_rejects_blocks_after_date_or_timed_deadline(
    candidate_updates: dict,
    planned_block: dict,
) -> None:
    plan_snapshot = snapshot(
        task_candidates=[candidate(**candidate_updates)],
        buffer_minutes=0,
    )

    assert_plan_error(
        "plan_after_deadline",
        plan_snapshot,
        output(blocks=[planned_block]),
    )


def test_allows_block_ending_exactly_at_timed_deadline() -> None:
    plan_snapshot = snapshot(
        task_candidates=[
            candidate(
                due_kind="datetime",
                due_at="2026-10-05T02:00:00Z",
            )
        ],
        buffer_minutes=0,
    )

    normalize_plan_payload(plan_snapshot, output())


@pytest.mark.parametrize(
    ("candidate_updates", "blocks", "unscheduled_items", "expected_code"),
    [
        (
            {"splittable": False},
            [
                block(
                    starts_at="09:00:00",
                    ends_at="09:30:00",
                    step_ids=[],
                ),
                block(
                    starts_at="09:30:00",
                    ends_at="10:00:00",
                    step_ids=[],
                ),
            ],
            [],
            "plan_not_splittable",
        ),
        (
            {"minimum_block_minutes": 30},
            [
                block(
                    starts_at="09:00:00",
                    ends_at="09:15:00",
                    step_ids=[],
                )
            ],
            [
                {
                    "candidate_key": "task-a",
                    "remaining_minutes": 45,
                    "reason": "no_capacity",
                    "explanation": "剩余时间不足",
                }
            ],
            "plan_block_too_short",
        ),
        (
            {"splittable": False},
            [
                block(
                    starts_at="09:00:00",
                    ends_at="09:30:00",
                    step_ids=[],
                )
            ],
            [
                {
                    "candidate_key": "task-a",
                    "remaining_minutes": 30,
                    "reason": "no_capacity",
                    "explanation": "剩余时间不足",
                }
            ],
            "plan_not_splittable",
        ),
    ],
)
def test_enforces_unsplittable_and_minimum_block_rules(
    candidate_updates: dict,
    blocks: list[dict],
    unscheduled_items: list[dict],
    expected_code: str,
) -> None:
    plan_snapshot = snapshot(
        task_candidates=[candidate(**candidate_updates)],
        buffer_minutes=0,
    )

    assert_plan_error(
        expected_code,
        plan_snapshot,
        output(blocks=blocks, unscheduled_items=unscheduled_items),
    )


def test_allows_fully_unscheduled_unsplittable_candidate() -> None:
    plan_snapshot = snapshot(
        task_candidates=[candidate(splittable=False)],
        buffer_minutes=0,
    )
    tool_output = output(
        blocks=[],
        unscheduled_items=[
            {
                "candidate_key": "task-a",
                "remaining_minutes": 60,
                "reason": "no_capacity",
                "explanation": "没有完整连续时段",
            }
        ],
    )

    normalize_plan_payload(plan_snapshot, tool_output)


@pytest.mark.parametrize(
    ("remaining_minutes", "expected_code"),
    [
        (None, "plan_estimate_mismatch"),
        (10, "plan_estimate_mismatch"),
    ],
)
def test_enforces_estimate_conservation(
    remaining_minutes: int | None,
    expected_code: str,
) -> None:
    unscheduled_items = (
        []
        if remaining_minutes is None
        else [
            {
                "candidate_key": "task-a",
                "remaining_minutes": remaining_minutes,
                "reason": "no_capacity",
                "explanation": "剩余时间不足",
            }
        ]
    )
    tool_output = output(
        blocks=[
            block(
                starts_at="09:00:00",
                ends_at="09:45:00",
                step_ids=[],
            )
        ],
        unscheduled_items=unscheduled_items,
    )

    assert_plan_error(
        expected_code,
        snapshot(buffer_minutes=0),
        tool_output,
    )


def test_requires_exactly_one_ai_estimate_for_missing_estimate() -> None:
    plan_snapshot = snapshot(
        task_candidates=[
            candidate(
                estimated_minutes=None,
                estimate_source=None,
            )
        ]
    )

    assert_plan_error(
        "missing_plan_estimate",
        plan_snapshot,
        output(),
    )
    assert_plan_error(
        "duplicate_plan_estimate",
        plan_snapshot,
        output(
            estimates=[
                {
                    "candidate_key": "task-a",
                    "estimated_minutes": 60,
                    "reason": "第一次估计",
                },
                {
                    "candidate_key": "task-a",
                    "estimated_minutes": 60,
                    "reason": "第二次估计",
                },
            ]
        ),
    )


def test_rejects_ai_estimate_for_candidate_with_user_estimate() -> None:
    assert_plan_error(
        "unexpected_plan_estimate",
        snapshot(),
        output(
            estimates=[
                {
                    "candidate_key": "task-a",
                    "estimated_minutes": 60,
                    "reason": "不应覆盖用户估时",
                }
            ]
        ),
    )


@pytest.mark.parametrize(
    ("estimated_minutes", "ends_at", "should_pass"),
    [
        (240, "12:00:00", True),
        (255, "12:15:00", False),
    ],
)
def test_daily_density_limit_is_inclusive(
    estimated_minutes: int,
    ends_at: str,
    should_pass: bool,
) -> None:
    plan_snapshot = snapshot(
        density="loose",
        buffer_minutes=0,
        task_candidates=[
            candidate(
                estimated_minutes=estimated_minutes,
                minimum_block_minutes=15,
            )
        ],
    )
    tool_output = output(
        blocks=[
            block(
                starts_at="08:00:00",
                ends_at="11:00:00",
                step_ids=[],
            ),
            block(
                starts_at="11:00:00",
                ends_at=ends_at,
                step_ids=[],
            ),
        ]
    )

    if should_pass:
        normalize_plan_payload(plan_snapshot, tool_output)
    else:
        assert_plan_error("plan_density_exceeded", plan_snapshot, tool_output)


def test_rejects_duplicate_unscheduled_candidate() -> None:
    item = {
        "candidate_key": "task-a",
        "remaining_minutes": 30,
        "reason": "no_capacity",
        "explanation": "剩余时间不足",
    }

    assert_plan_error(
        "duplicate_unscheduled_item",
        snapshot(buffer_minutes=0),
        output(
            blocks=[
                block(
                    starts_at="09:00:00",
                    ends_at="09:30:00",
                    step_ids=[],
                )
            ],
            unscheduled_items=[item, item],
        ),
    )
