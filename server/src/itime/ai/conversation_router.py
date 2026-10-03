from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request
from sqlalchemy import and_, or_, select

from itime.ai.conversation_models import Conversation, ConversationMessage
from itime.ai.conversation_schemas import (
    ConversationCreateInput,
    ConversationMessageCreateInput,
    ConversationUpdateInput,
)
from itime.ai.conversation_service import (
    ACTIVE_JOB_STATES,
    active_jobs_for_conversations,
    conversation_result,
    conversation_version_conflict,
    enqueue_message,
    jobs_for_messages,
    locked_conversation,
    message_result,
)
from itime.ai.models import AIJob
from itime.auth.security import now
from itime.auth.session import authorized, check_ai_access
from itime.errors import AppError
from itime.tasks.service import replay_operation, save_operation, validate_cursor

router = APIRouter(prefix="/api/v1/ai/conversations", tags=["AI 对话"])


@router.get("")
async def list_conversations(
    request: Request,
    archived: bool = False,
    before_time: datetime | None = None,
    before_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> dict:
    validate_cursor(before_time, before_id)
    async with authorized(request) as identity:
        statement = select(Conversation).where(
            Conversation.owner_id == identity.user.id,
            (
                Conversation.archived_at.is_not(None)
                if archived
                else Conversation.archived_at.is_(None)
            ),
        )
        if before_time is not None:
            statement = statement.where(
                or_(
                    Conversation.updated_at < before_time,
                    and_(
                        Conversation.updated_at == before_time,
                        Conversation.id < before_id,
                    ),
                )
            )
        rows = list(
            await identity.session.scalars(
                statement.order_by(
                    Conversation.updated_at.desc(),
                    Conversation.id.desc(),
                ).limit(limit + 1)
            )
        )
        page = rows[:limit]
        active_jobs = await active_jobs_for_conversations(
            identity.session,
            identity.user.id,
            [conversation.id for conversation in page],
        )
        return {
            "items": [
                conversation_result(
                    conversation,
                    active_job=active_jobs.get(conversation.id),
                )
                for conversation in page
            ],
            "next_cursor": (
                {
                    "before_time": page[-1].updated_at,
                    "before_id": page[-1].id,
                }
                if len(rows) > limit
                else None
            ),
        }


@router.post("", status_code=201)
async def create_conversation(
    data: ConversationCreateInput,
    request: Request,
) -> dict:
    scope = "ai_conversation.create"
    async with authorized(request, write=True) as identity:
        replay = await replay_operation(
            identity.session,
            identity.user.id,
            data.operation_id,
            scope,
            data,
        )
        if replay is not None:
            return replay
        conversation = Conversation(
            owner_id=identity.user.id,
            title=data.title or "新对话",
            title_source="user" if data.title is not None else "auto",
        )
        identity.session.add(conversation)
        await identity.session.flush()
        result = {
            "operation_id": data.operation_id,
            "conversation": conversation_result(conversation),
        }
        save_operation(
            identity.session,
            identity.user.id,
            data.operation_id,
            scope,
            data,
            result,
        )
        return result


@router.get("/{conversation_id}")
async def get_conversation(conversation_id: UUID, request: Request) -> dict:
    async with authorized(request) as identity:
        conversation = await identity.session.scalar(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.owner_id == identity.user.id,
            )
        )
        if conversation is None:
            raise AppError("conversation_not_found", "未找到此会话。", status=404)
        active_job = await identity.session.scalar(
            select(AIJob).where(
                AIJob.owner_id == identity.user.id,
                AIJob.conversation_id == conversation.id,
                AIJob.status.in_(ACTIVE_JOB_STATES),
            )
        )
        return conversation_result(conversation, active_job=active_job)


@router.patch("/{conversation_id}")
async def update_conversation(
    conversation_id: UUID,
    data: ConversationUpdateInput,
    request: Request,
) -> dict:
    scope = f"ai_conversation.update:{conversation_id}"
    async with authorized(request, write=True) as identity:
        replay = await replay_operation(
            identity.session,
            identity.user.id,
            data.operation_id,
            scope,
            data,
        )
        if replay is not None:
            return replay
        conversation = await locked_conversation(
            identity.session,
            identity.user.id,
            conversation_id,
        )
        if conversation.version != data.expected_version:
            raise conversation_version_conflict(conversation)
        changed = False
        if data.title is not None and (
            data.title != conversation.title or conversation.title_source != "user"
        ):
            conversation.title = data.title
            conversation.title_source = "user"
            changed = True
        if data.archived is not None:
            currently_archived = conversation.archived_at is not None
            if data.archived != currently_archived:
                if data.archived:
                    active_job = await identity.session.scalar(
                        select(AIJob.id).where(
                            AIJob.owner_id == identity.user.id,
                            AIJob.conversation_id == conversation.id,
                            AIJob.status.in_(ACTIVE_JOB_STATES),
                        )
                    )
                    if active_job is not None:
                        raise AppError(
                            "conversation_generation_active",
                            "回复生成期间不能归档，请先等待完成或取消。",
                            status=409,
                        )
                conversation.archived_at = now() if data.archived else None
                changed = True
        if changed:
            conversation.version += 1
            conversation.updated_at = now()
        result = {
            "operation_id": data.operation_id,
            "conversation": conversation_result(conversation),
        }
        save_operation(
            identity.session,
            identity.user.id,
            data.operation_id,
            scope,
            data,
            result,
        )
        return result


@router.get("/{conversation_id}/messages")
async def list_messages(
    conversation_id: UUID,
    request: Request,
    before_sequence: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict:
    async with authorized(request) as identity:
        conversation = await identity.session.scalar(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.owner_id == identity.user.id,
            )
        )
        if conversation is None:
            raise AppError("conversation_not_found", "未找到此会话。", status=404)
        statement = select(ConversationMessage).where(
            ConversationMessage.owner_id == identity.user.id,
            ConversationMessage.conversation_id == conversation.id,
        )
        if before_sequence is not None:
            statement = statement.where(
                ConversationMessage.sequence < before_sequence
            )
        rows = list(
            await identity.session.scalars(
                statement.order_by(ConversationMessage.sequence.desc()).limit(
                    limit + 1
                )
            )
        )
        page = rows[:limit]
        jobs = await jobs_for_messages(
            identity.session,
            identity.user.id,
            [message.id for message in page],
        )
        return {
            "conversation": conversation_result(conversation),
            "items": [
                message_result(message, job=jobs.get(message.id))
                for message in reversed(page)
            ],
            "next_before_sequence": (
                page[-1].sequence if len(rows) > limit else None
            ),
        }


@router.post("/{conversation_id}/messages", status_code=202)
async def create_message(
    conversation_id: UUID,
    data: ConversationMessageCreateInput,
    request: Request,
) -> dict:
    scope = f"ai_message.create:{conversation_id}"
    async with authorized(request, write=True) as identity:
        replay = await replay_operation(
            identity.session,
            identity.user.id,
            data.operation_id,
            scope,
            data,
        )
        if replay is not None:
            return replay
        check_ai_access(identity.user)
        return await enqueue_message(
            identity.session,
            identity.user.id,
            conversation_id,
            operation_id=data.operation_id,
            expected_version=data.expected_conversation_version,
            content=data.content,
            operation_data=data,
            timeout_seconds=request.app.state.settings.ai_job_timeout_seconds,
            response_mode=data.response_mode,
            planning=data.planning,
        )
