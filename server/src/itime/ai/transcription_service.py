import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from itime.ai.audio_validation import validate_voice_wav
from itime.ai.governance_service import (
    create_quality_sample_if_enabled,
    locked_attempt_body_log,
    validate_response_body,
)
from itime.ai.job_service import (
    ClaimedJob,
    cancel_job,
    clear_job_lease,
    delete_media,
    finish_running_attempt,
)
from itime.ai.models import (
    AIConfigVersion,
    AIJob,
    TemporaryMedia,
    VoiceTranscription,
)
from itime.ai.provider import ProviderConfig
from itime.ai.transcription_provider import (
    TranscriptionProviderResult,
    transcription_log_request_body,
)
from itime.ai.transcription_schemas import VoiceTranscriptionResolveInput
from itime.auth.security import now
from itime.database import Database
from itime.errors import AppError
from itime.quota.service import release_reservation, settle_success
from itime.tasks.models import UserOperation


class TranscriptionWorkUnavailable(Exception):
    def __init__(self, code: str, message: str, stage: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.stage = stage


@dataclass(frozen=True)
class TranscriptionWork:
    config: ProviderConfig
    audio: bytes
    filename: str
    purpose: str
    duration_ms: int
    request_body: dict


async def load_transcription_work(
    database: Database,
    cipher: Fernet,
    claim: ClaimedJob,
) -> TranscriptionWork:
    async with database.transaction() as session:
        config = await session.get(AIConfigVersion, claim.config_version_id)
        job = await session.scalar(
            select(AIJob).where(
                AIJob.id == claim.id,
                AIJob.owner_id == claim.owner_id,
                AIJob.capability == "transcription",
                AIJob.status == "running",
                AIJob.lease_id == claim.lease_id,
                AIJob.lease_until > now(),
            )
        )
        item = await session.scalar(
            select(VoiceTranscription).where(
                VoiceTranscription.job_id == claim.id,
                VoiceTranscription.owner_id == claim.owner_id,
                VoiceTranscription.resolution == "pending",
            )
        )
        media = await session.scalar(
            select(TemporaryMedia).where(
                TemporaryMedia.id == claim.input_media_id,
                TemporaryMedia.owner_id == claim.owner_id,
                TemporaryMedia.purpose == "voice_transcription",
            )
        )
        current = now()
        if job is None:
            raise TranscriptionWorkUnavailable(
                "ai_job_superseded",
                "作业已被取消或由其他执行进程接管。",
                "worker",
            )
        if config is None or config.capability != "transcription":
            raise TranscriptionWorkUnavailable(
                "ai_configuration_unavailable",
                "转写配置已不可用，请重新录制。",
                "configuration",
            )
        if item is None:
            raise TranscriptionWorkUnavailable(
                "voice_transcription_unavailable",
                "转写草稿状态无效，请重新录制。",
                "validation",
            )
        if (
            item.expires_at <= current
            or media is None
            or media.encrypted_content is None
            or media.expires_at <= current
        ):
            raise TranscriptionWorkUnavailable(
                "temporary_media_expired",
                "原录音已过期，请重新录制。",
                "media",
            )
        encrypted_content = bytes(media.encrypted_content)
        filename = media.original_filename
        content_type = media.content_type
        expected_hash = media.content_sha256
        expected_duration = media.duration_ms
        expected_sample_rate = media.sample_rate_hz
        expected_channel_count = media.channel_count
        expected_sample_width = media.sample_width_bits
        purpose = item.purpose
        provider = ProviderConfig(
            endpoint_url=config.endpoint_url,
            model=config.model,
            api_key_ciphertext=bytes(config.api_key_ciphertext),
            timeout_seconds=config.timeout_seconds,
            max_output_tokens=config.max_output_tokens,
        )

    try:
        audio = cipher.decrypt(encrypted_content)
    except InvalidToken as exc:
        raise TranscriptionWorkUnavailable(
            "temporary_media_unavailable",
            "原录音暂时无法读取，请重新录制。",
            "media",
        ) from exc
    try:
        validated = validate_voice_wav(audio, filename, content_type)
    except AppError as exc:
        raise TranscriptionWorkUnavailable(
            "temporary_media_corrupted",
            "原录音完整性校验失败，请重新录制。",
            "media",
        ) from exc
    if (
        validated.sha256 != expected_hash
        or validated.duration_ms != expected_duration
        or validated.sample_rate_hz != expected_sample_rate
        or validated.channel_count != expected_channel_count
        or validated.sample_width_bits != expected_sample_width
    ):
        raise TranscriptionWorkUnavailable(
            "temporary_media_corrupted",
            "原录音完整性校验失败，请重新录制。",
            "media",
        )
    return TranscriptionWork(
        config=provider,
        audio=audio,
        filename=filename,
        purpose=purpose,
        duration_ms=validated.duration_ms,
        request_body=transcription_log_request_body(
            provider,
            filename=filename,
            content_type=validated.content_type,
            byte_size=validated.byte_size,
            duration_ms=validated.duration_ms,
            content_sha256=validated.sha256,
            purpose=purpose,
        ),
    )


async def complete_transcription_job(
    database: Database,
    claim: ClaimedJob,
    result: TranscriptionProviderResult,
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
                AIJob.capability == "transcription",
                AIJob.status == "running",
                AIJob.lease_id == claim.lease_id,
                AIJob.lease_until > current,
            )
            .with_for_update()
        )
        if job is None:
            return False
        item = await session.scalar(
            select(VoiceTranscription)
            .where(
                VoiceTranscription.job_id == job.id,
                VoiceTranscription.owner_id == job.owner_id,
                VoiceTranscription.resolution == "pending",
            )
            .with_for_update()
        )
        if item is None:
            return False
        media = await session.scalar(
            select(TemporaryMedia)
            .where(
                TemporaryMedia.id == item.media_id,
                TemporaryMedia.owner_id == item.owner_id,
            )
            .with_for_update()
        )
        if media is None:
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

        if job.deadline_at <= current or item.expires_at <= current:
            await finish_running_attempt(
                session,
                job,
                status="timed_out",
                error_code="ai_job_deadline_exceeded",
                error_stage="deadline",
                provider_request_id=result.provider_request_id,
                http_status=result.http_status,
                provider_usage=result.usage,
            )
            job.status = "timed_out"
            job.error_code = "ai_job_deadline_exceeded"
            job.error_message = "语音转写已超过总等待时间，请重新录制。"
            job.finished_at = current
            job.updated_at = current
            job.version += 1
            await release_reservation(
                session,
                job=job,
                reason="timed_out",
            )
            delete_media(media, reason="expired")
            clear_job_lease(job)
            return False

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
        item.transcript = result.transcript
        item.transcript_sha256 = hashlib.sha256(
            result.transcript.encode()
        ).hexdigest()
        item.language = result.language
        item.ready_at = current
        item.updated_at = current
        item.version += 1
        job.status = "succeeded"
        job.output = {
            "resource_type": "voice_transcription",
            "transcription_id": str(item.id),
        }
        job.error_code = None
        job.error_message = None
        job.finished_at = current
        job.updated_at = current
        job.version += 1
        clear_job_lease(job)
        await create_quality_sample_if_enabled(
            session,
            job,
            body_log,
            retention_days=quality_sample_retention_days,
        )
        return True


async def reject_or_retry_transcription_job(
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
                AIJob.owner_id == claim.owner_id,
                AIJob.capability == "transcription",
                AIJob.status == "running",
                AIJob.lease_id == claim.lease_id,
                AIJob.lease_until > current,
            )
            .with_for_update()
        )
        if job is None:
            return None
        item = await session.scalar(
            select(VoiceTranscription)
            .where(
                VoiceTranscription.job_id == job.id,
                VoiceTranscription.owner_id == job.owner_id,
            )
            .with_for_update()
        )
        media = (
            await session.scalar(
                select(TemporaryMedia)
                .where(
                    TemporaryMedia.id == job.input_media_id,
                    TemporaryMedia.owner_id == job.owner_id,
                )
                .with_for_update()
            )
            if job.input_media_id is not None
            else None
        )
        can_retry = (
            retryable
            and item is not None
            and item.resolution == "pending"
            and item.expires_at > current
            and media is not None
            and media.encrypted_content is not None
            and media.expires_at > current
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
            clear_job_lease(job)
            return "queued"

        timed_out = job.deadline_at <= current
        status = "timed_out" if timed_out else "failed"
        final_code = (
            "ai_job_deadline_exceeded"
            if timed_out
            else error_code
        )
        final_message = (
            "语音转写已超过总等待时间，请重新录制。"
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
        await release_reservation(
            session,
            job=job,
            reason=status,
        )
        if media is not None:
            delete_media(media, reason="expired")
        clear_job_lease(job)
        return status


async def lock_transcription_graph(
    session: AsyncSession,
    owner_id: UUID,
    transcription_id: UUID,
) -> tuple[AIJob, VoiceTranscription, TemporaryMedia]:
    job_id = await session.scalar(
        select(VoiceTranscription.job_id).where(
            VoiceTranscription.id == transcription_id,
            VoiceTranscription.owner_id == owner_id,
        )
    )
    if job_id is None:
        raise AppError(
            "voice_transcription_not_found",
            "未找到此语音转写。",
            status=404,
        )
    job = await session.scalar(
        select(AIJob)
        .where(
            AIJob.id == job_id,
            AIJob.owner_id == owner_id,
            AIJob.capability == "transcription",
        )
        .with_for_update()
    )
    item = await session.scalar(
        select(VoiceTranscription)
        .where(
            VoiceTranscription.id == transcription_id,
            VoiceTranscription.owner_id == owner_id,
            VoiceTranscription.job_id == job_id,
        )
        .with_for_update()
    )
    media = (
        await session.scalar(
            select(TemporaryMedia)
            .where(
                TemporaryMedia.id == item.media_id,
                TemporaryMedia.owner_id == owner_id,
            )
            .with_for_update()
        )
        if item is not None
        else None
    )
    if job is None or item is None or media is None:
        raise AppError(
            "voice_transcription_state_invalid",
            "语音转写状态不完整，请保留请求编号并联系管理员。",
            status=503,
            saved="unknown",
        )
    return job, item, media


def _purge_transcript(
    item: VoiceTranscription,
    *,
    current: datetime,
) -> None:
    item.transcript = None
    item.transcript_sha256 = None
    item.language = None
    item.text_purged_at = current


async def _expire_locked_transcription(
    session: AsyncSession,
    job: AIJob,
    item: VoiceTranscription,
    media: TemporaryMedia,
    *,
    current: datetime,
) -> None:
    if job.status in {"queued", "running"}:
        await finish_running_attempt(
            session,
            job,
            status="timed_out",
            error_code="voice_transcription_expired",
            error_stage="retention",
        )
        job.status = "timed_out"
        job.output = None
        job.error_code = "voice_transcription_expired"
        job.error_message = "语音转写已超过 24 小时，请重新录制。"
        job.finished_at = current
        job.updated_at = current
        job.version += 1
        await release_reservation(
            session,
            job=job,
            reason="timed_out",
        )
        clear_job_lease(job)
    changed = False
    if item.resolution == "pending":
        item.resolution = "expired"
        item.resolved_at = current
        changed = True
    if item.transcript is not None or item.text_purged_at is None:
        _purge_transcript(item, current=current)
        changed = True
    if changed:
        item.updated_at = current
        item.version += 1
    delete_media(media, reason="expired")
    await session.execute(
        update(UserOperation)
        .where(
            UserOperation.owner_id == item.owner_id,
            UserOperation.scope == f"voice_transcription.resolve:{item.id}",
            UserOperation.response.is_not(None),
        )
        .values(
            response=None,
            response_purged_at=current,
        )
    )


async def resolve_transcription(
    session: AsyncSession,
    owner_id: UUID,
    transcription_id: UUID,
    data: VoiceTranscriptionResolveInput,
) -> tuple[VoiceTranscription, AIJob]:
    job, item, media = await lock_transcription_graph(
        session,
        owner_id,
        transcription_id,
    )
    current = now()
    if item.expires_at <= current:
        await _expire_locked_transcription(
            session,
            job,
            item,
            media,
            current=current,
        )
        return item, job
    if item.resolution != "pending":
        return item, job
    if item.version != data.expected_version:
        raise AppError(
            "voice_transcription_version_conflict",
            "语音转写状态已变化，请刷新后重试。",
            status=409,
            details={"current_version": item.version},
        )

    if data.action == "accept":
        if (
            job.status != "succeeded"
            or item.transcript is None
            or item.transcript_sha256 is None
            or item.ready_at is None
        ):
            raise AppError(
                "voice_transcription_not_ready",
                "转写尚未完成，暂时不能应用。",
                status=409,
                retryable=job.status in {"queued", "running"},
            )
        if data.transcript_sha256 != item.transcript_sha256:
            raise AppError(
                "voice_transcription_changed",
                "转写内容已变化，请确认最新结果后重试。",
                status=409,
                details={"current_version": item.version},
            )
        item.resolution = "accepted"
        item.resolved_at = current
        item.updated_at = current
        item.version += 1
        delete_media(media, reason="accepted")
        return item, job

    if job.status in {"queued", "running"}:
        changed = await cancel_job(
            session,
            job,
            transcription_resolution="discarded",
            transcription_media_reason="discarded",
        )
        if changed:
            await release_reservation(
                session,
                job=job,
                reason="cancelled",
            )
    else:
        item.resolution = "discarded"
        item.resolved_at = current
        _purge_transcript(item, current=current)
        item.updated_at = current
        item.version += 1
        delete_media(media, reason="discarded")
    return item, job


async def cleanup_expired_transcriptions(
    database: Database,
    *,
    limit: int = 100,
) -> int:
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    current = now()
    async with database.transaction() as session:
        candidates = list(
            (
                await session.execute(
                    select(
                        VoiceTranscription.id,
                        VoiceTranscription.job_id,
                    )
                    .where(
                        VoiceTranscription.expires_at <= current,
                        (
                            (VoiceTranscription.resolution == "pending")
                            | VoiceTranscription.transcript.is_not(None)
                        ),
                    )
                    .order_by(
                        VoiceTranscription.expires_at,
                        VoiceTranscription.id,
                    )
                    .limit(limit)
                )
            ).all()
        )

    processed = 0
    for transcription_id, job_id in candidates:
        async with database.transaction() as session:
            job = await session.scalar(
                select(AIJob)
                .where(
                    AIJob.id == job_id,
                    AIJob.capability == "transcription",
                )
                .with_for_update(skip_locked=True)
            )
            if job is None:
                continue
            item = await session.scalar(
                select(VoiceTranscription)
                .where(
                    VoiceTranscription.id == transcription_id,
                    VoiceTranscription.job_id == job.id,
                )
                .with_for_update()
            )
            if item is None or item.expires_at > current or (
                item.resolution != "pending" and item.transcript is None
            ):
                continue
            media = await session.scalar(
                select(TemporaryMedia)
                .where(
                    TemporaryMedia.id == item.media_id,
                    TemporaryMedia.owner_id == item.owner_id,
                )
                .with_for_update()
            )
            if media is None:
                continue
            await _expire_locked_transcription(
                session,
                job,
                item,
                media,
                current=current,
            )
            processed += 1
    return processed
