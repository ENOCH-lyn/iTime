from typing import Annotated

from fastapi import APIRouter, Query, Request

from itime.auth.session import authorized
from itime.sync.service import read_change_page, sync_status

router = APIRouter(prefix="/api/v1/sync", tags=["通用同步"])


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
