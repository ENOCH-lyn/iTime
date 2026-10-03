"""Permanent cleanup worker for expired account deletions."""

import argparse
import asyncio
import logging

from itime.account_deletion.service import (
    claim_next_deletion,
    purge_claimed_account,
    record_deletion_failure,
)
from itime.config import get_settings
from itime.database import Database
from itime.observability import configure_logging

logger = logging.getLogger("itime.account_deletion")


async def process_one(
    database: Database,
    *,
    lease_seconds: int = 120,
) -> bool:
    claim = await claim_next_deletion(
        database,
        lease_seconds=lease_seconds,
    )
    if claim is None:
        return False
    try:
        return await purge_claimed_account(database, claim)
    except Exception:
        await record_deletion_failure(database, claim)
        raise


async def run(*, once: bool = False) -> None:
    configure_logging()
    database = Database(get_settings())
    try:
        while True:
            try:
                processed = await process_one(database)
            except Exception as exc:
                logger.warning(
                    "account_deletion_worker_error",
                    extra={"error_type": type(exc).__name__},
                )
                if once:
                    raise RuntimeError(
                        "账号注销清理失败，请检查数据库。"
                    ) from None
                processed = False
            if once:
                break
            if not processed:
                await asyncio.sleep(5)
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="运行 iTime 账号注销清理进程")
    parser.add_argument(
        "--once",
        action="store_true",
        help="处理最多一个到期账号后退出",
    )
    args = parser.parse_args()
    asyncio.run(run(once=args.once))


if __name__ == "__main__":
    main()
