"""持久化 AI 作业执行进程；数据库租约之外调用上游。"""

import argparse
import asyncio
import hashlib
import logging
from collections.abc import Awaitable
from uuid import UUID

from cryptography.fernet import Fernet

from itime.ai.agent_runtime import (
    AgentOutcome,
    fail_agent_run,
    get_agent_run,
    resume_agent_run,
    start_agent_run,
)
from itime.ai.conversation_provider import (
    ConversationGenerationSuperseded,
    ConversationProviderError,
    generate_agent_reply,
    stream_conversation_reply,
)
from itime.ai.conversation_service import (
    ConversationWorkUnavailable,
    append_conversation_delta,
    append_conversation_reasoning_delta,
    cleanup_expired_job_events,
    complete_agent_job,
    complete_conversation_job,
    load_conversation_work,
    persist_agent_memories,
    reject_or_retry_conversation_job,
)
from itime.ai.cost_service import mark_provider_request_started
from itime.ai.governance_service import prepare_attempt_body_log
from itime.ai.job_service import (
    ClaimedJob,
    claim_next_job,
    cleanup_expired_media,
    reject_or_retry_job,
    renew_job_lease,
)
from itime.ai.transcription_provider import (
    TranscriptionGenerationSuperseded,
    TranscriptionProviderError,
    transcribe_audio,
)
from itime.ai.transcription_service import (
    TranscriptionWorkUnavailable,
    cleanup_expired_transcriptions,
    complete_transcription_job,
    load_transcription_work,
    reject_or_retry_transcription_job,
)
from itime.auth.security import now
from itime.calendar.ai_import_provider import (
    AIImportGenerationSuperseded,
    AIImportProviderError,
    ai_import_log_request_body,
    recognize_ai_import,
)
from itime.calendar.import_fetcher import (
    CalendarImportFetchError,
    RestrictedCalendarImportFetcher,
)
from itime.calendar.import_parsers import parse_ics
from itime.calendar.screenshot_service import (
    ScreenshotWorkUnavailable,
    apply_ai_import_result,
    apply_deterministic_import_result,
    cleanup_expired_calendar_import_inputs,
    load_calendar_import_work,
    reject_or_retry_calendar_import,
)
from itime.config import Settings, get_settings
from itime.database import Database
from itime.observability import configure_logging
from itime.planning.job_service import (
    PlanningWorkUnavailable,
    complete_planning_job,
    load_planning_work,
    reject_or_retry_planning_job,
)
from itime.planning.provider import (
    PlanningGenerationSuperseded,
    PlanningProviderError,
    stream_planning_reply,
)
from itime.review.job_service import (
    ReviewWorkUnavailable,
    complete_review_job,
    load_review_work,
    reject_or_retry_review_job,
)
from itime.review.provider import (
    ReviewGenerationSuperseded,
    ReviewProviderError,
    stream_review_reply,
)

logger = logging.getLogger("itime.ai")


class JobLeaseLost(Exception):
    pass


async def _with_lease_heartbeat[T](
    database: Database,
    claim: ClaimedJob,
    settings: Settings,
    operation: Awaitable[T],
) -> T:
    task = asyncio.create_task(operation)
    interval = max(1.0, min(30.0, settings.ai_job_lease_seconds / 3))
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=interval)
            if task in done:
                return task.result()
            if not await renew_job_lease(
                database,
                claim,
                lease_seconds=settings.ai_job_lease_seconds,
            ):
                raise JobLeaseLost
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def _process_screenshot(
    database: Database,
    settings: Settings,
    cipher: Fernet,
    claim: ClaimedJob,
) -> None:
    try:
        work = await load_calendar_import_work(
            database,
            cipher,
            claim,
        )
    except ScreenshotWorkUnavailable as exc:
        await reject_or_retry_calendar_import(
            database,
            claim,
            error_code=exc.code,
            error_message=exc.message,
            error_stage=exc.stage,
            retryable=False,
        )
        return

    recognition_context = work.context

    remaining = (claim.deadline_at - now()).total_seconds()
    if remaining <= 0:
        await reject_or_retry_calendar_import(
            database,
            claim,
            error_code="ai_job_deadline_exceeded",
            error_message="AI 作业已超过总等待时间，请重新提交。",
            error_stage="deadline",
            retryable=False,
        )
        return
    source_text = work.source_text
    source_display = None
    if work.source_type == "public_url":
        try:
            fetched = await RestrictedCalendarImportFetcher(settings).fetch(
                source_text or ""
            )
        except CalendarImportFetchError as exc:
            await reject_or_retry_calendar_import(
                database,
                claim,
                error_code=exc.code,
                error_message=exc.message,
                error_stage="fetch",
                retryable=exc.retryable,
                http_status=exc.http_status,
            )
            return
        source_display = fetched.display_url
        if fetched.kind == "ics":
            if not await prepare_attempt_body_log(
                database,
                claim,
                {
                    "source_type": "public_url",
                    "fetch": {
                        "display_url": fetched.display_url,
                        "media_type": fetched.media_type,
                        "content_sha256": fetched.content_sha256,
                        "redirect_count": fetched.redirect_count,
                    },
                    "provider_request": "not_required_for_ics",
                },
            ):
                return
            parsed = await asyncio.to_thread(
                parse_ics,
                fetched.ics_content or b"",
                default_time_zone=str(
                    work.context.get("time_zone") or "UTC"
                ),
            )
            await apply_deterministic_import_result(
                database,
                claim,
                parsed,
                source_display=fetched.display_url,
            )
            return
        source_text = fetched.text

    if work.source_type == "image":
        request_body = ai_import_log_request_body(
            work.config,
            source_type="image",
            context=work.context,
            content_type=work.content_type,
            byte_size=len(work.image or b""),
            content_sha256=hashlib.sha256(work.image or b"").hexdigest(),
        )
    else:
        request_body = ai_import_log_request_body(
            work.config,
            source_type=work.source_type,
            context=work.context,
            source_text=source_text,
        )
    if not await prepare_attempt_body_log(database, claim, request_body):
        return
    format_name = (
        "screenshot" if work.source_type == "image" else work.source_type
    )
    try:
        async def mark_started() -> bool:
            return await mark_provider_request_started(database, claim)

        provider_result = await _with_lease_heartbeat(
            database,
            claim,
            settings,
            recognize_ai_import(
                work.config,
                settings,
                cipher,
                source_type=work.source_type,
                image=work.image,
                content_type=work.content_type,
                source_text=source_text,
                context=work.context,
                timeout_seconds=remaining,
                on_request_started=mark_started,
            ),
        )
    except (JobLeaseLost, AIImportGenerationSuperseded):
        return
    except AIImportProviderError as exc:
        await reject_or_retry_calendar_import(
            database,
            claim,
            error_code=exc.code,
            error_message=exc.message,
            error_stage=exc.stage,
            retryable=exc.retryable,
            provider_request_id=exc.provider_request_id,
            http_status=exc.http_status,
            provider_usage=exc.usage,
        )
        return
    finally:
        work = None

    await apply_ai_import_result(
        database,
        claim,
        provider_result,
        format_name=format_name,
        source_display=source_display,
        quality_sample_retention_days=settings.ai_quality_sample_retention_days,
        context=recognition_context,
    )


async def _process_conversation(
    database: Database,
    settings: Settings,
    cipher: Fernet,
    claim: ClaimedJob,
) -> None:
    try:
        work = await load_conversation_work(database, claim, cipher)
    except ConversationWorkUnavailable as exc:
        await reject_or_retry_conversation_job(
            database,
            claim,
            None,
            error_code=exc.code,
            error_message=exc.message,
            error_stage=exc.stage,
            retryable=False,
        )
        return
    if not await prepare_attempt_body_log(database, claim, work.request_body):
        return

    remaining = (claim.deadline_at - now()).total_seconds()
    if remaining <= 0:
        await reject_or_retry_conversation_job(
            database,
            claim,
            work.generation,
            error_code="ai_job_deadline_exceeded",
            error_message="AI 作业已超过总等待时间，请重新发送。",
            error_stage="deadline",
            retryable=False,
        )
        return

    async def persist_delta(text: str) -> bool:
        return await append_conversation_delta(
            database,
            claim,
            generation_number=work.generation,
            text_delta=text,
            lease_seconds=settings.ai_job_lease_seconds,
        )

    async def persist_reasoning(text: str) -> bool:
        return await append_conversation_reasoning_delta(
            database,
            claim,
            generation_number=work.generation,
            text_delta=text,
            lease_seconds=settings.ai_job_lease_seconds,
        )

    async def persist_step(label: str) -> bool:
        return await append_conversation_reasoning_delta(
            database,
            claim,
            generation_number=work.generation,
            text_delta="",
            lease_seconds=settings.ai_job_lease_seconds,
            step=label,
        )

    async def mark_started() -> bool:
        return await mark_provider_request_started(database, claim)

    try:
        if work.agent_resume is not None:
            run = await get_agent_run(
                database,
                claim.owner_id,
                UUID(str(work.agent_run_id)),
            )
            if run is None:
                await reject_or_retry_conversation_job(
                    database,
                    claim,
                    work.generation,
                    error_code="ai_agent_run_missing",
                    error_message="Agent 会话已不存在，请重新发送消息。",
                    error_stage="validation",
                    retryable=False,
                )
                return
            operation = resume_agent_run(
                database,
                settings,
                cipher,
                claim=claim,
                run=run,
                config=work.config,
                response_message_id=work.response_message_id,
                decision=str(work.agent_resume.get("decision") or "approved"),
                on_request_started=mark_started,
                on_reasoning=persist_reasoning,
                on_step=persist_step,
            )
        elif (
            work.agent_context is not None
            and work.agent_context.get("_live_tool_loop") is True
        ):
            operation = start_agent_run(
                database,
                settings,
                cipher,
                claim=claim,
                config=work.config,
                messages=work.messages,
                context=work.agent_context,
                image_media_id=work.image_media_id,
                response_message_id=work.response_message_id,
                on_request_started=mark_started,
                on_reasoning=persist_reasoning,
                on_step=persist_step,
            )
        elif work.agent_context is not None:
            operation = generate_agent_reply(
                work.config,
                settings,
                cipher,
                messages=work.messages,
                context=work.agent_context,
                timeout_seconds=remaining,
                on_delta=persist_delta,
                on_request_started=mark_started,
                on_reasoning=persist_reasoning,
                on_step=persist_step,
            )
        else:
            operation = stream_conversation_reply(
                work.config,
                settings,
                cipher,
                messages=work.messages,
                timeout_seconds=remaining,
                on_delta=persist_delta,
                on_request_started=mark_started,
                on_reasoning=persist_reasoning,
            )
        provider_result = await _with_lease_heartbeat(
            database,
            claim,
            settings,
            operation,
        )
    except (JobLeaseLost, ConversationGenerationSuperseded):
        return
    except ConversationProviderError as exc:
        await reject_or_retry_conversation_job(
            database,
            claim,
            work.generation,
            error_code=exc.code,
            error_message=exc.message,
            error_stage=exc.stage,
            retryable=exc.retryable,
            provider_request_id=exc.provider_request_id,
            http_status=exc.http_status,
            provider_usage=exc.usage,
        )
        return
    except Exception:
        logger.exception(
            "agent_job_failed",
            extra={"job_id": str(claim.id)},
        )
        if work.agent_run_id is not None:
            await fail_agent_run(
                database,
                work.agent_run_id,
                "ai_agent_failed",
                "Agent 执行失败，请重新发送消息。",
            )
        await reject_or_retry_conversation_job(
            database,
            claim,
            work.generation,
            error_code="ai_agent_failed",
            error_message="Agent 执行失败，请重新发送消息。",
            error_stage="worker",
            retryable=False,
        )
        return
    if isinstance(provider_result, AgentOutcome):
        await complete_agent_job(
            database,
            claim,
            work.generation,
            provider_result,
            settings=settings,
            cipher=cipher,
            quality_sample_retention_days=settings.ai_quality_sample_retention_days,
        )
        await persist_agent_memories(
            database,
            claim,
            provider_result.memory_writes,
        )
        return
    await complete_conversation_job(
        database,
        claim,
        work.generation,
        provider_result,
        settings=settings,
        cipher=cipher,
        quality_sample_retention_days=settings.ai_quality_sample_retention_days,
    )
    await persist_agent_memories(
        database,
        claim,
        provider_result.memory_writes or [],
    )


async def _process_transcription(
    database: Database,
    settings: Settings,
    cipher: Fernet,
    claim: ClaimedJob,
) -> None:
    try:
        work = await load_transcription_work(
            database,
            cipher,
            claim,
        )
    except TranscriptionWorkUnavailable as exc:
        await reject_or_retry_transcription_job(
            database,
            claim,
            error_code=exc.code,
            error_message=exc.message,
            error_stage=exc.stage,
            retryable=False,
        )
        return
    if not await prepare_attempt_body_log(database, claim, work.request_body):
        return

    remaining = (claim.deadline_at - now()).total_seconds()
    if remaining <= 0:
        await reject_or_retry_transcription_job(
            database,
            claim,
            error_code="ai_job_deadline_exceeded",
            error_message="语音转写已超过总等待时间，请重新录制。",
            error_stage="deadline",
            retryable=False,
        )
        return

    async def mark_started() -> bool:
        return await mark_provider_request_started(database, claim)

    try:
        provider_result = await _with_lease_heartbeat(
            database,
            claim,
            settings,
            transcribe_audio(
                work.config,
                settings,
                cipher,
                audio=work.audio,
                filename=work.filename,
                timeout_seconds=remaining,
                on_request_started=mark_started,
            ),
        )
    except (JobLeaseLost, TranscriptionGenerationSuperseded):
        return
    except TranscriptionProviderError as exc:
        await reject_or_retry_transcription_job(
            database,
            claim,
            error_code=exc.code,
            error_message=exc.message,
            error_stage=exc.stage,
            retryable=exc.retryable,
            provider_request_id=exc.provider_request_id,
            http_status=exc.http_status,
            provider_usage=exc.usage,
        )
        return
    finally:
        work = None

    await complete_transcription_job(
        database,
        claim,
        provider_result,
        quality_sample_retention_days=settings.ai_quality_sample_retention_days,
    )


async def _process_planning(
    database: Database,
    settings: Settings,
    cipher: Fernet,
    claim: ClaimedJob,
) -> None:
    try:
        work = await load_planning_work(database, claim)
    except PlanningWorkUnavailable as exc:
        await reject_or_retry_planning_job(
            database,
            claim,
            error_code=exc.code,
            error_message=exc.message,
            error_stage=exc.stage,
            retryable=False,
        )
        return
    if not await prepare_attempt_body_log(database, claim, work.request_body):
        return

    remaining = (claim.deadline_at - now()).total_seconds()
    if remaining <= 0:
        await reject_or_retry_planning_job(
            database,
            claim,
            error_code="ai_job_deadline_exceeded",
            error_message="AI 作业已超过总等待时间，请重新生成。",
            error_stage="deadline",
            retryable=False,
        )
        return

    async def persist_delta(text: str) -> bool:
        if work.generation is None:
            return True
        return await append_conversation_delta(
            database,
            claim,
            generation_number=work.generation,
            text_delta=text,
            lease_seconds=settings.ai_job_lease_seconds,
        )

    async def mark_started() -> bool:
        return await mark_provider_request_started(database, claim)

    try:
        provider_result = await _with_lease_heartbeat(
            database,
            claim,
            settings,
            stream_planning_reply(
                work.config,
                settings,
                cipher,
                messages=work.messages,
                timeout_seconds=remaining,
                on_delta=persist_delta,
                on_request_started=mark_started,
            ),
        )
    except (JobLeaseLost, PlanningGenerationSuperseded):
        return
    except PlanningProviderError as exc:
        await reject_or_retry_planning_job(
            database,
            claim,
            error_code=exc.code,
            error_message=exc.message,
            error_stage=exc.stage,
            retryable=exc.retryable,
            provider_request_id=exc.provider_request_id,
            http_status=exc.http_status,
            provider_usage=exc.usage,
        )
        return
    await complete_planning_job(
        database,
        claim,
        work.snapshot,
        provider_result,
        quality_sample_retention_days=settings.ai_quality_sample_retention_days,
    )


async def _process_review(
    database: Database,
    settings: Settings,
    cipher: Fernet,
    claim: ClaimedJob,
) -> None:
    try:
        work = await load_review_work(database, claim)
    except ReviewWorkUnavailable as exc:
        await reject_or_retry_review_job(
            database,
            claim,
            error_code=exc.code,
            error_message=exc.message,
            error_stage=exc.stage,
            retryable=False,
        )
        return
    if not await prepare_attempt_body_log(database, claim, work.request_body):
        return

    remaining = (claim.deadline_at - now()).total_seconds()
    if remaining <= 0:
        await reject_or_retry_review_job(
            database,
            claim,
            error_code="ai_job_deadline_exceeded",
            error_message="AI 作业已超过总等待时间，请重新生成。",
            error_stage="deadline",
            retryable=False,
        )
        return

    async def accept_delta(_: str) -> bool:
        return True

    async def mark_started() -> bool:
        return await mark_provider_request_started(database, claim)

    try:
        provider_result = await _with_lease_heartbeat(
            database,
            claim,
            settings,
            stream_review_reply(
                work.config,
                settings,
                cipher,
                messages=work.messages,
                purpose=work.purpose,
                timeout_seconds=remaining,
                on_delta=accept_delta,
                on_request_started=mark_started,
            ),
        )
    except (JobLeaseLost, ReviewGenerationSuperseded):
        return
    except ReviewProviderError as exc:
        await reject_or_retry_review_job(
            database,
            claim,
            error_code=exc.code,
            error_message=exc.message,
            error_stage=exc.stage,
            retryable=exc.retryable,
            provider_request_id=exc.provider_request_id,
            http_status=exc.http_status,
            provider_usage=exc.usage,
        )
        return
    await complete_review_job(
        database,
        claim,
        provider_result,
        quality_sample_retention_days=settings.ai_quality_sample_retention_days,
    )


async def process_one(database: Database, settings: Settings) -> bool:
    claim = await claim_next_job(
        database,
        lease_seconds=settings.ai_job_lease_seconds,
        body_log_retention_days=settings.ai_body_log_retention_days,
    )
    if claim is None:
        return False
    if claim.terminal_error_code is not None:
        if claim.capability == "conversation":
            await reject_or_retry_conversation_job(
                database,
                claim,
                None,
                error_code=claim.terminal_error_code,
                error_message=claim.terminal_error_message
                or "AI 作业未能完成，请重新提交。",
                error_stage=claim.terminal_error_stage or "worker",
                retryable=False,
            )
        elif claim.capability == "planning":
            await reject_or_retry_planning_job(
                database,
                claim,
                error_code=claim.terminal_error_code,
                error_message=claim.terminal_error_message
                or "AI 作业未能完成，请重新生成。",
                error_stage=claim.terminal_error_stage or "worker",
                retryable=False,
            )
        elif claim.capability == "review":
            await reject_or_retry_review_job(
                database,
                claim,
                error_code=claim.terminal_error_code,
                error_message=claim.terminal_error_message
                or "AI 作业未能完成，请重新生成。",
                error_stage=claim.terminal_error_stage or "worker",
                retryable=False,
            )
        elif claim.capability == "transcription":
            await reject_or_retry_transcription_job(
                database,
                claim,
                error_code=claim.terminal_error_code,
                error_message=claim.terminal_error_message
                or "语音转写未能完成，请重新录制。",
                error_stage=claim.terminal_error_stage or "worker",
                retryable=False,
            )
        else:
            await reject_or_retry_job(
                database,
                claim,
                error_code=claim.terminal_error_code,
                error_message=claim.terminal_error_message
                or "AI 作业未能完成，请重新提交。",
                error_stage=claim.terminal_error_stage or "worker",
                retryable=False,
            )
        return True
    cipher = Fernet(
        (settings.ai_encryption_key or settings.encryption_key).get_secret_value()
    )
    if claim.capability == "calendar_screenshot":
        await _process_screenshot(database, settings, cipher, claim)
    elif claim.capability == "conversation":
        await _process_conversation(database, settings, cipher, claim)
    elif claim.capability == "planning":
        await _process_planning(database, settings, cipher, claim)
    elif claim.capability == "review":
        await _process_review(database, settings, cipher, claim)
    elif claim.capability == "transcription":
        await _process_transcription(database, settings, cipher, claim)
    else:
        await reject_or_retry_job(
            database,
            claim,
            error_code="ai_capability_unsupported",
            error_message="此 AI 作业能力暂不受执行进程支持。",
            error_stage="worker",
            retryable=False,
        )
    logger.info(
        "ai_job_processed",
        extra={
            "job_id": str(claim.id),
            "capability": claim.capability,
            "status": "processed",
        },
    )
    return True


async def run(*, once: bool = False) -> None:
    configure_logging()
    settings = get_settings()
    database = Database(settings)
    try:
        while True:
            try:
                await cleanup_expired_transcriptions(database)
                await cleanup_expired_media(database)
                await cleanup_expired_calendar_import_inputs(database)
                await cleanup_expired_job_events(database)
                found = await process_one(database, settings)
            except Exception as exc:
                logger.warning(
                    "ai_worker_error",
                    extra={"error_type": type(exc).__name__},
                )
                if once:
                    raise RuntimeError(
                        "AI 工作进程失败，请检查数据库与模型配置。"
                    ) from None
                found = False
            if once:
                break
            if not found:
                await asyncio.sleep(1)
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="运行 iTime AI 工作进程")
    parser.add_argument("--once", action="store_true", help="处理最多一条后退出")
    args = parser.parse_args()
    asyncio.run(run(once=args.once))


if __name__ == "__main__":
    main()
