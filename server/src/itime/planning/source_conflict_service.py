from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from itime.auth.security import now
from itime.calendar.models import CalendarSource
from itime.calendar.subscription_projection import query_subscription_occurrences
from itime.errors import AppError
from itime.planning.models import (
    Plan,
    PlanSourceConflict,
    PlanVersion,
    ScheduledBlock,
)
from itime.planning.schemas import ResolvePlanSourceConflictsInput
from itime.planning.snapshot_service import lock_planning_revision
from itime.tasks.service import (
    emit_change,
    replay_operation,
    save_operation,
)

MAX_CONFLICT_OCCURRENCES = 2000
MAX_CURRENT_OR_FUTURE_PLANS = 60
RESOLVE_SOURCE_CONFLICTS_SCOPE = "planning.source_conflicts.resolve"


@dataclass(frozen=True)
class PlanConflictBaseline:
    plan_id: UUID
    plan_version_id: UUID
    plan_version: int
    week_start_date: date
    query_start: datetime
    query_end: datetime
    occurrences: tuple[dict[str, Any], ...]
    details_limited: bool


async def capture_source_plan_baselines(
    session: AsyncSession,
    owner_id: UUID,
    source_id: UUID,
    at: datetime,
) -> list[PlanConflictBaseline]:
    """Serialize source refreshes with plan writes and capture current week inputs."""
    await lock_planning_revision(session, owner_id, write=True)
    earliest_week = at.astimezone(UTC).date() - timedelta(days=7)
    rows = list(
        (
            await session.execute(
                select(Plan, PlanVersion)
                .join(
                    PlanVersion,
                    and_(
                        PlanVersion.id == Plan.current_version_id,
                        PlanVersion.plan_id == Plan.id,
                        PlanVersion.owner_id == Plan.owner_id,
                    ),
                )
                .where(
                    Plan.owner_id == owner_id,
                    Plan.week_start_date >= earliest_week,
                )
                .order_by(Plan.week_start_date, Plan.id)
                .limit(MAX_CURRENT_OR_FUTURE_PLANS + 1)
                .with_for_update(of=Plan)
            )
        ).all()
    )
    if len(rows) > MAX_CURRENT_OR_FUTURE_PLANS:
        raise AppError(
            "plan_source_conflict_plan_limit",
            "当前或未来计划过多，暂时无法安全应用订阅更新。",
            status=409,
            retryable=True,
        )

    baselines: list[PlanConflictBaseline] = []
    for plan, version in rows:
        zone = ZoneInfo(plan.time_zone)
        query_start = datetime.combine(
            plan.week_start_date,
            time.min,
            tzinfo=zone,
        ).astimezone(UTC)
        query_end = datetime.combine(
            plan.week_start_date + timedelta(days=7),
            time.min,
            tzinfo=zone,
        ).astimezone(UTC)
        if query_end <= at.astimezone(UTC):
            continue
        occurrences, details_limited = await _source_occurrences(
            session,
            owner_id,
            source_id,
            query_start,
            query_end,
        )
        baselines.append(
            PlanConflictBaseline(
                plan_id=plan.id,
                plan_version_id=version.id,
                plan_version=version.version,
                week_start_date=plan.week_start_date,
                query_start=query_start,
                query_end=query_end,
                occurrences=tuple(occurrences),
                details_limited=details_limited,
            )
        )
    return baselines


async def record_source_plan_conflicts(
    session: AsyncSession,
    owner_id: UUID,
    source: CalendarSource,
    source_generation: int,
    baselines: list[PlanConflictBaseline],
    at: datetime,
) -> list[PlanSourceConflict]:
    created: list[PlanSourceConflict] = []
    for baseline in baselines:
        current_plan = await session.scalar(
            select(Plan).where(
                Plan.id == baseline.plan_id,
                Plan.owner_id == owner_id,
                Plan.current_version_id == baseline.plan_version_id,
                Plan.version == baseline.plan_version,
            )
        )
        if current_plan is None:
            continue
        after, after_limited = await _source_occurrences(
            session,
            owner_id,
            source.id,
            baseline.query_start,
            baseline.query_end,
        )
        details_limited = baseline.details_limited or after_limited
        blocks = list(
            await session.scalars(
                select(ScheduledBlock)
                .where(
                    ScheduledBlock.owner_id == owner_id,
                    ScheduledBlock.plan_version_id == baseline.plan_version_id,
                )
                .order_by(ScheduledBlock.position)
            )
        )
        changes = (
            []
            if details_limited
            else _schedule_changes(
                list(baseline.occurrences),
                after,
                blocks,
            )
        )
        if not details_limited and not changes:
            continue

        existing = await session.scalar(
            select(PlanSourceConflict).where(
                PlanSourceConflict.plan_version_id == baseline.plan_version_id,
                PlanSourceConflict.source_id == source.id,
                PlanSourceConflict.source_generation == source_generation,
            )
        )
        if existing is not None:
            created.append(existing)
            continue

        older_open = list(
            await session.scalars(
                select(PlanSourceConflict)
                .where(
                    PlanSourceConflict.owner_id == owner_id,
                    PlanSourceConflict.plan_version_id
                    == baseline.plan_version_id,
                    PlanSourceConflict.source_id == source.id,
                    PlanSourceConflict.status == "open",
                )
                .with_for_update()
            )
        )
        for conflict in older_open:
            conflict.status = "resolved"
            conflict.resolution = "superseded"
            conflict.resolved_at = at
            conflict.version += 1

        affected_block_ids = sorted(
            {
                block_id
                for change in changes
                for block_id in change["affected_block_ids"]
            }
        )
        conflict = PlanSourceConflict(
            owner_id=owner_id,
            plan_id=baseline.plan_id,
            plan_version_id=baseline.plan_version_id,
            source_id=source.id,
            source_generation=source_generation,
            status="open",
            resolution=None,
            changes=changes,
            affected_block_ids=affected_block_ids,
            details_limited=details_limited,
            version=1,
            detected_at=at,
            resolved_at=None,
        )
        session.add(conflict)
        current_plan.updated_at = at
        emit_change(
            session,
            owner_id,
            "plan",
            current_plan.id,
            "updated",
            current_plan.version,
        )
        created.append(conflict)
    if created:
        await session.flush()
    return created


async def open_source_conflict_summary(
    session: AsyncSession,
    owner_id: UUID,
    plan_version_id: UUID,
    plan_version: int,
) -> dict[str, Any]:
    summaries = await open_source_conflict_summaries(
        session,
        owner_id,
        {plan_version_id: plan_version},
    )
    return summaries.get(plan_version_id, _empty_summary())


async def open_source_conflict_summaries(
    session: AsyncSession,
    owner_id: UUID,
    plan_versions: dict[UUID, int],
) -> dict[UUID, dict[str, Any]]:
    if not plan_versions:
        return {}
    rows = list(
        (
            await session.execute(
                select(PlanSourceConflict, CalendarSource.name)
                .join(
                    CalendarSource,
                    and_(
                        CalendarSource.id == PlanSourceConflict.source_id,
                        CalendarSource.owner_id == PlanSourceConflict.owner_id,
                    ),
                )
                .where(
                    PlanSourceConflict.owner_id == owner_id,
                    PlanSourceConflict.plan_version_id.in_(plan_versions),
                    PlanSourceConflict.status == "open",
                    CalendarSource.deleted_at.is_(None),
                    CalendarSource.purged_at.is_(None),
                )
                .order_by(
                    PlanSourceConflict.plan_version_id,
                    PlanSourceConflict.detected_at.desc(),
                    PlanSourceConflict.id,
                )
            )
        ).all()
    )
    items_by_version: dict[UUID, list[dict[str, Any]]] = {}
    for conflict, source_name in rows:
        items_by_version.setdefault(conflict.plan_version_id, []).append(
            source_conflict_result(
                conflict,
                source_name,
                plan_versions[conflict.plan_version_id],
            )
        )
    return {
        version_id: _summary(items_by_version.get(version_id, []))
        for version_id in plan_versions
    }


async def supersede_open_source_conflicts(
    session: AsyncSession,
    owner_id: UUID,
    source_id: UUID,
    resolved_at: datetime,
) -> int:
    conflicts = list(
        await session.scalars(
            select(PlanSourceConflict)
            .where(
                PlanSourceConflict.owner_id == owner_id,
                PlanSourceConflict.source_id == source_id,
                PlanSourceConflict.status == "open",
            )
            .order_by(PlanSourceConflict.id)
            .with_for_update()
        )
    )
    for conflict in conflicts:
        conflict.status = "resolved"
        conflict.resolution = "superseded"
        conflict.resolved_at = resolved_at
        conflict.version += 1
    return len(conflicts)


async def resolve_source_conflicts(
    session: AsyncSession,
    owner_id: UUID,
    plan_id: UUID,
    data: ResolvePlanSourceConflictsInput,
) -> dict[str, Any]:
    scope = f"{RESOLVE_SOURCE_CONFLICTS_SCOPE}:{plan_id}"
    await _advisory_lock(
        session,
        f"plan-source-conflicts:{owner_id}:{data.operation_id}",
    )
    replay = await replay_operation(
        session,
        owner_id,
        data.operation_id,
        scope,
        data,
    )
    if replay is not None:
        return replay

    await lock_planning_revision(session, owner_id, write=True)
    plan = await session.scalar(
        select(Plan)
        .where(Plan.id == plan_id, Plan.owner_id == owner_id)
        .with_for_update()
    )
    if plan is None:
        raise AppError("plan_not_found", "未找到此周计划。", status=404)
    if plan.version != data.expected_plan_version:
        raise AppError(
            "plan_source_conflict_plan_changed",
            "当前计划版本已变化，请重新读取来源冲突。",
            status=409,
            retryable=True,
            details={"current_plan_version": plan.version},
        )

    conflicts = list(
        await session.scalars(
            select(PlanSourceConflict)
            .where(
                PlanSourceConflict.owner_id == owner_id,
                PlanSourceConflict.plan_id == plan.id,
                PlanSourceConflict.plan_version_id == plan.current_version_id,
                PlanSourceConflict.id.in_(data.conflict_ids),
            )
            .order_by(PlanSourceConflict.id)
            .with_for_update()
        )
    )
    if len(conflicts) != len(data.conflict_ids):
        raise AppError(
            "plan_source_conflict_not_found",
            "未找到当前计划的来源冲突。",
            status=404,
        )
    if any(conflict.status != "open" for conflict in conflicts):
        raise AppError(
            "plan_source_conflict_changed",
            "来源冲突已在其他页面或设备处理，请重新读取。",
            status=409,
            retryable=True,
            details={"current_plan_version": plan.version},
        )

    resolved_at = now()
    for conflict in conflicts:
        conflict.status = "resolved"
        conflict.resolution = data.resolution
        conflict.resolved_at = resolved_at
        conflict.version += 1
    plan.updated_at = resolved_at
    emit_change(
        session,
        owner_id,
        "plan",
        plan.id,
        "updated",
        plan.version,
    )
    await session.flush()
    summary = await open_source_conflict_summary(
        session,
        owner_id,
        plan.current_version_id,
        plan.version,
    )
    result = {
        "operation_id": str(data.operation_id),
        "plan_id": str(plan.id),
        "plan_version": plan.version,
        "source_conflicts": summary,
    }
    save_operation(
        session,
        owner_id,
        data.operation_id,
        scope,
        data,
        result,
    )
    await session.flush()
    return result


async def resolve_replanned_conflicts(
    session: AsyncSession,
    owner_id: UUID,
    plan_version_id: UUID | None,
    resolved_at: datetime,
) -> int:
    if plan_version_id is None:
        return 0
    conflicts = list(
        await session.scalars(
            select(PlanSourceConflict)
            .where(
                PlanSourceConflict.owner_id == owner_id,
                PlanSourceConflict.plan_version_id == plan_version_id,
                PlanSourceConflict.status == "open",
            )
            .with_for_update()
        )
    )
    for conflict in conflicts:
        conflict.status = "resolved"
        conflict.resolution = "replanned"
        conflict.resolved_at = resolved_at
        conflict.version += 1
    return len(conflicts)


def source_conflict_result(
    conflict: PlanSourceConflict,
    source_name: str,
    plan_version: int,
) -> dict[str, Any]:
    return {
        "id": str(conflict.id),
        "plan_id": str(conflict.plan_id),
        "plan_version_id": str(conflict.plan_version_id),
        "plan_version": plan_version,
        "source_id": str(conflict.source_id),
        "source_name": source_name,
        "source_generation": conflict.source_generation,
        "status": conflict.status,
        "resolution": conflict.resolution,
        "changes": list(conflict.changes),
        "affected_block_ids": list(conflict.affected_block_ids),
        "details_limited": conflict.details_limited,
        "version": conflict.version,
        "detected_at": _datetime_text(conflict.detected_at),
        "resolved_at": _datetime_text(conflict.resolved_at),
    }


def _summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "open_count": len(items),
        "overlap_count": sum(bool(item["affected_block_ids"]) for item in items),
        "items": items,
    }


def _empty_summary() -> dict[str, Any]:
    return _summary([])


async def _advisory_lock(session: AsyncSession, key: str) -> None:
    await session.execute(
        select(func.pg_advisory_xact_lock(func.hashtextextended(key, 0)))
    )


async def _source_occurrences(
    session: AsyncSession,
    owner_id: UUID,
    source_id: UUID,
    query_start: datetime,
    query_end: datetime,
) -> tuple[list[dict[str, Any]], bool]:
    try:
        items = await query_subscription_occurrences(
            session,
            owner_id,
            query_start=query_start,
            query_end=query_end,
            remaining_limit=MAX_CONFLICT_OCCURRENCES,
            source_id=source_id,
        )
    except AppError as exc:
        if exc.code != "calendar_result_too_large":
            raise
        return [], True
    return items, False


def _schedule_changes(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    blocks: list[ScheduledBlock],
) -> list[dict[str, Any]]:
    before_by_key = {str(item["occurrence_key"]): item for item in before}
    after_by_key = {str(item["occurrence_key"]): item for item in after}
    changes: list[dict[str, Any]] = []
    matched_keys = set(before_by_key) & set(after_by_key)
    for occurrence_key in sorted(matched_keys):
        change = _schedule_change(
            before_by_key[occurrence_key],
            after_by_key[occurrence_key],
            blocks,
        )
        if change is not None:
            changes.append(change)

    unmatched_before = [
        item
        for key, item in before_by_key.items()
        if key not in matched_keys and _blocks_time(item)
    ]
    unmatched_after = [
        item
        for key, item in after_by_key.items()
        if key not in matched_keys and _blocks_time(item)
    ]
    before_by_event = _occurrences_by_event(unmatched_before)
    after_by_event = _occurrences_by_event(unmatched_after)
    for event_id in sorted(set(before_by_event) | set(after_by_event)):
        old_items = sorted(before_by_event.get(event_id, []), key=_occurrence_sort_key)
        new_items = sorted(after_by_event.get(event_id, []), key=_occurrence_sort_key)
        paired = min(len(old_items), len(new_items))
        for index in range(paired):
            change = _schedule_change(old_items[index], new_items[index], blocks)
            if change is not None:
                changes.append(change)
        for old in old_items[paired:]:
            change = _schedule_change(old, None, blocks)
            if change is not None:
                changes.append(change)
        for new in new_items[paired:]:
            change = _schedule_change(None, new, blocks)
            if change is not None:
                changes.append(change)
    changes.sort(
        key=lambda item: (
            _change_time(item),
            item["occurrence_key"],
            item["kind"],
        )
    )
    return changes


def _schedule_change(
    old: dict[str, Any] | None,
    new: dict[str, Any] | None,
    blocks: list[ScheduledBlock],
) -> dict[str, Any] | None:
    old_blocks_time = _blocks_time(old)
    new_blocks_time = _blocks_time(new)
    if not old_blocks_time and not new_blocks_time:
        return None
    if old_blocks_time and new_blocks_time:
        if _schedule_signature(old) == _schedule_signature(new):
            return None
        kind = "moved"
    elif new_blocks_time:
        kind = "added"
    else:
        kind = "removed"
    effective = new or old
    if effective is None:
        return None
    return {
        "kind": kind,
        "occurrence_key": str(effective["occurrence_key"]),
        "event_id": str(effective["subscription_event_id"]),
        "name": str(effective["name"]),
        "before": _occurrence_snapshot(old),
        "after": _occurrence_snapshot(new),
        "affected_block_ids": (
            _affected_block_ids(new, blocks)
            if new_blocks_time and new is not None
            else []
        ),
    }


def _occurrences_by_event(
    occurrences: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for occurrence in occurrences:
        event_id = str(occurrence["subscription_event_id"])
        grouped.setdefault(event_id, []).append(occurrence)
    return grouped


def _occurrence_sort_key(occurrence: dict[str, Any]) -> tuple[str, str]:
    return (
        _datetime_text(occurrence.get("start_at")) or "",
        str(occurrence["occurrence_key"]),
    )


def _change_time(change: dict[str, Any]) -> str:
    after = change.get("after")
    before = change.get("before")
    if isinstance(after, dict) and isinstance(after.get("start_at"), str):
        return after["start_at"]
    if isinstance(before, dict) and isinstance(before.get("start_at"), str):
        return before["start_at"]
    return ""


def _blocks_time(occurrence: dict[str, Any] | None) -> bool:
    return bool(
        occurrence is not None
        and occurrence.get("busy") is True
        and occurrence.get("status") == "scheduled"
    )


def _schedule_signature(occurrence: dict[str, Any] | None) -> tuple[Any, ...]:
    if occurrence is None:
        return ()
    return (
        occurrence.get("local_start_date"),
        occurrence.get("local_start_time"),
        occurrence.get("local_end_date"),
        occurrence.get("local_end_time"),
        occurrence.get("time_zone"),
        occurrence.get("start_at"),
        occurrence.get("end_at"),
        occurrence.get("busy"),
        occurrence.get("status"),
    )


def _occurrence_snapshot(
    occurrence: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if occurrence is None:
        return None
    return {
        "name": str(occurrence["name"]),
        "local_start_date": _date_text(occurrence.get("local_start_date")),
        "local_start_time": _time_text(occurrence.get("local_start_time")),
        "local_end_date": _date_text(occurrence.get("local_end_date")),
        "local_end_time": _time_text(occurrence.get("local_end_time")),
        "time_zone": str(occurrence["time_zone"]),
        "start_at": _datetime_text(occurrence.get("start_at")),
        "end_at": _datetime_text(occurrence.get("end_at")),
        "busy": bool(occurrence["busy"]),
        "status": str(occurrence["status"]),
    }


def _affected_block_ids(
    occurrence: dict[str, Any],
    blocks: list[ScheduledBlock],
) -> list[str]:
    start_at = occurrence.get("start_at")
    end_at = occurrence.get("end_at")
    if not isinstance(start_at, datetime) or not isinstance(end_at, datetime):
        return []
    return [
        str(block.id)
        for block in blocks
        if start_at < block.ends_at and end_at > block.starts_at
    ]


def _datetime_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _date_text(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _time_text(value: time | None) -> str | None:
    return value.isoformat() if value is not None else None
