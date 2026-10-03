"""持久化 AI 作业执行进程；数据库租约之外调用上游。"""

import argparse
import asyncio
import logging
from collections.abc import Awaitable

from cryptography.fernet import Fernet

from itime.ai.conversation_provider import (
    ConversationGenerationSuperseded,
    ConversationProviderError,
    stream_conversation_reply,
)
from itime.ai.conversation_service import (
    ConversationWorkUnavailable,
    append_conversation_delta,
    cleanup_expired_job_events,
    complete_conversation_job,
    load_conversation_work,
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
from itime.ai.screenshot_provider import (
    ScreenshotGenerationSuperseded,
    ScreenshotProviderError,
    recognize_screenshot,
)
from itime.auth.security import now
from itime.calendar.screenshot_service import (
    ScreenshotWorkUnavailable,
    apply_screenshot_result,
    load_screenshot_work,
)
from itime.config import Settings, get_settings
from itime.database import Database
from itime.observability import configure_logging

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
        work = await load_screenshot_work(
            database,
            cipher,
            claim,
        )
    except ScreenshotWorkUnavailable as exc:
        await reject_or_retry_job(
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
        await reject_or_retry_job(
            database,
            claim,
            error_code="ai_job_deadline_exceeded",
            error_message="AI 作业已超过总等待时间，请重新提交。",
            error_stage="deadline",
            retryable=False,
        )
        return
    try:
        async def mark_started() -> bool:
            return await mark_provider_request_started(database, claim)

        provider_result = await _with_lease_heartbeat(
            database,
            claim,
            settings,
            recognize_screenshot(
                work.config,
                settings,
                cipher,
                image=work.image,
                content_type=work.content_type,
                context=work.context,
                timeout_seconds=remaining,
                on_request_started=mark_started,
            ),
        )
    except (JobLeaseLost, ScreenshotGenerationSuperseded):
        return
    except ScreenshotProviderError as exc:
        await reject_or_retry_job(
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

    await apply_screenshot_result(
        database,
        claim,
        provider_result,
        quality_sample_retention_days=settings.ai_quality_sample_retention_days,
    )


async def _process_conversation(
    database: Database,
    settings: Settings,
    cipher: Fernet,
    claim: ClaimedJob,
) -> None:
    try:
        work = await load_conversation_work(database, claim)
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

    async def mark_started() -> bool:
        return await mark_provider_request_started(database, claim)

    try:
        provider_result = await _with_lease_heartbeat(
            database,
            claim,
            settings,
            stream_conversation_reply(
                work.config,
                settings,
                cipher,
                messages=work.messages,
                timeout_seconds=remaining,
                on_delta=persist_delta,
                on_request_started=mark_started,
            ),
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
    await complete_conversation_job(
        database,
        claim,
        work.generation,
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
                await cleanup_expired_media(database)
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
