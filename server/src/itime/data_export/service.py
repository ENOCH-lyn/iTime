from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from itime.auth.security import now
from itime.config import Settings
from itime.data_export.archive import ExportArchive
from itime.data_export.models import DataExportJob
from itime.database import Database
from itime.errors import AppError

EXPORT_CLEANUP_BATCH_SIZE = 100


@dataclass(frozen=True)
class ClaimedExport:
    id: UUID
    owner_id: UUID
    lease_id: UUID
    attempt_number: int


def export_cipher(settings: Settings) -> Fernet:
    key = settings.export_encryption_key or settings.encryption_key
    return Fernet(key.get_secret_value())


def decrypt_export_artifact(settings: Settings, ciphertext: bytes) -> bytes:
    try:
        return export_cipher(settings).decrypt(ciphertext)
    except InvalidToken as exc:
        raise AppError(
            "data_export_artifact_unavailable",
            "导出文件暂时无法读取，请重新生成。",
            status=503,
            retryable=True,
        ) from exc


def effective_export_status(
    job: DataExportJob,
    *,
    current_time=None,
) -> str:
    current = current_time or now()
    if (
        job.status == "succeeded"
        and job.expires_at is not None
        and job.expires_at <= current
    ):
        return "expired"
    return job.status


def export_job_result(
    job: DataExportJob,
    *,
    current_time=None,
) -> dict:
    status = effective_export_status(job, current_time=current_time)
    return {
        "id": job.id,
        "status": status,
        "schema_version": job.schema_version,
        "attempt_count": job.attempt_count,
        "snapshot_at": job.snapshot_at,
        "filename": job.filename,
        "byte_size": job.artifact_byte_size,
        "sha256": job.artifact_sha256,
        "expires_at": job.expires_at,
        "error": (
            {"code": job.error_code, "message": job.error_message}
            if job.error_code is not None
            else None
        ),
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "finished_at": job.finished_at,
    }


async def require_export_job(
    session: AsyncSession,
    owner_id: UUID,
    job_id: UUID,
    *,
    lock: bool = False,
) -> DataExportJob:
    statement = select(DataExportJob).where(
        DataExportJob.id == job_id,
        DataExportJob.owner_id == owner_id,
    )
    if lock:
        statement = statement.with_for_update()
    job = await session.scalar(statement)
    if job is None:
        raise AppError(
            "data_export_not_found",
            "未找到此数据导出。",
            status=404,
        )
    return job


async def claim_next_export(
    database: Database,
    *,
    lease_seconds: int,
) -> ClaimedExport | None:
    current = now()
    async with database.transaction() as session:
        for _ in range(EXPORT_CLEANUP_BATCH_SIZE):
            job = await session.scalar(
                select(DataExportJob)
                .where(
                    or_(
                        (
                            (DataExportJob.status == "queued")
                            & (DataExportJob.available_at <= current)
                        ),
                        (
                            (DataExportJob.status == "running")
                            & (DataExportJob.lease_until <= current)
                        ),
                    )
                )
                .order_by(
                    DataExportJob.available_at,
                    DataExportJob.created_at,
                    DataExportJob.id,
                )
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if job is None:
                return None
            if job.attempt_count >= job.max_attempts:
                job.status = "failed"
                job.lease_id = None
                job.lease_until = None
                job.error_code = "data_export_generation_failed"
                job.error_message = "数据导出多次生成失败，请稍后重新生成。"
                job.finished_at = current
                job.updated_at = current
                continue
            lease_id = uuid4()
            job.status = "running"
            job.attempt_count += 1
            job.lease_id = lease_id
            job.lease_until = current + timedelta(seconds=lease_seconds)
            job.updated_at = current
            await session.flush()
            return ClaimedExport(
                id=job.id,
                owner_id=job.owner_id,
                lease_id=lease_id,
                attempt_number=job.attempt_count,
            )
    return None


async def renew_export_lease(
    database: Database,
    claim: ClaimedExport,
    *,
    lease_seconds: int,
) -> bool:
    current = now()
    async with database.transaction() as session:
        job = await session.scalar(
            select(DataExportJob)
            .where(
                DataExportJob.id == claim.id,
                DataExportJob.owner_id == claim.owner_id,
            )
            .with_for_update()
        )
        if (
            job is None
            or job.status != "running"
            or job.lease_id != claim.lease_id
            or job.lease_until is None
            or job.lease_until <= current
        ):
            return False
        job.lease_until = current + timedelta(seconds=lease_seconds)
        job.updated_at = current
        return True


async def complete_export(
    database: Database,
    settings: Settings,
    claim: ClaimedExport,
    *,
    snapshot_at: datetime,
    archive: ExportArchive,
    ciphertext: bytes,
) -> bool:
    current = now()
    async with database.transaction() as session:
        job = await session.scalar(
            select(DataExportJob)
            .where(
                DataExportJob.id == claim.id,
                DataExportJob.owner_id == claim.owner_id,
            )
            .with_for_update()
        )
        if (
            job is None
            or job.status != "running"
            or job.lease_id != claim.lease_id
            or job.lease_until is None
            or job.lease_until <= current
        ):
            return False
        job.status = "succeeded"
        job.lease_id = None
        job.lease_until = None
        job.snapshot_at = snapshot_at
        job.artifact_ciphertext = ciphertext
        job.artifact_sha256 = archive.sha256
        job.artifact_byte_size = archive.byte_size
        job.filename = archive.filename
        job.expires_at = current + timedelta(
            hours=settings.export_artifact_retention_hours
        )
        job.error_code = None
        job.error_message = None
        job.finished_at = current
        job.updated_at = current
        return True


async def fail_export(
    database: Database,
    claim: ClaimedExport,
    *,
    code: str,
    message: str,
    retryable: bool,
) -> bool:
    current = now()
    async with database.transaction() as session:
        job = await session.scalar(
            select(DataExportJob)
            .where(
                DataExportJob.id == claim.id,
                DataExportJob.owner_id == claim.owner_id,
            )
            .with_for_update()
        )
        if (
            job is None
            or job.status != "running"
            or job.lease_id != claim.lease_id
        ):
            return False
        job.lease_id = None
        job.lease_until = None
        job.updated_at = current
        if retryable and job.attempt_count < job.max_attempts:
            job.status = "queued"
            job.available_at = current + timedelta(
                seconds=min(2 ** job.attempt_count, 30)
            )
            return True
        job.status = "failed"
        job.error_code = code
        job.error_message = message
        job.finished_at = current
        return True


async def cleanup_expired_exports(
    database: Database,
    *,
    limit: int = EXPORT_CLEANUP_BATCH_SIZE,
    retention_days: int = 30,
) -> tuple[int, int]:
    if limit < 1 or limit > EXPORT_CLEANUP_BATCH_SIZE:
        raise ValueError(
            f"limit must be between 1 and {EXPORT_CLEANUP_BATCH_SIZE}"
        )
    current = now()
    async with database.transaction() as session:
        expired = list(
            await session.scalars(
                select(DataExportJob)
                .where(
                    DataExportJob.status == "succeeded",
                    DataExportJob.expires_at <= current,
                    DataExportJob.artifact_ciphertext.is_not(None),
                )
                .order_by(DataExportJob.expires_at, DataExportJob.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        for job in expired:
            job.status = "expired"
            job.artifact_ciphertext = None
            job.updated_at = current

    if retention_days < 1:
        raise ValueError("retention_days must be positive")
    metadata_cutoff = current - timedelta(days=retention_days)
    async with database.transaction() as session:
        old_ids = list(
            await session.scalars(
                select(DataExportJob.id)
                .where(
                    DataExportJob.status.in_(("failed", "expired")),
                    DataExportJob.finished_at <= metadata_cutoff,
                    DataExportJob.artifact_ciphertext.is_(None),
                )
                .order_by(DataExportJob.finished_at, DataExportJob.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        if old_ids:
            await session.execute(
                delete(DataExportJob).where(DataExportJob.id.in_(old_ids))
            )
    return len(expired), len(old_ids)
