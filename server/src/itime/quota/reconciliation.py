"""Release quota held by AI jobs that can no longer run."""

import argparse
import asyncio
import logging

from itime.ai.conversation_service import reject_or_retry_conversation_job
from itime.ai.job_service import claim_next_job, reject_or_retry_job
from itime.config import Settings, get_settings
from itime.database import Database
from itime.observability import configure_logging
from itime.planning.job_service import reject_or_retry_planning_job

logger = logging.getLogger("itime.quota")


async def reconcile_one(database: Database, settings: Settings) -> bool:
    claim = await claim_next_job(
        database,
        lease_seconds=settings.ai_job_lease_seconds,
        body_log_retention_days=settings.ai_body_log_retention_days,
        terminal_only=True,
    )
    if claim is None:
        return False
    if claim.terminal_error_code is None:
        raise RuntimeError("terminal-only claim returned runnable AI job")
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


async def run(*, limit: int = 100) -> int:
    configure_logging()
    settings = get_settings()
    database = Database(settings)
    processed = 0
    try:
        while processed < limit and await reconcile_one(database, settings):
            processed += 1
    finally:
        await database.close()
    logger.info("ai_quota_reconciliation_complete", extra={"processed": processed})
    return processed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="终态化无法继续的 AI 作业并释放用户额度预留"
    )
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    if not 1 <= args.limit <= 1000:
        parser.error("--limit 必须在 1 到 1000 之间")
    asyncio.run(run(limit=args.limit))


if __name__ == "__main__":
    main()
