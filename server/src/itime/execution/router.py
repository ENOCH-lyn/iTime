from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request

from itime.auth.security import now
from itime.auth.session import authorized
from itime.execution.schemas import (
    FocusManualCreateInput,
    FocusProgressInput,
    FocusRevisionInput,
    FocusStartInput,
    FocusTaskCompletionInput,
    FocusTransitionInput,
)
from itime.execution.service import (
    complete_focus_task,
    create_manual_focus,
    current_focus,
    focus_detail,
    list_focus_sessions,
    revise_focus,
    start_focus,
    transition_focus,
    update_focus_progress,
)

router = APIRouter(prefix="/api/v1", tags=["执行与困难"])


@router.get("/focus-sessions/current")
async def get_current_focus(request: Request) -> dict:
    current_time = now()
    async with authorized(request) as identity:
        return {
            "focus": await current_focus(
                identity.session,
                identity.user.id,
                current_time=current_time,
            ),
            "server_now": current_time,
        }


@router.get("/focus-sessions")
async def get_focus_sessions(
    request: Request,
    start: datetime,
    end: datetime,
    status: Literal["running", "paused", "finished", "abandoned"] | None = None,
    before_time: datetime | None = None,
    before_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> dict:
    current_time = now()
    async with authorized(request) as identity:
        return await list_focus_sessions(
            identity.session,
            identity.user.id,
            start=start,
            end=end,
            status=status,
            before_time=before_time,
            before_id=before_id,
            limit=limit,
            current_time=current_time,
        )


@router.post("/focus-sessions", status_code=201)
async def create_focus(data: FocusStartInput, request: Request) -> dict:
    current_time = now()
    async with authorized(request, write=True) as identity:
        return await start_focus(
            identity.session,
            identity.user.id,
            data,
            current_time=current_time,
        )


@router.post("/focus-sessions/manual", status_code=201)
async def create_manual(
    data: FocusManualCreateInput,
    request: Request,
) -> dict:
    current_time = now()
    async with authorized(request, write=True) as identity:
        return await create_manual_focus(
            identity.session,
            identity.user.id,
            data,
            current_time=current_time,
        )


@router.get("/focus-sessions/{focus_id}")
async def get_focus(focus_id: UUID, request: Request) -> dict:
    current_time = now()
    async with authorized(request) as identity:
        return await focus_detail(
            identity.session,
            identity.user.id,
            focus_id,
            current_time=current_time,
        )


@router.put("/focus-sessions/{focus_id}")
async def revise(
    focus_id: UUID,
    data: FocusRevisionInput,
    request: Request,
) -> dict:
    current_time = now()
    async with authorized(request, write=True) as identity:
        return await revise_focus(
            identity.session,
            identity.user.id,
            focus_id,
            data,
            current_time=current_time,
        )


@router.post("/focus-sessions/{focus_id}/transitions")
async def transition(
    focus_id: UUID,
    data: FocusTransitionInput,
    request: Request,
) -> dict:
    current_time = now()
    async with authorized(request, write=True) as identity:
        return await transition_focus(
            identity.session,
            identity.user.id,
            focus_id,
            data,
            current_time=current_time,
        )


@router.put("/focus-sessions/{focus_id}/progress")
async def update_progress(
    focus_id: UUID,
    data: FocusProgressInput,
    request: Request,
) -> dict:
    current_time = now()
    async with authorized(request, write=True) as identity:
        return await update_focus_progress(
            identity.session,
            identity.user.id,
            focus_id,
            data,
            current_time=current_time,
        )


@router.post("/focus-sessions/{focus_id}/complete-task")
async def complete_linked_task(
    focus_id: UUID,
    data: FocusTaskCompletionInput,
    request: Request,
) -> dict:
    current_time = now()
    async with authorized(request, write=True) as identity:
        return await complete_focus_task(
            identity.session,
            identity.user.id,
            focus_id,
            data,
            current_time=current_time,
        )
