"""Persistent personal-data export worker."""

import argparse
import asyncio
import logging
from datetime import datetime

from itime.config import Settings, get_settings
from itime.data_export.archive import (
    ExportBuildError,
    ExportLimits,
    build_export_archive,
)
from itime.data_export.service import (
    claim_next_export,
    cleanup_expired_exports,
    complete_export,
    export_cipher,
    fail_export,
    renew_export_lease,
)
from itime.data_export.snapshot import read_consistent_snapshot
from itime.database import Database
from itime.errors import AppError
from itime.observability import configure_logging

logger = logging.getLogger("itime.data_export")


async def process_one(database: Database, settings: Settings) -> bool:
    claim = await claim_next_export(
        database,
        lease_seconds=settings.export_job_lease_seconds,
    )
    if claim is None:
        return False
    try:
        snapshot = await read_consistent_snapshot(
            database,
            claim.owner_id,
            settings,
        )
        if not await renew_export_lease(
            database,
            claim,
            lease_seconds=settings.export_job_lease_seconds,
        ):
            return True
        archive = await asyncio.to_thread(
            build_export_archive,
            snapshot,
            claim.id,
            ExportLimits(
                max_uncompressed_bytes=settings.export_max_uncompressed_bytes,
                max_archive_bytes=settings.export_max_archive_bytes,
            ),
        )
        if not await renew_export_lease(
            database,
            claim,
            lease_seconds=settings.export_job_lease_seconds,
        ):
            return True
        ciphertext = await asyncio.to_thread(
            export_cipher(settings).encrypt,
            archive.content,
        )
        snapshot_value = snapshot["snapshot_at"]
        if not isinstance(snapshot_value, str):
            raise RuntimeError("export snapshot timestamp is invalid")
        snapshot_at = datetime.fromisoformat(
            snapshot_value.replace("Z", "+00:00")
        )
        await complete_export(
            database,
            settings,
            claim,
            snapshot_at=snapshot_at,
            archive=archive,
            ciphertext=ciphertext,
        )
    except ExportBuildError as exc:
        await fail_export(
            database,
            claim,
            code=exc.code,
            message=exc.message,
            retryable=False,
        )
    except AppError as exc:
        await fail_export(
            database,
            claim,
            code=exc.code,
            message=exc.message,
            retryable=False,
        )
    except Exception as exc:
        logger.warning(
            "data_export_generation_error",
            extra={
                "job_id": str(claim.id),
                "attempt": claim.attempt_number,
                "error_type": type(exc).__name__,
            },
        )
        await fail_export(
            database,
            claim,
            code="data_export_generation_failed",
            message="数据导出生成失败，请稍后重新生成。",
            retryable=True,
        )
    return True


async def run(*, once: bool = False) -> None:
    configure_logging()
    settings = get_settings()
    database = Database(settings)
    last_cleanup = 0.0
    try:
        while True:
            try:
                found = await process_one(database, settings)
                current_loop_time = asyncio.get_running_loop().time()
                if once or current_loop_time - last_cleanup >= 60:
                    await cleanup_expired_exports(
                        database,
                        retention_days=settings.export_job_retention_days,
                    )
                    last_cleanup = current_loop_time
            except Exception as exc:
                logger.warning(
                    "data_export_worker_error",
                    extra={"error_type": type(exc).__name__},
                )
                if once:
                    raise RuntimeError(
                        "数据导出进程失败，请检查数据库与导出配置。"
                    ) from None
                found = False
            if once:
                break
            if not found:
                await asyncio.sleep(1)
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="运行 iTime 本人数据导出进程")
    parser.add_argument("--once", action="store_true", help="处理最多一个导出后退出")
    args = parser.parse_args()
    asyncio.run(run(once=args.once))


if __name__ == "__main__":
    main()
