import asyncio
from datetime import datetime, timedelta
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, Query, Request
from sqlalchemy import and_, func, or_, select, text

from itime.ai.audio_validation import MAX_VOICE_BYTES, validate_voice_wav
from itime.ai.job_service import job_result
from itime.ai.models import AIJob, TemporaryMedia, VoiceTranscription
from itime.ai.routing_service import (
    load_active_config_route,
    replace_job_config_route,
)
from itime.ai.transcription_schemas import (
    TranscriptionPurpose,
    VoiceTranscriptionUploadOperation,
)
from itime.auth.security import now
from itime.auth.session import authorized, check_ai_access
from itime.errors import AppError
from itime.quota.service import (
    estimate_audio_reservation_budget,
    get_or_create_daily_allowance,
    maximum_reservation_estimate,
    reserve_job_budget,
)
from itime.tasks.service import (
    replay_operation,
    save_operation,
    validate_cursor,
)

router = APIRouter(
    prefix="/api/v1/voice-transcriptions",
    tags=["语音转写"],
)

ACCOUNT_ACTIVE_LIMIT = 3
ACCOUNT_WINDOW_LIMIT = 20
ACCOUNT_WINDOW = timedelta(minutes=10)
TRANSCRIPTION_RETENTION = timedelta(hours=24)
TRANSCRIPTION_ADMISSION_LOCK = 4_681_123_902


def transcription_result(item: VoiceTranscription) -> dict:
    return {
        "id": item.id,
        "job_id": item.job_id,
        "purpose": item.purpose,
        "transcript": item.transcript,
        "transcript_sha256": item.transcript_sha256,
        "language": item.language,
        "resolution": item.resolution,
        "ready_at": item.ready_at,
        "resolved_at": item.resolved_at,
        "text_purged_at": item.text_purged_at,
        "expires_at": item.expires_at,
        "version": item.version,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def transcription_with_job(
    item: VoiceTranscription,
    job: AIJob,
) -> dict:
    return {
        "transcription": transcription_result(item),
        "job": job_result(job),
    }


def _safe_filename(filename: str) -> str:
    cleaned = filename.strip()
    if (
        not cleaned
        or "/" in cleaned
        or "\\" in cleaned
        or any(ord(character) < 32 for character in cleaned)
    ):
        raise AppError(
            "invalid_audio_filename",
            "录音文件名无效，请重新录制。",
            status=422,
        )
    return cleaned


async def _check_admission_limits(
    request: Request,
    *,
    session,
    owner_id: UUID,
    current: datetime,
) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": TRANSCRIPTION_ADMISSION_LOCK},
    )
    active_for_owner = await session.scalar(
        select(func.count())
        .select_from(AIJob)
        .where(
            AIJob.owner_id == owner_id,
            AIJob.capability == "transcription",
            AIJob.status.in_(("queued", "running")),
        )
    )
    if active_for_owner >= ACCOUNT_ACTIVE_LIMIT:
        raise AppError(
            "ai_job_limit_reached",
            "已有 3 个语音转写作业正在处理，请完成后再录制。",
            status=429,
            retryable=True,
        )

    recent_for_owner = await session.scalar(
        select(func.count())
        .select_from(VoiceTranscription)
        .where(
            VoiceTranscription.owner_id == owner_id,
            VoiceTranscription.created_at >= current - ACCOUNT_WINDOW,
        )
    )
    if recent_for_owner >= ACCOUNT_WINDOW_LIMIT:
        raise AppError(
            "voice_transcription_rate_limit_reached",
            "最近 10 分钟已接收 20 段录音，请稍后再试。",
            status=429,
            retryable=True,
        )

    active_globally = await session.scalar(
        select(func.count())
        .select_from(AIJob)
        .where(
            AIJob.capability == "transcription",
            AIJob.status.in_(("queued", "running")),
        )
    )
    if active_globally >= request.app.state.settings.ai_transcription_queue_limit:
        raise AppError(
            "ai_transcription_queue_full",
            "语音转写队列暂时已满，请稍后重试或继续手工输入。",
            status=503,
            retryable=True,
        )


@router.post("", status_code=202)
async def create_voice_transcription(
    request: Request,
    purpose: Annotated[TranscriptionPurpose, Query()],
    filename: Annotated[str, Query(min_length=1, max_length=255)],
    operation_id: Annotated[UUID, Header(alias="X-Operation-ID")],
) -> dict:
    async with authorized(request) as identity:
        check_ai_access(identity.user)
    filename = _safe_filename(filename)

    contents = await request.body()
    if len(contents) > MAX_VOICE_BYTES:
        raise AppError(
            "audio_too_large",
            "录音不能超过 2 MiB。",
            status=413,
        )
    audio = await asyncio.to_thread(
        validate_voice_wav,
        contents,
        filename,
        request.headers.get("content-type", ""),
    )
    encrypted_content = await asyncio.to_thread(
        request.app.state.security.ai_cipher.encrypt,
        contents,
    )
    operation = VoiceTranscriptionUploadOperation(
        operation_id=operation_id,
        purpose=purpose,
        filename=filename,
        content_type=audio.content_type,
        content_sha256=audio.sha256,
        byte_size=audio.byte_size,
        duration_ms=audio.duration_ms,
    )

    async with authorized(request, write=True) as identity:
        check_ai_access(identity.user)
        replay = await replay_operation(
            identity.session,
            identity.user.id,
            operation_id,
            "voice_transcription.upload",
            operation,
        )
        if replay is not None:
            return replay

        current = now()
        await _check_admission_limits(
            request,
            session=identity.session,
            owner_id=identity.user.id,
            current=current,
        )
        route = await load_active_config_route(
            identity.session,
            "transcription",
            unavailable_message=(
                "语音转写暂未配置，请稍后重试或继续手工输入。"
            ),
        )
        daily = await get_or_create_daily_allowance(
            identity.session,
            identity.user.id,
            accepted_at=current,
        )
        estimate = maximum_reservation_estimate(
            [
                estimate_audio_reservation_budget(
                    route_config,
                    duration_ms=audio.duration_ms,
                )
                for route_config in route.configs
            ]
        )
        expires_at = current + TRANSCRIPTION_RETENTION
        media = TemporaryMedia(
            id=uuid4(),
            owner_id=identity.user.id,
            purpose="voice_transcription",
            original_filename=filename,
            content_type=audio.content_type,
            content_sha256=audio.sha256,
            byte_size=audio.byte_size,
            width=None,
            height=None,
            duration_ms=audio.duration_ms,
            sample_rate_hz=audio.sample_rate_hz,
            channel_count=audio.channel_count,
            sample_width_bits=audio.sample_width_bits,
            encrypted_content=encrypted_content,
            expires_at=expires_at,
            created_at=current,
        )
        identity.session.add(media)
        await identity.session.flush()

        input_summary = {
            "purpose": purpose,
            "filename": filename,
            "content_type": audio.content_type,
            "byte_size": audio.byte_size,
            "duration_ms": audio.duration_ms,
            "sample_rate_hz": audio.sample_rate_hz,
            "channel_count": audio.channel_count,
            "sample_width_bits": audio.sample_width_bits,
            "content_sha256": audio.sha256,
        }
        job = AIJob(
            id=uuid4(),
            owner_id=identity.user.id,
            capability="transcription",
            config_version_id=route.primary.id,
            input_media_id=media.id,
            quota_mode="user",
            input_summary=input_summary,
            available_at=current,
            deadline_at=current
            + timedelta(
                seconds=request.app.state.settings.ai_job_timeout_seconds
            ),
            created_at=current,
            updated_at=current,
        )
        identity.session.add(job)
        await identity.session.flush()
        await replace_job_config_route(identity.session, job, route)

        transcription = VoiceTranscription(
            id=uuid4(),
            owner_id=identity.user.id,
            job_id=job.id,
            media_id=media.id,
            purpose=purpose,
            expires_at=expires_at,
            created_at=current,
            updated_at=current,
        )
        identity.session.add(transcription)
        await reserve_job_budget(
            identity.session,
            owner_id=identity.user.id,
            job=job,
            accepted_at=current,
            estimate=estimate,
            daily=daily,
        )
        await identity.session.flush()

        result = {
            "operation_id": operation_id,
            **transcription_with_job(transcription, job),
        }
        save_operation(
            identity.session,
            identity.user.id,
            operation_id,
            "voice_transcription.upload",
            operation,
            result,
        )
        return result


@router.get("")
async def list_voice_transcriptions(
    request: Request,
    purpose: TranscriptionPurpose | None = None,
    before_time: datetime | None = None,
    before_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> dict:
    validate_cursor(before_time, before_id)
    async with authorized(request) as identity:
        statement = (
            select(VoiceTranscription, AIJob)
            .join(
                AIJob,
                and_(
                    AIJob.id == VoiceTranscription.job_id,
                    AIJob.owner_id == VoiceTranscription.owner_id,
                ),
            )
            .where(VoiceTranscription.owner_id == identity.user.id)
        )
        if purpose is not None:
            statement = statement.where(
                VoiceTranscription.purpose == purpose
            )
        if before_time is not None:
            statement = statement.where(
                or_(
                    VoiceTranscription.created_at < before_time,
                    and_(
                        VoiceTranscription.created_at == before_time,
                        VoiceTranscription.id < before_id,
                    ),
                )
            )
        rows = list(
            (
                await identity.session.execute(
                    statement.order_by(
                        VoiceTranscription.created_at.desc(),
                        VoiceTranscription.id.desc(),
                    ).limit(limit + 1)
                )
            ).all()
        )
        page = rows[:limit]
        return {
            "items": [
                transcription_with_job(item, job)
                for item, job in page
            ],
            "next_cursor": (
                {
                    "before_time": page[-1][0].created_at,
                    "before_id": page[-1][0].id,
                }
                if len(rows) > limit
                else None
            ),
        }


@router.get("/{transcription_id}")
async def get_voice_transcription(
    transcription_id: UUID,
    request: Request,
) -> dict:
    async with authorized(request) as identity:
        row = (
            await identity.session.execute(
                select(VoiceTranscription, AIJob)
                .join(
                    AIJob,
                    and_(
                        AIJob.id == VoiceTranscription.job_id,
                        AIJob.owner_id == VoiceTranscription.owner_id,
                    ),
                )
                .where(
                    VoiceTranscription.id == transcription_id,
                    VoiceTranscription.owner_id == identity.user.id,
                )
            )
        ).one_or_none()
        if row is None:
            raise AppError(
                "voice_transcription_not_found",
                "未找到此语音转写。",
                status=404,
            )
        return transcription_with_job(*row)
