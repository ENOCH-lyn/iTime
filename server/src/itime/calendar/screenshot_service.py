import hashlib
from dataclasses import dataclass
from datetime import timedelta

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select

from itime.ai.job_service import (
    ClaimedJob,
    clear_job_lease,
    finish_running_attempt,
)
from itime.ai.models import AIConfigVersion, AIJob, TemporaryMedia
from itime.ai.provider import ProviderConfig
from itime.ai.screenshot_provider import ScreenshotProviderResult
from itime.auth.security import now
from itime.calendar.import_parsers import ParsedCandidate
from itime.calendar.import_service import (
    prepare_candidate_rows,
    refresh_batch_state,
)
from itime.calendar.models import CalendarImportBatch, CalendarSource
from itime.database import Database

SCREENSHOT_REVIEW_DAYS = 30


class ScreenshotWorkUnavailable(Exception):
    def __init__(self, code: str, message: str, stage: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.stage = stage


@dataclass(frozen=True)
class ScreenshotWork:
    config: ProviderConfig
    image: bytes
    content_type: str
    filename: str
    context: dict


async def load_screenshot_work(
    database: Database,
    cipher: Fernet,
    claim: ClaimedJob,
) -> ScreenshotWork:
    async with database.transaction() as session:
        config = await session.get(AIConfigVersion, claim.config_version_id)
        media = await session.scalar(
            select(TemporaryMedia).where(
                TemporaryMedia.id == claim.input_media_id,
                TemporaryMedia.owner_id == claim.owner_id,
            )
        )
        job = await session.scalar(
            select(AIJob).where(
                AIJob.id == claim.id,
                AIJob.owner_id == claim.owner_id,
                AIJob.status == "running",
                AIJob.lease_id == claim.lease_id,
                AIJob.lease_until > now(),
            )
        )
        if job is None:
            raise ScreenshotWorkUnavailable(
                "ai_job_superseded",
                "作业已被取消或由其他执行进程接管。",
                "worker",
            )
        if config is None:
            raise ScreenshotWorkUnavailable(
                "ai_configuration_unavailable",
                "识别配置已不可用，请重新提交。",
                "configuration",
            )
        if (
            media is None
            or media.encrypted_content is None
            or media.expires_at <= now()
        ):
            raise ScreenshotWorkUnavailable(
                "temporary_media_expired",
                "原图已过期，请重新上传。",
                "media",
            )
        encrypted_content = bytes(media.encrypted_content)
        content_type = media.content_type
        filename = media.original_filename
        expected_hash = media.content_sha256
        context = job.input_summary.get("recognition_context")
        if not isinstance(context, dict):
            raise ScreenshotWorkUnavailable(
                "ai_job_input_invalid",
                "识别输入快照无效，请重新上传。",
                "validation",
            )
        provider = ProviderConfig(
            endpoint_url=config.endpoint_url,
            model=config.model,
            api_key_ciphertext=bytes(config.api_key_ciphertext),
            timeout_seconds=config.timeout_seconds,
            max_output_tokens=config.max_output_tokens,
        )
    try:
        image = cipher.decrypt(encrypted_content)
    except InvalidToken as exc:
        raise ScreenshotWorkUnavailable(
            "temporary_media_unavailable",
            "原图暂时无法读取，请重新上传。",
            "media",
        ) from exc
    if hashlib.sha256(image).hexdigest() != expected_hash:
        raise ScreenshotWorkUnavailable(
            "temporary_media_corrupted",
            "原图完整性校验失败，请重新上传。",
            "media",
        )
    return ScreenshotWork(
        config=provider,
        image=image,
        content_type=content_type,
        filename=filename,
        context=context,
    )


async def apply_screenshot_result(
    database: Database,
    claim: ClaimedJob,
    result: ScreenshotProviderResult,
) -> str | None:
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
            return None
        if job.deadline_at <= current:
            await finish_running_attempt(
                session,
                job,
                status="timed_out",
                error_code="ai_job_deadline_exceeded",
                error_stage="deadline",
                provider_request_id=result.provider_request_id,
                http_status=result.http_status,
            )
            job.status = "timed_out"
            job.error_code = "ai_job_deadline_exceeded"
            job.error_message = "AI 作业已超过总等待时间，请重新提交。"
            job.finished_at = current
            job.updated_at = current
            job.version += 1
            clear_job_lease(job)
            return None

        parsed = [
            ParsedCandidate(
                raw_data={
                    "raw_text": item.raw_text,
                    "confidence": item.confidence,
                },
                draft=item.draft,
                warnings=[
                    {
                        "code": "ai_recognition_warning",
                        "message": warning,
                    }
                    for warning in item.warnings
                ],
            )
            for item in result.output.items
        ]
        candidates = await prepare_candidate_rows(
            session,
            claim.owner_id,
            "screenshot",
            parsed,
        )
        source = CalendarSource(
            owner_id=claim.owner_id,
            kind="screenshot",
            name=str(job.input_summary.get("filename") or "课表截图")[:255],
        )
        session.add(source)
        await session.flush()
        batch = CalendarImportBatch(
            owner_id=claim.owner_id,
            source_id=source.id,
            origin_job_id=job.id,
            format="screenshot",
            filename=source.name,
            content_sha256=str(job.input_summary["content_sha256"]),
            candidate_count=len(candidates),
            expires_at=current + timedelta(days=SCREENSHOT_REVIEW_DAYS),
        )
        session.add(batch)
        await session.flush()
        for candidate in candidates:
            candidate.batch_id = batch.id
        session.add_all(candidates)
        await session.flush()
        await refresh_batch_state(session, batch)
        await finish_running_attempt(
            session,
            job,
            status="succeeded",
            provider_request_id=result.provider_request_id,
            http_status=result.http_status,
        )
        job.status = "succeeded"
        job.output = {
            "resource_type": "calendar_import_batch",
            "batch_id": str(batch.id),
            "candidate_count": len(candidates),
        }
        job.error_code = None
        job.error_message = None
        job.finished_at = current
        job.updated_at = current
        job.version += 1
        clear_job_lease(job)
        return str(batch.id)
