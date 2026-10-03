import asyncio
import json
import time
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, or_, select

from itime.ai.conversation_models import AIJobEvent
from itime.ai.conversation_service import (
    cancel_conversation_projection,
)
from itime.ai.job_service import cancel_job, job_result, locked_job
from itime.ai.models import AIJob
from itime.ai.schemas import AIJobCancelInput, Capability
from itime.auth.session import authorized
from itime.errors import AppError
from itime.quota.service import release_reservation
from itime.tasks.service import replay_operation, save_operation, validate_cursor

router = APIRouter(prefix="/api/v1/ai/jobs", tags=["AI 作业"])
SSE_WINDOW_SECONDS = 15.0
SSE_POLL_SECONDS = 0.25


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
            await release_reservation(
                identity.session,
                job=job,
                reason="cancelled",
            )
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


def _event_payload(event: AIJobEvent) -> dict:
    return {
        "sequence": event.sequence,
        "generation": event.generation,
        "kind": event.kind,
        "payload": event.payload,
        "created_at": event.created_at,
    }


def _sse_event(event: AIJobEvent) -> str:
    data = json.dumps(
        jsonable_encoder(_event_payload(event)),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"id: {event.sequence}\nevent: {event.kind}\ndata: {data}\n\n"


@router.get("/{job_id}/events")
async def stream_ai_job_events(
    job_id: UUID,
    request: Request,
    after: Annotated[int, Query(ge=0)] = 0,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    cursor = after
    if last_event_id is not None:
        try:
            parsed_last_event_id = int(last_event_id)
        except ValueError as exc:
            raise AppError(
                "invalid_event_cursor",
                "事件续读位置无效，请重新加载消息。",
            ) from exc
        if parsed_last_event_id < 0:
            raise AppError(
                "invalid_event_cursor",
                "事件续读位置无效，请重新加载消息。",
            )
        cursor = max(cursor, parsed_last_event_id)

    async with authorized(request) as identity:
        job = await identity.session.scalar(
            select(AIJob).where(
                AIJob.id == job_id,
                AIJob.owner_id == identity.user.id,
            )
        )
        if job is None:
            raise AppError("ai_job_not_found", "未找到此 AI 作业。", status=404)
        owner_id = identity.user.id
        database = request.app.state.database

    async def event_stream():
        current_sequence = cursor
        stop_at = time.monotonic() + SSE_WINDOW_SECONDS
        while True:
            async with database.transaction() as session:
                current_job = await session.scalar(
                    select(AIJob).where(
                        AIJob.id == job_id,
                        AIJob.owner_id == owner_id,
                    )
                )
                if current_job is None:
                    return
                events = list(
                    await session.scalars(
                        select(AIJobEvent)
                        .where(
                            AIJobEvent.job_id == job_id,
                            AIJobEvent.owner_id == owner_id,
                            AIJobEvent.sequence > current_sequence,
                        )
                        .order_by(AIJobEvent.sequence)
                        .limit(100)
                    )
                )
                terminal = current_job.status in {
                    "succeeded",
                    "failed",
                    "timed_out",
                    "cancelled",
                }
            for event in events:
                current_sequence = event.sequence
                yield _sse_event(event)
            if terminal and not events:
                return
            if time.monotonic() >= stop_at:
                yield ": keep-alive\n\n"
                return
            if not events:
                await asyncio.sleep(SSE_POLL_SECONDS)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
