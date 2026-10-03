import base64
import hashlib
import hmac
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import Response as BinaryResponse
from sqlalchemy import select

from itime.auth.security import now
from itime.auth.session import audit, authorized
from itime.data_export.models import DataExportJob
from itime.data_export.schemas import DATA_EXPORT_SCHEMA_VERSION, DataExportCreate
from itime.data_export.service import (
    decrypt_export_artifact,
    effective_export_status,
    export_job_result,
    require_export_job,
)
from itime.errors import AppError
from itime.tasks.service import replay_operation, save_operation

router = APIRouter(prefix="/api/v1/data-exports", tags=["本人数据导出"])


@router.post("", status_code=202)
async def create_data_export(
    data: DataExportCreate,
    request: Request,
    response: Response,
) -> dict:
    scope = "data_export.create"
    async with authorized(request, write=True) as identity:
        replay = await replay_operation(
            identity.session,
            identity.user.id,
            data.operation_id,
            scope,
            data,
        )
        if replay is not None:
            response.status_code = 202
            return replay
        active = await identity.session.scalar(
            select(DataExportJob)
            .where(
                DataExportJob.owner_id == identity.user.id,
                DataExportJob.status.in_(("queued", "running")),
            )
            .order_by(DataExportJob.created_at, DataExportJob.id)
            .limit(1)
        )
        reused = active is not None
        if active is None:
            current = now()
            active = DataExportJob(
                owner_id=identity.user.id,
                status="queued",
                schema_version=DATA_EXPORT_SCHEMA_VERSION,
                attempt_count=0,
                max_attempts=3,
                available_at=current,
                created_at=current,
                updated_at=current,
            )
            identity.session.add(active)
            await identity.session.flush()
            audit(
                request,
                identity.session,
                identity.user,
                "data_export_requested",
                active.id,
            )
        result = {
            "operation_id": data.operation_id,
            "reused": reused,
            "job": export_job_result(active),
        }
        save_operation(
            identity.session,
            identity.user.id,
            data.operation_id,
            scope,
            data,
            result,
        )
        response.status_code = 202
        return result


@router.get("")
async def list_data_exports(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=20)] = 20,
) -> dict:
    async with authorized(request) as identity:
        jobs = list(
            await identity.session.scalars(
                select(DataExportJob)
                .where(DataExportJob.owner_id == identity.user.id)
                .order_by(
                    DataExportJob.created_at.desc(),
                    DataExportJob.id.desc(),
                )
                .limit(limit)
            )
        )
        current = now()
        return {
            "jobs": [
                export_job_result(job, current_time=current)
                for job in jobs
            ]
        }


@router.get("/{job_id}")
async def get_data_export(job_id: UUID, request: Request) -> dict:
    async with authorized(request) as identity:
        job = await require_export_job(
            identity.session,
            identity.user.id,
            job_id,
        )
        return {"job": export_job_result(job)}


@router.get("/{job_id}/download")
async def download_data_export(
    job_id: UUID,
    request: Request,
) -> BinaryResponse:
    async with authorized(request, write=True) as identity:
        job = await require_export_job(
            identity.session,
            identity.user.id,
            job_id,
            lock=True,
        )
        status = effective_export_status(job)
        if status == "expired":
            raise AppError(
                "data_export_expired",
                "此导出文件已过期并等待清理，请重新生成。",
                status=410,
            )
        if status == "failed":
            raise AppError(
                job.error_code or "data_export_failed",
                job.error_message or "数据导出生成失败，请重新生成。",
                status=409,
                retryable=True,
            )
        if status != "succeeded":
            raise AppError(
                "data_export_not_ready",
                "数据导出仍在生成，请稍后再下载。",
                status=409,
                retryable=True,
            )
        if (
            job.artifact_ciphertext is None
            or job.artifact_sha256 is None
            or job.artifact_byte_size is None
            or job.filename is None
        ):
            raise AppError(
                "data_export_artifact_unavailable",
                "导出文件暂时无法读取，请重新生成。",
                status=503,
                retryable=True,
            )
        content = decrypt_export_artifact(
            request.app.state.settings,
            job.artifact_ciphertext,
        )
        digest = hashlib.sha256(content).hexdigest()
        if (
            len(content) != job.artifact_byte_size
            or not hmac.compare_digest(digest, job.artifact_sha256)
        ):
            raise AppError(
                "data_export_artifact_unavailable",
                "导出文件完整性校验失败，请重新生成。",
                status=503,
                retryable=True,
            )
        audit(
            request,
            identity.session,
            identity.user,
            "data_export_downloaded",
            job.id,
            details={"byte_size": job.artifact_byte_size},
        )
        return BinaryResponse(
            content=content,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{job.filename}"',
                "Content-Length": str(job.artifact_byte_size),
                "Digest": (
                    "sha-256="
                    + base64.b64encode(bytes.fromhex(job.artifact_sha256)).decode()
                ),
                "Cache-Control": "no-store, private",
                "X-Content-Type-Options": "nosniff",
            },
        )
