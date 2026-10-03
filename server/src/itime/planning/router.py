from datetime import date, time
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from itime.auth.session import authorized
from itime.planning.schemas import (
    DEFAULT_AVAILABILITY_END,
    DEFAULT_AVAILABILITY_START,
    DEFAULT_BUFFER_MINUTES,
    PlanningContextQuery,
)
from itime.planning.snapshot_service import planning_context

router = APIRouter(prefix="/api/v1/planning", tags=["AI 规划"])


@router.get("/context")
async def get_planning_context(
    request: Request,
    scope: Literal["day", "week"],
    target_date: Annotated[date, Query(alias="date")],
    density: Literal["loose", "balanced", "tight"] = "balanced",
    buffer_minutes: Annotated[
        int,
        Query(alias="buffer", ge=0, le=60, multiple_of=5),
    ] = DEFAULT_BUFFER_MINUTES,
    availability_start: time = DEFAULT_AVAILABILITY_START,
    availability_end: time = DEFAULT_AVAILABILITY_END,
    task_id: Annotated[list[UUID] | None, Query()] = None,
) -> dict:
    query = _planning_query(
        scope=scope,
        target_date=target_date,
        density=density,
        buffer_minutes=buffer_minutes,
        availability_start=availability_start,
        availability_end=availability_end,
        task_ids=task_id or [],
        instruction="",
    )
    async with authorized(request) as identity:
        context = await planning_context(
            identity.session,
            identity.user.id,
            query,
        )
        return {"context": context}


def _planning_query(**values: object) -> PlanningContextQuery:
    try:
        return PlanningContextQuery.model_validate(values)
    except ValidationError as exc:
        aliases = {
            "target_date": "date",
            "buffer_minutes": "buffer",
            "task_ids": "task_id",
        }
        errors = []
        for error in exc.errors(include_url=False, include_input=False):
            location = error["loc"]
            field = aliases.get(str(location[-1]), str(location[-1]))
            errors.append({**error, "loc": ("query", field)})
        raise RequestValidationError(errors) from exc
