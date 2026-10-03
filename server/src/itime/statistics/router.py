from datetime import date
from typing import Literal

from fastapi import APIRouter, Request

from itime.auth.security import now
from itime.auth.session import authorized
from itime.statistics.service import read_statistics

router = APIRouter(prefix="/api/v1/statistics", tags=["个人统计"])


@router.get("")
async def get_statistics(
    request: Request,
    scope: Literal["day", "week", "month"],
    anchor_date: date,
) -> dict:
    current_time = now()
    async with authorized(request) as identity:
        return await read_statistics(
            identity.session,
            identity.user.id,
            scope=scope,
            anchor_date=anchor_date,
            current_time=current_time,
        )
