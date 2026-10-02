import hashlib
import json
from collections import defaultdict
from datetime import datetime
from math import ceil
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from itime.auth.security import now
from itime.errors import AppError
from itime.tasks.models import (
    ChangeEvent,
    Label,
    Project,
    Task,
    TaskLabel,
    TaskLink,
    TaskSchedule,
    TaskStep,
    UserOperation,
)
from itime.tasks.schedule_service import task_schedule_result


def validate_cursor(before_time: datetime | None, before_id: UUID | None) -> None:
    if (before_time is None) != (before_id is None) or (
        before_time is not None and before_time.tzinfo is None
    ):
        raise AppError("invalid_cursor", "列表游标无效，请重新加载。")


def normalized_label(name: str) -> str:
    return " ".join(name.split()).lower()


def request_hash(scope: str, data: BaseModel) -> str:
    payload = json.dumps(
        {"scope": scope, "body": data.model_dump(mode="json")},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


async def replay_operation(
    session: AsyncSession,
    owner_id: UUID,
    operation_id: UUID,
    scope: str,
    data: BaseModel,
) -> dict | None:
    operation = await session.get(UserOperation, (owner_id, operation_id))
    if operation is None:
        return None
    if operation.scope != scope or operation.request_hash != request_hash(scope, data):
        raise AppError(
            "operation_id_reused",
            "此操作编号已经用于不同的内容，请刷新状态后重新提交。",
            status=409,
        )
    if operation.response is None:
        raise AppError(
            "operation_result_expired",
            "此操作已经保存，但原响应已按数据生命周期清理，请刷新当前状态。",
            status=409,
            saved="saved",
        )
    return operation.response


def save_operation(
    session: AsyncSession,
    owner_id: UUID,
    operation_id: UUID,
    scope: str,
    data: BaseModel,
    response: dict,
) -> None:
    encoded = jsonable_encoder(
        response,
        custom_encoder={
            datetime: lambda value: value.isoformat().replace("+00:00", "Z")
        },
    )
    session.add(
        UserOperation(
            owner_id=owner_id,
            operation_id=operation_id,
            scope=scope,
            request_hash=request_hash(scope, data),
            response=encoded,
        )
    )


def emit_change(
    session: AsyncSession,
    owner_id: UUID,
    entity_type: str,
    entity_id: UUID,
    action: str,
    version: int,
) -> None:
    session.add(
        ChangeEvent(
            owner_id=owner_id,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            entity_version=version,
        )
    )


def project_result(
    project: Project,
    *,
    task_count: int | None = None,
    open_task_count: int | None = None,
) -> dict:
    result = {
        "id": project.id,
        "kind": project.kind,
        "name": project.name,
        "description": project.description,
        "version": project.version,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
    }
    if task_count is not None:
        result["task_count"] = task_count
        result["open_task_count"] = open_task_count or 0
    return result


def label_result(label: Label, *, task_count: int | None = None) -> dict:
    result = {
        "id": label.id,
        "name": label.name,
        "version": label.version,
    }
    if task_count is not None:
        result["task_count"] = task_count
    return result


async def require_project(
    session: AsyncSession, owner_id: UUID, project_id: UUID | None
) -> Project | None:
    if project_id is None:
        return None
    project = await session.scalar(
        select(Project).where(Project.id == project_id, Project.owner_id == owner_id)
    )
    if project is None:
        raise AppError("project_not_found", "未找到此项目或目标。", status=404)
    return project


async def locked_task(
    session: AsyncSession,
    owner_id: UUID,
    task_id: UUID,
    *,
    editable: bool = True,
) -> Task:
    task = await session.scalar(
        select(Task)
        .where(Task.id == task_id, Task.owner_id == owner_id)
        .with_for_update()
    )
    if task is None:
        raise AppError("task_not_found", "未找到此任务。", status=404)
    if editable:
        ensure_task_editable(task)
    return task


def task_lifecycle(task: Task) -> str:
    if task.purged_at is not None:
        return "purged"
    if task.deleted_at is not None:
        return "trashed"
    if task.archived_at is not None:
        return "archived"
    return "active"


def ensure_task_readable(task: Task) -> None:
    if task.purged_at is not None:
        raise AppError(
            "task_purged",
            "此任务已超过回收站恢复期并完成清理。",
            status=410,
            details={"lifecycle": "purged"},
        )


def ensure_task_editable(task: Task) -> None:
    ensure_task_readable(task)
    lifecycle = task_lifecycle(task)
    if lifecycle == "trashed":
        raise AppError(
            "task_in_trash",
            "此任务位于回收站，请先恢复后再修改。",
            status=409,
            details={"lifecycle": lifecycle},
        )
    if lifecycle == "archived":
        raise AppError(
            "task_archived",
            "此任务已归档，请先取消归档后再修改。",
            status=409,
            details={"lifecycle": lifecycle},
        )


def task_version_conflict(task: Task) -> AppError:
    return AppError(
        "task_version_conflict",
        "此任务已在其他页面或设备更新，请刷新后比较再保存。",
        status=409,
        details={"current_version": task.version},
    )


async def resolve_labels(
    session: AsyncSession,
    owner_id: UUID,
    names: list[str],
    *,
    create: bool,
) -> list[Label]:
    labels: list[Label] = []
    for name in names:
        normalized = normalized_label(name)
        label = await session.scalar(
            select(Label).where(
                Label.owner_id == owner_id,
                Label.normalized_name == normalized,
            )
        )
        if label is None and create:
            label = Label(owner_id=owner_id, name=name, normalized_name=normalized)
            session.add(label)
            await session.flush()
            emit_change(session, owner_id, "label", label.id, "created", label.version)
        if label is not None:
            labels.append(label)
    return labels


async def replace_task_labels(
    session: AsyncSession,
    task: Task,
    names: list[str],
) -> bool:
    labels = await resolve_labels(session, task.owner_id, names, create=True)
    desired = {label.id for label in labels}
    current = set(
        await session.scalars(select(TaskLabel.label_id).where(TaskLabel.task_id == task.id))
    )
    if current == desired:
        return False
    await session.execute(delete(TaskLabel).where(TaskLabel.task_id == task.id))
    session.add_all(
        TaskLabel(task_id=task.id, label_id=label_id, owner_id=task.owner_id)
        for label_id in desired
    )
    return True


async def replace_task_links(
    session: AsyncSession,
    task: Task,
    links: list,
) -> bool:
    current = list(
        await session.scalars(
            select(TaskLink).where(TaskLink.task_id == task.id).order_by(TaskLink.position)
        )
    )
    desired = [(link.title, str(link.url)) for link in links]
    existing = [(link.title, link.url) for link in current]
    if desired == existing:
        return False
    await session.execute(delete(TaskLink).where(TaskLink.task_id == task.id))
    session.add_all(
        TaskLink(
            task_id=task.id,
            owner_id=task.owner_id,
            title=link.title,
            url=str(link.url),
            position=position,
        )
        for position, link in enumerate(links)
    )
    return True


async def task_context(session: AsyncSession, tasks: list[Task]) -> dict:
    task_ids = [task.id for task in tasks]
    if not task_ids:
        return {
            "projects": {},
            "labels": defaultdict(list),
            "step_counts": {},
            "link_counts": {},
            "schedules": {},
        }
    project_ids = {task.project_id for task in tasks if task.project_id is not None}
    projects = (
        {
            project.id: project
            for project in await session.scalars(
                select(Project).where(Project.id.in_(project_ids))
            )
        }
        if project_ids
        else {}
    )
    labels: defaultdict[UUID, list[Label]] = defaultdict(list)
    rows = await session.execute(
        select(TaskLabel.task_id, Label)
        .join(
            Label,
            and_(
                Label.id == TaskLabel.label_id,
                Label.owner_id == TaskLabel.owner_id,
            ),
        )
        .where(TaskLabel.task_id.in_(task_ids))
        .order_by(Label.normalized_name, Label.id)
    )
    for task_id, label in rows:
        labels[task_id].append(label)
    step_rows = await session.execute(
        select(
            TaskStep.task_id,
            func.count(TaskStep.id),
            func.count(TaskStep.id).filter(TaskStep.completed.is_(True)),
        )
        .where(TaskStep.task_id.in_(task_ids))
        .group_by(TaskStep.task_id)
    )
    step_counts = {
        task_id: {"total": total, "completed": completed}
        for task_id, total, completed in step_rows
    }
    link_counts = {
        task_id: count
        for task_id, count in await session.execute(
            select(TaskLink.task_id, func.count(TaskLink.id))
            .where(TaskLink.task_id.in_(task_ids))
            .group_by(TaskLink.task_id)
        )
    }
    schedules = {
        schedule.task_id: schedule
        for schedule in await session.scalars(
            select(TaskSchedule).where(TaskSchedule.task_id.in_(task_ids))
        )
    }
    return {
        "projects": projects,
        "labels": labels,
        "step_counts": step_counts,
        "link_counts": link_counts,
        "schedules": schedules,
    }


def task_summary_result(task: Task, context: dict) -> dict:
    project = context["projects"].get(task.project_id)
    remaining_days = (
        max(0, ceil((task.purge_after - now()).total_seconds() / 86_400))
        if task.deleted_at is not None and task.purge_after is not None
        else None
    )
    return {
        "id": task.id,
        "title": task.title,
        "description": task.description,
        "project": (
            {"id": project.id, "kind": project.kind, "name": project.name}
            if project is not None
            else None
        ),
        "estimated_minutes": task.estimated_minutes,
        "estimate_source": task.estimate_source,
        "priority": task.priority,
        "status": task.status,
        "completed_at": task.completed_at,
        "lifecycle": task_lifecycle(task),
        "archived_at": task.archived_at,
        "deleted_at": task.deleted_at,
        "purge_after": task.purge_after,
        "purged_at": task.purged_at,
        "trash_remaining_days": remaining_days,
        "version": task.version,
        "labels": [label_result(label) for label in context["labels"][task.id]],
        "step_progress": context["step_counts"].get(task.id, {"total": 0, "completed": 0}),
        "link_count": context["link_counts"].get(task.id, 0),
        "schedule": task_schedule_result(context["schedules"].get(task.id)),
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }


async def task_summaries(session: AsyncSession, tasks: list[Task]) -> list[dict]:
    context = await task_context(session, tasks)
    return [task_summary_result(task, context) for task in tasks]


async def task_detail(session: AsyncSession, task: Task) -> dict:
    ensure_task_readable(task)
    summary = (await task_summaries(session, [task]))[0]
    steps = list(
        await session.scalars(
            select(TaskStep).where(TaskStep.task_id == task.id).order_by(TaskStep.position)
        )
    )
    links = list(
        await session.scalars(
            select(TaskLink).where(TaskLink.task_id == task.id).order_by(TaskLink.position)
        )
    )
    summary["steps"] = [
        {
            "id": step.id,
            "title": step.title,
            "position": step.position,
            "completed": step.completed,
            "completed_at": step.completed_at,
            "created_at": step.created_at,
            "updated_at": step.updated_at,
        }
        for step in steps
    ]
    summary["links"] = [
        {
            "id": link.id,
            "title": link.title,
            "url": link.url,
            "position": link.position,
        }
        for link in links
    ]
    return summary
