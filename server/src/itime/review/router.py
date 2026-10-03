from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response

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
    ReviewCreateInput,
    ReviewFactsUpdateInput,
)
from itime.review.service import (
    create_or_get_review,
    list_reviews,
    review_detail,
    update_review_facts,
)

router = APIRouter(prefix="/api/v1", tags=["复盘与记忆"])


@router.get("/reviews")
async def get_reviews(
    request: Request,
    before_date: date | None = None,
    before_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> dict:
    async with authorized(request) as identity:
        return await list_reviews(
            identity.session,
            identity.user.id,
            before_date=before_date,
            before_id=before_id,
            limit=limit,
        )


@router.post("/reviews", status_code=201)
async def create_review(
    data: ReviewCreateInput,
    request: Request,
    response: Response,
) -> dict:
    current_time = now()
    async with authorized(request, write=True) as identity:
        result = await create_or_get_review(
            identity.session,
            identity.user.id,
            data,
            current_time=current_time,
        )
        if not result["created"]:
            response.status_code = 200
        return result


@router.get("/reviews/{review_id}")
async def get_review(review_id: UUID, request: Request) -> dict:
    current_time = now()
    async with authorized(request) as identity:
        return await review_detail(
            identity.session,
            identity.user.id,
            review_id,
            current_time=current_time,
        )


@router.put("/reviews/{review_id}/facts")
async def save_review_facts(
    review_id: UUID,
    data: ReviewFactsUpdateInput,
    request: Request,
) -> dict:
    current_time = now()
    async with authorized(request, write=True) as identity:
        return await update_review_facts(
            identity.session,
            identity.user.id,
            review_id,
            data,
            current_time=current_time,
        )


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
