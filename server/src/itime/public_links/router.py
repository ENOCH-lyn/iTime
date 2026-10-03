from uuid import UUID

from fastapi import APIRouter, Request

from itime.auth.session import authorized
from itime.public_links.service import queue_public_link_refresh
from itime.tasks.schemas import TaskLinkRefreshInput
from itime.tasks.service import (
    locked_task,
    replay_operation,
    save_operation,
    task_detail,
)

router = APIRouter(prefix="/api/v1", tags=["任务公开链接"])


@router.post("/tasks/{task_id}/links/{link_id}/refresh")
async def refresh_task_link(
    task_id: UUID,
    link_id: UUID,
    data: TaskLinkRefreshInput,
    request: Request,
) -> dict:
    scope = f"tasks.links.refresh:{task_id}:{link_id}"
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
        task = await locked_task(
            identity.session,
            identity.user.id,
            task_id,
        )
        await queue_public_link_refresh(
            identity.session,
            identity.user.id,
            task.id,
            link_id,
        )
        await identity.session.flush()
        result = {
            "operation_id": data.operation_id,
            "task": await task_detail(identity.session, task),
        }
        save_operation(
            identity.session,
            identity.user.id,
            data.operation_id,
            scope,
            data,
            result,
        )
        return result
