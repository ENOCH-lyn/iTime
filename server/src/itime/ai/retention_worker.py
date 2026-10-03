"""AI 技术正文日志与质量样本到期清理进程。"""

import argparse
import asyncio
import logging

from itime.ai.governance_service import cleanup_expired_governance
from itime.config import get_settings
from itime.database import Database
from itime.observability import configure_logging

logger = logging.getLogger("itime.ai.retention")


async def run(*, once: bool = False) -> None:
    configure_logging()
    database = Database(get_settings())
    try:
        while True:
            try:
                body_logs, quality_samples = await cleanup_expired_governance(
                    database
                )
            except Exception as exc:
                logger.warning(
                    "ai_retention_worker_error",
                    extra={"error_type": type(exc).__name__},
                )
                if once:
                    raise RuntimeError(
                        "AI 生命周期清理进程失败，请检查数据库状态。"
                    ) from None
                body_logs = quality_samples = 0
            if once:
                break
            if body_logs == 0 and quality_samples == 0:
                await asyncio.sleep(60)
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="运行 iTime AI 生命周期清理进程")
    parser.add_argument("--once", action="store_true", help="处理每类最多一批后退出")
    args = parser.parse_args()
    asyncio.run(run(once=args.once))


if __name__ == "__main__":
    main()
