from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator

from itime.calendar.time_math import resolve_local_datetime
from itime.errors import AppError
from itime.schemas import Input

UnscheduledReason = Literal[
    "no_capacity",
    "deadline_conflict",
    "fixed_conflict",
    "missing_estimate",
    "outside_scope",
]
EstimateSource = Literal["user", "ai"]

MAX_ESTIMATED_MINUTES = 525_600
MAX_BLOCK_MINUTES = 180
MAX_OUTPUT_ITEMS = 100
DENSITY_LIMITS = {
    "loose": 240,
    "balanced": 360,
    "tight": 480,
}

ShortText = Annotated[str, Field(min_length=1, max_length=500)]


class _ToolModel(Input):
    pass


class PlanningEstimateOutput(_ToolModel):
    candidate_key: str = Field(min_length=1, max_length=200)
    estimated_minutes: int = Field(ge=1, le=MAX_ESTIMATED_MINUTES)
    reason: ShortText

    @field_validator("candidate_key", "reason")
    @classmethod
    def clean_required_text(cls, value: str) -> str:
        return _clean_required(value)


class PlanningBlockOutput(_ToolModel):
    candidate_key: str = Field(min_length=1, max_length=200)
    local_date: date
    starts_at: time
    ends_at: time
    step_ids: list[UUID] = Field(default_factory=list, max_length=100)

    @field_validator("candidate_key")
    @classmethod
    def clean_candidate_key(cls, value: str) -> str:
        return _clean_required(value)


class PlanningUnscheduledItemOutput(_ToolModel):
    candidate_key: str = Field(min_length=1, max_length=200)
    remaining_minutes: int = Field(ge=1, le=MAX_ESTIMATED_MINUTES)
    reason: UnscheduledReason
    explanation: ShortText

    @field_validator("candidate_key", "explanation")
    @classmethod
    def clean_required_text(cls, value: str) -> str:
        return _clean_required(value)


class PlanningToolOutput(_ToolModel):
    summary: str = Field(min_length=1, max_length=1000)
    assumptions: list[ShortText] = Field(default_factory=list, max_length=10)
    estimates: list[PlanningEstimateOutput] = Field(
        default_factory=list,
        max_length=MAX_OUTPUT_ITEMS,
    )
    blocks: list[PlanningBlockOutput] = Field(
        default_factory=list,
        max_length=MAX_OUTPUT_ITEMS,
    )
    unscheduled_items: list[PlanningUnscheduledItemOutput] = Field(
        default_factory=list,
        max_length=MAX_OUTPUT_ITEMS,
    )

    @field_validator("summary")
    @classmethod
    def clean_summary(cls, value: str) -> str:
        return _clean_required(value)

    @field_validator("assumptions")
    @classmethod
    def clean_assumptions(cls, values: list[str]) -> list[str]:
        return [_clean_required(value) for value in values]


class _NormalizedModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )


class NormalizedBlock(_NormalizedModel):
    candidate_key: str
    task_id: UUID
    occurrence_key: str | None
    local_date: date
    local_starts_at: time
    local_ends_at: time
    time_zone: str
    starts_at: datetime
    ends_at: datetime
    step_ids: tuple[UUID, ...]
    estimate_source: EstimateSource
    estimated_minutes: int


class NormalizedUnscheduledItem(_NormalizedModel):
    candidate_key: str
    task_id: UUID
    occurrence_key: str | None
    remaining_minutes: int
    reason: UnscheduledReason
    explanation: str
    estimate_source: EstimateSource
    estimated_minutes: int


class NormalizedPlan(_NormalizedModel):
    summary: str
    assumptions: tuple[str, ...]
    blocks: tuple[NormalizedBlock, ...]
    unscheduled_items: tuple[NormalizedUnscheduledItem, ...]


@dataclass(frozen=True)
class _Candidate:
    key: str
    task_id: UUID
    occurrence_key: str | None
    step_ids: frozenset[UUID]
    estimated_minutes: int | None
    estimate_source: EstimateSource | None
    splittable: bool
    minimum_block_minutes: int
    due_kind: str
    due_date: date | None
    due_at: datetime | None


@dataclass(frozen=True)
class _Snapshot:
    scope: str
    target_date: date
    range_start: date
    range_end: date
    time_zone: str
    density: str
    buffer_minutes: int
    available_starts_at: time
    available_ends_at: time
    candidates: dict[str, _Candidate]
    fixed_intervals: tuple[tuple[datetime, datetime], ...]


def normalize_plan_payload(
    snapshot: dict,
    output: PlanningToolOutput,
) -> NormalizedPlan:
    """Normalize untrusted tool output and run every deterministic hard check."""
    context = _snapshot_context(snapshot)
    parsed_output = PlanningToolOutput.model_validate(output)
    adopted_estimates = _adopted_estimates(context, parsed_output.estimates)

    normalized_blocks: list[NormalizedBlock] = []
    seen_steps: set[UUID] = set()
    for raw_block in parsed_output.blocks:
        plan_candidate = _candidate(context, raw_block.candidate_key)
        requested_steps = set(raw_block.step_ids)
        if not requested_steps.issubset(plan_candidate.step_ids):
            raise _plan_error(
                "unknown_plan_step",
                "计划引用了输入快照中不存在的任务步骤。",
                candidate_key=plan_candidate.key,
            )
        if len(requested_steps) != len(raw_block.step_ids) or seen_steps & requested_steps:
            raise _plan_error(
                "duplicate_plan_step",
                "同一步骤不能在计划块中重复引用。",
                candidate_key=plan_candidate.key,
            )
        seen_steps.update(requested_steps)
        starts_at, ends_at, _ = _resolved_block_interval(
            context,
            raw_block.local_date,
            raw_block.starts_at,
            raw_block.ends_at,
        )
        estimate_source, estimated_minutes = adopted_estimates[plan_candidate.key]
        normalized_blocks.append(
            NormalizedBlock(
                candidate_key=plan_candidate.key,
                task_id=plan_candidate.task_id,
                occurrence_key=plan_candidate.occurrence_key,
                local_date=raw_block.local_date,
                local_starts_at=raw_block.starts_at,
                local_ends_at=raw_block.ends_at,
                time_zone=context.time_zone,
                starts_at=starts_at,
                ends_at=ends_at,
                step_ids=tuple(raw_block.step_ids),
                estimate_source=estimate_source,
                estimated_minutes=estimated_minutes,
            )
        )

    normalized_unscheduled: list[NormalizedUnscheduledItem] = []
    seen_unscheduled: set[str] = set()
    for raw_item in parsed_output.unscheduled_items:
        plan_candidate = _candidate(context, raw_item.candidate_key)
        if plan_candidate.key in seen_unscheduled:
            raise _plan_error(
                "duplicate_unscheduled_item",
                "每个候选最多只能有一条未排项。",
                candidate_key=plan_candidate.key,
            )
        seen_unscheduled.add(plan_candidate.key)
        estimate_source, estimated_minutes = adopted_estimates[plan_candidate.key]
        normalized_unscheduled.append(
            NormalizedUnscheduledItem(
                candidate_key=plan_candidate.key,
                task_id=plan_candidate.task_id,
                occurrence_key=plan_candidate.occurrence_key,
                remaining_minutes=raw_item.remaining_minutes,
                reason=raw_item.reason,
                explanation=raw_item.explanation,
                estimate_source=estimate_source,
                estimated_minutes=estimated_minutes,
            )
        )

    normalized = NormalizedPlan(
        summary=parsed_output.summary,
        assumptions=tuple(parsed_output.assumptions),
        blocks=tuple(normalized_blocks),
        unscheduled_items=tuple(normalized_unscheduled),
    )
    validate_plan_blocks(
        snapshot,
        list(normalized.blocks),
        list(normalized.unscheduled_items),
    )
    return normalized


def validate_plan_blocks(
    snapshot: dict,
    blocks: list[NormalizedBlock],
    unscheduled: list[NormalizedUnscheduledItem],
) -> None:
    """Validate normalized blocks without database or mutable process state."""
    context = _snapshot_context(snapshot)
    if len(blocks) > MAX_OUTPUT_ITEMS or len(unscheduled) > MAX_OUTPUT_ITEMS:
        raise _plan_error("plan_output_too_large", "计划输出项目数量超过限制。")

    durations: dict[str, list[int]] = defaultdict(list)
    adopted: dict[str, tuple[EstimateSource, int]] = {}
    blocks_by_candidate: dict[str, list[NormalizedBlock]] = defaultdict(list)
    daily_minutes: dict[date, int] = defaultdict(int)
    seen_steps: set[UUID] = set()

    for plan_block in blocks:
        plan_candidate = _candidate(context, plan_block.candidate_key)
        _validate_block_identity(plan_block, plan_candidate, context)
        requested_steps = set(plan_block.step_ids)
        if not requested_steps.issubset(plan_candidate.step_ids):
            raise _plan_error(
                "unknown_plan_step",
                "计划引用了输入快照中不存在的任务步骤。",
                candidate_key=plan_candidate.key,
            )
        if len(requested_steps) != len(plan_block.step_ids) or seen_steps & requested_steps:
            raise _plan_error(
                "duplicate_plan_step",
                "同一步骤不能在计划块中重复引用。",
                candidate_key=plan_candidate.key,
            )
        seen_steps.update(requested_steps)

        starts_at, ends_at, duration_minutes = _resolved_block_interval(
            context,
            plan_block.local_date,
            plan_block.local_starts_at,
            plan_block.local_ends_at,
        )
        if plan_block.starts_at != starts_at or plan_block.ends_at != ends_at:
            raise _plan_error(
                "plan_time_mismatch",
                "计划块的本地时间与 UTC 时间不一致。",
                candidate_key=plan_candidate.key,
            )
        if duration_minutes < plan_candidate.minimum_block_minutes:
            raise _plan_error(
                "plan_block_too_short",
                "计划块短于任务要求的最小连续时长。",
                candidate_key=plan_candidate.key,
            )
        _validate_deadline(plan_candidate, plan_block.local_date, ends_at)
        _record_adopted_estimate(adopted, plan_block)
        durations[plan_candidate.key].append(duration_minutes)
        blocks_by_candidate[plan_candidate.key].append(plan_block)
        daily_minutes[plan_block.local_date] += duration_minutes

    unscheduled_minutes: dict[str, int] = {}
    for item in unscheduled:
        plan_candidate = _candidate(context, item.candidate_key)
        _validate_unscheduled_identity(item, plan_candidate)
        if plan_candidate.key in unscheduled_minutes:
            raise _plan_error(
                "duplicate_unscheduled_item",
                "每个候选最多只能有一条未排项。",
                candidate_key=plan_candidate.key,
            )
        _record_adopted_estimate(adopted, item)
        unscheduled_minutes[plan_candidate.key] = item.remaining_minutes

    _validate_interval_conflicts(context, blocks)
    _validate_estimate_conservation(
        context,
        durations,
        blocks_by_candidate,
        unscheduled_minutes,
        adopted,
    )
    _validate_density(context, daily_minutes)


def _clean_required(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("must not be blank")
    return cleaned


def _plan_error(code: str, message: str, **details: Any) -> AppError:
    return AppError(code, message, details=details)


def _snapshot_context(snapshot: dict) -> _Snapshot:
    if not isinstance(snapshot, dict):
        raise _plan_error("invalid_plan_snapshot", "规划输入快照格式不正确。")
    try:
        scope = snapshot["scope"]
        target_date = _as_date(snapshot["target_date"])
        if scope == "day":
            range_start = target_date
            range_end = target_date
        elif scope == "week":
            range_start = _as_date(snapshot["week_start_date"])
            range_end = _as_date(snapshot["week_end_date"])
        else:
            raise ValueError("invalid scope")
        if range_end < range_start:
            raise ValueError("invalid date range")

        time_zone = str(snapshot["time_zone"])
        ZoneInfo(time_zone)
        density = str(snapshot["density"])
        if density not in DENSITY_LIMITS:
            raise ValueError("invalid density")
        buffer_minutes = snapshot["buffer_minutes"]
        if (
            isinstance(buffer_minutes, bool)
            or not isinstance(buffer_minutes, int)
            or not 0 <= buffer_minutes <= 60
            or buffer_minutes % 5
        ):
            raise ValueError("invalid buffer")
        available_starts_at = _as_time(
            _snapshot_value(
                snapshot,
                "available_starts_at",
                "availability_start",
            )
        )
        available_ends_at = _as_time(
            _snapshot_value(
                snapshot,
                "available_ends_at",
                "availability_end",
            )
        )
        if available_starts_at >= available_ends_at:
            raise ValueError("invalid availability")
        candidates = _snapshot_candidates(snapshot.get("task_candidates"))
        fixed_intervals = _fixed_intervals(snapshot.get("fixed_occurrences", []))
    except AppError:
        raise
    except (KeyError, TypeError, ValueError, ZoneInfoNotFoundError) as exc:
        raise _plan_error(
            "invalid_plan_snapshot",
            "规划输入快照格式不正确。",
        ) from exc
    return _Snapshot(
        scope=scope,
        target_date=target_date,
        range_start=range_start,
        range_end=range_end,
        time_zone=time_zone,
        density=density,
        buffer_minutes=buffer_minutes,
        available_starts_at=available_starts_at,
        available_ends_at=available_ends_at,
        candidates=candidates,
        fixed_intervals=fixed_intervals,
    )


def _snapshot_value(snapshot: dict, primary: str, alias: str) -> Any:
    if primary in snapshot:
        return snapshot[primary]
    return snapshot[alias]


def _snapshot_candidates(raw_candidates: Any) -> dict[str, _Candidate]:
    if not isinstance(raw_candidates, list) or len(raw_candidates) > MAX_OUTPUT_ITEMS:
        raise ValueError("invalid candidates")
    candidates: dict[str, _Candidate] = {}
    for raw in raw_candidates:
        if not isinstance(raw, dict):
            raise ValueError("invalid candidate")
        key = _clean_required(str(raw["candidate_key"]))
        if key in candidates:
            raise ValueError("duplicate candidate")
        estimated_minutes = raw.get("estimated_minutes")
        if estimated_minutes is not None and (
            isinstance(estimated_minutes, bool)
            or not isinstance(estimated_minutes, int)
            or not 1 <= estimated_minutes <= MAX_ESTIMATED_MINUTES
        ):
            raise ValueError("invalid estimate")
        raw_source = raw.get("estimate_source")
        estimate_source: EstimateSource | None
        if estimated_minutes is None:
            if raw_source is not None:
                raise ValueError("estimate source without estimate")
            estimate_source = None
        else:
            estimate_source = raw_source or "user"
            if estimate_source not in {"user", "ai"}:
                raise ValueError("invalid estimate source")
        minimum_block_minutes = raw.get("minimum_block_minutes", 15)
        if (
            isinstance(minimum_block_minutes, bool)
            or not isinstance(minimum_block_minutes, int)
            or not 1 <= minimum_block_minutes <= 1440
        ):
            raise ValueError("invalid minimum block")
        splittable = raw.get("splittable", True)
        if not isinstance(splittable, bool):
            raise ValueError("invalid splittable")
        due_kind = str(raw.get("due_kind", "none"))
        if due_kind not in {"none", "date", "datetime"}:
            raise ValueError("invalid due kind")
        due_date = (
            _as_date(raw["due_date"])
            if due_kind == "date" and raw.get("due_date") is not None
            else None
        )
        due_at = (
            _as_datetime(raw["due_at"])
            if due_kind == "datetime" and raw.get("due_at") is not None
            else None
        )
        if due_kind == "date" and due_date is None:
            raise ValueError("missing date deadline")
        if due_kind == "datetime" and due_at is None:
            raise ValueError("missing timed deadline")
        candidates[key] = _Candidate(
            key=key,
            task_id=_as_uuid(raw["task_id"]),
            occurrence_key=_optional_string(raw.get("occurrence_key")),
            step_ids=_candidate_step_ids(raw),
            estimated_minutes=estimated_minutes,
            estimate_source=estimate_source,
            splittable=splittable,
            minimum_block_minutes=minimum_block_minutes,
            due_kind=due_kind,
            due_date=due_date,
            due_at=due_at,
        )
    return candidates


def _candidate_step_ids(raw: dict) -> frozenset[UUID]:
    raw_steps = raw.get("steps", raw.get("step_ids", []))
    if not isinstance(raw_steps, list) or len(raw_steps) > 100:
        raise ValueError("invalid steps")
    step_ids = [
        _as_uuid(item["id"] if isinstance(item, dict) else item)
        for item in raw_steps
    ]
    if len(step_ids) != len(set(step_ids)):
        raise ValueError("duplicate snapshot step")
    return frozenset(step_ids)


def _fixed_intervals(raw_occurrences: Any) -> tuple[tuple[datetime, datetime], ...]:
    if not isinstance(raw_occurrences, list):
        raise ValueError("invalid fixed occurrences")
    intervals: list[tuple[datetime, datetime]] = []
    for raw in raw_occurrences:
        if (
            not isinstance(raw, dict)
            or raw.get("busy") is not True
            or raw.get("status") != "scheduled"
        ):
            continue
        warnings = raw.get("warning_codes", [])
        if not isinstance(warnings, list) or any(
            warning in {"invalid_local_time", "ambiguous_local_time"}
            for warning in warnings
        ):
            raise _plan_error(
                "plan_fixed_time_unresolved",
                "固定占用包含无法确定的本地时刻，本次计划不能安全生成。",
            )
        try:
            starts_at = _as_datetime(raw["start_at"])
            ends_at = _as_datetime(raw["end_at"])
        except (KeyError, TypeError, ValueError) as exc:
            raise _plan_error(
                "plan_fixed_time_unresolved",
                "固定占用缺少有效时间，本次计划不能安全生成。",
            ) from exc
        if ends_at <= starts_at:
            raise _plan_error(
                "plan_fixed_time_unresolved",
                "固定占用的时间范围无效，本次计划不能安全生成。",
            )
        intervals.append((starts_at, ends_at))
    return tuple(sorted(intervals))


def _adopted_estimates(
    context: _Snapshot,
    estimates: list[PlanningEstimateOutput],
) -> dict[str, tuple[EstimateSource, int]]:
    ai_estimates: dict[str, int] = {}
    for estimate in estimates:
        plan_candidate = _candidate(context, estimate.candidate_key)
        if plan_candidate.key in ai_estimates:
            raise _plan_error(
                "duplicate_plan_estimate",
                "每个候选最多只能有一条 AI 估时。",
                candidate_key=plan_candidate.key,
            )
        if plan_candidate.estimated_minutes is not None:
            raise _plan_error(
                "unexpected_plan_estimate",
                "AI 不能覆盖输入快照中的既有估时。",
                candidate_key=plan_candidate.key,
            )
        ai_estimates[plan_candidate.key] = estimate.estimated_minutes

    adopted: dict[str, tuple[EstimateSource, int]] = {}
    for key, plan_candidate in context.candidates.items():
        if plan_candidate.estimated_minutes is not None:
            adopted[key] = (
                plan_candidate.estimate_source or "user",
                plan_candidate.estimated_minutes,
            )
        elif key in ai_estimates:
            adopted[key] = ("ai", ai_estimates[key])
        else:
            raise _plan_error(
                "missing_plan_estimate",
                "缺少无用户估时候选的 AI 估时。",
                candidate_key=key,
            )
    return adopted


def _candidate(context: _Snapshot, key: str) -> _Candidate:
    try:
        return context.candidates[key]
    except KeyError as exc:
        raise _plan_error(
            "unknown_plan_candidate",
            "计划引用了输入快照中不存在的候选。",
            candidate_key=key,
        ) from exc


def _resolved_block_interval(
    context: _Snapshot,
    local_date: date,
    starts_at: time,
    ends_at: time,
) -> tuple[datetime, datetime, int]:
    if not context.range_start <= local_date <= context.range_end:
        raise _plan_error(
            "plan_date_out_of_range",
            "计划块日期不在请求范围内。",
            local_date=local_date.isoformat(),
        )
    if not _aligned_time(starts_at) or not _aligned_time(ends_at):
        raise _plan_error(
            "plan_time_not_aligned",
            "计划块必须按 15 分钟对齐。",
            local_date=local_date.isoformat(),
        )
    if starts_at >= ends_at:
        raise _plan_error(
            "invalid_plan_time",
            "计划块结束时刻必须晚于开始时刻。",
            local_date=local_date.isoformat(),
        )
    if (
        starts_at < context.available_starts_at
        or ends_at > context.available_ends_at
    ):
        raise _plan_error(
            "plan_outside_availability",
            "计划块必须位于可规划时段内。",
            local_date=local_date.isoformat(),
        )
    resolved_start = _resolve_plan_local(
        datetime.combine(local_date, starts_at),
        context.time_zone,
    )
    resolved_end = _resolve_plan_local(
        datetime.combine(local_date, ends_at),
        context.time_zone,
    )
    duration_seconds = (resolved_end - resolved_start).total_seconds()
    if duration_seconds <= 0 or duration_seconds % 60:
        raise _plan_error(
            "invalid_plan_time",
            "计划块在所选时区中的实际时长无效。",
            local_date=local_date.isoformat(),
        )
    duration_minutes = int(duration_seconds // 60)
    if duration_minutes > MAX_BLOCK_MINUTES:
        raise _plan_error(
            "plan_block_too_long",
            "单个计划块不能超过 180 分钟。",
            local_date=local_date.isoformat(),
        )
    return resolved_start, resolved_end, duration_minutes


def _resolve_plan_local(local_value: datetime, time_zone: str) -> datetime:
    resolved, warning = resolve_local_datetime(
        local_value,
        time_zone,
        preferred_offset_minutes=None,
        strict_ambiguous=True,
    )
    if resolved is None:
        raise _plan_error(
            warning or "invalid_local_time",
            "计划块使用了夏令时切换期间不存在的本地时刻。",
            local_time=local_value.isoformat(),
        )
    return resolved


def _aligned_time(value: time) -> bool:
    return (
        value.tzinfo is None
        and value.second == 0
        and value.microsecond == 0
        and value.minute % 15 == 0
    )


def _validate_block_identity(
    block: NormalizedBlock,
    plan_candidate: _Candidate,
    context: _Snapshot,
) -> None:
    if (
        block.task_id != plan_candidate.task_id
        or block.occurrence_key != plan_candidate.occurrence_key
        or block.time_zone != context.time_zone
    ):
        raise _plan_error(
            "plan_candidate_mismatch",
            "计划块与输入快照候选不一致。",
            candidate_key=plan_candidate.key,
        )
    _validate_estimate_identity(
        plan_candidate,
        block.estimate_source,
        block.estimated_minutes,
    )


def _validate_unscheduled_identity(
    item: NormalizedUnscheduledItem,
    plan_candidate: _Candidate,
) -> None:
    if (
        item.task_id != plan_candidate.task_id
        or item.occurrence_key != plan_candidate.occurrence_key
    ):
        raise _plan_error(
            "plan_candidate_mismatch",
            "未排项与输入快照候选不一致。",
            candidate_key=plan_candidate.key,
        )
    _validate_estimate_identity(
        plan_candidate,
        item.estimate_source,
        item.estimated_minutes,
    )


def _validate_estimate_identity(
    plan_candidate: _Candidate,
    estimate_source: EstimateSource,
    estimated_minutes: int,
) -> None:
    if not 1 <= estimated_minutes <= MAX_ESTIMATED_MINUTES:
        raise _plan_error(
            "invalid_plan_estimate",
            "采用估时超出允许范围。",
            candidate_key=plan_candidate.key,
        )
    if plan_candidate.estimated_minutes is not None and (
        estimated_minutes != plan_candidate.estimated_minutes
        or estimate_source != plan_candidate.estimate_source
    ):
        raise _plan_error(
            "plan_estimate_changed",
            "计划不能覆盖输入快照中的既有估时。",
            candidate_key=plan_candidate.key,
        )
    if plan_candidate.estimated_minutes is None and estimate_source != "ai":
        raise _plan_error(
            "invalid_plan_estimate",
            "无既有估时的候选必须使用 AI 估时。",
            candidate_key=plan_candidate.key,
        )


def _validate_deadline(
    plan_candidate: _Candidate,
    local_date: date,
    ends_at: datetime,
) -> None:
    after_deadline = (
        plan_candidate.due_kind == "date"
        and plan_candidate.due_date is not None
        and local_date > plan_candidate.due_date
    ) or (
        plan_candidate.due_kind == "datetime"
        and plan_candidate.due_at is not None
        and ends_at > plan_candidate.due_at
    )
    if after_deadline:
        raise _plan_error(
            "plan_after_deadline",
            "计划块结束时间晚于任务截止时间。",
            candidate_key=plan_candidate.key,
        )


def _record_adopted_estimate(
    adopted: dict[str, tuple[EstimateSource, int]],
    item: NormalizedBlock | NormalizedUnscheduledItem,
) -> None:
    value = (item.estimate_source, item.estimated_minutes)
    existing = adopted.setdefault(item.candidate_key, value)
    if existing != value:
        raise _plan_error(
            "plan_estimate_mismatch",
            "同一候选使用了不一致的采用估时。",
            candidate_key=item.candidate_key,
        )


def _validate_interval_conflicts(
    context: _Snapshot,
    blocks: list[NormalizedBlock],
) -> None:
    buffer = timedelta(minutes=context.buffer_minutes)
    ordered_blocks = sorted(blocks, key=lambda item: (item.starts_at, item.ends_at))
    for index, current in enumerate(ordered_blocks):
        for other in ordered_blocks[index + 1 :]:
            if other.starts_at >= current.ends_at + buffer:
                break
            if _intervals_too_close(
                current.starts_at,
                current.ends_at,
                other.starts_at,
                other.ends_at,
                buffer,
            ):
                raise _plan_error(
                    "plan_block_conflict",
                    "计划块之间重叠或未满足缓冲。",
                )
    for plan_block in ordered_blocks:
        for fixed_start, fixed_end in context.fixed_intervals:
            if fixed_start >= plan_block.ends_at + buffer:
                break
            if fixed_end + buffer <= plan_block.starts_at:
                continue
            if _intervals_too_close(
                plan_block.starts_at,
                plan_block.ends_at,
                fixed_start,
                fixed_end,
                buffer,
            ):
                raise _plan_error(
                    "plan_fixed_conflict",
                    "计划块与固定占用重叠或未满足缓冲。",
                    candidate_key=plan_block.candidate_key,
                )


def _intervals_too_close(
    first_start: datetime,
    first_end: datetime,
    second_start: datetime,
    second_end: datetime,
    buffer: timedelta,
) -> bool:
    return first_start < second_end + buffer and second_start < first_end + buffer


def _validate_estimate_conservation(
    context: _Snapshot,
    durations: dict[str, list[int]],
    blocks_by_candidate: dict[str, list[NormalizedBlock]],
    unscheduled_minutes: dict[str, int],
    adopted: dict[str, tuple[EstimateSource, int]],
) -> None:
    for key, plan_candidate in context.candidates.items():
        plan_durations = durations.get(key, [])
        remaining = unscheduled_minutes.get(key, 0)
        estimate = adopted.get(key)
        if estimate is None or sum(plan_durations) + remaining != estimate[1]:
            raise _plan_error(
                "plan_estimate_mismatch",
                "每个候选的已排分钟与未排分钟之和必须等于采用估时。",
                candidate_key=key,
            )
        if not plan_candidate.splittable and plan_durations:
            if (
                len(plan_durations) != 1
                or remaining != 0
                or plan_durations[0] != estimate[1]
            ):
                raise _plan_error(
                    "plan_not_splittable",
                    "不可拆任务只能安排为一个完整计划块。",
                    candidate_key=key,
                )
        if len(blocks_by_candidate.get(key, [])) != len(plan_durations):
            raise _plan_error(
                "invalid_plan_output",
                "计划块统计不一致。",
                candidate_key=key,
            )


def _validate_density(
    context: _Snapshot,
    daily_minutes: dict[date, int],
) -> None:
    limit = DENSITY_LIMITS[context.density]
    for local_date, minutes in daily_minutes.items():
        if minutes > limit:
            raise _plan_error(
                "plan_density_exceeded",
                "单日任务分钟超过所选密度上限。",
                local_date=local_date.isoformat(),
                limit_minutes=limit,
            )


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        raise ValueError("datetime is not a date")
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def _as_time(value: Any) -> time:
    parsed = value if isinstance(value, time) else time.fromisoformat(value)
    if parsed.tzinfo is not None:
        raise ValueError("time must be local")
    return parsed


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value)
        parsed = datetime.fromisoformat(
            f"{text[:-1]}+00:00" if text.endswith("Z") else text
        )
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("datetime must include an offset")
    return parsed.astimezone(UTC)


def _as_uuid(value: Any) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    return _clean_required(str(value))
