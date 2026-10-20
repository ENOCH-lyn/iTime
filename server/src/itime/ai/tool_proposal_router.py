from uuid import UUID

from fastapi import APIRouter, Request

from itime.ai.action_proposals import (
    apply_proposal,
    get_proposal,
    proposal_result,
    reject_proposal,
)
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


@router.post("/{proposal_id}/apply")
async def apply_tool_proposal(
    proposal_id: UUID,
    data: ProposalActionInput,
    request: Request,
) -> dict:
    async with authorized(request, write=True) as identity:
        return await apply_proposal(
            identity.session,
            identity.user.id,
            proposal_id,
            expected_version=data.expected_proposal_version,
            current_time=now(),
        )


@router.post("/{proposal_id}/reject")
async def reject_tool_proposal(
    proposal_id: UUID,
    data: ProposalActionInput,
    request: Request,
) -> dict:
    async with authorized(request, write=True) as identity:
        return await reject_proposal(
            identity.session,
            identity.user.id,
            proposal_id,
            expected_version=data.expected_proposal_version,
            current_time=now(),
        )
