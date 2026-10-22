import hashlib
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select

from itime.ai.governance_service import (
    create_quality_sample_if_enabled,
    locked_attempt_body_log,
    validate_response_body,
)
from itime.ai.job_service import (
    ClaimedJob,
    clear_job_lease,
    delete_media,
    finish_running_attempt,
    reject_or_retry_job,
)
from itime.ai.models import AIConfigVersion, AIJob, TemporaryMedia
from itime.ai.provider import ProviderConfig
from itime.auth.security import now
from itime.calendar.ai_import_provider import AIImportProviderResult
from itime.calendar.import_parsers import ParsedCandidate
from itime.calendar.import_service import (
    new_batch_expiry,
    prepare_candidate_rows,
    refresh_batch_state,
)
from itime.calendar.models import (
    AcademicTerm,
    CalendarImportBatch,
    CalendarSource,
    TermPeriod,
)
from itime.database import Database
from itime.errors import AppError
from itime.quota.service import release_reservation, settle_success


class ScreenshotWorkUnavailable(Exception):
    def __init__(self, code: str, message: str, stage: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.stage = stage


async def recognition_context(session, owner_id, time_zone: str) -> dict:
    terms = list(
        await session.scalars(
            select(AcademicTerm)
            .where(AcademicTerm.owner_id == owner_id)
            .order_by(AcademicTerm.updated_at.desc(), AcademicTerm.id.desc())
            .limit(50)
        )
    )
    term_ids = [term.id for term in terms]
    periods_by_term: dict[UUID, list[dict]] = {
        term.id: [] for term in terms
    }
    if term_ids:
        periods = await session.scalars(
            select(TermPeriod)
            .where(
                TermPeriod.owner_id == owner_id,
                TermPeriod.term_id.in_(term_ids),
            )
            .order_by(TermPeriod.term_id, TermPeriod.period_number)
        )
        for period in periods:
            periods_by_term[period.term_id].append(
                {
                    "number": period.period_number,
                    "name": period.name,
                    "starts_at": period.starts_at.isoformat(),
                    "ends_at": period.ends_at.isoformat(),
                }
            )
    return {
        "time_zone": time_zone,
        "terms": [
            {
                "id": str(term.id),
                "name": term.name,
                "start_date": term.start_date.isoformat(),
                "end_date": term.end_date.isoformat(),
                "first_week_monday": term.first_week_monday.isoformat(),
                "time_zone": term.time_zone,
                "periods": periods_by_term[term.id],
            }
            for term in terms
        ],
    }


@dataclass(frozen=True)
class CalendarImportWork:
    config: ProviderConfig
    source_type: str
    context: dict
    batch_id: UUID | None
    image: bytes | None = None
    content_type: str | None = None
    source_text: str | None = None


async def load_calendar_import_work(
    database: Database,
    cipher: Fernet,
    claim: ClaimedJob,
) -> CalendarImportWork:
    encrypted_content: bytes
    expected_hash: str
    content_type: str | None = None
    batch_id: UUID | None = None
    async with database.transaction() as session:
        config = await session.get(AIConfigVersion, claim.config_version_id)
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
                "课表解析配置已不可用，请重新提交。",
                "configuration",
            )
        context = job.input_summary.get("recognition_context")
        if not isinstance(context, dict):
            raise ScreenshotWorkUnavailable(
                "ai_job_input_invalid",
                "课表解析输入快照无效，请重新提交。",
                "validation",
            )
        batch = await session.scalar(
            select(CalendarImportBatch)
            .where(
                CalendarImportBatch.origin_job_id == job.id,
                CalendarImportBatch.owner_id == job.owner_id,
            )
            .with_for_update()
        )
        source_type = batch.source_type if batch is not None else "image"
        if batch is not None:
            batch_id = batch.id
            batch.state = "processing"
            batch.error_code = None
            batch.error_message = None
            batch.updated_at = now()
        if source_type == "image":
            media = await session.scalar(
                select(TemporaryMedia).where(
                    TemporaryMedia.id == claim.input_media_id,
                    TemporaryMedia.owner_id == claim.owner_id,
                )
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
            expected_hash = media.content_sha256
            content_type = media.content_type
        else:
            if (
                batch is None
                or batch.input_body_ciphertext is None
                or batch.input_body_expires_at is None
                or batch.input_body_expires_at <= now()
            ):
                raise ScreenshotWorkUnavailable(
                    "calendar_import_input_expired",
                    "导入内容已过期，请重新提交。",
                    "input",
                )
            encrypted_content = bytes(batch.input_body_ciphertext)
            expected_hash = batch.content_sha256
        provider = ProviderConfig(
            endpoint_url=config.endpoint_url,
            model=config.model,
            api_key_ciphertext=bytes(config.api_key_ciphertext),
            timeout_seconds=config.timeout_seconds,
            max_output_tokens=config.max_output_tokens,
        )
    try:
        contents = cipher.decrypt(encrypted_content)
    except InvalidToken as exc:
        raise ScreenshotWorkUnavailable(
            "calendar_import_input_unavailable",
            "导入内容暂时无法读取，请重新提交。",
            "input",
        ) from exc
    if hashlib.sha256(contents).hexdigest() != expected_hash:
        raise ScreenshotWorkUnavailable(
            "calendar_import_input_corrupted",
            "导入内容完整性校验失败，请重新提交。",
            "input",
        )
    try:
        source_text = (
            contents.decode() if source_type in {"public_url", "text"} else None
        )
    except UnicodeDecodeError as exc:
        raise ScreenshotWorkUnavailable(
            "calendar_import_input_invalid",
            "导入文字编码无效，请重新提交。",
            "input",
        ) from exc
    return CalendarImportWork(
        config=provider,
        source_type=source_type,
        context=context,
        batch_id=batch_id,
        image=contents if source_type == "image" else None,
        content_type=content_type,
        source_text=source_text,
    )


async def _apply_import_candidates(
    database: Database,
    claim: ClaimedJob,
    parsed: list[ParsedCandidate],
    *,
    format_name: str,
    provider_result: AIImportProviderResult | None,
    source_display: str | None = None,
    quality_sample_retention_days: int = 30,
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
        body_log = await locked_attempt_body_log(
            session,
            job,
            claim.attempt_number,
        )
        if body_log is None or body_log.status != "running":
            return None
        if provider_result is not None:
            validate_response_body(provider_result.response_body)
            body_log.response_body = provider_result.response_body
        if job.deadline_at <= current:
            await finish_running_attempt(
                session,
                job,
                status="timed_out",
                error_code="ai_job_deadline_exceeded",
                error_stage="deadline",
                provider_request_id=(
                    provider_result.provider_request_id
                    if provider_result is not None
                    else None
                ),
                http_status=(
                    provider_result.http_status
                    if provider_result is not None
                    else None
                ),
                provider_usage=(
                    provider_result.usage
                    if provider_result is not None
                    else None
                ),
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
            return None

        batch = await session.scalar(
            select(CalendarImportBatch)
            .where(
                CalendarImportBatch.origin_job_id == job.id,
                CalendarImportBatch.owner_id == job.owner_id,
            )
            .with_for_update()
        )
        if batch is None:
            source = CalendarSource(
                owner_id=claim.owner_id,
                kind="image",
                name=str(job.input_summary.get("filename") or "课表截图")[:255],
            )
            session.add(source)
            await session.flush()
            batch = CalendarImportBatch(
                owner_id=claim.owner_id,
                source_id=source.id,
                origin_job_id=job.id,
                format="screenshot",
                source_type="image",
                filename=source.name,
                content_sha256=str(job.input_summary["content_sha256"]),
                state="processing",
                candidate_count=0,
                source_display=source.name,
                input_summary={
                    key: value
                    for key, value in job.input_summary.items()
                    if key != "recognition_context"
                },
                expires_at=new_batch_expiry(),
            )
            session.add(batch)
            await session.flush()
        elif source_display is not None:
            batch.source_display = source_display
        batch.format = format_name
        batch.error_code = None
        batch.error_message = None
        candidates = await prepare_candidate_rows(
            session,
            claim.owner_id,
            format_name,
            parsed,
        )
        batch.candidate_count = len(candidates)
        for candidate in candidates:
            candidate.batch_id = batch.id
        session.add_all(candidates)
        await session.flush()
        await refresh_batch_state(session, batch)

        finalized = await finish_running_attempt(
            session,
            job,
            status="succeeded",
            provider_request_id=(
                provider_result.provider_request_id
                if provider_result is not None
                else None
            ),
            http_status=(
                provider_result.http_status
                if provider_result is not None
                else None
            ),
            provider_usage=(
                provider_result.usage
                if provider_result is not None
                else None
            ),
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
        if provider_result is not None:
            await create_quality_sample_if_enabled(
                session,
                job,
                body_log,
                retention_days=quality_sample_retention_days,
            )
        return str(batch.id)


def _screenshot_term(context: dict, today: date) -> dict | None:
    terms = context.get("terms")
    if not isinstance(terms, list) or not terms:
        return None
    for item in terms:
        if not isinstance(item, dict):
            continue
        try:
            if (
                date.fromisoformat(item["start_date"])
                <= today
                <= date.fromisoformat(item["end_date"])
            ):
                return item
        except (KeyError, TypeError, ValueError):
            continue
    return terms[0] if isinstance(terms[0], dict) else None


def _normalize_screenshot_draft(
    draft: dict,
    context: dict,
    today: date,
) -> tuple[dict, list[str]]:
    kind = draft.get("kind")
    term = _screenshot_term(context, today)
    recurrence = draft.get("recurrence")
    if kind == "fixed" and isinstance(recurrence, dict) and term:
        if (
            recurrence.get("frequency") != "none"
            and recurrence.get("until") is None
            and recurrence.get("count") is None
            and term.get("end_date")
        ):
            recurrence["until"] = term["end_date"]
            draft["term_id"] = term.get("id")
            return draft, [
                "图片未显示固定安排的结束日期，已按当前学期结束日导入。",
            ]
        return draft, []
    if kind != "course":
        return draft, []
    if (
        draft.get("start_period_number") is not None
        and draft.get("end_period_number") is not None
    ):
        draft["starts_at"] = None
        draft["ends_at"] = None
        draft["end_day_offset"] = 0
    if not isinstance(recurrence, dict):
        return draft, []
    if recurrence.get("frequency") == "weekly":
        recurrence["count"] = None
        recurrence["until"] = None
    if recurrence.get("frequency") != "weekly" or recurrence.get("week_numbers"):
        return draft, []
    weekdays = recurrence.get("weekdays")
    if not isinstance(weekdays, list) or not weekdays:
        return draft, []
    if not term or not term.get("first_week_monday"):
        return draft, []
    try:
        first_monday = date.fromisoformat(term["first_week_monday"])
        end_date = date.fromisoformat(term["end_date"])
        weekday = int(weekdays[0])
    except (KeyError, TypeError, ValueError):
        return draft, []
    if weekday < 1 or weekday > 7:
        return draft, []
    week_count = ((end_date - first_monday).days // 7) + 1
    if week_count < 1:
        return draft, []
    recurrence["week_numbers"] = list(range(1, week_count + 1))
    draft["term_id"] = term.get("id")
    draft["start_date"] = None
    return draft, [
        "图片未显示课程周次，已按当前学期全周导入，请在审核时确认。",
    ]


async def apply_ai_import_result(
    database: Database,
    claim: ClaimedJob,
    result: AIImportProviderResult,
    *,
    format_name: str,
    source_display: str | None = None,
    quality_sample_retention_days: int = 30,
    context: dict | None = None,
) -> str | None:
    context = context or {}
    parsed: list[ParsedCandidate] = []
    for item in result.output.items:
        warnings = list(item.warnings)
        draft = item.draft
        if isinstance(draft, dict):
            draft, extra = _normalize_screenshot_draft(
                draft,
                context,
                now().date(),
            )
            warnings.extend(extra)
        parsed.append(
            ParsedCandidate(
                raw_data={
                    "raw_text": item.raw_text,
                    "confidence": item.confidence,
                },
                draft=draft,
                warnings=[
                    {
                        "code": "ai_recognition_warning",
                        "message": warning,
                    }
                    for warning in warnings
                ],
            )
        )
    return await _apply_import_candidates(
        database,
        claim,
        parsed,
        format_name=format_name,
        provider_result=result,
        source_display=source_display,
        quality_sample_retention_days=quality_sample_retention_days,
    )


async def apply_deterministic_import_result(
    database: Database,
    claim: ClaimedJob,
    parsed: list[ParsedCandidate],
    *,
    source_display: str,
) -> str | None:
    return await _apply_import_candidates(
        database,
        claim,
        parsed,
        format_name="ics",
        provider_result=None,
        source_display=source_display,
    )


async def apply_screenshot_result(
    database: Database,
    claim: ClaimedJob,
    result: AIImportProviderResult,
    *,
    quality_sample_retention_days: int = 30,
) -> str | None:
    return await apply_ai_import_result(
        database,
        claim,
        result,
        format_name="screenshot",
        quality_sample_retention_days=quality_sample_retention_days,
    )


async def reject_or_retry_calendar_import(
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
    status = await reject_or_retry_job(
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
    if status is None:
        return None
    async with database.transaction() as session:
        batch = await session.scalar(
            select(CalendarImportBatch)
            .where(
                CalendarImportBatch.origin_job_id == claim.id,
                CalendarImportBatch.owner_id == claim.owner_id,
            )
            .with_for_update()
        )
        if batch is None:
            return status
        batch.state = "queued" if status == "queued" else "failed"
        batch.error_code = error_code
        batch.error_message = error_message
        batch.updated_at = now()
        if status != "queued":
            batch.input_body_ciphertext = None
            batch.input_body_expires_at = None
            if claim.input_media_id is not None:
                media = await session.scalar(
                    select(TemporaryMedia)
                    .where(
                        TemporaryMedia.id == claim.input_media_id,
                        TemporaryMedia.owner_id == claim.owner_id,
                    )
                    .with_for_update()
                )
                if media is not None and media.encrypted_content is not None:
                    delete_media(media, reason="cancelled")
    return status


async def cleanup_expired_calendar_import_inputs(
    database: Database,
    *,
    limit: int = 100,
) -> int:
    async with database.transaction() as session:
        rows = list(
            await session.scalars(
                select(CalendarImportBatch)
                .where(
                    CalendarImportBatch.input_body_ciphertext.is_not(None),
                    CalendarImportBatch.input_body_expires_at <= now(),
                )
                .order_by(
                    CalendarImportBatch.input_body_expires_at,
                    CalendarImportBatch.id,
                )
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        for batch in rows:
            batch.input_body_ciphertext = None
            batch.input_body_expires_at = None
            batch.updated_at = now()
        return len(rows)
