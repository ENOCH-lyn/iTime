"""Persisted public-link fetch worker."""

import argparse
import asyncio
import logging
from typing import Protocol

from itime.config import Settings, get_settings
from itime.database import Database
from itime.observability import configure_logging
from itime.public_links.fetcher import (
    PublicLinkFetchError,
    PublicLinkFetchResult,
    RestrictedPublicLinkFetcher,
)
from itime.public_links.service import (
    ClaimedPublicLink,
    claim_next_public_link,
    complete_public_link,
    fail_public_link,
    renew_public_link_lease,
)

logger = logging.getLogger("itime.public_links")


class PublicLinkFetcher(Protocol):
    async def fetch(self, url: str) -> PublicLinkFetchResult: ...


async def process_one(
    database: Database,
    settings: Settings,
    *,
    fetcher: PublicLinkFetcher | None = None,
) -> bool:
    claim = await claim_next_public_link(
        database,
        lease_seconds=settings.public_link_lease_seconds,
    )
    if claim is None:
        return False
    active_fetcher = fetcher or RestrictedPublicLinkFetcher(settings)
    try:
        result = await active_fetcher.fetch(claim.url)
    except PublicLinkFetchError as exc:
        await _record_failure(
            database,
            claim,
            error_code=exc.code,
            error_message=exc.message,
            retryable=exc.retryable,
            http_status=exc.http_status,
        )
        return True
    except Exception:
        await _record_failure(
            database,
            claim,
            error_code="public_link_worker_failed",
            error_message="网页读取进程发生异常，系统会稍后重试。",
            retryable=True,
        )
        return True
    if not await renew_public_link_lease(
        database,
        claim,
        lease_seconds=settings.public_link_lease_seconds,
    ):
        return True
    applied = await complete_public_link(database, claim, result)
    logger.info(
        "public_link_processed",
        extra={
            "link_id": str(claim.link_id),
            "task_id": str(claim.task_id),
            "attempt_number": claim.attempt_number,
            "status": "succeeded" if applied else "superseded",
        },
    )
    return True


async def _record_failure(
    database: Database,
    claim: ClaimedPublicLink,
    *,
    error_code: str,
    error_message: str,
    retryable: bool,
    http_status: int | None = None,
) -> None:
    applied = await fail_public_link(
        database,
        claim,
        error_code=error_code,
        error_message=error_message,
        retryable=retryable,
        http_status=http_status,
    )
    logger.info(
        "public_link_processed",
        extra={
            "link_id": str(claim.link_id),
            "task_id": str(claim.task_id),
            "attempt_number": claim.attempt_number,
            "status": "failed" if applied else "superseded",
            "error_code": error_code,
        },
    )


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
                    "public_link_worker_error",
                    extra={"error_type": type(exc).__name__},
                )
                if once:
                    raise RuntimeError(
                        "公开链接读取进程失败，请检查数据库与网络配置。"
                    ) from None
                found = False
            if once:
                break
            if not found:
                await asyncio.sleep(1)
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="运行 iTime 公开链接读取进程")
    parser.add_argument("--once", action="store_true", help="处理最多一个链接后退出")
    args = parser.parse_args()
    asyncio.run(run(once=args.once))


if __name__ == "__main__":
    main()
