from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request
from sqlalchemy import and_, or_, select

from itime.ai.conversation_service import cancel_conversation_projection
from itime.ai.job_service import cancel_job, job_result, locked_job
from itime.ai.models import AIJob
from itime.ai.schemas import AIJobCancelInput, Capability
from itime.auth.session import authorized
from itime.errors import AppError
from itime.tasks.service import replay_operation, save_operation, validate_cursor

router = APIRouter(prefix="/api/v1/ai/jobs", tags=["AI 作业"])


@router.get("")
async def list_ai_jobs(
    request: Request,
    capability: Capability | None = None,
    before_time: datetime | None = None,
    before_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> dict:
    validate_cursor(before_time, before_id)
    async with authorized(request) as identity:
        statement = select(AIJob).where(AIJob.owner_id == identity.user.id)
        if capability is not None:
            statement = statement.where(AIJob.capability == capability)
        if before_time is not None:
            statement = statement.where(
                or_(
                    AIJob.created_at < before_time,
                    and_(
                        AIJob.created_at == before_time,
                        AIJob.id < before_id,
                    ),
                )
            )
        rows = list(
            await identity.session.scalars(
                statement.order_by(AIJob.created_at.desc(), AIJob.id.desc()).limit(
                    limit + 1
                )
            )
        )
        page = rows[:limit]
        return {
            "items": [job_result(job) for job in page],
            "next_cursor": (
                {"before_time": page[-1].created_at, "before_id": page[-1].id}
                if len(rows) > limit
                else None
            ),
        }


@router.get("/{job_id}")
async def get_ai_job(job_id: UUID, request: Request) -> dict:
    async with authorized(request) as identity:
        job = await identity.session.scalar(
            select(AIJob).where(
                AIJob.id == job_id,
                AIJob.owner_id == identity.user.id,
            )
        )
        if job is None:
            raise AppError("ai_job_not_found", "未找到此 AI 作业。", status=404)
        return job_result(job)


@router.post("/{job_id}/cancel")
async def cancel_ai_job(
    job_id: UUID,
    data: AIJobCancelInput,
    request: Request,
) -> dict:
    scope = f"ai_job.cancel:{job_id}"
    async with authorized(request, write=True) as identity:
        replay = await replay_operation(
            identity.session,
            identity.user.id,
            data.operation_id,
            scope,
            data,
        )
        if replay is not None:
            return replay
        job = await locked_job(identity.session, identity.user.id, job_id)
        changed = await cancel_job(identity.session, job)
        if changed:
            await cancel_conversation_projection(identity.session, job)
        result = {"operation_id": data.operation_id, "job": job_result(job)}
        save_operation(
            identity.session,
            identity.user.id,
            data.operation_id,
            scope,
            data,
            result,
        )
        return result
