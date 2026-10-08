from datetime import time
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from itime.ai.conversation_provider import AgentTaskPlan
from itime.auth.security import now
from itime.errors import AppError
from itime.planning.job_service import planning_snapshot_payload
from itime.planning.models import Proposal, ProposalChange
from itime.planning.plan_service import create_plan_version
from itime.planning.schemas import PlanningRequestFields, ProposalActionInput
from itime.planning.snapshot_service import build_planning_snapshot
from itime.planning.validation import (
    PlanningBlockOutput,
    PlanningToolOutput,
    normalize_plan_payload,
)
from itime.tasks.models import Task
from itime.tasks.service import emit_change, replay_operation, save_operation

APPLY_SCOPE = "agent_proposal.apply"
REJECT_SCOPE = "agent_proposal.reject"


def _datetime(value) -> str | None:
    return value.isoformat() if value is not None else None


def _change_result(change: ProposalChange) -> dict:
    return {
        "id": str(change.id),
        "position": change.position,
        "action": change.action,
        "entity_type": change.entity_type,
        "target_id": str(change.target_id) if change.target_id else None,
        "before_data": change.before_data,
        "after_data": change.after_data,
    }


async def _proposal(
    session: AsyncSession,
    owner_id: UUID,
    proposal_id: UUID,
    *,
    lock: bool = False,
) -> Proposal:
    statement = select(Proposal).where(
        Proposal.id == proposal_id,
        Proposal.owner_id == owner_id,
        Proposal.kind == "task_plan",
    )
    if lock:
        statement = statement.with_for_update()
    proposal = await session.scalar(statement)
    if proposal is None:
        raise AppError("proposal_not_found", "未找到此提案。", status=404)
    return proposal


async def proposal_result(
    session: AsyncSession,
    owner_id: UUID,
    proposal: Proposal,
) -> dict:
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
    effective_status = proposal.status
    if proposal.status == "pending" and proposal.expires_at <= now():
        effective_status = "expired"
    return {
        "id": str(proposal.id),
        "kind": proposal.kind,
        "status": proposal.status,
        "effective_status": effective_status,
        "title": proposal.title,
        "summary": proposal.summary,
        "assumptions": list(proposal.assumptions),
        "warnings": list(proposal.warnings),
        "payload": proposal.payload,
        "changes": [_change_result(change) for change in changes],
        "version": proposal.version,
        "expires_at": _datetime(proposal.expires_at),
        "applied_at": _datetime(proposal.applied_at),
        "rejected_at": _datetime(proposal.rejected_at),
        "applied_plan_id": (
            str(proposal.applied_plan_id) if proposal.applied_plan_id else None
        ),
        "applied_plan_version_id": (
            str(proposal.applied_plan_version_id)
            if proposal.applied_plan_version_id
            else None
        ),
        "created_at": _datetime(proposal.created_at),
        "updated_at": _datetime(proposal.updated_at),
    }


async def get_agent_proposal(
    session: AsyncSession,
    owner_id: UUID,
    proposal_id: UUID,
) -> dict:
    return await proposal_result(
        session,
        owner_id,
        await _proposal(session, owner_id, proposal_id),
    )


def _check_pending(proposal: Proposal, expected_version: int) -> None:
    if proposal.version != expected_version:
        raise AppError(
            "proposal_version_conflict",
            "此提案已在其他页面更新，请刷新后重试。",
            status=409,
            details={"current_version": proposal.version},
        )
    if proposal.status == "rejected":
        raise AppError(
            "proposal_rejected",
            "此提案已经拒绝，不能再确认。",
            status=409,
        )
    if proposal.expires_at <= now():
        raise AppError(
            "proposal_expired",
            "此提案已过期，请重新生成。",
            status=409,
        )


async def apply_agent_proposal(
    session: AsyncSession,
    owner_id: UUID,
    proposal_id: UUID,
    data: ProposalActionInput,
) -> dict:
    scope = f"{APPLY_SCOPE}:{proposal_id}"
    replay = await replay_operation(
        session,
        owner_id,
        data.operation_id,
        scope,
        data,
    )
    if replay is not None:
        return replay
    proposal = await _proposal(session, owner_id, proposal_id, lock=True)
    if proposal.status == "applied":
        if proposal.version not in {
            data.expected_proposal_version,
            data.expected_proposal_version + 1,
        }:
            raise AppError(
                "proposal_version_conflict",
                "此提案已在其他页面更新，请刷新后重试。",
                status=409,
                details={"current_version": proposal.version},
            )
        result = {
            "operation_id": str(data.operation_id),
            "proposal": await proposal_result(session, owner_id, proposal),
        }
        save_operation(
            session,
            owner_id,
            data.operation_id,
            scope,
            data,
            result,
        )
        return result
    _check_pending(proposal, data.expected_proposal_version)
    task_plan = AgentTaskPlan.model_validate(proposal.payload)
    current = now()
    task = Task(
        id=uuid4(),
        owner_id=owner_id,
        project_id=None,
        title=task_plan.task_title,
        description=task_plan.task_description,
        estimated_minutes=task_plan.estimated_minutes,
        estimate_source="ai",
        priority=0,
        status="open",
        version=1,
        created_at=current,
        updated_at=current,
    )
    session.add(task)
    await session.flush()
    emit_change(session, owner_id, "task", task.id, "created", task.version)

    request = PlanningRequestFields(
        scope="day",
        target_date=task_plan.local_date,
        density="balanced",
        buffer_minutes=15,
        availability_start=time(0, 0),
        availability_end=time(23, 45),
        task_ids=[task.id],
        instruction=task_plan.assistant_message,
    )
    snapshot = await build_planning_snapshot(session, owner_id, request)
    candidate = next(
        (
            item
            for item in snapshot.task_candidates
            if item["task_id"] == str(task.id)
        ),
        None,
    )
    if candidate is None:
        raise AppError(
            "agent_task_plan_invalid",
            "新任务未进入规划候选，请重新生成。",
            status=409,
        )
    normalized = normalize_plan_payload(
        planning_snapshot_payload(snapshot),
        PlanningToolOutput(
            summary=task_plan.assistant_message,
            assumptions=task_plan.assumptions,
            estimates=[],
            blocks=[
                PlanningBlockOutput(
                    candidate_key=candidate["candidate_key"],
                    local_date=task_plan.local_date,
                    starts_at=task_plan.starts_at,
                    ends_at=task_plan.ends_at,
                    step_ids=[],
                )
            ],
            unscheduled_items=[],
        ),
    )
    proposal.input_snapshot_id = snapshot.id
    internal_operation_id = uuid5(
        NAMESPACE_URL,
        f"itime:agent-task-plan:{owner_id}:{proposal.id}:{data.operation_id}",
    )
    created = await create_plan_version(
        session,
        owner_id,
        normalized,
        snapshot=snapshot,
        source="ai",
        proposal_id=proposal.id,
        expected_revision=snapshot.revision,
        expected_plan_version=snapshot.current_plan_version or 0,
        operation_id=internal_operation_id,
    )
    version = created["plan"]["current_version"]
    proposal.status = "applied"
    proposal.applied_at = current
    proposal.applied_plan_id = UUID(created["plan"]["id"])
    proposal.applied_plan_version_id = UUID(version["id"])
    proposal.version += 1
    proposal.updated_at = current
    result = {
        "operation_id": str(data.operation_id),
        "proposal": await proposal_result(session, owner_id, proposal),
        "application": {
            "task_id": str(task.id),
            "plan_id": created["plan"]["id"],
            "plan_version_id": version["id"],
        },
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


async def reject_agent_proposal(
    session: AsyncSession,
    owner_id: UUID,
    proposal_id: UUID,
    data: ProposalActionInput,
) -> dict:
    scope = f"{REJECT_SCOPE}:{proposal_id}"
    replay = await replay_operation(
        session,
        owner_id,
        data.operation_id,
        scope,
        data,
    )
    if replay is not None:
        return replay
    proposal = await _proposal(session, owner_id, proposal_id, lock=True)
    if proposal.status == "applied":
        raise AppError(
            "proposal_applied",
            "此提案已经确认，不能再拒绝。",
            status=409,
        )
    if proposal.status == "pending":
        _check_pending(proposal, data.expected_proposal_version)
        proposal.status = "rejected"
        proposal.rejected_at = now()
        proposal.updated_at = proposal.rejected_at
        proposal.version += 1
    result = {
        "operation_id": str(data.operation_id),
        "proposal": await proposal_result(session, owner_id, proposal),
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
