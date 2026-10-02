"""持久化 AI 作业执行进程；数据库租约之外调用上游。"""

import argparse
import asyncio
import logging

from cryptography.fernet import Fernet

from itime.ai.job_service import (
    claim_next_job,
    cleanup_expired_media,
    reject_or_retry_job,
)
from itime.ai.screenshot_provider import (
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


async def process_one(database: Database, settings: Settings) -> bool:
    claim = await claim_next_job(
        database,
        capability="calendar_screenshot",
        lease_seconds=settings.ai_job_lease_seconds,
    )
    if claim is None:
        return False
    cipher = Fernet(
        (settings.ai_encryption_key or settings.encryption_key).get_secret_value()
    )
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
        return True

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
        return True
    try:
        provider_result = await recognize_screenshot(
            work.config,
            settings,
            cipher,
            image=work.image,
            content_type=work.content_type,
            context=work.context,
            timeout_seconds=remaining,
        )
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
        )
        return True
    finally:
        work = None

    batch_id = await apply_screenshot_result(database, claim, provider_result)
    logger.info(
        "ai_job_processed",
        extra={
            "job_id": str(claim.id),
            "capability": claim.capability,
            "status": "succeeded" if batch_id is not None else "superseded",
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
