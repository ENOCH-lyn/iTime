from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from sqlalchemy import select

from itime.ai.action_proposals import (
    apply_proposal,
    get_proposal,
    proposal_result,
    reject_proposal,
)
from itime.ai.conversation_models import Conversation, ConversationMessage
from itime.ai.conversation_schemas import ConversationMessageCreateInput
from itime.ai.conversation_service import enqueue_message
from itime.ai.models import AIJob
from itime.auth.security import now
from itime.auth.session import authorized
from itime.errors import AppError
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
        proposal = await get_proposal(
            identity.session,
            identity.user.id,
            proposal_id,
        )
        source_message = (
            await identity.session.get(ConversationMessage, proposal.source_message_id)
            if proposal.source_message_id is not None
            else None
        )
        result = await apply_proposal(
            identity.session,
            identity.user.id,
            proposal_id,
            expected_version=data.expected_proposal_version,
            current_time=now(),
        )
        if (
            proposal.tool_name in {"create_term", "update_term"}
            and source_message is not None
            and any(word in source_message.content for word in ("导入", "课表", "课程"))
        ):
            conversation = await identity.session.get(
                Conversation,
                proposal.conversation_id,
            )
            prior_job = await identity.session.scalar(
                select(AIJob)
                .where(
                    AIJob.owner_id == identity.user.id,
                    AIJob.conversation_id == proposal.conversation_id,
                    AIJob.capability == "conversation",
                )
                .order_by(AIJob.created_at.desc())
                .limit(1)
            )
            if conversation is not None and conversation.archived_at is None:
                image_media_id = (
                    UUID(str(prior_job.input_summary["image_media_id"]))
                    if prior_job is not None
                    and prior_job.input_summary.get("image_media_id")
                    else None
                )
                content = "学期已创建。请继续完成刚才的课表导入。"
                operation_id = uuid4()
                try:
                    continuation = await enqueue_message(
                        identity.session,
                        identity.user.id,
                        conversation.id,
                        operation_id=operation_id,
                        expected_version=conversation.version,
                        content=content,
                        operation_data=ConversationMessageCreateInput(
                            operation_id=operation_id,
                            expected_conversation_version=conversation.version,
                            content=content,
                            response_mode="auto",
                            image_media_id=image_media_id,
                        ),
                        timeout_seconds=request.app.state.settings.ai_job_timeout_seconds,
                        image_media_id=image_media_id,
                    )
                    result["continuation_job_id"] = continuation["job"]["id"]
                except AppError as exc:
                    result["continuation_error"] = exc.message
        return result


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
