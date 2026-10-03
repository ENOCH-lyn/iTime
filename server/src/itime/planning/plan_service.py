from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from itime.auth.security import now
from itime.errors import AppError
from itime.planning.models import (
    Plan,
    PlanningInputRevision,
    PlanningInputSnapshot,
    PlanUnscheduledItem,
    PlanVersion,
    ScheduledBlock,
)
from itime.planning.validation import (
    NormalizedBlock,
    NormalizedPlan,
    NormalizedUnscheduledItem,
    validate_plan_blocks,
)
from itime.schemas import Input
from itime.tasks.models import Task, TaskStep
from itime.tasks.service import replay_operation, save_operation

PlanSource = Literal["ai", "manual"]
PLAN_VERSION_OPERATION_SCOPE = "planning.plan_version.create"


class _CreatePlanVersionOperation(Input):
    operation_id: UUID
    snapshot: dict[str, Any]
    normalized: NormalizedPlan
    source: PlanSource
    proposal_id: UUID | None
    expected_revision: int
    expected_plan_version: int


def scheduled_block_result(block: ScheduledBlock) -> dict:
    return {
        "id": str(block.id),
        "position": block.position,
        "task_id": str(block.task_id),
        "occurrence_key": block.occurrence_key,
        "task_title_snapshot": block.task_title_snapshot,
        "step_ids": list(block.step_ids),
        "step_titles_snapshot": list(block.step_titles_snapshot),
        "local_date": block.local_date.isoformat(),
        "local_starts_at": block.local_starts_at.isoformat(),
        "local_ends_at": block.local_ends_at.isoformat(),
        "time_zone": block.time_zone,
        "starts_at": _datetime_result(block.starts_at),
        "ends_at": _datetime_result(block.ends_at),
        "estimate_source": block.estimate_source,
        "estimated_minutes": block.estimated_minutes,
        "created_at": _datetime_result(block.created_at),
    }


def unscheduled_item_result(item: PlanUnscheduledItem) -> dict:
    return {
        "id": str(item.id),
        "position": item.position,
        "task_id": str(item.task_id),
        "occurrence_key": item.occurrence_key,
        "task_title_snapshot": item.task_title_snapshot,
        "remaining_minutes": item.remaining_minutes,
        "reason": item.reason,
        "explanation": item.explanation,
        "created_at": _datetime_result(item.created_at),
    }


def plan_version_result(
    version: PlanVersion,
    blocks: list[ScheduledBlock],
    unscheduled_items: list[PlanUnscheduledItem],
) -> dict:
    return {
        "id": str(version.id),
        "plan_id": str(version.plan_id),
        "version": version.version,
        "source": version.source,
        "proposal_id": str(version.proposal_id) if version.proposal_id else None,
        "scope": version.scope,
        "target_date": version.target_date.isoformat(),
        "density": version.density,
        "buffer_minutes": version.buffer_minutes,
        "availability_start": version.availability_start.isoformat(),
        "availability_end": version.availability_end.isoformat(),
        "summary": version.summary,
        "assumptions": list(version.assumptions),
        "input_revision": version.input_revision,
        "blocks": [scheduled_block_result(block) for block in blocks],
        "unscheduled_items": [
            unscheduled_item_result(item) for item in unscheduled_items
        ],
        "created_at": _datetime_result(version.created_at),
    }


def plan_result(plan: Plan, current_version: dict) -> dict:
    return {
        "id": str(plan.id),
        "week_start_date": plan.week_start_date.isoformat(),
        "week_end_date": (plan.week_start_date + timedelta(days=6)).isoformat(),
        "time_zone": plan.time_zone,
        "current_version_id": str(plan.current_version_id),
        "version": plan.version,
        "current_version": current_version,
        "created_at": _datetime_result(plan.created_at),
        "updated_at": _datetime_result(plan.updated_at),
    }


async def read_week_plan(
    session: AsyncSession,
    owner_id: UUID,
    week_start_date: date,
    *,
    version: int | None = None,
) -> dict | None:
    plan = await session.scalar(
        select(Plan).where(
            Plan.owner_id == owner_id,
            Plan.week_start_date == week_start_date,
        )
    )
    if plan is None:
        return None
    selected_version = await _load_plan_version(
        session,
        owner_id,
        plan,
        version=version,
    )
    return plan_result(plan, selected_version)


async def read_plan_history(
    session: AsyncSession,
    owner_id: UUID,
    plan_id: UUID,
) -> list[dict]:
    plan = await session.scalar(
        select(Plan).where(Plan.id == plan_id, Plan.owner_id == owner_id)
    )
    if plan is None:
        raise AppError("plan_not_found", "未找到此周计划。", status=404)

    versions = list(
        await session.scalars(
            select(PlanVersion)
            .where(
                PlanVersion.plan_id == plan.id,
                PlanVersion.owner_id == owner_id,
            )
            .order_by(PlanVersion.version.desc())
        )
    )
    if not versions:
        return []
    blocks, unscheduled = await _load_version_children(
        session,
        owner_id,
        [version.id for version in versions],
    )
    return [
        plan_version_result(
            version,
            blocks.get(version.id, []),
            unscheduled.get(version.id, []),
        )
        for version in versions
    ]


async def create_plan_version(
    session: AsyncSession,
    owner_id: UUID,
    normalized: NormalizedPlan,
    *,
    snapshot: PlanningInputSnapshot | Mapping[str, Any],
    source: PlanSource,
    proposal_id: UUID | None,
    expected_revision: int,
    expected_plan_version: int,
    operation_id: UUID,
) -> dict:
    """Create and select one immutable, complete version of a natural week."""
    snapshot_data = _snapshot_data(snapshot)
    operation = _CreatePlanVersionOperation(
        operation_id=operation_id,
        snapshot=snapshot_data,
        normalized=normalized,
        source=source,
        proposal_id=proposal_id,
        expected_revision=expected_revision,
        expected_plan_version=expected_plan_version,
    )
    _validate_source(source, proposal_id)
    _validate_snapshot_identity(snapshot_data, owner_id, expected_revision)
    week_start, target_date = _plan_dates(snapshot_data)

    # The operation lock makes an unknown-result retry deterministic even when
    # the original request is still committing.
    await _advisory_lock(session, f"planning-operation:{owner_id}:{operation_id}")
    replay = await replay_operation(
        session,
        owner_id,
        operation_id,
        PLAN_VERSION_OPERATION_SCOPE,
        operation,
    )
    if replay is not None:
        return replay

    # A row may not exist for an account with no prior planning inputs. The
    # owner lock serializes that first row and the first Plan aggregate.
    await _advisory_lock(session, f"planning-owner:{owner_id}")
    revision = await _locked_planning_revision(session, owner_id)
    if revision.revision != expected_revision:
        raise AppError(
            "planning_revision_conflict",
            "规划输入已变化，请刷新后重新生成或保存。",
            status=409,
            details={"current_revision": revision.revision},
        )

    plan = await session.scalar(
        select(Plan)
        .where(
            Plan.owner_id == owner_id,
            Plan.week_start_date == week_start,
        )
        .with_for_update()
    )
    current_version = plan.version if plan is not None else 0
    if current_version != expected_plan_version:
        raise AppError(
            "plan_version_conflict",
            "此周计划已在其他页面或设备更新，请刷新后比较再保存。",
            status=409,
            details={"current_version": current_version},
        )

    previous_version_id = plan.current_version_id if plan is not None else None
    validate_plan_blocks(
        snapshot_data,
        list(normalized.blocks),
        list(normalized.unscheduled_items),
    )
    task_titles, step_details = await _snapshot_titles(
        session,
        owner_id,
        normalized,
    )

    version_id = uuid4()
    current = now()
    if plan is None:
        plan = Plan(
            id=uuid4(),
            owner_id=owner_id,
            week_start_date=week_start,
            time_zone=str(snapshot_data["time_zone"]),
            current_version_id=version_id,
            version=1,
            created_at=current,
            updated_at=current,
        )
        session.add(plan)
        next_version = 1
    else:
        next_version = plan.version + 1
        plan.current_version_id = version_id
        plan.version = next_version
        plan.updated_at = current

    old_blocks: list[ScheduledBlock] = []
    old_unscheduled: list[PlanUnscheduledItem] = []
    if previous_version_id is not None and snapshot_data["scope"] == "day":
        old_blocks, old_unscheduled = await _current_version_children(
            session,
            owner_id,
            previous_version_id,
        )

    version = PlanVersion(
        id=version_id,
        plan_id=plan.id,
        owner_id=owner_id,
        version=next_version,
        source=source,
        proposal_id=proposal_id,
        scope=str(snapshot_data["scope"]),
        target_date=target_date,
        density=str(snapshot_data["density"]),
        buffer_minutes=int(snapshot_data["buffer_minutes"]),
        availability_start=_time_value(
            _snapshot_value(
                snapshot_data,
                "availability_start",
                "available_starts_at",
            )
        ),
        availability_end=_time_value(
            _snapshot_value(
                snapshot_data,
                "availability_end",
                "available_ends_at",
            )
        ),
        summary=normalized.summary,
        assumptions=list(normalized.assumptions),
        input_revision=revision.revision,
        created_at=current,
    )
    session.add(version)
    await session.flush([plan, version])

    block_values = _merged_block_values(
        normalized.blocks,
        old_blocks,
        target_date=target_date,
        scope=str(snapshot_data["scope"]),
        task_titles=task_titles,
        step_details=step_details,
    )
    unscheduled_values = _merged_unscheduled_values(
        normalized.unscheduled_items,
        old_unscheduled,
        scope=str(snapshot_data["scope"]),
        affected_candidates=_candidate_identities(snapshot_data),
        task_titles=task_titles,
    )
    blocks = [
        ScheduledBlock(
            id=uuid4(),
            plan_version_id=version.id,
            owner_id=owner_id,
            position=position,
            created_at=current,
            **values,
        )
        for position, values in enumerate(block_values)
    ]
    unscheduled_items = [
        PlanUnscheduledItem(
            id=uuid4(),
            plan_version_id=version.id,
            owner_id=owner_id,
            position=position,
            created_at=current,
            **values,
        )
        for position, values in enumerate(unscheduled_values)
    ]
    session.add_all([*blocks, *unscheduled_items])
    await session.flush()

    serialized_version = plan_version_result(version, blocks, unscheduled_items)
    result = {
        "operation_id": str(operation_id),
        "plan": plan_result(plan, serialized_version),
    }
    save_operation(
        session,
        owner_id,
        operation_id,
        PLAN_VERSION_OPERATION_SCOPE,
        operation,
        result,
    )
    await session.flush()
    return result


async def _load_plan_version(
    session: AsyncSession,
    owner_id: UUID,
    plan: Plan,
    *,
    version: int | None,
) -> dict:
    statement = select(PlanVersion).where(
        PlanVersion.plan_id == plan.id,
        PlanVersion.owner_id == owner_id,
    )
    if version is None:
        statement = statement.where(PlanVersion.id == plan.current_version_id)
    else:
        statement = statement.where(PlanVersion.version == version)
    selected = await session.scalar(statement)
    if selected is None:
        raise AppError("plan_version_not_found", "未找到此计划版本。", status=404)
    blocks, unscheduled = await _load_version_children(
        session,
        owner_id,
        [selected.id],
    )
    return plan_version_result(
        selected,
        blocks.get(selected.id, []),
        unscheduled.get(selected.id, []),
    )


async def _load_version_children(
    session: AsyncSession,
    owner_id: UUID,
    version_ids: list[UUID],
) -> tuple[
    dict[UUID, list[ScheduledBlock]],
    dict[UUID, list[PlanUnscheduledItem]],
]:
    blocks: defaultdict[UUID, list[ScheduledBlock]] = defaultdict(list)
    for block in await session.scalars(
        select(ScheduledBlock)
        .where(
            ScheduledBlock.owner_id == owner_id,
            ScheduledBlock.plan_version_id.in_(version_ids),
        )
        .order_by(ScheduledBlock.plan_version_id, ScheduledBlock.position)
    ):
        blocks[block.plan_version_id].append(block)

    unscheduled: defaultdict[UUID, list[PlanUnscheduledItem]] = defaultdict(list)
    for item in await session.scalars(
        select(PlanUnscheduledItem)
        .where(
            PlanUnscheduledItem.owner_id == owner_id,
            PlanUnscheduledItem.plan_version_id.in_(version_ids),
        )
        .order_by(
            PlanUnscheduledItem.plan_version_id,
            PlanUnscheduledItem.position,
        )
    ):
        unscheduled[item.plan_version_id].append(item)
    return dict(blocks), dict(unscheduled)


async def _current_version_children(
    session: AsyncSession,
    owner_id: UUID,
    version_id: UUID,
) -> tuple[list[ScheduledBlock], list[PlanUnscheduledItem]]:
    blocks, unscheduled = await _load_version_children(
        session,
        owner_id,
        [version_id],
    )
    return (
        blocks.get(version_id, []),
        unscheduled.get(version_id, []),
    )


async def _locked_planning_revision(
    session: AsyncSession,
    owner_id: UUID,
) -> PlanningInputRevision:
    await session.execute(
        insert(PlanningInputRevision)
        .values(owner_id=owner_id, revision=0, updated_at=now())
        .on_conflict_do_nothing(index_elements=[PlanningInputRevision.owner_id])
    )
    revision = await session.scalar(
        select(PlanningInputRevision)
        .where(PlanningInputRevision.owner_id == owner_id)
        .with_for_update()
    )
    if revision is None:
        raise AppError("user_not_found", "未找到此账号。", status=404)
    return revision


async def _advisory_lock(session: AsyncSession, key: str) -> None:
    await session.execute(
        select(func.pg_advisory_xact_lock(func.hashtextextended(key, 0)))
    )


async def _snapshot_titles(
    session: AsyncSession,
    owner_id: UUID,
    normalized: NormalizedPlan,
) -> tuple[dict[UUID, str], dict[UUID, tuple[UUID, str]]]:
    task_ids = {
        item.task_id
        for item in (*normalized.blocks, *normalized.unscheduled_items)
    }
    task_titles = {
        task_id: title
        for task_id, title in await session.execute(
            select(Task.id, Task.title).where(
                Task.owner_id == owner_id,
                Task.id.in_(task_ids),
            )
        )
    }
    if set(task_titles) != task_ids:
        raise AppError(
            "plan_candidate_changed",
            "计划引用的任务已变化，请刷新后重新生成或保存。",
            status=409,
        )

    step_ids = {
        step_id for block in normalized.blocks for step_id in block.step_ids
    }
    step_details = {
        step_id: (task_id, title)
        for step_id, task_id, title in await session.execute(
            select(TaskStep.id, TaskStep.task_id, TaskStep.title).where(
                TaskStep.owner_id == owner_id,
                TaskStep.id.in_(step_ids),
            )
        )
    }
    if set(step_details) != step_ids:
        raise AppError(
            "plan_candidate_changed",
            "计划引用的任务步骤已变化，请刷新后重新生成或保存。",
            status=409,
        )
    for block in normalized.blocks:
        if any(step_details[step_id][0] != block.task_id for step_id in block.step_ids):
            raise AppError(
                "plan_candidate_changed",
                "计划引用的任务步骤已变化，请刷新后重新生成或保存。",
                status=409,
            )
    return task_titles, step_details


def _merged_block_values(
    normalized: tuple[NormalizedBlock, ...],
    old_blocks: list[ScheduledBlock],
    *,
    target_date: date,
    scope: str,
    task_titles: dict[UUID, str],
    step_details: dict[UUID, tuple[UUID, str]],
) -> list[dict[str, Any]]:
    values = [
        _existing_block_values(block)
        for block in old_blocks
        if scope == "day" and block.local_date != target_date
    ]
    values.extend(
        {
            "task_id": block.task_id,
            "occurrence_key": block.occurrence_key,
            "task_title_snapshot": task_titles[block.task_id],
            "step_ids": [str(step_id) for step_id in block.step_ids],
            "step_titles_snapshot": [
                step_details[step_id][1] for step_id in block.step_ids
            ],
            "local_date": block.local_date,
            "local_starts_at": block.local_starts_at,
            "local_ends_at": block.local_ends_at,
            "time_zone": block.time_zone,
            "starts_at": block.starts_at,
            "ends_at": block.ends_at,
            "estimate_source": block.estimate_source,
            "estimated_minutes": block.estimated_minutes,
        }
        for block in normalized
    )
    values.sort(
        key=lambda item: (
            item["local_date"],
            item["local_starts_at"],
            item["local_ends_at"],
            str(item["task_id"]),
        )
    )
    return values


def _existing_block_values(block: ScheduledBlock) -> dict[str, Any]:
    return {
        "task_id": block.task_id,
        "occurrence_key": block.occurrence_key,
        "task_title_snapshot": block.task_title_snapshot,
        "step_ids": list(block.step_ids),
        "step_titles_snapshot": list(block.step_titles_snapshot),
        "local_date": block.local_date,
        "local_starts_at": block.local_starts_at,
        "local_ends_at": block.local_ends_at,
        "time_zone": block.time_zone,
        "starts_at": block.starts_at,
        "ends_at": block.ends_at,
        "estimate_source": block.estimate_source,
        "estimated_minutes": block.estimated_minutes,
    }


def _merged_unscheduled_values(
    normalized: tuple[NormalizedUnscheduledItem, ...],
    old_items: list[PlanUnscheduledItem],
    *,
    scope: str,
    affected_candidates: set[tuple[UUID, str | None]],
    task_titles: dict[UUID, str],
) -> list[dict[str, Any]]:
    values = [
        _existing_unscheduled_values(item)
        for item in old_items
        if scope == "day"
        and (item.task_id, item.occurrence_key) not in affected_candidates
    ]
    values.extend(
        {
            "task_id": item.task_id,
            "occurrence_key": item.occurrence_key,
            "task_title_snapshot": task_titles[item.task_id],
            "remaining_minutes": item.remaining_minutes,
            "reason": item.reason,
            "explanation": item.explanation,
        }
        for item in normalized
    )
    return values


def _existing_unscheduled_values(
    item: PlanUnscheduledItem,
) -> dict[str, Any]:
    return {
        "task_id": item.task_id,
        "occurrence_key": item.occurrence_key,
        "task_title_snapshot": item.task_title_snapshot,
        "remaining_minutes": item.remaining_minutes,
        "reason": item.reason,
        "explanation": item.explanation,
    }


def _candidate_identities(
    snapshot: Mapping[str, Any],
) -> set[tuple[UUID, str | None]]:
    identities: set[tuple[UUID, str | None]] = set()
    for raw in snapshot.get("task_candidates", []):
        identities.add(
            (
                UUID(str(raw["task_id"])),
                str(raw["occurrence_key"]) if raw.get("occurrence_key") else None,
            )
        )
    return identities


def _snapshot_data(
    snapshot: PlanningInputSnapshot | Mapping[str, Any],
) -> dict[str, Any]:
    if isinstance(snapshot, PlanningInputSnapshot):
        return {
            "id": snapshot.id,
            "owner_id": snapshot.owner_id,
            "revision": snapshot.revision,
            "scope": snapshot.scope,
            "target_date": snapshot.target_date,
            "week_start_date": snapshot.week_start_date,
            "week_end_date": snapshot.week_end_date,
            "time_zone": snapshot.time_zone,
            "density": snapshot.density,
            "buffer_minutes": snapshot.buffer_minutes,
            "availability_start": snapshot.availability_start,
            "availability_end": snapshot.availability_end,
            "task_candidates": snapshot.task_candidates,
            "fixed_occurrences": snapshot.fixed_occurrences,
            "current_plan_id": snapshot.current_plan_id,
            "current_plan_version": snapshot.current_plan_version,
            "preserved_dates": snapshot.preserved_dates,
            "content_sha256": snapshot.content_sha256,
        }
    if not isinstance(snapshot, Mapping):
        raise AppError("invalid_plan_snapshot", "规划输入快照格式不正确。")
    return dict(snapshot)


def _validate_snapshot_identity(
    snapshot: Mapping[str, Any],
    owner_id: UUID,
    expected_revision: int,
) -> None:
    if expected_revision < 0:
        raise AppError("invalid_plan_revision", "规划输入修订号无效。")
    try:
        snapshot_owner_id = (
            UUID(str(snapshot["owner_id"]))
            if snapshot.get("owner_id") is not None
            else owner_id
        )
        snapshot_revision = (
            int(snapshot["revision"])
            if snapshot.get("revision") is not None
            else expected_revision
        )
    except (TypeError, ValueError) as exc:
        raise AppError(
            "invalid_plan_snapshot",
            "规划输入快照身份信息不正确。",
        ) from exc
    if snapshot_owner_id != owner_id:
        raise AppError("invalid_plan_snapshot", "规划输入快照不属于当前账号。")
    if snapshot_revision != expected_revision:
        raise AppError(
            "planning_snapshot_revision_mismatch",
            "规划输入快照与预期修订号不一致，请刷新后重试。",
            status=409,
        )


def _plan_dates(snapshot: Mapping[str, Any]) -> tuple[date, date]:
    try:
        week_start = _date_value(snapshot["week_start_date"])
        week_end = _date_value(snapshot["week_end_date"])
        target_date = _date_value(snapshot["target_date"])
        if (
            week_start.weekday() != 0
            or week_end != week_start + timedelta(days=6)
            or not week_start <= target_date <= week_end
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise AppError(
            "invalid_plan_snapshot",
            "规划输入快照日期范围不正确。",
        ) from exc
    return week_start, target_date


def _validate_source(source: str, proposal_id: UUID | None) -> None:
    if source not in {"ai", "manual"} or ((source == "ai") != (proposal_id is not None)):
        raise AppError(
            "invalid_plan_source",
            "计划版本来源与提案不一致。",
        )


def _snapshot_value(
    snapshot: Mapping[str, Any],
    primary: str,
    alias: str,
) -> Any:
    if primary in snapshot:
        return snapshot[primary]
    return snapshot[alias]


def _date_value(value: Any) -> date:
    if isinstance(value, datetime):
        raise ValueError
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _time_value(value: Any) -> time:
    parsed = value if isinstance(value, time) else time.fromisoformat(str(value))
    if parsed.tzinfo is not None:
        raise AppError("invalid_plan_snapshot", "规划输入快照时间格式不正确。")
    return parsed


def _datetime_result(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.isoformat()
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
