from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from itime.ai.models import AIJob, AIJobAttempt, TemporaryMedia
from itime.auth.security import now
from itime.database import Database
from itime.errors import AppError

TERMINAL_JOB_STATES = {"succeeded", "failed", "timed_out", "cancelled"}


@dataclass(frozen=True)
class ClaimedJob:
    id: UUID
    owner_id: UUID
    capability: str
    config_version_id: UUID
    input_media_id: UUID
    lease_id: UUID
    attempt_number: int
    deadline_at: datetime


def job_result(job: AIJob) -> dict:
    return {
        "id": job.id,
        "capability": job.capability,
        "status": job.status,
        "input_summary": job.input_summary,
        "output": job.output,
        "error": (
            {"code": job.error_code, "message": job.error_message}
            if job.error_code is not None
            else None
        ),
        "attempt_count": job.attempt_count,
        "max_attempts": job.max_attempts,
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


async def cancel_job(session: AsyncSession, job: AIJob) -> bool:
    if job.status in TERMINAL_JOB_STATES:
        return False
    current = now()
    await _finish_running_attempt(session, job, status="cancelled")
    job.status = "cancelled"
    job.output = None
    job.error_code = None
    job.error_message = None
    job.finished_at = current
    job.updated_at = current
    job.version += 1
    _clear_lease(job)
    return True


async def _finish_running_attempt(
    session: AsyncSession,
    job: AIJob,
    *,
    status: str,
    error_code: str | None = None,
    error_stage: str | None = None,
    provider_request_id: str | None = None,
    http_status: int | None = None,
) -> None:
    if job.lease_id is None:
        return
    await session.execute(
        update(AIJobAttempt)
        .where(
            AIJobAttempt.job_id == job.id,
            AIJobAttempt.lease_id == job.lease_id,
            AIJobAttempt.status == "running",
        )
        .values(
            status=status,
            error_code=error_code,
            error_stage=error_stage,
            provider_request_id=provider_request_id,
            http_status=http_status,
            finished_at=now(),
        )
    )


def _clear_lease(job: AIJob) -> None:
    job.lease_id = None
    job.lease_until = None


async def claim_next_job(
    database: Database,
    *,
    capability: str | None = None,
    lease_seconds: int = 300,
) -> ClaimedJob | None:
    current = now()
    for _ in range(20):
        async with database.transaction() as session:
            statement = (
                select(AIJob)
                .where(
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
                .with_for_update(skip_locked=True)
            )
            if capability is not None:
                statement = statement.where(AIJob.capability == capability)
            job = await session.scalar(statement)
            if job is None:
                return None
            stale_running = job.status == "running"
            if stale_running and job.lease_until is not None and job.lease_until > current:
                return None
            if job.deadline_at <= current:
                await _finish_running_attempt(
                    session,
                    job,
                    status="timed_out",
                    error_code="ai_job_deadline_exceeded",
                    error_stage="deadline",
                )
                job.status = "timed_out"
                job.error_code = "ai_job_deadline_exceeded"
                job.error_message = "AI 作业已超过总等待时间，请重新提交。"
                job.finished_at = current
                job.updated_at = current
                job.version += 1
                _clear_lease(job)
                continue
            if job.attempt_count >= job.max_attempts:
                await _finish_running_attempt(
                    session,
                    job,
                    status="failed",
                    error_code="ai_job_attempts_exhausted",
                    error_stage="worker",
                )
                job.status = "failed"
                job.error_code = "ai_job_attempts_exhausted"
                job.error_message = "AI 作业重试次数已用尽，请重新提交。"
                job.finished_at = current
                job.updated_at = current
                job.version += 1
                _clear_lease(job)
                continue
            if stale_running:
                await _finish_running_attempt(
                    session,
                    job,
                    status="abandoned",
                    error_code="ai_job_lease_expired",
                    error_stage="worker",
                )

            lease_id = uuid4()
            job.status = "running"
            job.attempt_count += 1
            job.lease_id = lease_id
            job.lease_until = min(
                current + timedelta(seconds=lease_seconds),
                job.deadline_at,
            )
            job.error_code = None
            job.error_message = None
            job.updated_at = current
            job.version += 1
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
            return ClaimedJob(
                id=job.id,
                owner_id=job.owner_id,
                capability=job.capability,
                config_version_id=job.config_version_id,
                input_media_id=job.input_media_id,
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
) -> bool:
    current = now()
    async with database.transaction() as session:
        job = await session.scalar(
            select(AIJob)
            .where(
                AIJob.id == claim.id,
                AIJob.status == "running",
                AIJob.lease_id == claim.lease_id,
            )
            .with_for_update()
        )
        if job is None:
            return False
        if job.deadline_at <= current:
            await _finish_running_attempt(
                session,
                job,
                status="timed_out",
                error_code="ai_job_deadline_exceeded",
                error_stage="deadline",
                provider_request_id=provider_request_id,
                http_status=http_status,
            )
            job.status = "timed_out"
            job.error_code = "ai_job_deadline_exceeded"
            job.error_message = "AI 作业已超过总等待时间，请重新提交。"
            job.finished_at = current
            job.updated_at = current
            job.version += 1
            _clear_lease(job)
            return False
        await _finish_running_attempt(
            session,
            job,
            status="succeeded",
            provider_request_id=provider_request_id,
            http_status=http_status,
        )
        job.status = "succeeded"
        job.output = output
        job.error_code = None
        job.error_message = None
        job.finished_at = current
        job.updated_at = current
        job.version += 1
        _clear_lease(job)
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
) -> str | None:
    current = now()
    async with database.transaction() as session:
        job = await session.scalar(
            select(AIJob)
            .where(
                AIJob.id == claim.id,
                AIJob.status == "running",
                AIJob.lease_id == claim.lease_id,
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
            await _finish_running_attempt(
                session,
                job,
                status="retryable_failed",
                error_code=error_code,
                error_stage=error_stage,
                provider_request_id=provider_request_id,
                http_status=http_status,
            )
            job.status = "queued"
            job.available_at = current + timedelta(seconds=delay)
            job.error_code = error_code
            job.error_message = error_message
            job.updated_at = current
            job.version += 1
            _clear_lease(job)
            return "queued"

        timed_out = job.deadline_at <= current
        status = "timed_out" if timed_out else "failed"
        final_code = "ai_job_deadline_exceeded" if timed_out else error_code
        final_message = (
            "AI 作业已超过总等待时间，请重新提交。"
            if timed_out
            else error_message
        )
        await _finish_running_attempt(
            session,
            job,
            status=status,
            error_code=final_code,
            error_stage="deadline" if timed_out else error_stage,
            provider_request_id=provider_request_id,
            http_status=http_status,
        )
        job.status = status
        job.error_code = final_code
        job.error_message = final_message
        job.finished_at = current
        job.updated_at = current
        job.version += 1
        _clear_lease(job)
        return status


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


async def cleanup_expired_media(database: Database) -> int:
    current = now()
    async with database.transaction() as session:
        media_rows = list(
            await session.scalars(
                select(TemporaryMedia)
                .where(
                    TemporaryMedia.encrypted_content.is_not(None),
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
