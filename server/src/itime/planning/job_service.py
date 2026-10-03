from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from itime.ai.conversation_models import (
    AIJobEvent,
    ChatGeneration,
    Conversation,
    ConversationMessage,
)
from itime.ai.governance_service import (
    create_quality_sample_if_enabled,
    locked_attempt_body_log,
    validate_request_body,
    validate_response_body,
)
from itime.ai.job_service import (
    ClaimedJob,
    clear_job_lease,
    finish_running_attempt,
    job_result,
    reject_or_retry_job,
)
from itime.ai.models import AIConfigCapability, AIConfigVersion, AIJob
from itime.ai.provider import ProviderConfig
from itime.auth.security import now
from itime.database import Database
from itime.errors import AppError
from itime.planning.models import (
    PlanningInputSnapshot,
    Proposal,
    ProposalChange,
)
from itime.planning.provider import (
    PlanningProviderResult,
    planning_request_body,
)
from itime.planning.schemas import PlanningGenerationInput
from itime.planning.snapshot_service import build_planning_snapshot
from itime.planning.validation import NormalizedPlan, normalize_plan_payload
from itime.quota.service import (
    estimate_reservation_budget,
    get_or_create_daily_allowance,
    release_reservation,
    reserve_job_budget,
    settle_success,
)
from itime.tasks.service import replay_operation, save_operation

ACTIVE_JOB_STATES = ("queued", "running")
MAX_ACTIVE_USER_AI_JOBS = 3
PLANNING_GENERATION_SCOPE = "planning_generation.create"
PLANNING_PROPOSAL_DAYS = 7
PLANNING_REQUEST_SCHEMA_VERSION = 1
EVENT_RETENTION = timedelta(days=7)


class PlanningWorkUnavailable(Exception):
    def __init__(self, code: str, message: str, stage: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.stage = stage


@dataclass(frozen=True)
class PlanningWork:
    config: ProviderConfig
    snapshot: dict[str, Any]
    messages: list[dict[str, object]]
    request_body: dict
    generation: int | None = None


async def enqueue_planning_generation(
    session: AsyncSession,
    owner_id: UUID,
    data: PlanningGenerationInput,
    *,
    timeout_seconds: int,
) -> dict:
    """Create an independent planning job and its quota reservation atomically."""
    replay = await replay_operation(
        session,
        owner_id,
        data.operation_id,
        PLANNING_GENERATION_SCOPE,
        data,
    )
    if replay is not None:
        return replay

    await lock_owner_ai_job_capacity(session, owner_id)
    snapshot = await build_planning_snapshot(session, owner_id, data)
    if not snapshot.task_candidates:
        raise AppError(
            "planning_candidates_empty",
            "当前范围没有可规划任务，请先添加或选择未完成任务。",
            status=409,
        )

    state = await session.get(AIConfigCapability, "planning")
    config = (
        await session.get(AIConfigVersion, state.current_version_id)
        if state is not None and state.current_version_id is not None
        else None
    )
    if config is None or config.status != "published":
        raise AppError(
            "ai_capability_unavailable",
            "AI 规划暂未配置，请稍后重试；任务和日历仍可手动使用。",
            status=503,
            retryable=True,
        )

    provider = planning_provider_config(config)
    snapshot_payload = planning_snapshot_payload(snapshot)
    messages = planning_provider_messages(snapshot_payload)
    request_body = planning_request_body(provider, messages)
    validate_request_body(request_body)
    input_summary = {
        "resource_type": "planning_snapshot",
        "planning_snapshot_id": str(snapshot.id),
        "scope": snapshot.scope,
        "target_date": snapshot.target_date.isoformat(),
        "candidate_count": len(snapshot.task_candidates),
        "content_sha256": snapshot.content_sha256,
        "request_schema_version": PLANNING_REQUEST_SCHEMA_VERSION,
    }
    estimate = estimate_reservation_budget(
        config,
        request_body=request_body,
        input_summary=input_summary,
        capability="planning",
    )
    current = now()
    daily = await get_or_create_daily_allowance(
        session,
        owner_id,
        accepted_at=current,
    )
    job = AIJob(
        id=uuid4(),
        owner_id=owner_id,
        capability="planning",
        config_version_id=config.id,
        input_media_id=None,
        conversation_id=None,
        planning_snapshot_id=snapshot.id,
        quota_mode="user",
        status="queued",
        input_summary=input_summary,
        available_at=current,
        deadline_at=current + timedelta(seconds=timeout_seconds),
        attempt_count=0,
        max_attempts=3,
        last_event_sequence=0,
        version=1,
        created_at=current,
        updated_at=current,
    )
    session.add(job)
    await session.flush()
    append_planning_event(
        session,
        job,
        generation=0,
        payload={"status": "queued", "attempt": 0},
    )
    await reserve_job_budget(
        session,
        owner_id=owner_id,
        job=job,
        accepted_at=current,
        estimate=estimate,
        daily=daily,
    )
    result = {
        "operation_id": data.operation_id,
        "job": job_result(job),
    }
    save_operation(
        session,
        owner_id,
        data.operation_id,
        PLANNING_GENERATION_SCOPE,
        data,
        result,
    )
    await session.flush()
    return result


async def load_planning_work(
    database: Database,
    claim: ClaimedJob,
) -> PlanningWork:
    if claim.planning_snapshot_id is None:
        raise PlanningWorkUnavailable(
            "ai_job_input_invalid",
            "规划作业缺少输入快照，请重新生成。",
            "validation",
        )
    current = now()
    async with database.transaction() as session:
        job = await session.scalar(
            select(AIJob)
            .where(
                AIJob.id == claim.id,
                AIJob.owner_id == claim.owner_id,
                AIJob.capability == "planning",
                AIJob.planning_snapshot_id == claim.planning_snapshot_id,
                AIJob.status == "running",
                AIJob.lease_id == claim.lease_id,
                AIJob.lease_until > current,
                AIJob.deadline_at > current,
            )
            .with_for_update()
        )
        if job is None:
            raise PlanningWorkUnavailable(
                "ai_job_superseded",
                "作业已被取消或由其他执行进程接管。",
                "worker",
            )
        config = await session.get(AIConfigVersion, job.config_version_id)
        snapshot = await session.scalar(
            select(PlanningInputSnapshot).where(
                PlanningInputSnapshot.id == claim.planning_snapshot_id,
                PlanningInputSnapshot.owner_id == claim.owner_id,
            )
        )
        if config is None or config.capability != "planning":
            raise PlanningWorkUnavailable(
                "ai_configuration_unavailable",
                "规划配置已不可用，请重新生成。",
                "configuration",
            )
        if snapshot is None:
            raise PlanningWorkUnavailable(
                "ai_job_input_invalid",
                "规划输入快照已不可用，请重新生成。",
                "validation",
            )
        if (
            job.input_summary.get("request_schema_version")
            != PLANNING_REQUEST_SCHEMA_VERSION
        ):
            raise PlanningWorkUnavailable(
                "ai_job_request_changed",
                "规划请求协议已更新，请重新生成。",
                "configuration",
            )
        provider = planning_provider_config(config)
        snapshot_payload = planning_snapshot_payload(snapshot)
        generation_number = None
        conversation_context = None
        if job.conversation_id is not None:
            from itime.ai.conversation_service import (
                append_job_event,
                conversation_provider_messages,
            )

            generation = await session.scalar(
                select(ChatGeneration)
                .where(
                    ChatGeneration.job_id == job.id,
                    ChatGeneration.owner_id == job.owner_id,
                    ChatGeneration.conversation_id == job.conversation_id,
                )
                .with_for_update()
            )
            if generation is None:
                raise PlanningWorkUnavailable(
                    "ai_job_input_invalid",
                    "规划作业缺少对话消息映射，请重新发送。",
                    "validation",
                )
            response_message = await session.scalar(
                select(ConversationMessage)
                .where(
                    ConversationMessage.id == generation.response_message_id,
                    ConversationMessage.owner_id == job.owner_id,
                )
                .with_for_update()
            )
            if response_message is None:
                raise PlanningWorkUnavailable(
                    "ai_job_input_invalid",
                    "规划回复位置已不可用，请重新发送。",
                    "validation",
                )
            generation.stream_generation += 1
            if generation.stream_generation > job.max_attempts:
                raise PlanningWorkUnavailable(
                    "ai_job_attempts_exhausted",
                    "AI 作业重试次数已用尽，请重新发送。",
                    "worker",
                )
            if generation.stream_generation > 1:
                response_message.content = ""
                append_job_event(
                    session,
                    job,
                    generation=generation.stream_generation,
                    kind="content_reset",
                    payload={"message_id": str(response_message.id)},
                )
            response_message.status = "running"
            response_message.error_code = None
            response_message.error_message = None
            response_message.updated_at = current
            append_job_event(
                session,
                job,
                generation=generation.stream_generation,
                kind="job_state",
                payload={
                    "status": "running",
                    "message_id": str(response_message.id),
                    "attempt": claim.attempt_number,
                },
            )
            conversation_context = await conversation_provider_messages(
                session,
                owner_id=job.owner_id,
                conversation_id=generation.conversation_id,
                history_through_sequence=generation.history_through_sequence,
                context_message_ids=generation.context_message_ids,
            )
            generation_number = generation.stream_generation
        messages = planning_provider_messages(
            snapshot_payload,
            conversation_context=conversation_context,
        )
        request_body = planning_request_body(provider, messages)
        validate_request_body(request_body)
        return PlanningWork(
            config=provider,
            snapshot=snapshot_payload,
            messages=messages,
            request_body=request_body,
            generation=generation_number,
        )


async def complete_planning_job(
    database: Database,
    claim: ClaimedJob,
    snapshot: dict[str, Any],
    result: PlanningProviderResult,
    *,
    quality_sample_retention_days: int = 30,
) -> bool:
    """Validate and persist a visible proposal with every terminal fact."""
    current = now()
    async with database.transaction() as session:
        job = await session.scalar(
            select(AIJob)
            .where(
                AIJob.id == claim.id,
                AIJob.owner_id == claim.owner_id,
                AIJob.capability == "planning",
                AIJob.status == "running",
                AIJob.lease_id == claim.lease_id,
                AIJob.lease_until > current,
            )
            .with_for_update()
        )
        if job is None:
            return False
        generation = None
        message = None
        conversation = None
        if job.conversation_id is not None:
            generation = await session.scalar(
                select(ChatGeneration)
                .where(
                    ChatGeneration.job_id == job.id,
                    ChatGeneration.owner_id == job.owner_id,
                    ChatGeneration.conversation_id == job.conversation_id,
                )
                .with_for_update()
            )
            if generation is None:
                return False
            message = await session.scalar(
                select(ConversationMessage)
                .where(
                    ConversationMessage.id == generation.response_message_id,
                    ConversationMessage.owner_id == job.owner_id,
                )
                .with_for_update()
            )
            conversation = await session.scalar(
                select(Conversation)
                .where(
                    Conversation.id == generation.conversation_id,
                    Conversation.owner_id == job.owner_id,
                )
                .with_for_update()
            )
            if (
                message is None
                or conversation is None
                or message.status != "running"
            ):
                return False
        body_log = await locked_attempt_body_log(
            session,
            job,
            claim.attempt_number,
        )
        if body_log is None or body_log.status != "running":
            return False
        validate_response_body(result.response_body)
        body_log.response_body = result.response_body

        if job.deadline_at <= current:
            await _finish_planning_failure(
                session,
                job,
                status="timed_out",
                error_code="ai_job_deadline_exceeded",
                error_message="AI 作业已超过总等待时间，请重新生成。",
                error_stage="deadline",
                provider_request_id=result.provider_request_id,
                http_status=result.http_status,
                provider_usage=result.usage,
            )
            return False

        try:
            normalized = normalize_plan_payload(snapshot, result.output)
        except AppError as exc:
            await _finish_planning_failure(
                session,
                job,
                status="failed",
                error_code=exc.code,
                error_message=exc.message,
                error_stage="validation",
                provider_request_id=result.provider_request_id,
                http_status=result.http_status,
                provider_usage=result.usage,
            )
            return False

        proposal = _create_proposal(
            session,
            job,
            snapshot,
            normalized,
            current,
            source_message_id=message.id if message is not None else None,
        )
        await session.flush()
        finalized = await finish_running_attempt(
            session,
            job,
            status="succeeded",
            provider_request_id=result.provider_request_id,
            http_status=result.http_status,
            provider_usage=result.usage,
        )
        if finalized is None:
            raise AppError(
                "ai_quota_state_invalid",
                "最终成功尝试状态不完整。",
                status=503,
                saved="unknown",
            )
        await settle_success(
            session,
            job=job,
            provider_cost=finalized.provider_cost,
        )
        if message is not None:
            message.content = result.content
            message.status = "complete"
            message.error_code = None
            message.error_message = None
            message.updated_at = current
            message.completed_at = current
        job.status = "succeeded"
        job.output = {
            "resource_type": "planning_proposal",
            "proposal_id": str(proposal.id),
            **(
                {"message_id": str(message.id)}
                if message is not None
                else {}
            ),
        }
        job.error_code = None
        job.error_message = None
        job.finished_at = current
        job.updated_at = current
        job.version += 1
        append_planning_event(
            session,
            job,
            generation=(
                generation.stream_generation
                if generation is not None
                else claim.attempt_number
            ),
            payload={
                "status": "succeeded",
                "attempt": claim.attempt_number,
                "proposal_id": str(proposal.id),
                **(
                    {"message_id": str(message.id)}
                    if message is not None
                    else {}
                ),
            },
        )
        clear_job_lease(job)
        if conversation is not None:
            conversation.version += 1
            conversation.updated_at = current
        await create_quality_sample_if_enabled(
            session,
            job,
            body_log,
            retention_days=quality_sample_retention_days,
        )
        return True


async def reject_or_retry_planning_job(
    database: Database,
    claim: ClaimedJob,
    *,
    error_code: str,
    error_message: str,
    error_stage: str,
    retryable: bool,
    provider_request_id: str | None = None,
    http_status: int | None = None,
    provider_usage: object | None = None,
) -> str | None:
    if claim.conversation_id is None:
        return await reject_or_retry_job(
            database,
            claim,
            error_code=error_code,
            error_message=error_message,
            error_stage=error_stage,
            retryable=retryable,
            provider_request_id=provider_request_id,
            http_status=http_status,
            provider_usage=provider_usage,
        )

    current = now()
    async with database.transaction() as session:
        job = await session.scalar(
            select(AIJob)
            .where(
                AIJob.id == claim.id,
                AIJob.owner_id == claim.owner_id,
                AIJob.capability == "planning",
                AIJob.status == "running",
                AIJob.lease_id == claim.lease_id,
                AIJob.lease_until > current,
            )
            .with_for_update()
        )
        if job is None:
            return None
        generation = await session.scalar(
            select(ChatGeneration)
            .where(ChatGeneration.job_id == job.id)
            .with_for_update()
        )
        if generation is None:
            return None
        message = await session.scalar(
            select(ConversationMessage)
            .where(
                ConversationMessage.id == generation.response_message_id,
                ConversationMessage.owner_id == job.owner_id,
            )
            .with_for_update()
        )
        if message is None:
            return None
        can_retry = (
            retryable
            and job.deadline_at > current
            and job.attempt_count < job.max_attempts
            and current + timedelta(seconds=min(30, 2**job.attempt_count))
            < job.deadline_at
        )
        if can_retry:
            delay = min(30, 2**job.attempt_count)
            await finish_running_attempt(
                session,
                job,
                status="retryable_failed",
                error_code=error_code,
                error_stage=error_stage,
                provider_request_id=provider_request_id,
                http_status=http_status,
                provider_usage=provider_usage,
            )
            job.status = "queued"
            job.available_at = current + timedelta(seconds=delay)
            job.error_code = error_code
            job.error_message = error_message
            job.updated_at = current
            job.version += 1
            message.status = "queued"
            message.error_code = None
            message.error_message = None
            message.updated_at = current
            append_planning_event(
                session,
                job,
                generation=generation.stream_generation,
                payload={
                    "status": "queued",
                    "message_id": str(message.id),
                    "attempt": claim.attempt_number,
                    "retrying": True,
                    "error_code": error_code,
                },
            )
            clear_job_lease(job)
            return "queued"

        timed_out = job.deadline_at <= current
        status = "timed_out" if timed_out else "failed"
        final_code = "ai_job_deadline_exceeded" if timed_out else error_code
        final_message = (
            "AI 作业已超过总等待时间，请重新生成。"
            if timed_out
            else error_message
        )
        await finish_running_attempt(
            session,
            job,
            status=status,
            error_code=final_code,
            error_stage="deadline" if timed_out else error_stage,
            provider_request_id=provider_request_id,
            http_status=http_status,
            provider_usage=provider_usage,
        )
        job.status = status
        job.error_code = final_code
        job.error_message = final_message
        job.finished_at = current
        job.updated_at = current
        job.version += 1
        message.status = status
        message.error_code = final_code
        message.error_message = final_message
        message.completed_at = current
        message.updated_at = current
        append_planning_event(
            session,
            job,
            generation=generation.stream_generation,
            payload={
                "status": status,
                "message_id": str(message.id),
                "attempt": claim.attempt_number,
                "error_code": final_code,
                "error_message": final_message,
            },
        )
        clear_job_lease(job)
        conversation = await session.get(Conversation, generation.conversation_id)
        if conversation is not None:
            conversation.version += 1
            conversation.updated_at = current
        await release_reservation(session, job=job, reason=status)
        return status


def planning_snapshot_payload(snapshot: PlanningInputSnapshot) -> dict[str, Any]:
    return {
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
        "instruction": snapshot.user_instructions,
        "selected_task_ids": list(snapshot.selected_task_ids),
        "task_candidates": copy.deepcopy(snapshot.task_candidates),
        "fixed_occurrences": copy.deepcopy(snapshot.fixed_occurrences),
        "current_plan_id": (
            str(snapshot.current_plan_id)
            if snapshot.current_plan_id is not None
            else None
        ),
        "current_plan_version": snapshot.current_plan_version,
        "preserved_dates": list(snapshot.preserved_dates),
        "content_sha256": snapshot.content_sha256,
    }


def planning_provider_messages(
    snapshot: dict[str, Any],
    *,
    conversation_context: list[dict[str, str]] | None = None,
) -> list[dict[str, object]]:
    return [
        *(conversation_context or []),
        {
            "role": "user",
            "content": json.dumps(
                {"planning_snapshot": snapshot},
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        }
    ]


def append_planning_event(
    session: AsyncSession,
    job: AIJob,
    *,
    generation: int,
    payload: dict,
) -> AIJobEvent:
    job.last_event_sequence += 1
    event = AIJobEvent(
        job_id=job.id,
        owner_id=job.owner_id,
        sequence=job.last_event_sequence,
        generation=generation,
        kind="job_state",
        payload=payload,
        expires_at=now() + EVENT_RETENTION,
    )
    session.add(event)
    return event


async def lock_owner_ai_job_capacity(
    session: AsyncSession,
    owner_id: UUID,
) -> None:
    await session.execute(
        select(
            func.pg_advisory_xact_lock(
                func.hashtextextended(f"user-ai-jobs:{owner_id}", 0)
            )
        )
    )
    active_count = await session.scalar(
        select(func.count())
        .select_from(AIJob)
        .where(
            AIJob.owner_id == owner_id,
            AIJob.quota_mode == "user",
            AIJob.status.in_(ACTIVE_JOB_STATES),
        )
    )
    if active_count is not None and active_count >= MAX_ACTIVE_USER_AI_JOBS:
        raise AppError(
            "ai_job_limit_reached",
            "已有 3 个 AI 作业正在处理，请等待完成后再试。",
            status=429,
            retryable=True,
        )


def planning_provider_config(config: AIConfigVersion) -> ProviderConfig:
    return ProviderConfig(
        endpoint_url=config.endpoint_url,
        model=config.model,
        api_key_ciphertext=bytes(config.api_key_ciphertext),
        timeout_seconds=config.timeout_seconds,
        max_output_tokens=config.max_output_tokens,
    )


def _create_proposal(
    session: AsyncSession,
    job: AIJob,
    snapshot: dict[str, Any],
    normalized: NormalizedPlan,
    current: datetime,
    *,
    source_message_id: UUID | None = None,
) -> Proposal:
    proposal = Proposal(
        id=uuid4(),
        owner_id=job.owner_id,
        kind="plan",
        source_job_id=job.id,
        conversation_id=job.conversation_id,
        source_message_id=source_message_id,
        input_snapshot_id=job.planning_snapshot_id,
        schema_version=1,
        status="pending",
        title=_proposal_title(snapshot),
        summary=normalized.summary,
        assumptions=list(normalized.assumptions),
        warnings=[],
        payload=normalized.model_dump(mode="json"),
        expires_at=current + timedelta(days=PLANNING_PROPOSAL_DAYS),
        version=1,
        created_at=current,
        updated_at=current,
    )
    session.add(proposal)
    before_plan = (
        {
            "plan_id": str(snapshot["current_plan_id"]),
            "version": snapshot["current_plan_version"],
        }
        if snapshot.get("current_plan_id") is not None
        else None
    )
    after_plan = {
        "scope": snapshot["scope"],
        "target_date": snapshot["target_date"],
        "week_start_date": snapshot["week_start_date"],
        "week_end_date": snapshot["week_end_date"],
        "summary": normalized.summary,
        "assumptions": list(normalized.assumptions),
        "unscheduled_items": [
            _unscheduled_change_data(snapshot, item.model_dump(mode="json"))
            for item in normalized.unscheduled_items
        ],
    }
    changes = [
        ProposalChange(
            id=uuid4(),
            proposal_id=proposal.id,
            owner_id=job.owner_id,
            position=0,
            action="update" if before_plan is not None else "create",
            entity_type="plan",
            target_id=(
                UUID(str(snapshot["current_plan_id"]))
                if before_plan is not None
                else None
            ),
            before_data=before_plan,
            after_data=after_plan,
            created_at=current,
        )
    ]
    changes.extend(
        ProposalChange(
            id=uuid4(),
            proposal_id=proposal.id,
            owner_id=job.owner_id,
            position=position,
            action="create",
            entity_type="scheduled_block",
            target_id=None,
            before_data=None,
            after_data=_block_change_data(
                snapshot,
                block.model_dump(mode="json"),
            ),
            created_at=current,
        )
        for position, block in enumerate(normalized.blocks, start=1)
    )
    session.add_all(changes)
    return proposal


async def _finish_planning_failure(
    session: AsyncSession,
    job: AIJob,
    *,
    status: str,
    error_code: str,
    error_message: str,
    error_stage: str,
    provider_request_id: str | None,
    http_status: int | None,
    provider_usage: object | None,
) -> None:
    generation = None
    message = None
    conversation = None
    if job.conversation_id is not None:
        generation = await session.scalar(
            select(ChatGeneration)
            .where(
                ChatGeneration.job_id == job.id,
                ChatGeneration.owner_id == job.owner_id,
            )
            .with_for_update()
        )
        if generation is not None:
            message = await session.scalar(
                select(ConversationMessage)
                .where(
                    ConversationMessage.id == generation.response_message_id,
                    ConversationMessage.owner_id == job.owner_id,
                )
                .with_for_update()
            )
            conversation = await session.scalar(
                select(Conversation)
                .where(
                    Conversation.id == generation.conversation_id,
                    Conversation.owner_id == job.owner_id,
                )
                .with_for_update()
            )
    await finish_running_attempt(
        session,
        job,
        status=status,
        error_code=error_code,
        error_stage=error_stage,
        provider_request_id=provider_request_id,
        http_status=http_status,
        provider_usage=provider_usage,
    )
    current = now()
    job.status = status
    job.output = None
    job.error_code = error_code
    job.error_message = error_message
    job.finished_at = current
    job.updated_at = current
    job.version += 1
    if message is not None:
        message.status = status
        message.error_code = error_code
        message.error_message = error_message
        message.completed_at = current
        message.updated_at = current
    await release_reservation(session, job=job, reason=status)
    append_planning_event(
        session,
        job,
        generation=(
            generation.stream_generation
            if generation is not None
            else job.attempt_count
        ),
        payload={
            "status": status,
            "attempt": job.attempt_count,
            "error_code": error_code,
            "error_message": error_message,
            **(
                {"message_id": str(message.id)}
                if message is not None
                else {}
            ),
        },
    )
    clear_job_lease(job)
    if conversation is not None:
        conversation.version += 1
        conversation.updated_at = current


def _proposal_title(snapshot: dict[str, Any]) -> str:
    suffix = "单日规划" if snapshot["scope"] == "day" else "整周规划"
    return f"{snapshot['target_date']} {suffix}"


def _candidate(snapshot: dict[str, Any], candidate_key: str) -> dict:
    for candidate in snapshot["task_candidates"]:
        if candidate["candidate_key"] == candidate_key:
            return candidate
    raise AppError(
        "invalid_plan_snapshot",
        "规划提案引用的快照候选不存在。",
    )


def _block_change_data(snapshot: dict[str, Any], block: dict) -> dict:
    candidate = _candidate(snapshot, block["candidate_key"])
    step_titles = {
        str(step["id"]): step["title"] for step in candidate.get("steps", [])
    }
    return {
        **block,
        "task_title": candidate["title"],
        "step_titles": [
            step_titles[str(step_id)]
            for step_id in block["step_ids"]
            if str(step_id) in step_titles
        ],
    }


def _unscheduled_change_data(snapshot: dict[str, Any], item: dict) -> dict:
    candidate = _candidate(snapshot, item["candidate_key"])
    return {**item, "task_title": candidate["title"]}
