from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request

from itime.auth.security import now
from itime.auth.session import authorized
from itime.review.memory_service import (
    create_memory,
    list_memories,
    memory_detail,
    memory_settings,
    memory_settings_result,
    restore_memory,
    trash_memory,
    update_memory,
    update_memory_settings,
)
from itime.review.schemas import (
    MemoryCreateInput,
    MemoryLifecycleInput,
    MemorySettingsUpdateInput,
    MemoryUpdateInput,
)

router = APIRouter(prefix="/api/v1", tags=["复盘与记忆"])


@router.get("/memory-settings")
async def get_memory_settings(request: Request) -> dict:
    async with authorized(request) as identity:
        settings = await memory_settings(identity.session, identity.user.id)
        return {"settings": memory_settings_result(settings)}


@router.put("/memory-settings")
async def save_memory_settings(
    data: MemorySettingsUpdateInput,
    request: Request,
) -> dict:
    current_time = now()
    async with authorized(request, write=True) as identity:
        return await update_memory_settings(
            identity.session,
            identity.user.id,
            data,
            current_time=current_time,
        )


@router.get("/memories")
async def get_memories(
    request: Request,
    lifecycle: Literal["active", "trashed"] = "active",
    before_time: datetime | None = None,
    before_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> dict:
    async with authorized(request) as identity:
        return await list_memories(
            identity.session,
            identity.user.id,
            lifecycle=lifecycle,
            before_time=before_time,
            before_id=before_id,
            limit=limit,
        )


@router.post("/memories", status_code=201)
async def create_manual_memory(
    data: MemoryCreateInput,
    request: Request,
) -> dict:
    current_time = now()
    async with authorized(request, write=True) as identity:
        return await create_memory(
            identity.session,
            identity.user.id,
            data,
            current_time=current_time,
        )


@router.get("/memories/{memory_id}")
async def get_memory(memory_id: UUID, request: Request) -> dict:
    async with authorized(request) as identity:
        return {
            "memory": await memory_detail(
                identity.session,
                identity.user.id,
                memory_id,
            )
        }


@router.put("/memories/{memory_id}")
async def edit_memory(
    memory_id: UUID,
    data: MemoryUpdateInput,
    request: Request,
) -> dict:
    current_time = now()
    async with authorized(request, write=True) as identity:
        return await update_memory(
            identity.session,
            identity.user.id,
            memory_id,
            data,
            current_time=current_time,
        )


@router.delete("/memories/{memory_id}")
async def delete_memory(
    memory_id: UUID,
    data: MemoryLifecycleInput,
    request: Request,
) -> dict:
    current_time = now()
    async with authorized(request, write=True) as identity:
        return await trash_memory(
            identity.session,
            identity.user.id,
            memory_id,
            data,
            current_time=current_time,
        )


@router.post("/memories/{memory_id}/restore")
async def recover_memory(
    memory_id: UUID,
    data: MemoryLifecycleInput,
    request: Request,
) -> dict:
    current_time = now()
    async with authorized(request, write=True) as identity:
        return await restore_memory(
            identity.session,
            identity.user.id,
            memory_id,
            data,
            current_time=current_time,
        )
