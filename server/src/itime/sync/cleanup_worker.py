"""Expired synchronization event cleanup process."""

import argparse
import asyncio
import logging

from itime.config import get_settings
from itime.database import Database
from itime.observability import configure_logging
from itime.sync.service import cleanup_expired_events

logger = logging.getLogger("itime.sync.cleanup")


async def run(*, once: bool = False) -> None:
    configure_logging()
    settings = get_settings()
    database = Database(settings)
    try:
        while True:
            try:
                cleaned = await cleanup_expired_events(
                    database,
                    retention_days=settings.sync_event_retention_days,
                )
            except Exception as exc:
                logger.warning(
                    "sync_cleanup_worker_error",
                    extra={"error_type": type(exc).__name__},
                )
                if once:
                    raise RuntimeError(
                        "同步事件清理进程失败，请检查数据库状态。"
                    ) from None
                cleaned = 0
            if once:
                break
            if cleaned == 0:
                await asyncio.sleep(60)
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="运行 iTime 同步事件清理进程")
    parser.add_argument("--once", action="store_true", help="处理最多一批后退出")
    args = parser.parse_args()
    asyncio.run(run(once=args.once))


if __name__ == "__main__":
    main()
