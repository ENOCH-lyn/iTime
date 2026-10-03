import json
import logging
from datetime import timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from itime.ai.governance_models import (
    AIBodyLog,
    AIQualitySample,
    UserPrivacySetting,
)
from itime.ai.models import AIJob
from itime.auth.security import now
from itime.database import Database

MAX_AI_BODY_BYTES = 1024 * 1024
logger = logging.getLogger("itime.ai.governance")


def validate_request_body(request_body: dict) -> None:
    encoded = json.dumps(
        request_body,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    if len(encoded) > MAX_AI_BODY_BYTES:
        raise ValueError("AI request body exceeds governance storage limit")


def validate_response_body(response_body: str) -> None:
    if len(response_body.encode()) > MAX_AI_BODY_BYTES:
        raise ValueError("AI response body exceeds governance storage limit")


def add_attempt_body_log(
    session: AsyncSession,
    job: AIJob,
    *,
    attempt_number: int,
    started_at,
    retention_days: int,
) -> AIBodyLog:
    initial_request = {
        "capability": job.capability,
        "input_summary": job.input_summary,
        "state": "not_prepared",
    }
    validate_request_body(initial_request)
    body_log = AIBodyLog(
        job_id=job.id,
        owner_id=job.owner_id,
        attempt_number=attempt_number,
        capability=job.capability,
        request_body=initial_request,
        status="running",
        started_at=started_at,
        expires_at=started_at + timedelta(days=retention_days),
    )
    session.add(body_log)
    return body_log


async def prepare_attempt_body_log(
    database: Database,
    claim,
    request_body: dict,
) -> bool:
    validate_request_body(request_body)
    current = now()
    async with database.transaction() as session:
        job = await session.scalar(
            select(AIJob)
            .where(
                AIJob.id == claim.id,
                AIJob.owner_id == claim.owner_id,
                AIJob.status == "running",
                AIJob.lease_id == claim.lease_id,
                AIJob.lease_until > current,
            )
            .with_for_update()
        )
        if job is None:
            return False
        body_log = await session.scalar(
            select(AIBodyLog)
            .where(
                AIBodyLog.job_id == job.id,
                AIBodyLog.attempt_number == claim.attempt_number,
                AIBodyLog.status == "running",
            )
            .with_for_update()
        )
        if body_log is None:
            return False
        body_log.request_body = request_body
        body_log.request_prepared_at = current
        return True


async def locked_attempt_body_log(
    session: AsyncSession,
    job: AIJob,
    attempt_number: int,
) -> AIBodyLog | None:
    return await session.scalar(
        select(AIBodyLog)
        .where(
            AIBodyLog.job_id == job.id,
            AIBodyLog.owner_id == job.owner_id,
            AIBodyLog.attempt_number == attempt_number,
        )
        .with_for_update()
    )


async def finish_attempt_body_log(
    session: AsyncSession,
    job: AIJob,
    *,
    status: str,
    error_code: str | None,
    error_stage: str | None,
    provider_request_id: str | None,
    http_status: int | None,
    finished_at,
) -> None:
    if job.attempt_count < 1:
        return
    await session.execute(
        update(AIBodyLog)
        .where(
            AIBodyLog.job_id == job.id,
            AIBodyLog.attempt_number == job.attempt_count,
            AIBodyLog.status == "running",
        )
        .values(
            status=status,
            error_code=error_code,
            error_stage=error_stage,
            provider_request_id=provider_request_id,
            http_status=http_status,
            finished_at=finished_at,
        )
    )


async def create_quality_sample_if_enabled(
    session: AsyncSession,
    job: AIJob,
    body_log: AIBodyLog,
    *,
    retention_days: int,
) -> bool:
    setting = await session.scalar(
        select(UserPrivacySetting)
        .where(UserPrivacySetting.owner_id == job.owner_id)
        .with_for_update()
    )
    if setting is None:
        logger.warning(
            "ai_quality_sampling_skipped",
            extra={
                "job_id": str(job.id),
                "capability": job.capability,
                "reason": "privacy_setting_missing",
            },
        )
        return False
    if not setting.quality_improvement_enabled:
        return False
    existing = await session.scalar(
        select(AIQualitySample.id).where(
            AIQualitySample.source_job_id == job.id
        )
    )
    if existing is not None:
        return False
    sampled_at = now()
    session.add(
        AIQualitySample(
            source_job_id=job.id,
            source_body_log_id=body_log.id,
            owner_id=job.owner_id,
            capability=job.capability,
            request_body=body_log.request_body,
            response_body=body_log.response_body,
            sampled_at=sampled_at,
            expires_at=sampled_at + timedelta(days=retention_days),
        )
    )
    return True


async def cleanup_expired_governance(
    database: Database,
    *,
    body_limit: int = 500,
    sample_limit: int = 500,
) -> tuple[int, int]:
    current = now()
    async with database.transaction() as session:
        body_ids = list(
            await session.scalars(
                select(AIBodyLog.id)
                .where(AIBodyLog.expires_at <= current)
                .order_by(AIBodyLog.expires_at, AIBodyLog.id)
                .limit(body_limit)
                .with_for_update(skip_locked=True)
            )
        )
        if body_ids:
            await session.execute(
                delete(AIBodyLog).where(AIBodyLog.id.in_(body_ids))
            )
        sample_ids = list(
            await session.scalars(
                select(AIQualitySample.id)
                .where(AIQualitySample.expires_at <= current)
                .order_by(AIQualitySample.expires_at, AIQualitySample.id)
                .limit(sample_limit)
                .with_for_update(skip_locked=True)
            )
        )
        if sample_ids:
            await session.execute(
                delete(AIQualitySample).where(
                    AIQualitySample.id.in_(sample_ids)
                )
            )
        return len(body_ids), len(sample_ids)
