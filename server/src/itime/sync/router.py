from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query, Request

from itime.auth.session import authorized
from itime.sync.schemas import (
    OccurrenceStepCompletionInput,
    StepCompletionInput,
)
from itime.sync.service import (
    merge_task_occurrence_step_completion,
    merge_task_step_completion,
    read_change_page,
    sync_status,
)
from itime.sync.snapshot import build_snapshot, read_entity

router = APIRouter(prefix="/api/v1/sync", tags=["通用同步"])


@router.get("/snapshot")
async def snapshot(request: Request) -> dict:
    async with authorized(request) as identity:
        return await build_snapshot(
            identity.session,
            request.app.state.security,
            identity.user.id,
        )


@router.get("/entities/{entity_type}/{entity_id}")
async def entity(
    entity_type: Annotated[str, Path(max_length=40)],
    entity_id: UUID,
    request: Request,
) -> dict:
    async with authorized(request) as identity:
        return await read_entity(
            identity.session,
            identity.user.id,
            entity_type,
            entity_id,
        )


@router.get("/changes")
async def changes(
    request: Request,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> dict:
    async with authorized(request) as identity:
        return await read_change_page(
            identity.session,
            request.app.state.security,
            identity.user.id,
            cursor_token=cursor,
            limit=limit,
        )


@router.get("/status")
async def status(request: Request) -> dict:
    async with authorized(request) as identity:
        return await sync_status(
            identity.session,
            identity.user.id,
            retention_days=request.app.state.settings.sync_event_retention_days,
        )


@router.put("/tasks/{task_id}/steps/{step_id}/completion")
async def update_task_step_completion(
    task_id: UUID,
    step_id: UUID,
    data: StepCompletionInput,
    request: Request,
) -> dict:
    async with authorized(request, write=True) as identity:
        return await merge_task_step_completion(
            identity.session,
            identity.user.id,
            task_id,
            step_id,
            data,
        )


@router.put("/task-occurrences/steps/completion")
async def update_task_occurrence_step_completion(
    data: OccurrenceStepCompletionInput,
    request: Request,
) -> dict:
    async with authorized(request, write=True) as identity:
        return await merge_task_occurrence_step_completion(
            identity.session,
            identity.user.id,
            data,
        )
