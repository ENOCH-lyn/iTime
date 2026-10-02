"""持久化日历订阅刷新进程；数据库租约之外访问不可信来源。"""

import argparse
import asyncio
import logging

from itime.calendar.subscription_fetcher import (
    FetchValidators,
    RestrictedCalendarFetcher,
    SubscriptionFetchError,
)
from itime.calendar.subscription_parser import parse_subscription_ics
from itime.calendar.subscription_service import (
    apply_subscription_snapshot,
    claim_next_subscription,
    complete_not_modified,
    decrypt_subscription_url,
    fail_subscription_refresh,
    renew_subscription_lease,
)
from itime.config import Settings, get_settings
from itime.database import Database
from itime.errors import AppError
from itime.observability import configure_logging

logger = logging.getLogger("itime.calendar.subscription")


async def process_one(
    database: Database,
    settings: Settings,
    *,
    fetcher: RestrictedCalendarFetcher | None = None,
) -> bool:
    claim = await claim_next_subscription(
        database,
        lease_seconds=settings.calendar_subscription_lease_seconds,
    )
    if claim is None:
        return False
    try:
        source_url = decrypt_subscription_url(settings, claim.encrypted_url)
        final_url = (
            decrypt_subscription_url(settings, claim.encrypted_final_url)
            if claim.encrypted_final_url is not None
            else None
        )
    except AppError as exc:
        await fail_subscription_refresh(
            database,
            claim,
            error_code=exc.code,
            error_message=exc.message,
        )
        return True
    validators = (
        FetchValidators(
            final_url=final_url,
            etag=claim.etag,
            last_modified=claim.last_modified,
        )
        if final_url is not None and (claim.etag or claim.last_modified)
        else None
    )
    active_fetcher = fetcher or RestrictedCalendarFetcher(settings)
    try:
        result = await active_fetcher.fetch(source_url, validators=validators)
    except SubscriptionFetchError as exc:
        await fail_subscription_refresh(
            database,
            claim,
            error_code=exc.code,
            error_message=exc.message,
            http_status=exc.http_status,
        )
        return True
    if result.status == 304:
        await complete_not_modified(database, claim, result)
        return True
    if result.content is None:
        await fail_subscription_refresh(
            database,
            claim,
            error_code="calendar_subscription_response_missing",
            error_message="订阅服务器未返回日历内容。",
            http_status=result.status,
        )
        return True
    if not await renew_subscription_lease(
        database,
        claim,
        lease_seconds=settings.calendar_subscription_lease_seconds,
    ):
        return True
    try:
        snapshot = await asyncio.to_thread(
            parse_subscription_ics,
            result.content,
            default_time_zone=claim.default_time_zone,
        )
    except AppError as exc:
        await fail_subscription_refresh(
            database,
            claim,
            error_code=exc.code,
            error_message=exc.message,
            http_status=result.status,
        )
        return True
    if not await renew_subscription_lease(
        database,
        claim,
        lease_seconds=settings.calendar_subscription_lease_seconds,
    ):
        return True
    try:
        applied = await apply_subscription_snapshot(
            database,
            settings,
            claim,
            result,
            snapshot,
        )
    except AppError as exc:
        await fail_subscription_refresh(
            database,
            claim,
            error_code=exc.code,
            error_message=exc.message,
            http_status=result.status,
        )
        return True
    logger.info(
        "calendar_subscription_processed",
        extra={
            "source_id": str(claim.source_id),
            "attempt_number": claim.attempt_number,
            "status": "succeeded" if applied else "superseded",
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
                found = await process_one(database, settings)
            except Exception as exc:
                logger.warning(
                    "calendar_subscription_worker_error",
                    extra={"error_type": type(exc).__name__},
                )
                if once:
                    raise RuntimeError(
                        "日历订阅刷新进程失败，请检查数据库与网络配置。"
                    ) from None
                found = False
            if once:
                break
            if not found:
                await asyncio.sleep(1)
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="运行 iTime 日历订阅刷新进程")
    parser.add_argument("--once", action="store_true", help="处理最多一个订阅后退出")
    args = parser.parse_args()
    asyncio.run(run(once=args.once))


if __name__ == "__main__":
    main()
