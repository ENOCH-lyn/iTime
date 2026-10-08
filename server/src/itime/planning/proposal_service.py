from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import ValidationError
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from itime.ai.job_service import job_result
from itime.ai.models import AIJob
from itime.auth.security import now
from itime.errors import AppError
from itime.planning.models import (
    Plan,
    PlanningInputRevision,
    PlanningInputSnapshot,
    PlanUnscheduledItem,
    PlanVersion,
    Proposal,
    ProposalChange,
    ScheduledBlock,
)
from itime.planning.plan_service import create_plan_version, plan_version_result
from itime.planning.schemas import (
    PlanningRequestFields,
    ProposalActionInput,
    ProposalEffectiveStatus,
)
from itime.planning.snapshot_service import lock_planning_revision, planning_context
from itime.planning.validation import NormalizedPlan, validate_plan_blocks
from itime.tasks.service import replay_operation, save_operation

APPLY_OPERATION_SCOPE = "planning.proposal.apply"
REJECT_OPERATION_SCOPE = "planning.proposal.reject"
PROPOSAL_LIFETIME = timedelta(days=7)


async def list_proposals(
    session: AsyncSession,
    owner_id: UUID,
    *,
    before_time: datetime | None,
    before_id: UUID | None,
    limit: int,
) -> dict:
    statement = (
        select(Proposal, PlanningInputSnapshot)
        .join(
            PlanningInputSnapshot,
            and_(
                PlanningInputSnapshot.id == Proposal.input_snapshot_id,
                PlanningInputSnapshot.owner_id == Proposal.owner_id,
            ),
        )
        .where(Proposal.owner_id == owner_id, Proposal.kind == "plan")
    )
    if before_time is not None:
        statement = statement.where(
            or_(
                Proposal.created_at < before_time,
                and_(
                    Proposal.created_at == before_time,
                    Proposal.id < before_id,
                ),
            )
        )
    rows = list(
        (
            await session.execute(
                statement.order_by(
                    Proposal.created_at.desc(),
                    Proposal.id.desc(),
                ).limit(limit + 1)
            )
        ).all()
    )
    page = rows[:limit]
    revision, plans = await _current_states(
        session,
        owner_id,
        [snapshot.week_start_date for _, snapshot in page],
    )
    items = [
        _proposal_result(
            proposal,
            snapshot,
            revision=revision,
            current_plan=plans.get(snapshot.week_start_date),
        )
        for proposal, snapshot in page
    ]
    return {
        "items": items,
        "next_cursor": (
            {
                "before_time": page[-1][0].created_at,
                "before_id": page[-1][0].id,
            }
            if len(rows) > limit
            else None
        ),
    }


async def proposal_detail(
    session: AsyncSession,
    owner_id: UUID,
    proposal_id: UUID,
) -> dict:
    proposal, snapshot = await _proposal_and_snapshot(
        session,
        owner_id,
        proposal_id,
    )
    revision, plans = await _current_states(
        session,
        owner_id,
        [snapshot.week_start_date],
    )
    changes = list(
        await session.scalars(
            select(ProposalChange)
            .where(
                ProposalChange.owner_id == owner_id,
                ProposalChange.proposal_id == proposal.id,
            )
            .order_by(ProposalChange.position, ProposalChange.id)
        )
    )
    job = await session.scalar(
        select(AIJob).where(
            AIJob.id == proposal.source_job_id,
            AIJob.owner_id == owner_id,
        )
    )
    if job is None or job.planning_snapshot_id != snapshot.id:
        raise _invalid_proposal()
    result = _proposal_result(
        proposal,
        snapshot,
        revision=revision,
        current_plan=plans.get(snapshot.week_start_date),
    )
    result.update(
        {
            "payload": proposal.payload,
            "changes": [_change_result(change) for change in changes],
            "job": job_result(job),
        }
    )
    return result


async def apply_proposal(
    session: AsyncSession,
    owner_id: UUID,
    proposal_id: UUID,
    data: ProposalActionInput,
) -> dict:
    scope = f"{APPLY_OPERATION_SCOPE}:{proposal_id}"
    await _advisory_lock(session, f"proposal-operation:{owner_id}:{data.operation_id}")
    replay = await replay_operation(
        session,
        owner_id,
        data.operation_id,
        scope,
        data,
    )
    if replay is not None:
        return replay

    proposal, snapshot = await _locked_proposal_and_snapshot(
        session,
        owner_id,
        proposal_id,
    )
    await _advisory_lock(session, f"planning-owner:{owner_id}")
    revision = await lock_planning_revision(session, owner_id, write=True)
    current_plan = await _locked_plan(session, owner_id, snapshot)

    if proposal.status == "applied":
        _check_terminal_expected_version(proposal, data.expected_proposal_version)
        result = await _applied_result(
            session,
            owner_id,
            proposal,
            snapshot,
            revision,
            current_plan,
            data.operation_id,
        )
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
    if proposal.status == "rejected":
        raise AppError(
            "proposal_rejected",
            "此提案已经拒绝，不能再确认。",
            status=409,
            details={"current_version": proposal.version},
        )
    _check_expected_version(proposal, data.expected_proposal_version)
    expires_at = _expiry_deadline(proposal)
    if expires_at <= now():
        raise AppError(
            "proposal_expired",
            "此提案已超过 7 天有效期，请重新生成。",
            status=409,
            details={
                "current_version": proposal.version,
                "expires_at": _datetime_result(expires_at),
            },
        )

    conflict = _conflict_details(snapshot, revision.revision, current_plan)
    if conflict is not None:
        raise _proposal_conflicted(conflict)

    current_context = await _rebuild_current_context(
        session,
        owner_id,
        snapshot,
    )
    normalized = _normalized_payload(proposal)
    try:
        validate_plan_blocks(
            current_context,
            list(normalized.blocks),
            list(normalized.unscheduled_items),
        )
    except AppError as error:
        raise _proposal_conflicted(
            {
                "categories": ["hard_constraints"],
                "validation_code": error.code,
                "snapshot_revision": snapshot.revision,
                "current_revision": revision.revision,
            }
        ) from error

    internal_operation_id = uuid5(
        NAMESPACE_URL,
        (
            f"itime:proposal-plan-version:{owner_id}:"
            f"{proposal.id}:{data.operation_id}"
        ),
    )
    created = await create_plan_version(
        session,
        owner_id,
        normalized,
        snapshot=current_context,
        source="ai",
        proposal_id=proposal.id,
        expected_revision=snapshot.revision,
        expected_plan_version=snapshot.current_plan_version or 0,
        operation_id=internal_operation_id,
    )
    applied_version = created["plan"]["current_version"]
    applied_at = now()
    proposal.status = "applied"
    proposal.applied_at = applied_at
    proposal.applied_plan_id = UUID(created["plan"]["id"])
    proposal.applied_plan_version_id = UUID(applied_version["id"])
    proposal.version += 1
    proposal.updated_at = applied_at
    await session.flush()

    await session.refresh(revision)
    result = {
        "operation_id": str(data.operation_id),
        "proposal": _proposal_result(
            proposal,
            snapshot,
            revision=revision.revision,
            current_plan=await _locked_plan(session, owner_id, snapshot),
        ),
        "application": _application_result(applied_version),
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


async def reject_proposal(
    session: AsyncSession,
    owner_id: UUID,
    proposal_id: UUID,
    data: ProposalActionInput,
) -> dict:
    scope = f"{REJECT_OPERATION_SCOPE}:{proposal_id}"
    await _advisory_lock(session, f"proposal-operation:{owner_id}:{data.operation_id}")
    replay = await replay_operation(
        session,
        owner_id,
        data.operation_id,
        scope,
        data,
    )
    if replay is not None:
        return replay

    proposal, snapshot = await _locked_proposal_and_snapshot(
        session,
        owner_id,
        proposal_id,
    )
    if proposal.status == "applied":
        raise AppError(
            "proposal_applied",
            "此提案已经应用，不能再拒绝。",
            status=409,
            details={"current_version": proposal.version},
        )
    if proposal.status == "rejected":
        _check_terminal_expected_version(proposal, data.expected_proposal_version)
    if proposal.status == "pending":
        _check_expected_version(proposal, data.expected_proposal_version)
        rejected_at = now()
        proposal.status = "rejected"
        proposal.rejected_at = rejected_at
        proposal.version += 1
        proposal.updated_at = rejected_at
        await session.flush()

    revision, plans = await _current_states(
        session,
        owner_id,
        [snapshot.week_start_date],
    )
    result = {
        "operation_id": str(data.operation_id),
        "proposal": _proposal_result(
            proposal,
            snapshot,
            revision=revision,
            current_plan=plans.get(snapshot.week_start_date),
        ),
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


async def _proposal_and_snapshot(
    session: AsyncSession,
    owner_id: UUID,
    proposal_id: UUID,
) -> tuple[Proposal, PlanningInputSnapshot]:
    row = (
        await session.execute(
            select(Proposal, PlanningInputSnapshot)
            .join(
                PlanningInputSnapshot,
                and_(
                    PlanningInputSnapshot.id == Proposal.input_snapshot_id,
                    PlanningInputSnapshot.owner_id == Proposal.owner_id,
                ),
            )
            .where(
                Proposal.id == proposal_id,
                Proposal.owner_id == owner_id,
            )
        )
    ).one_or_none()
    if row is None:
        raise AppError("proposal_not_found", "未找到此提案。", status=404)
    return row


async def _locked_proposal_and_snapshot(
    session: AsyncSession,
    owner_id: UUID,
    proposal_id: UUID,
) -> tuple[Proposal, PlanningInputSnapshot]:
    proposal = await session.scalar(
        select(Proposal)
        .where(
            Proposal.id == proposal_id,
            Proposal.owner_id == owner_id,
        )
        .with_for_update()
    )
    if proposal is None:
        raise AppError("proposal_not_found", "未找到此提案。", status=404)
    snapshot = await session.scalar(
        select(PlanningInputSnapshot).where(
            PlanningInputSnapshot.id == proposal.input_snapshot_id,
            PlanningInputSnapshot.owner_id == owner_id,
        )
    )
    if snapshot is None:
        raise _invalid_proposal()
    job_snapshot_id = await session.scalar(
        select(AIJob.planning_snapshot_id).where(
            AIJob.id == proposal.source_job_id,
            AIJob.owner_id == owner_id,
        )
    )
    if job_snapshot_id != snapshot.id:
        raise _invalid_proposal()
    return proposal, snapshot


async def _locked_plan(
    session: AsyncSession,
    owner_id: UUID,
    snapshot: PlanningInputSnapshot,
) -> Plan | None:
    return await session.scalar(
        select(Plan)
        .where(
            Plan.owner_id == owner_id,
            Plan.week_start_date == snapshot.week_start_date,
        )
        .with_for_update()
    )


async def _current_states(
    session: AsyncSession,
    owner_id: UUID,
    week_starts: list[date],
) -> tuple[int, dict[date, Plan]]:
    revision = await session.get(PlanningInputRevision, owner_id)
    unique_weeks = set(week_starts)
    plans = (
        {
            plan.week_start_date: plan
            for plan in await session.scalars(
                select(Plan).where(
                    Plan.owner_id == owner_id,
                    Plan.week_start_date.in_(unique_weeks),
                )
            )
        }
        if unique_weeks
        else {}
    )
    return revision.revision if revision is not None else 0, plans


async def _rebuild_current_context(
    session: AsyncSession,
    owner_id: UUID,
    snapshot: PlanningInputSnapshot,
) -> dict:
    try:
        request = PlanningRequestFields(
            scope=snapshot.scope,
            target_date=snapshot.target_date,
            density=snapshot.density,
            buffer_minutes=snapshot.buffer_minutes,
            availability_start=snapshot.availability_start,
            availability_end=snapshot.availability_end,
            task_ids=[UUID(value) for value in snapshot.selected_task_ids],
            instruction=snapshot.user_instructions,
        )
        return await planning_context(session, owner_id, request)
    except (ValidationError, ValueError) as error:
        raise _invalid_proposal() from error
    except AppError as error:
        if error.status >= 500:
            raise
        raise _proposal_conflicted(
            {
                "categories": ["current_context"],
                "validation_code": error.code,
                "snapshot_revision": snapshot.revision,
            }
        ) from error


def _normalized_payload(proposal: Proposal) -> NormalizedPlan:
    try:
        return NormalizedPlan.model_validate(proposal.payload)
    except ValidationError as error:
        raise _invalid_proposal() from error


def _check_expected_version(proposal: Proposal, expected: int) -> None:
    if proposal.version != expected:
        raise _proposal_version_conflict(proposal)


def _check_terminal_expected_version(proposal: Proposal, expected: int) -> None:
    if expected not in {proposal.version - 1, proposal.version}:
        raise _proposal_version_conflict(proposal)


def _proposal_version_conflict(proposal: Proposal) -> AppError:
    return AppError(
        "proposal_version_conflict",
        "此提案已在其他页面或设备更新，请刷新后重试。",
        status=409,
        details={"current_version": proposal.version},
    )


def _conflict_details(
    snapshot: PlanningInputSnapshot,
    current_revision: int,
    current_plan: Plan | None,
) -> dict[str, Any] | None:
    categories: list[str] = []
    if current_revision != snapshot.revision:
        categories.append("planning_revision")
    base_plan_matches = (
        snapshot.current_plan_id is None
        and snapshot.current_plan_version is None
        and current_plan is None
    ) or (
        current_plan is not None
        and current_plan.id == snapshot.current_plan_id
        and current_plan.version == snapshot.current_plan_version
    )
    if not base_plan_matches:
        categories.append("base_plan")
    if not categories:
        return None
    return {
        "categories": categories,
        "snapshot_revision": snapshot.revision,
        "current_revision": current_revision,
        "base_plan": {
            "id": str(snapshot.current_plan_id) if snapshot.current_plan_id else None,
            "version": snapshot.current_plan_version,
        },
        "current_plan": (
            {"id": str(current_plan.id), "version": current_plan.version}
            if current_plan is not None
            else None
        ),
    }


def _proposal_result(
    proposal: Proposal,
    snapshot: PlanningInputSnapshot,
    *,
    revision: int,
    current_plan: Plan | None,
) -> dict:
    effective_status, conflict = _effective_status(
        proposal,
        snapshot,
        revision,
        current_plan,
    )
    return {
        "id": str(proposal.id),
        "kind": proposal.kind,
        "source_job_id": str(proposal.source_job_id),
        "conversation_id": (
            str(proposal.conversation_id) if proposal.conversation_id else None
        ),
        "source_message_id": (
            str(proposal.source_message_id) if proposal.source_message_id else None
        ),
        "input_snapshot_id": str(proposal.input_snapshot_id),
        "schema_version": proposal.schema_version,
        "status": proposal.status,
        "effective_status": effective_status,
        "title": proposal.title,
        "summary": proposal.summary,
        "assumptions": list(proposal.assumptions),
        "warnings": list(proposal.warnings),
        "expires_at": _datetime_result(_expiry_deadline(proposal)),
        "applied_at": _optional_datetime_result(proposal.applied_at),
        "rejected_at": _optional_datetime_result(proposal.rejected_at),
        "applied_plan_id": (
            str(proposal.applied_plan_id) if proposal.applied_plan_id else None
        ),
        "applied_plan_version_id": (
            str(proposal.applied_plan_version_id)
            if proposal.applied_plan_version_id
            else None
        ),
        "version": proposal.version,
        "created_at": _datetime_result(proposal.created_at),
        "updated_at": _datetime_result(proposal.updated_at),
        "input_snapshot": {
            "id": str(snapshot.id),
            "revision": snapshot.revision,
            "scope": snapshot.scope,
            "target_date": snapshot.target_date.isoformat(),
            "week_start_date": snapshot.week_start_date.isoformat(),
            "week_end_date": snapshot.week_end_date.isoformat(),
            "time_zone": snapshot.time_zone,
            "density": snapshot.density,
            "buffer_minutes": snapshot.buffer_minutes,
            "availability_start": snapshot.availability_start.isoformat(),
            "availability_end": snapshot.availability_end.isoformat(),
            "current_plan_id": (
                str(snapshot.current_plan_id) if snapshot.current_plan_id else None
            ),
            "current_plan_version": snapshot.current_plan_version,
        },
        "current_state": {
            "revision": revision,
            "plan": (
                {"id": str(current_plan.id), "version": current_plan.version}
                if current_plan is not None
                else None
            ),
        },
        "conflict": conflict,
    }


def _effective_status(
    proposal: Proposal,
    snapshot: PlanningInputSnapshot,
    revision: int,
    current_plan: Plan | None,
) -> tuple[ProposalEffectiveStatus, dict[str, Any] | None]:
    if proposal.status in {"applied", "rejected"}:
        return proposal.status, None
    if _expiry_deadline(proposal) <= now():
        return "expired", None
    conflict = _conflict_details(snapshot, revision, current_plan)
    if conflict is not None:
        return "conflicted", conflict
    return "pending", None


async def _applied_result(
    session: AsyncSession,
    owner_id: UUID,
    proposal: Proposal,
    snapshot: PlanningInputSnapshot,
    revision: PlanningInputRevision,
    current_plan: Plan | None,
    operation_id: UUID,
) -> dict:
    if proposal.applied_plan_id is None or proposal.applied_plan_version_id is None:
        raise _invalid_proposal()
    version = await session.scalar(
        select(PlanVersion).where(
            PlanVersion.id == proposal.applied_plan_version_id,
            PlanVersion.plan_id == proposal.applied_plan_id,
            PlanVersion.owner_id == owner_id,
            PlanVersion.proposal_id == proposal.id,
        )
    )
    if version is None:
        raise _invalid_proposal()
    blocks = list(
        await session.scalars(
            select(ScheduledBlock)
            .where(
                ScheduledBlock.owner_id == owner_id,
                ScheduledBlock.plan_version_id == version.id,
            )
            .order_by(ScheduledBlock.position)
        )
    )
    unscheduled = list(
        await session.scalars(
            select(PlanUnscheduledItem)
            .where(
                PlanUnscheduledItem.owner_id == owner_id,
                PlanUnscheduledItem.plan_version_id == version.id,
            )
            .order_by(PlanUnscheduledItem.position)
        )
    )
    serialized_version = plan_version_result(version, blocks, unscheduled)
    return {
        "operation_id": str(operation_id),
        "proposal": _proposal_result(
            proposal,
            snapshot,
            revision=revision.revision,
            current_plan=current_plan,
        ),
        "application": _application_result(serialized_version),
    }


def _application_result(version: dict) -> dict:
    return {
        "plan_id": version["plan_id"],
        "plan_version_id": version["id"],
        "plan_version": version["version"],
        "version": version,
    }


def _change_result(change: ProposalChange) -> dict:
    return {
        "id": str(change.id),
        "position": change.position,
        "action": change.action,
        "entity_type": change.entity_type,
        "target_id": str(change.target_id) if change.target_id else None,
        "before_data": change.before_data,
        "after_data": change.after_data,
        "created_at": _datetime_result(change.created_at),
    }


def _proposal_conflicted(details: dict[str, Any]) -> AppError:
    return AppError(
        "proposal_conflicted",
        "规划输入或基础计划已变化，请重新生成提案。",
        status=409,
        details=details,
    )


def _invalid_proposal() -> AppError:
    return AppError(
        "proposal_payload_invalid",
        "提案结构不完整或已损坏，请重新生成。",
        status=409,
    )


async def _advisory_lock(session: AsyncSession, key: str) -> None:
    await session.execute(
        select(func.pg_advisory_xact_lock(func.hashtextextended(key, 0)))
    )


def _datetime_result(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _optional_datetime_result(value: datetime | None) -> str | None:
    return _datetime_result(value) if value is not None else None


def _expiry_deadline(proposal: Proposal) -> datetime:
    return min(proposal.expires_at, proposal.created_at + PROPOSAL_LIFETIME)
