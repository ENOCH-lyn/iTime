from uuid import UUID

from fastapi import APIRouter, Request
from sqlalchemy import select

from itime.ai.action_proposals import (
    apply_proposal,
    get_proposal,
    proposal_result,
    reject_proposal,
)
from itime.ai.agent_models import AgentRun
from itime.ai.conversation_service import enqueue_agent_resume
from itime.auth.security import now
from itime.auth.session import authorized
from itime.planning.schemas import ProposalActionInput

router = APIRouter(prefix="/api/v1/ai/tool-proposals", tags=["AI 工具预览"])


@router.get("/{proposal_id}")
async def read_proposal(proposal_id: UUID, request: Request) -> dict:
    async with authorized(request) as identity:
        proposal = await get_proposal(
            identity.session,
            identity.user.id,
            proposal_id,
        )
        return {"proposal": proposal_result(proposal)}


async def _waiting_run(session, owner_id: UUID, proposal_id: UUID) -> AgentRun | None:
    return await session.scalar(
        select(AgentRun).where(
            AgentRun.owner_id == owner_id,
            AgentRun.pending_proposal_id == proposal_id,
            AgentRun.status == "waiting_approval",
        )
    )


@router.post("/{proposal_id}/apply")
async def apply_tool_proposal(
    proposal_id: UUID,
    data: ProposalActionInput,
    request: Request,
) -> dict:
    async with authorized(request, write=True) as identity:
        proposal = await get_proposal(
            identity.session,
            identity.user.id,
            proposal_id,
        )
        run = await _waiting_run(identity.session, identity.user.id, proposal_id)
        result = await apply_proposal(
            identity.session,
            identity.user.id,
            proposal_id,
            expected_version=data.expected_proposal_version,
            current_time=now(),
        )
        if run is not None:
            result["agent_resume"] = await enqueue_agent_resume(
                identity.session,
                identity.user.id,
                proposal=proposal,
                decision="approved",
                timeout_seconds=request.app.state.settings.ai_job_timeout_seconds,
            )
        return result


@router.post("/{proposal_id}/reject")
async def reject_tool_proposal(
    proposal_id: UUID,
    data: ProposalActionInput,
    request: Request,
) -> dict:
    async with authorized(request, write=True) as identity:
        proposal = await get_proposal(
            identity.session,
            identity.user.id,
            proposal_id,
        )
        run = await _waiting_run(identity.session, identity.user.id, proposal_id)
        result = await reject_proposal(
            identity.session,
            identity.user.id,
            proposal_id,
            expected_version=data.expected_proposal_version,
            current_time=now(),
        )
        if run is not None:
            result["agent_resume"] = await enqueue_agent_resume(
                identity.session,
                identity.user.id,
                proposal=proposal,
                decision="rejected",
                timeout_seconds=request.app.state.settings.ai_job_timeout_seconds,
            )
        return result
