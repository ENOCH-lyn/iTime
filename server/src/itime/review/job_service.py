from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from itime.ai.governance_service import (
    create_quality_sample_if_enabled,
    locked_attempt_body_log,
    validate_request_body,
    validate_response_body,
)
from itime.ai.job_service import (
    ClaimedJob,
    append_planning_job_event,
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
from itime.planning.job_service import lock_owner_ai_job_capacity
from itime.quota.service import (
    estimate_reservation_budget,
    get_or_create_daily_allowance,
    release_reservation,
    reserve_job_budget,
    settle_success,
)
from itime.review.fact_service import (
    build_review_snapshot,
    review_snapshot_payload,
)
from itime.review.models import (
    MemoryProposal,
    ReviewInputSnapshot,
    ReviewMessage,
    ReviewReportVersion,
    ReviewSession,
)
from itime.review.provider import (
    ReviewProviderResult,
    ReviewPurpose,
    review_request_body,
)
from itime.review.schemas import (
    ReviewQuestionGenerationInput,
    ReviewQuestionOutput,
    ReviewReportGenerationInput,
    ReviewReportOutput,
)
from itime.review.service import (
    ACTIVE_JOB_STATES,
    owned_review,
    review_result,
    review_version_conflict,
)
from itime.tasks.service import emit_change, replay_operation, save_operation

REVIEW_REQUEST_SCHEMA_VERSION = 1


class ReviewWorkUnavailable(Exception):
    def __init__(self, code: str, message: str, stage: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.stage = stage


@dataclass(frozen=True)
class ReviewWork:
    config: ProviderConfig
    snapshot: dict[str, Any]
    messages: list[dict[str, object]]
    request_body: dict
    purpose: ReviewPurpose


def review_provider_config(config: AIConfigVersion) -> ProviderConfig:
    return ProviderConfig(
        endpoint_url=config.endpoint_url,
        model=config.model,
        api_key_ciphertext=bytes(config.api_key_ciphertext),
        timeout_seconds=config.timeout_seconds,
        max_output_tokens=config.max_output_tokens,
    )


def review_provider_messages(snapshot: dict[str, Any]) -> list[dict[str, object]]:
    return [
        {
            "role": "user",
            "content": json.dumps(
                {"review_snapshot": snapshot},
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        }
    ]


async def _active_review_job(
    session: AsyncSession,
    owner_id: UUID,
    review_id: UUID,
) -> UUID | None:
    return await session.scalar(
        select(AIJob.id).where(
            AIJob.owner_id == owner_id,
            AIJob.review_session_id == review_id,
            AIJob.status.in_(ACTIVE_JOB_STATES),
        )
    )


async def _append_answer(
    session: AsyncSession,
    review: ReviewSession,
    answer: str | None,
    *,
    current_time: datetime,
) -> None:
    last_message = await session.scalar(
        select(ReviewMessage)
        .where(
            ReviewMessage.owner_id == review.owner_id,
            ReviewMessage.review_session_id == review.id,
        )
        .order_by(ReviewMessage.sequence.desc())
        .limit(1)
    )
    if last_message is None:
        if answer is not None:
            raise AppError(
                "review_answer_unexpected",
                "首次追问前不需要填写回答。",
                status=409,
            )
        return
    if last_message.role == "assistant":
        if answer is None:
            raise AppError(
                "review_answer_required",
                "请回答当前问题，或直接跳过并生成报告。",
                status=422,
            )
        session.add(
            ReviewMessage(
                owner_id=review.owner_id,
                review_session_id=review.id,
                sequence=last_message.sequence + 1,
                role="user",
                kind="answer",
                content=answer,
                source_job_id=None,
                created_at=current_time,
            )
        )
        review.input_revision += 1
        review.version += 1
        review.updated_at = current_time
        emit_change(
            session,
            review.owner_id,
            "review_session",
            review.id,
            "updated",
            review.version,
        )
        await session.flush()
        return
    if answer is not None:
        raise AppError(
            "review_answer_already_saved",
            "上一条回答已经保存，请重试生成问题或直接生成报告。",
            status=409,
        )


async def enqueue_review_question(
    session: AsyncSession,
    owner_id: UUID,
    review_id: UUID,
    data: ReviewQuestionGenerationInput,
    *,
    timeout_seconds: int,
) -> dict:
    return await _enqueue_review_generation(
        session,
        owner_id,
        review_id,
        data,
        purpose="question",
        generation_instruction="",
        answer=data.answer,
        timeout_seconds=timeout_seconds,
    )


async def enqueue_review_report(
    session: AsyncSession,
    owner_id: UUID,
    review_id: UUID,
    data: ReviewReportGenerationInput,
    *,
    timeout_seconds: int,
) -> dict:
    return await _enqueue_review_generation(
        session,
        owner_id,
        review_id,
        data,
        purpose="report",
        generation_instruction=data.instruction,
        answer=None,
        timeout_seconds=timeout_seconds,
    )


async def _enqueue_review_generation(
    session: AsyncSession,
    owner_id: UUID,
    review_id: UUID,
    data: ReviewQuestionGenerationInput | ReviewReportGenerationInput,
    *,
    purpose: ReviewPurpose,
    generation_instruction: str,
    answer: str | None,
    timeout_seconds: int,
) -> dict:
    scope = f"review.{purpose}.create:{review_id}"
    replay = await replay_operation(
        session,
        owner_id,
        data.operation_id,
        scope,
        data,
    )
    if replay is not None:
        return replay
    review = await owned_review(session, owner_id, review_id, lock=True)
    if review.version != data.expected_version:
        raise review_version_conflict(review)
    if await _active_review_job(session, owner_id, review.id) is not None:
        raise AppError(
            "review_generation_active",
            "此复盘仍有 AI 作业正在处理，请等待完成或先取消。",
            status=409,
        )
    if purpose == "question":
        if review.question_count >= 3:
            raise AppError(
                "review_question_limit_reached",
                "本次复盘已完成三轮追问，可以直接生成报告。",
                status=409,
            )
        await _append_answer(
            session,
            review,
            answer,
            current_time=now(),
        )
    await lock_owner_ai_job_capacity(session, owner_id)
    state = await session.get(AIConfigCapability, "review")
    config = (
        await session.get(AIConfigVersion, state.current_version_id)
        if state is not None and state.current_version_id is not None
        else None
    )
    if config is None or config.status != "published":
        raise AppError(
            "ai_capability_unavailable",
            "AI 复盘暂未配置，请稍后重试；已有记录仍可查看。",
            status=503,
            retryable=True,
        )
    current = now()
    snapshot = await build_review_snapshot(
        session,
        review,
        purpose=purpose,
        generation_instruction=generation_instruction,
        current_time=current,
    )
    snapshot_payload = review_snapshot_payload(snapshot)
    provider = review_provider_config(config)
    messages = review_provider_messages(snapshot_payload)
    request_body = review_request_body(provider, messages, purpose)
    validate_request_body(request_body)
    input_summary = {
        "resource_type": "review_snapshot",
        "review_session_id": str(review.id),
        "review_snapshot_id": str(snapshot.id),
        "purpose": purpose,
        "activity_date": review.activity_date.isoformat(),
        "facts_sha256": snapshot.facts_sha256,
        "request_schema_version": REVIEW_REQUEST_SCHEMA_VERSION,
    }
    estimate = estimate_reservation_budget(
        config,
        request_body=request_body,
        input_summary=input_summary,
        capability="review",
    )
    daily = await get_or_create_daily_allowance(
        session,
        owner_id,
        accepted_at=current,
    )
    job = AIJob(
        id=uuid4(),
        owner_id=owner_id,
        capability="review",
        config_version_id=config.id,
        input_media_id=None,
        conversation_id=None,
        planning_snapshot_id=None,
        review_session_id=review.id,
        review_snapshot_id=snapshot.id,
        quota_mode="user",
        status="queued",
        input_summary=input_summary,
        output=None,
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
    append_planning_job_event(
        session,
        job,
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
        "review": review_result(review),
        "job": job_result(job),
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


async def load_review_work(
    database: Database,
    claim: ClaimedJob,
) -> ReviewWork:
    if claim.review_session_id is None or claim.review_snapshot_id is None:
        raise ReviewWorkUnavailable(
            "ai_job_input_invalid",
            "复盘作业缺少输入快照，请重新生成。",
            "validation",
        )
    current = now()
    async with database.transaction() as session:
        job = await session.scalar(
            select(AIJob)
            .where(
                AIJob.id == claim.id,
                AIJob.owner_id == claim.owner_id,
                AIJob.capability == "review",
                AIJob.status == "running",
                AIJob.lease_id == claim.lease_id,
                AIJob.lease_until > current,
                AIJob.deadline_at > current,
            )
            .with_for_update()
        )
        if job is None:
            raise ReviewWorkUnavailable(
                "ai_job_superseded",
                "作业已被取消或由其他执行进程接管。",
                "worker",
            )
        snapshot = await session.scalar(
            select(ReviewInputSnapshot).where(
                ReviewInputSnapshot.id == claim.review_snapshot_id,
                ReviewInputSnapshot.owner_id == claim.owner_id,
                ReviewInputSnapshot.review_session_id == claim.review_session_id,
            )
        )
        config = await session.get(AIConfigVersion, job.config_version_id)
        if snapshot is None:
            raise ReviewWorkUnavailable(
                "ai_job_input_invalid",
                "复盘输入快照已不可用，请重新生成。",
                "validation",
            )
        if config is None or config.capability != "review":
            raise ReviewWorkUnavailable(
                "ai_configuration_unavailable",
                "复盘配置已不可用，请重新生成。",
                "configuration",
            )
        purpose: ReviewPurpose = (
            "question" if snapshot.purpose == "question" else "report"
        )
        payload = review_snapshot_payload(snapshot)
        messages = review_provider_messages(payload)
        provider = review_provider_config(config)
        request_body = review_request_body(provider, messages, purpose)
        validate_request_body(request_body)
        return ReviewWork(
            config=provider,
            snapshot=payload,
            messages=messages,
            request_body=request_body,
            purpose=purpose,
        )


def _allowed_sources(snapshot: ReviewInputSnapshot) -> tuple[set[str], set[str]]:
    fact_sources: set[str] = set()
    for value in snapshot.facts.values():
        if not isinstance(value, dict):
            continue
        items = value.get("items")
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("source_key"), str):
                fact_sources.add(item["source_key"])
    user_sources: set[str] = set()
    if snapshot.user_context.get("supplemental_facts"):
        user_sources.add("user:supplemental_facts")
    messages = snapshot.user_context.get("messages")
    if isinstance(messages, list):
        user_sources.update(
            item["source_key"]
            for item in messages
            if isinstance(item, dict)
            and item.get("role") == "user"
            and isinstance(item.get("source_key"), str)
        )
    memory_sources = {
        f"memory:{item['id']}"
        for item in snapshot.memory_context
        if isinstance(item, dict) and item.get("id")
    }
    return fact_sources | user_sources, user_sources | memory_sources


def _validated_report(
    snapshot: ReviewInputSnapshot,
    output: ReviewReportOutput,
) -> tuple[list[dict], list[dict], list[str]]:
    fact_sources, candidate_sources = _allowed_sources(snapshot)
    facts = [item.model_dump(mode="json") for item in output.facts]
    unknown = [
        item["source_key"] for item in facts if item["source_key"] not in fact_sources
    ]
    if unknown:
        raise AppError(
            "review_fact_source_invalid",
            "复盘报告引用了输入快照中不存在的事实。",
            status=422,
            details={"source_keys": unknown[:10]},
        )
    candidates = [
        item.model_dump(mode="json")
        for item in output.memory_candidates
        if set(item.source_keys).issubset(candidate_sources)
    ]
    uncertainties = list(output.uncertainties)
    removed = len(output.memory_candidates) - len(candidates)
    if removed and len(uncertainties) < 20:
        uncertainties.append(
            f"{removed} 项记忆建议因缺少用户明确来源而未保留。"
        )
    return facts, candidates, uncertainties


async def complete_review_job(
    database: Database,
    claim: ClaimedJob,
    result: ReviewProviderResult,
    *,
    quality_sample_retention_days: int = 30,
) -> bool:
    current = now()
    async with database.transaction() as session:
        job = await session.scalar(
            select(AIJob)
            .where(
                AIJob.id == claim.id,
                AIJob.owner_id == claim.owner_id,
                AIJob.capability == "review",
                AIJob.status == "running",
                AIJob.lease_id == claim.lease_id,
                AIJob.lease_until > current,
            )
            .with_for_update()
        )
        if job is None:
            return False
        review = await session.scalar(
            select(ReviewSession)
            .where(
                ReviewSession.id == job.review_session_id,
                ReviewSession.owner_id == job.owner_id,
            )
            .with_for_update()
        )
        snapshot = await session.scalar(
            select(ReviewInputSnapshot).where(
                ReviewInputSnapshot.id == job.review_snapshot_id,
                ReviewInputSnapshot.owner_id == job.owner_id,
            )
        )
        body_log = await locked_attempt_body_log(
            session,
            job,
            claim.attempt_number,
        )
        if review is None or snapshot is None or body_log is None:
            return False
        if body_log.status != "running":
            return False
        validate_response_body(result.response_body)
        body_log.response_body = result.response_body
        if job.deadline_at <= current:
            await _finish_review_failure(
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

        if snapshot.purpose == "question":
            if not isinstance(result.output, ReviewQuestionOutput):
                await _finish_review_failure(
                    session,
                    job,
                    status="failed",
                    error_code="review_output_invalid",
                    error_message="复盘服务返回了错误的结果类型，请重新尝试。",
                    error_stage="validation",
                    provider_request_id=result.provider_request_id,
                    http_status=result.http_status,
                    provider_usage=result.usage,
                )
                return False
            if review.question_count >= 3:
                await _finish_review_failure(
                    session,
                    job,
                    status="failed",
                    error_code="review_question_limit_reached",
                    error_message="本次复盘已完成三轮追问。",
                    error_stage="validation",
                    provider_request_id=result.provider_request_id,
                    http_status=result.http_status,
                    provider_usage=result.usage,
                )
                return False
            last_sequence = (
                await session.scalar(
                    select(func.max(ReviewMessage.sequence)).where(
                        ReviewMessage.review_session_id == review.id
                    )
                )
                or 0
            )
            message = ReviewMessage(
                owner_id=job.owner_id,
                review_session_id=review.id,
                sequence=last_sequence + 1,
                role="assistant",
                kind="question",
                content=result.output.question,
                source_job_id=job.id,
                created_at=current,
            )
            session.add(message)
            review.question_count += 1
            review.version += 1
            review.updated_at = current
            emit_change(
                session,
                job.owner_id,
                "review_session",
                review.id,
                "updated",
                review.version,
            )
            await session.flush()
            output = {
                "resource_type": "review_question",
                "review_session_id": str(review.id),
                "message_id": str(message.id),
            }
        else:
            if not isinstance(result.output, ReviewReportOutput):
                await _finish_review_failure(
                    session,
                    job,
                    status="failed",
                    error_code="review_output_invalid",
                    error_message="复盘服务返回了错误的结果类型，请重新尝试。",
                    error_stage="validation",
                    provider_request_id=result.provider_request_id,
                    http_status=result.http_status,
                    provider_usage=result.usage,
                )
                return False
            try:
                facts, candidates, uncertainties = _validated_report(
                    snapshot,
                    result.output,
                )
            except AppError as exc:
                await _finish_review_failure(
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
            next_version = (
                await session.scalar(
                    select(func.max(ReviewReportVersion.version)).where(
                        ReviewReportVersion.review_session_id == review.id
                    )
                )
                or 0
            ) + 1
            report = ReviewReportVersion(
                owner_id=job.owner_id,
                review_session_id=review.id,
                version=next_version,
                source_job_id=job.id,
                input_snapshot_id=snapshot.id,
                summary=result.output.summary,
                facts=facts,
                user_statements=list(result.output.user_statements),
                insights=[
                    item.model_dump(mode="json")
                    for item in result.output.insights
                ],
                next_actions=list(result.output.next_actions),
                uncertainties=uncertainties,
                created_at=current,
            )
            session.add(report)
            await session.flush()
            for position, candidate in enumerate(candidates):
                session.add(
                    MemoryProposal(
                        owner_id=job.owner_id,
                        report_version_id=report.id,
                        position=position,
                        kind=candidate["kind"],
                        title=candidate["title"],
                        content=candidate["content"],
                        rationale=candidate["rationale"],
                        status="pending",
                        confirmed_at=None,
                        rejected_at=None,
                        version=1,
                        created_at=current,
                        updated_at=current,
                    )
                )
            emit_change(
                session,
                job.owner_id,
                "review_report",
                report.id,
                "created",
                report.version,
            )
            output = {
                "resource_type": "review_report",
                "review_session_id": str(review.id),
                "report_id": str(report.id),
                "version": report.version,
            }

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
        job.status = "succeeded"
        job.output = output
        job.error_code = None
        job.error_message = None
        job.finished_at = current
        job.updated_at = current
        job.version += 1
        append_planning_job_event(
            session,
            job,
            payload={
                "status": "succeeded",
                "attempt": claim.attempt_number,
                **output,
            },
        )
        clear_job_lease(job)
        await create_quality_sample_if_enabled(
            session,
            job,
            body_log,
            retention_days=quality_sample_retention_days,
        )
        return True


async def _finish_review_failure(
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
    job.status = status
    job.error_code = error_code
    job.error_message = error_message
    job.finished_at = now()
    job.updated_at = job.finished_at
    job.version += 1
    append_planning_job_event(
        session,
        job,
        payload={
            "status": status,
            "attempt": job.attempt_count,
            "error_code": error_code,
            "error_message": error_message,
        },
    )
    await release_reservation(session, job=job, reason=status)
    clear_job_lease(job)


async def reject_or_retry_review_job(
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
