from uuid import UUID

from fastapi import APIRouter, Request

from itime.ai.agent_proposal_service import (
    apply_agent_proposal,
    get_agent_proposal,
    reject_agent_proposal,
)
from itime.auth.session import authorized
from itime.planning.schemas import ProposalActionInput

router = APIRouter(prefix="/api/v1/ai/action-proposals", tags=["AI Agent 提案"])


@router.get("/{proposal_id}")
async def get_proposal(proposal_id: UUID, request: Request) -> dict:
    async with authorized(request) as identity:
        return {
            "proposal": await get_agent_proposal(
                identity.session,
                identity.user.id,
                proposal_id,
            )
        }


@router.post("/{proposal_id}/apply")
async def apply_proposal(
    proposal_id: UUID,
    data: ProposalActionInput,
    request: Request,
) -> dict:
    async with authorized(request, write=True) as identity:
        return await apply_agent_proposal(
            identity.session,
            identity.user.id,
            proposal_id,
            data,
        )


@router.post("/{proposal_id}/reject")
async def reject_proposal(
    proposal_id: UUID,
    data: ProposalActionInput,
    request: Request,
) -> dict:
    async with authorized(request, write=True) as identity:
        return await reject_agent_proposal(
            identity.session,
            identity.user.id,
            proposal_id,
            data,
        )
