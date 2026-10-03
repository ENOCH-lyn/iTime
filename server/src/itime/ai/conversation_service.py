import re
import unicodedata
from datetime import timedelta
from uuid import UUID

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from itime.ai.conversation_models import (
    AIJobEvent,
    ChatGeneration,
    Conversation,
    ConversationMessage,
)
from itime.ai.job_service import job_result
from itime.ai.models import AIConfigCapability, AIConfigVersion, AIJob
from itime.auth.security import now
from itime.errors import AppError
from itime.tasks.service import save_operation

ACTIVE_JOB_STATES = {"queued", "running"}
EVENT_RETENTION = timedelta(days=7)
MAX_ACTIVE_CONVERSATION_JOBS = 3
MAX_CURRENT_CONTEXT_MESSAGES = 30
MAX_CURRENT_CONTEXT_CHARS = 48_000
MAX_RELATED_CONTEXT_CHARS = 12_000
MAX_RELATED_USER_MESSAGES = 3
RELATED_CANDIDATE_LIMIT = 200


def conversation_result(
    conversation: Conversation,
    *,
    active_job: AIJob | None = None,
) -> dict:
    return {
        "id": conversation.id,
        "title": conversation.title,
        "title_source": conversation.title_source,
        "version": conversation.version,
        "archived": conversation.archived_at is not None,
        "archived_at": conversation.archived_at,
        "created_at": conversation.created_at,
        "updated_at": conversation.updated_at,
        "active_job": job_result(active_job) if active_job is not None else None,
    }


def message_result(
    message: ConversationMessage,
    *,
    job: AIJob | None = None,
) -> dict:
    return {
        "id": message.id,
        "conversation_id": message.conversation_id,
        "sequence": message.sequence,
        "role": message.role,
        "status": message.status,
        "content": message.content,
        "error": (
            {"code": message.error_code, "message": message.error_message}
            if message.error_code is not None
            else None
        ),
        "job": job_result(job) if job is not None else None,
        "created_at": message.created_at,
        "updated_at": message.updated_at,
        "completed_at": message.completed_at,
    }


async def locked_conversation(
    session: AsyncSession,
    owner_id: UUID,
    conversation_id: UUID,
) -> Conversation:
    conversation = await session.scalar(
        select(Conversation)
        .where(
            Conversation.id == conversation_id,
            Conversation.owner_id == owner_id,
        )
        .with_for_update()
    )
    if conversation is None:
        raise AppError(
            "conversation_not_found",
            "未找到此会话。",
            status=404,
        )
    return conversation


def conversation_version_conflict(conversation: Conversation) -> AppError:
    return AppError(
        "conversation_version_conflict",
        "此会话已在其他页面或设备更新，请刷新后重试。",
        status=409,
        details={"current_version": conversation.version},
    )


def _normalized_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(normalized.split())


def _text_units(value: str) -> tuple[set[str], set[str]]:
    normalized = _normalized_text(value)
    compact = "".join(character for character in normalized if character.isalnum())
    bigrams = {
        compact[index : index + 2]
        for index in range(max(0, len(compact) - 1))
    }
    words = {
        token
        for token in re.findall(r"[a-z0-9_]+|[\u3400-\u9fff]+", normalized)
        if len(token) >= 2
    }
    return words, bigrams


def _relevance_score(query: str, candidate: str) -> int:
    query = _normalized_text(query)
    candidate = _normalized_text(candidate[:4000])
    if len("".join(character for character in query if character.isalnum())) < 2:
        return 0
    query_words, query_bigrams = _text_units(query)
    candidate_words, candidate_bigrams = _text_units(candidate)
    score = 4 * len(query_bigrams & candidate_bigrams)
    score += 12 * len(query_words & candidate_words)
    if query in candidate:
        score += 100
    return score


async def select_relevant_context(
    session: AsyncSession,
    owner_id: UUID,
    conversation_id: UUID,
    content: str,
) -> list[str]:
    if _relevance_score(content, content) == 0:
        return []
    candidates = list(
        await session.scalars(
            select(ConversationMessage)
            .where(
                ConversationMessage.owner_id == owner_id,
                ConversationMessage.conversation_id != conversation_id,
                ConversationMessage.role == "user",
                ConversationMessage.status == "complete",
            )
            .order_by(
                ConversationMessage.created_at.desc(),
                ConversationMessage.id.desc(),
            )
            .limit(RELATED_CANDIDATE_LIMIT)
        )
    )
    scored = [
        (score, message)
        for message in candidates
        if (score := _relevance_score(content, message.content)) > 0
    ]
    selected = [
        message
        for _, message in sorted(
            scored,
            key=lambda item: (item[0], item[1].created_at, str(item[1].id)),
            reverse=True,
        )[:MAX_RELATED_USER_MESSAGES]
    ]
    if not selected:
        return []

    following_keys = [
        (message.conversation_id, message.sequence + 1) for message in selected
    ]
    following = {
        (message.conversation_id, message.sequence): message
        for message in await session.scalars(
            select(ConversationMessage).where(
                ConversationMessage.owner_id == owner_id,
                tuple_(
                    ConversationMessage.conversation_id,
                    ConversationMessage.sequence,
                ).in_(following_keys),
                ConversationMessage.role == "assistant",
                ConversationMessage.status == "complete",
            )
        )
    }
    selected.sort(key=lambda message: (message.created_at, str(message.id)))
    message_ids: list[str] = []
    used_chars = 0
    for message in selected:
        pair = [message]
        assistant = following.get((message.conversation_id, message.sequence + 1))
        if assistant is not None:
            pair.append(assistant)
        pair_chars = sum(len(item.content) for item in pair)
        if used_chars + pair_chars > MAX_RELATED_CONTEXT_CHARS:
            continue
        message_ids.extend(str(item.id) for item in pair)
        used_chars += pair_chars
    return message_ids


async def active_jobs_for_conversations(
    session: AsyncSession,
    owner_id: UUID,
    conversation_ids: list[UUID],
) -> dict[UUID, AIJob]:
    if not conversation_ids:
        return {}
    jobs = await session.scalars(
        select(AIJob).where(
            AIJob.owner_id == owner_id,
            AIJob.conversation_id.in_(conversation_ids),
            AIJob.status.in_(ACTIVE_JOB_STATES),
        )
    )
    return {
        job.conversation_id: job
        for job in jobs
        if job.conversation_id is not None
    }


async def jobs_for_messages(
    session: AsyncSession,
    owner_id: UUID,
    message_ids: list[UUID],
) -> dict[UUID, AIJob]:
    if not message_ids:
        return {}
    rows = await session.execute(
        select(ChatGeneration.response_message_id, AIJob)
        .join(AIJob, AIJob.id == ChatGeneration.job_id)
        .where(
            ChatGeneration.owner_id == owner_id,
            ChatGeneration.response_message_id.in_(message_ids),
        )
    )
    return {message_id: job for message_id, job in rows}


def append_job_event(
    session: AsyncSession,
    job: AIJob,
    *,
    generation: int,
    kind: str,
    payload: dict,
) -> AIJobEvent:
    current = now()
    job.last_event_sequence += 1
    event = AIJobEvent(
        job_id=job.id,
        owner_id=job.owner_id,
        sequence=job.last_event_sequence,
        generation=generation,
        kind=kind,
        payload=payload,
        expires_at=current + EVENT_RETENTION,
    )
    session.add(event)
    return event


def _automatic_title(content: str) -> str:
    compact = " ".join(content.split())
    return compact[:48]


async def enqueue_message(
    session: AsyncSession,
    owner_id: UUID,
    conversation_id: UUID,
    *,
    operation_id: UUID,
    expected_version: int,
    content: str,
    operation_data,
    timeout_seconds: int,
) -> dict:
    conversation = await locked_conversation(session, owner_id, conversation_id)
    if conversation.version != expected_version:
        raise conversation_version_conflict(conversation)
    if conversation.archived_at is not None:
        raise AppError(
            "conversation_archived",
            "此会话已归档，请先重新打开后再发送消息。",
            status=409,
        )
    active_job = await session.scalar(
        select(AIJob.id).where(
            AIJob.owner_id == owner_id,
            AIJob.conversation_id == conversation.id,
            AIJob.status.in_(ACTIVE_JOB_STATES),
        )
    )
    if active_job is not None:
        raise AppError(
            "conversation_generation_active",
            "此会话仍在生成回复，请等待完成或先取消。",
            status=409,
        )
    active_count = await session.scalar(
        select(func.count())
        .select_from(AIJob)
        .where(
            AIJob.owner_id == owner_id,
            AIJob.capability == "conversation",
            AIJob.status.in_(ACTIVE_JOB_STATES),
        )
    )
    if active_count >= MAX_ACTIVE_CONVERSATION_JOBS:
        raise AppError(
            "ai_job_limit_reached",
            "已有 3 个对话作业正在处理，请完成后再发送。",
            status=429,
            retryable=True,
        )

    state = await session.get(AIConfigCapability, "conversation")
    config = (
        await session.get(AIConfigVersion, state.current_version_id)
        if state is not None and state.current_version_id is not None
        else None
    )
    if config is None or config.status != "published":
        raise AppError(
            "ai_capability_unavailable",
            "全局 AI 对话暂未配置，请稍后重试；任务和日历仍可手动使用。",
            status=503,
            retryable=True,
        )

    context_message_ids = await select_relevant_context(
        session,
        owner_id,
        conversation.id,
        content,
    )
    last_sequence = (
        await session.scalar(
            select(func.max(ConversationMessage.sequence)).where(
                ConversationMessage.conversation_id == conversation.id,
            )
        )
        or 0
    )
    current = now()
    request_message = ConversationMessage(
        owner_id=owner_id,
        conversation_id=conversation.id,
        sequence=last_sequence + 1,
        role="user",
        status="complete",
        content=content,
        completed_at=current,
        created_at=current,
        updated_at=current,
    )
    response_message = ConversationMessage(
        owner_id=owner_id,
        conversation_id=conversation.id,
        sequence=last_sequence + 2,
        role="assistant",
        status="queued",
        content="",
        created_at=current,
        updated_at=current,
    )
    session.add_all([request_message, response_message])
    await session.flush()

    job = AIJob(
        owner_id=owner_id,
        capability="conversation",
        config_version_id=config.id,
        input_media_id=None,
        conversation_id=conversation.id,
        input_summary={
            "resource_type": "conversation_message",
            "request_message_id": str(request_message.id),
            "response_message_id": str(response_message.id),
            "history_through_sequence": request_message.sequence,
            "related_context_count": len(context_message_ids),
        },
        available_at=current,
        deadline_at=current + timedelta(seconds=timeout_seconds),
    )
    session.add(job)
    await session.flush()
    session.add(
        ChatGeneration(
            job_id=job.id,
            owner_id=owner_id,
            conversation_id=conversation.id,
            request_message_id=request_message.id,
            response_message_id=response_message.id,
            history_through_sequence=request_message.sequence,
            context_message_ids=context_message_ids,
        )
    )
    append_job_event(
        session,
        job,
        generation=0,
        kind="job_state",
        payload={
            "status": "queued",
            "message_id": str(response_message.id),
            "attempt": 0,
        },
    )

    if conversation.title_source == "auto" and last_sequence == 0:
        conversation.title = _automatic_title(content)
    conversation.version += 1
    conversation.updated_at = current
    await session.flush()
    result = {
        "operation_id": operation_id,
        "conversation": conversation_result(conversation, active_job=job),
        "messages": [
            message_result(request_message),
            message_result(response_message, job=job),
        ],
        "job": job_result(job),
    }
    save_operation(
        session,
        owner_id,
        operation_id,
        f"ai_message.create:{conversation.id}",
        operation_data,
        result,
    )
    return result
