from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from itime.ai.conversation_models import AIJobEvent, ChatGeneration
from itime.ai.cost_service import add_provider_cost
from itime.ai.governance_service import (
    add_attempt_body_log,
    finish_attempt_body_log,
)
from itime.ai.models import (
    AIJob,
    AIJobAttempt,
    AIJobConfigRoute,
    AIProviderCost,
    TemporaryMedia,
    VoiceTranscription,
)
from itime.auth.models import User
from itime.auth.security import now
from itime.database import Database
from itime.errors import AppError
from itime.quota.service import release_reservation, settle_success
from itime.tasks.models import UserOperation

TERMINAL_JOB_STATES = {"succeeded", "failed", "timed_out", "cancelled"}
JOB_EVENT_RETENTION = timedelta(days=7)


@dataclass(frozen=True)
class ClaimedJob:
    id: UUID
    owner_id: UUID
    capability: str
    config_version_id: UUID
    input_media_id: UUID | None
    conversation_id: UUID | None
    planning_snapshot_id: UUID | None
    review_session_id: UUID | None
    review_snapshot_id: UUID | None
    lease_id: UUID
    attempt_number: int
    deadline_at: datetime
    terminal_error_code: str | None = None
    terminal_error_message: str | None = None
    terminal_error_stage: str | None = None


@dataclass(frozen=True)
class FinalizedAttempt:
    attempt: AIJobAttempt
    provider_cost: AIProviderCost


def job_result(job: AIJob) -> dict:
    return {
        "id": job.id,
        "capability": job.capability,
        "conversation_id": job.conversation_id,
        "review_session_id": job.review_session_id,
        "status": job.status,
        "input_summary": {
            key: value
            for key, value in job.input_summary.items()
            if not key.startswith("_")
        },
        "output": job.output,
        "error": (
            {"code": job.error_code, "message": job.error_message}
            if job.error_code is not None
            else None
        ),
        "attempt_count": job.attempt_count,
        "max_attempts": job.max_attempts,
        "last_event_sequence": job.last_event_sequence,
        "deadline_at": job.deadline_at,
        "version": job.version,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "finished_at": job.finished_at,
    }


async def locked_job(
    session: AsyncSession,
    owner_id: UUID,
    job_id: UUID,
) -> AIJob:
    job = await session.scalar(
        select(AIJob)
        .where(AIJob.id == job_id, AIJob.owner_id == owner_id)
        .with_for_update()
    )
    if job is None:
        raise AppError("ai_job_not_found", "未找到此 AI 作业。", status=404)
    return job


async def _cancel_transcription_projection(
    session: AsyncSession,
    job: AIJob,
    *,
    current: datetime,
    resolution: str,
    media_reason: str,
) -> None:
    item = await session.scalar(
        select(VoiceTranscription)
        .where(
            VoiceTranscription.job_id == job.id,
            VoiceTranscription.owner_id == job.owner_id,
        )
        .with_for_update()
    )
    if item is None:
        return
    media = await session.scalar(
        select(TemporaryMedia)
        .where(
            TemporaryMedia.id == item.media_id,
            TemporaryMedia.owner_id == item.owner_id,
        )
        .with_for_update()
    )
    if item.resolution == "pending":
        item.resolution = resolution
        item.transcript = None
        item.transcript_sha256 = None
        item.language = None
        item.resolved_at = current
        item.text_purged_at = current
        item.updated_at = current
        item.version += 1
    if media is not None:
        delete_media(media, reason=media_reason)


async def _cancel_calendar_import_projection(
    session: AsyncSession,
    job: AIJob,
    *,
    current: datetime,
    media_reason: str,
) -> None:
    from itime.calendar.models import CalendarImportBatch

    batch = await session.scalar(
        select(CalendarImportBatch)
        .where(
            CalendarImportBatch.origin_job_id == job.id,
            CalendarImportBatch.owner_id == job.owner_id,
        )
        .with_for_update()
    )
    if batch is not None:
        batch.state = "cancelled"
        batch.error_code = None
        batch.error_message = None
        batch.input_body_ciphertext = None
        batch.input_body_expires_at = None
        batch.updated_at = current
    if job.input_media_id is not None:
        media = await session.scalar(
            select(TemporaryMedia)
            .where(
                TemporaryMedia.id == job.input_media_id,
                TemporaryMedia.owner_id == job.owner_id,
            )
            .with_for_update()
        )
        if media is not None and media.encrypted_content is not None:
            delete_media(media, reason=media_reason)


async def cancel_job(
    session: AsyncSession,
    job: AIJob,
    *,
    transcription_resolution: str = "discarded",
    transcription_media_reason: str = "cancelled",
) -> bool:
    if job.status in TERMINAL_JOB_STATES:
        return False
    current = now()
    await finish_running_attempt(session, job, status="cancelled")
    job.status = "cancelled"
    job.output = None
    job.error_code = None
    job.error_message = None
    job.finished_at = current
    job.updated_at = current
    job.version += 1
    if job.capability == "transcription":
        await _cancel_transcription_projection(
            session,
            job,
            current=current,
            resolution=transcription_resolution,
            media_reason=transcription_media_reason,
        )
    elif job.capability == "calendar_screenshot":
        await _cancel_calendar_import_projection(
            session,
            job,
            current=current,
            media_reason=transcription_media_reason,
        )
    message_id = (
        await session.scalar(
            select(ChatGeneration.response_message_id).where(
                ChatGeneration.job_id == job.id
            )
        )
        if job.capability == "planning" and job.conversation_id is not None
        else None
    )
    append_planning_job_event(
        session,
        job,
        payload={
            "status": "cancelled",
            "attempt": job.attempt_count,
            **({"message_id": str(message_id)} if message_id is not None else {}),
        },
    )
    clear_job_lease(job)
    return True


async def cancel_active_ai_jobs_for_owner(
    session: AsyncSession,
    owner_id: UUID,
) -> int:
    from itime.ai.conversation_service import cancel_conversation_projection

    jobs = list(
        await session.scalars(
            select(AIJob)
            .where(
                AIJob.owner_id == owner_id,
                AIJob.status.in_(("queued", "running")),
            )
            .order_by(AIJob.created_at, AIJob.id)
            .with_for_update()
        )
    )
    cancelled = 0
    for job in jobs:
        if not await cancel_job(
            session,
            job,
            transcription_media_reason="account_deletion",
        ):
            continue
        await cancel_conversation_projection(session, job)
        await release_reservation(
            session,
            job=job,
            reason="cancelled",
        )
        cancelled += 1
    return cancelled


async def finish_running_attempt(
    session: AsyncSession,
    job: AIJob,
    *,
    status: str,
    error_code: str | None = None,
    error_stage: str | None = None,
    provider_request_id: str | None = None,
    http_status: int | None = None,
    provider_usage: object | None = None,
) -> FinalizedAttempt | None:
    if job.lease_id is None:
        return None
    finished_at = now()
    attempt = await session.scalar(
        select(AIJobAttempt)
        .where(
            AIJobAttempt.job_id == job.id,
            AIJobAttempt.lease_id == job.lease_id,
            AIJobAttempt.status == "running",
        )
        .with_for_update()
    )
    if attempt is None:
        return None
    attempt.status = status
    attempt.error_code = error_code
    attempt.error_stage = error_stage
    attempt.provider_request_id = provider_request_id
    attempt.http_status = http_status
    attempt.finished_at = finished_at
    await finish_attempt_body_log(
        session,
        job,
        status=status,
        error_code=error_code,
        error_stage=error_stage,
        provider_request_id=provider_request_id,
        http_status=http_status,
        finished_at=finished_at,
    )
    provider_cost = await add_provider_cost(
        session,
        job,
        attempt,
        provider_usage=provider_usage,
    )
    return FinalizedAttempt(attempt=attempt, provider_cost=provider_cost)


def clear_job_lease(job: AIJob) -> None:
    job.lease_id = None
    job.lease_until = None


async def claim_next_job(
    database: Database,
    *,
    capability: str | None = None,
    lease_seconds: int = 300,
    body_log_retention_days: int = 7,
    terminal_only: bool = False,
) -> ClaimedJob | None:
    for _ in range(20):
        current = now()
        async with database.transaction() as session:
            statement = (
                select(AIJob)
                .join(User, User.id == AIJob.owner_id)
                .where(
                    User.status == "active",
                    or_(
                        and_(
                            AIJob.status == "queued",
                            AIJob.available_at <= current,
                        ),
                        and_(
                            AIJob.status == "running",
                            AIJob.lease_until <= current,
                        ),
                    )
                )
                .order_by(AIJob.available_at, AIJob.created_at, AIJob.id)
                .limit(1)
                .with_for_update(skip_locked=True, of=AIJob)
            )
            if capability is not None:
                statement = statement.where(AIJob.capability == capability)
            if terminal_only:
                statement = statement.where(
                    or_(
                        AIJob.deadline_at <= current,
                        AIJob.attempt_count >= AIJob.max_attempts,
                    )
                )
            job = await session.scalar(statement)
            if job is None:
                return None
            stale_running = job.status == "running"
            if stale_running and job.lease_until is not None and job.lease_until > current:
                return None
            terminal_error_code = None
            terminal_error_message = None
            terminal_error_stage = None
            if job.deadline_at <= current:
                terminal_error_code = "ai_job_deadline_exceeded"
                terminal_error_message = "AI 作业已超过总等待时间，请重新提交。"
                terminal_error_stage = "deadline"
            elif job.attempt_count >= job.max_attempts:
                terminal_error_code = "ai_job_attempts_exhausted"
                terminal_error_message = "AI 作业重试次数已用尽，请重新提交。"
                terminal_error_stage = "worker"

            if stale_running:
                await finish_running_attempt(
                    session,
                    job,
                    status=(
                        "timed_out"
                        if terminal_error_code == "ai_job_deadline_exceeded"
                        else "abandoned"
                    ),
                    error_code=(
                        terminal_error_code
                        if terminal_error_code == "ai_job_deadline_exceeded"
                        else "ai_job_lease_expired"
                    ),
                    error_stage=(
                        terminal_error_stage
                        if terminal_error_code == "ai_job_deadline_exceeded"
                        else "worker"
                    ),
                )

            route_config_id = job.config_version_id
            if terminal_error_code is None:
                next_attempt = job.attempt_count + 1
                selected_config_id = await session.scalar(
                    select(AIJobConfigRoute.config_version_id)
                    .where(
                        AIJobConfigRoute.job_id == job.id,
                        AIJobConfigRoute.attempt_number == next_attempt,
                    )
                    .with_for_update(read=True)
                )
                if selected_config_id is None:
                    terminal_error_code = "ai_job_route_invalid"
                    terminal_error_message = (
                        "AI 作业调用路由不完整，请重新提交。"
                    )
                    terminal_error_stage = "configuration"
                else:
                    route_config_id = selected_config_id

            lease_id = uuid4()
            job.status = "running"
            job.lease_id = lease_id
            job.lease_until = (
                current + timedelta(seconds=min(30, lease_seconds))
                if terminal_error_code is not None
                else min(
                    current + timedelta(seconds=lease_seconds),
                    job.deadline_at,
                )
            )
            job.error_code = None
            job.error_message = None
            job.updated_at = current
            job.version += 1
            if terminal_error_code is not None:
                await session.flush()
                return ClaimedJob(
                    id=job.id,
                    owner_id=job.owner_id,
                    capability=job.capability,
                    config_version_id=route_config_id,
                    input_media_id=job.input_media_id,
                    conversation_id=job.conversation_id,
                    planning_snapshot_id=job.planning_snapshot_id,
                    review_session_id=job.review_session_id,
                    review_snapshot_id=job.review_snapshot_id,
                    lease_id=lease_id,
                    attempt_number=job.attempt_count,
                    deadline_at=job.deadline_at,
                    terminal_error_code=terminal_error_code,
                    terminal_error_message=terminal_error_message,
                    terminal_error_stage=terminal_error_stage,
                )

            job.attempt_count += 1
            session.add(
                AIJobAttempt(
                    job_id=job.id,
                    owner_id=job.owner_id,
                    attempt_number=job.attempt_count,
                    lease_id=lease_id,
                    status="running",
                    started_at=current,
                )
            )
            await session.flush()
            add_attempt_body_log(
                session,
                job,
                attempt_number=job.attempt_count,
                started_at=current,
                retention_days=body_log_retention_days,
            )
            append_planning_job_event(
                session,
                job,
                payload={
                    "status": "running",
                    "attempt": job.attempt_count,
                },
            )
            await session.flush()
            return ClaimedJob(
                id=job.id,
                owner_id=job.owner_id,
                capability=job.capability,
                config_version_id=route_config_id,
                input_media_id=job.input_media_id,
                conversation_id=job.conversation_id,
                planning_snapshot_id=job.planning_snapshot_id,
                review_session_id=job.review_session_id,
                review_snapshot_id=job.review_snapshot_id,
                lease_id=lease_id,
                attempt_number=job.attempt_count,
                deadline_at=job.deadline_at,
            )
    return None


async def complete_job(
    database: Database,
    claim: ClaimedJob,
    output: dict,
    *,
    provider_request_id: str | None = None,
    http_status: int | None = None,
    provider_usage: object | None = None,
) -> bool:
    current = now()
    async with database.transaction() as session:
        job = await session.scalar(
            select(AIJob)
            .where(
                AIJob.id == claim.id,
                AIJob.status == "running",
                AIJob.lease_id == claim.lease_id,
                AIJob.lease_until > current,
            )
            .with_for_update()
        )
        if job is None:
            return False
        if job.deadline_at <= current:
            await finish_running_attempt(
                session,
                job,
                status="timed_out",
                error_code="ai_job_deadline_exceeded",
                error_stage="deadline",
                provider_request_id=provider_request_id,
                http_status=http_status,
                provider_usage=provider_usage,
            )
            job.status = "timed_out"
            job.error_code = "ai_job_deadline_exceeded"
            job.error_message = "AI 作业已超过总等待时间，请重新提交。"
            job.finished_at = current
            job.updated_at = current
            job.version += 1
            await release_reservation(
                session,
                job=job,
                reason="timed_out",
            )
            clear_job_lease(job)
            return False
        finalized = await finish_running_attempt(
            session,
            job,
            status="succeeded",
            provider_request_id=provider_request_id,
            http_status=http_status,
            provider_usage=provider_usage,
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
        clear_job_lease(job)
        return True


async def reject_or_retry_job(
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
    current = now()
    async with database.transaction() as session:
        job = await session.scalar(
            select(AIJob)
            .where(
                AIJob.id == claim.id,
                AIJob.status == "running",
                AIJob.lease_id == claim.lease_id,
                AIJob.lease_until > current,
            )
            .with_for_update()
        )
        if job is None:
            return None
        can_retry = (
            retryable
            and job.attempt_count < job.max_attempts
            and current + timedelta(seconds=min(30, 2 ** job.attempt_count))
            < job.deadline_at
        )
        if can_retry:
            delay = min(30, 2 ** job.attempt_count)
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
            append_planning_job_event(
                session,
                job,
                payload={
                    "status": "queued",
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
            "AI 作业已超过总等待时间，请重新提交。"
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
        append_planning_job_event(
            session,
            job,
            payload={
                "status": status,
                "attempt": claim.attempt_number,
                "error_code": final_code,
                "error_message": final_message,
            },
        )
        await release_reservation(
            session,
            job=job,
            reason=status,
        )
        clear_job_lease(job)
        return status


def append_planning_job_event(
    session: AsyncSession,
    job: AIJob,
    *,
    payload: dict,
) -> AIJobEvent | None:
    if job.capability not in {"planning", "review"} or (
        job.conversation_id is not None and payload.get("status") == "running"
    ):
        return None
    job.last_event_sequence += 1
    event = AIJobEvent(
        job_id=job.id,
        owner_id=job.owner_id,
        sequence=job.last_event_sequence,
        generation=job.attempt_count,
        kind="job_state",
        payload=payload,
        expires_at=now() + JOB_EVENT_RETENTION,
    )
    session.add(event)
    return event


async def renew_job_lease(
    database: Database,
    claim: ClaimedJob,
    *,
    lease_seconds: int,
) -> bool:
    current = now()
    result = None
    async with database.transaction() as session:
        result = await session.execute(
            update(AIJob)
            .where(
                AIJob.id == claim.id,
                AIJob.status == "running",
                AIJob.lease_id == claim.lease_id,
                AIJob.lease_until > current,
                AIJob.deadline_at > current,
            )
            .values(
                lease_until=min(
                    current + timedelta(seconds=lease_seconds),
                    claim.deadline_at,
                ),
                updated_at=current,
            )
        )
    return result.rowcount == 1


def delete_media(
    media: TemporaryMedia,
    *,
    reason: str,
) -> bool:
    if media.encrypted_content is None:
        return False
    media.encrypted_content = None
    media.deleted_at = now()
    media.deletion_reason = reason
    return True


async def clear_temporary_media_for_account_deletion(
    session: AsyncSession,
    owner_id: UUID,
    at: datetime,
) -> int:
    media_rows = list(
        await session.scalars(
            select(TemporaryMedia)
            .where(
                TemporaryMedia.owner_id == owner_id,
                TemporaryMedia.encrypted_content.is_not(None),
            )
            .order_by(TemporaryMedia.created_at, TemporaryMedia.id)
            .with_for_update()
        )
    )
    for media in media_rows:
        media.encrypted_content = None
        media.deleted_at = at
        media.deletion_reason = "account_deletion"
    return len(media_rows)


async def purge_voice_transcriptions_for_account_deletion(
    session: AsyncSession,
    owner_id: UUID,
    at: datetime,
) -> None:
    job_ids = list(
        await session.scalars(
            select(VoiceTranscription.job_id)
            .where(VoiceTranscription.owner_id == owner_id)
            .order_by(VoiceTranscription.job_id)
        )
    )
    if not job_ids:
        return
    list(
        await session.scalars(
            select(AIJob)
            .where(
                AIJob.owner_id == owner_id,
                AIJob.id.in_(job_ids),
            )
            .order_by(AIJob.id)
            .with_for_update()
        )
    )
    items = list(
        await session.scalars(
            select(VoiceTranscription)
            .where(VoiceTranscription.owner_id == owner_id)
            .order_by(VoiceTranscription.job_id)
            .with_for_update()
        )
    )
    media_ids = [item.media_id for item in items]
    media_rows = list(
        await session.scalars(
            select(TemporaryMedia)
            .where(
                TemporaryMedia.owner_id == owner_id,
                TemporaryMedia.id.in_(media_ids),
            )
            .order_by(TemporaryMedia.id)
            .with_for_update()
        )
    )
    for item in items:
        changed = False
        if item.resolution == "pending":
            item.resolution = "discarded"
            item.resolved_at = at
            item.text_purged_at = at
            changed = True
        if item.transcript is not None:
            item.transcript = None
            item.transcript_sha256 = None
            item.language = None
            item.text_purged_at = at
            changed = True
        if changed:
            item.updated_at = at
            item.version += 1
    for media in media_rows:
        if media.encrypted_content is not None:
            media.encrypted_content = None
            media.deleted_at = at
            media.deletion_reason = "account_deletion"
    await session.execute(
        update(UserOperation)
        .where(
            UserOperation.owner_id == owner_id,
            UserOperation.scope.startswith(
                "voice_transcription.resolve:",
                autoescape=True,
            ),
            UserOperation.response.is_not(None),
        )
        .values(
            response=None,
            response_purged_at=at,
        )
    )


async def cleanup_expired_media(database: Database) -> int:
    current = now()
    async with database.transaction() as session:
        media_rows = list(
            await session.scalars(
                select(TemporaryMedia)
                .where(
                    TemporaryMedia.encrypted_content.is_not(None),
                    TemporaryMedia.purpose != "voice_transcription",
                    TemporaryMedia.expires_at <= current,
                )
                .order_by(TemporaryMedia.expires_at, TemporaryMedia.id)
                .limit(100)
                .with_for_update(skip_locked=True)
            )
        )
        for media in media_rows:
            delete_media(media, reason="expired")
        return len(media_rows)
