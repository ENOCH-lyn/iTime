from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request
from sqlalchemy import and_, delete, func, or_, select

from itime.auth.security import now
from itime.auth.session import authorized
from itime.errors import AppError
from itime.tasks.models import Label, Project, Task, TaskLabel, TaskLink, TaskStep
from itime.tasks.schemas import (
    CompletionInput,
    ProjectCreate,
    ProjectUpdate,
    StepCreate,
    StepDelete,
    StepReorder,
    StepUpdate,
    TaskBulkInput,
    TaskCreate,
    TaskUpdate,
)
from itime.tasks.service import (
    emit_change,
    label_result,
    locked_task,
    normalized_label,
    project_result,
    replace_task_labels,
    replace_task_links,
    replay_operation,
    require_project,
    resolve_labels,
    save_operation,
    task_detail,
    task_summaries,
    task_version_conflict,
    validate_cursor,
)

router = APIRouter(prefix="/api/v1", tags=["任务与目标"])


@router.post("/projects", status_code=201)
async def create_project(data: ProjectCreate, request: Request) -> dict:
    scope = "projects.create"
    async with authorized(request, write=True) as identity:
        replay = await replay_operation(
            identity.session, identity.user.id, data.operation_id, scope, data
        )
        if replay is not None:
            return replay
        project = Project(
            owner_id=identity.user.id,
            kind=data.kind,
            name=data.name,
            description=data.description,
        )
        identity.session.add(project)
        await identity.session.flush()
        emit_change(
            identity.session,
            identity.user.id,
            "project",
            project.id,
            "created",
            project.version,
        )
        result = {"operation_id": data.operation_id, "project": project_result(project)}
        save_operation(
            identity.session,
            identity.user.id,
            data.operation_id,
            scope,
            data,
            result,
        )
        return result


@router.get("/projects")
async def list_projects(
    request: Request,
    search: Annotated[str | None, Query(max_length=100)] = None,
    kind: Literal["all", "project", "goal"] = "all",
    before_time: datetime | None = None,
    before_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict:
    validate_cursor(before_time, before_id)
    async with authorized(request) as identity:
        statement = select(Project).where(Project.owner_id == identity.user.id)
        if search and (term := search.strip()):
            statement = statement.where(
                or_(
                    Project.name.icontains(term, autoescape=True),
                    Project.description.icontains(term, autoescape=True),
                )
            )
        if kind != "all":
            statement = statement.where(Project.kind == kind)
        if before_time is not None:
            statement = statement.where(
                or_(
                    Project.updated_at < before_time,
                    and_(Project.updated_at == before_time, Project.id < before_id),
                )
            )
        rows = list(
            await identity.session.scalars(
                statement.order_by(Project.updated_at.desc(), Project.id.desc()).limit(limit + 1)
            )
        )
        page = rows[:limit]
        project_ids = [project.id for project in page]
        counts = (
            {
                project_id: (total, opened)
                for project_id, total, opened in await identity.session.execute(
                    select(
                        Task.project_id,
                        func.count(Task.id),
                        func.count(Task.id).filter(Task.status == "open"),
                    )
                    .where(
                        Task.owner_id == identity.user.id,
                        Task.project_id.in_(project_ids),
                    )
                    .group_by(Task.project_id)
                )
            }
            if project_ids
            else {}
        )
        return {
            "items": [
                project_result(
                    project,
                    task_count=counts.get(project.id, (0, 0))[0],
                    open_task_count=counts.get(project.id, (0, 0))[1],
                )
                for project in page
            ],
            "next_cursor": (
                {"before_time": page[-1].updated_at, "before_id": page[-1].id}
                if len(rows) > limit
                else None
            ),
        }


@router.get("/projects/{project_id}")
async def get_project(project_id: UUID, request: Request) -> dict:
    async with authorized(request) as identity:
        project = await identity.session.scalar(
            select(Project).where(
                Project.id == project_id,
                Project.owner_id == identity.user.id,
            )
        )
        if project is None:
            raise AppError("project_not_found", "未找到此项目或目标。", status=404)
        counts = (
            await identity.session.execute(
                select(
                    func.count(Task.id),
                    func.count(Task.id).filter(Task.status == "open"),
                ).where(
                    Task.owner_id == identity.user.id,
                    Task.project_id == project.id,
                )
            )
        ).one()
        return {
            "project": project_result(
                project,
                task_count=counts[0],
                open_task_count=counts[1],
            )
        }


@router.put("/projects/{project_id}")
async def update_project(
    project_id: UUID, data: ProjectUpdate, request: Request
) -> dict:
    scope = f"projects.update:{project_id}"
    async with authorized(request, write=True) as identity:
        replay = await replay_operation(
            identity.session, identity.user.id, data.operation_id, scope, data
        )
        if replay is not None:
            return replay
        project = await identity.session.scalar(
            select(Project)
            .where(Project.id == project_id, Project.owner_id == identity.user.id)
            .with_for_update()
        )
        if project is None:
            raise AppError("project_not_found", "未找到此项目或目标。", status=404)
        if project.version != data.expected_version:
            raise AppError(
                "project_version_conflict",
                "此项目或目标已在其他页面更新，请刷新后比较再保存。",
                status=409,
                details={"current_version": project.version},
            )
        changed = (
            project.kind != data.kind
            or project.name != data.name
            or project.description != data.description
        )
        if changed:
            project.kind = data.kind
            project.name = data.name
            project.description = data.description
            project.version += 1
            project.updated_at = now()
            emit_change(
                identity.session,
                identity.user.id,
                "project",
                project.id,
                "updated",
                project.version,
            )
        result = {"operation_id": data.operation_id, "project": project_result(project)}
        save_operation(
            identity.session,
            identity.user.id,
            data.operation_id,
            scope,
            data,
            result,
        )
        return result


@router.get("/labels")
async def list_labels(
    request: Request,
    search: Annotated[str | None, Query(max_length=40)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> dict:
    async with authorized(request) as identity:
        statement = select(Label).where(Label.owner_id == identity.user.id)
        if search and (term := search.strip()):
            statement = statement.where(Label.name.icontains(term, autoescape=True))
        labels = list(
            await identity.session.scalars(
                statement.order_by(Label.normalized_name, Label.id).limit(limit)
            )
        )
        ids = [label.id for label in labels]
        counts = (
            {
                label_id: count
                for label_id, count in await identity.session.execute(
                    select(TaskLabel.label_id, func.count(TaskLabel.task_id))
                    .where(TaskLabel.label_id.in_(ids))
                    .group_by(TaskLabel.label_id)
                )
            }
            if ids
            else {}
        )
        return {
            "items": [
                label_result(label, task_count=counts.get(label.id, 0))
                for label in labels
            ]
        }


@router.post("/tasks", status_code=201)
async def create_task(data: TaskCreate, request: Request) -> dict:
    scope = "tasks.create"
    async with authorized(request, write=True) as identity:
        replay = await replay_operation(
            identity.session, identity.user.id, data.operation_id, scope, data
        )
        if replay is not None:
            return replay
        await require_project(identity.session, identity.user.id, data.project_id)
        task = Task(
            owner_id=identity.user.id,
            project_id=data.project_id,
            title=data.title,
            description=data.description,
            estimated_minutes=data.estimated_minutes,
            estimate_source="user" if data.estimated_minutes is not None else None,
            priority=data.priority,
        )
        identity.session.add(task)
        await identity.session.flush()
        identity.session.add_all(
            TaskStep(
                task_id=task.id,
                owner_id=identity.user.id,
                title=step.title,
                position=position,
            )
            for position, step in enumerate(data.steps)
        )
        identity.session.add_all(
            TaskLink(
                task_id=task.id,
                owner_id=identity.user.id,
                title=link.title,
                url=str(link.url),
                position=position,
            )
            for position, link in enumerate(data.links)
        )
        await replace_task_labels(identity.session, task, data.labels)
        await identity.session.flush()
        emit_change(
            identity.session,
            identity.user.id,
            "task",
            task.id,
            "created",
            task.version,
        )
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


@router.get("/tasks")
async def list_tasks(
    request: Request,
    search: Annotated[str | None, Query(max_length=100)] = None,
    status: Literal["all", "open", "completed"] = "all",
    project_id: UUID | None = None,
    location: Literal["all", "inbox"] = "all",
    priority: Annotated[int | None, Query(ge=0, le=3)] = None,
    label: Annotated[str | None, Query(max_length=40)] = None,
    before_time: datetime | None = None,
    before_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict:
    validate_cursor(before_time, before_id)
    if project_id is not None and location == "inbox":
        raise AppError("invalid_filter", "项目筛选与收集箱筛选不能同时使用。")
    async with authorized(request) as identity:
        if project_id is not None:
            await require_project(identity.session, identity.user.id, project_id)
        statement = select(Task).where(Task.owner_id == identity.user.id)
        if search and (term := search.strip()):
            statement = statement.where(
                or_(
                    Task.title.icontains(term, autoescape=True),
                    Task.description.icontains(term, autoescape=True),
                )
            )
        if status != "all":
            statement = statement.where(Task.status == status)
        if project_id is not None:
            statement = statement.where(Task.project_id == project_id)
        elif location == "inbox":
            statement = statement.where(Task.project_id.is_(None))
        if priority is not None:
            statement = statement.where(Task.priority == priority)
        if label and (label_name := normalized_label(label)):
            label_task_ids = (
                select(TaskLabel.task_id)
                .join(
                    Label,
                    and_(
                        Label.id == TaskLabel.label_id,
                        Label.owner_id == TaskLabel.owner_id,
                    ),
                )
                .where(
                    TaskLabel.owner_id == identity.user.id,
                    Label.normalized_name == label_name,
                )
            )
            statement = statement.where(Task.id.in_(label_task_ids))
        if before_time is not None:
            statement = statement.where(
                or_(
                    Task.updated_at < before_time,
                    and_(Task.updated_at == before_time, Task.id < before_id),
                )
            )
        rows = list(
            await identity.session.scalars(
                statement.order_by(Task.updated_at.desc(), Task.id.desc()).limit(limit + 1)
            )
        )
        page = rows[:limit]
        return {
            "items": await task_summaries(identity.session, page),
            "next_cursor": (
                {"before_time": page[-1].updated_at, "before_id": page[-1].id}
                if len(rows) > limit
                else None
            ),
        }


@router.get("/tasks/{task_id}")
async def get_task(task_id: UUID, request: Request) -> dict:
    async with authorized(request) as identity:
        task = await identity.session.scalar(
            select(Task).where(Task.id == task_id, Task.owner_id == identity.user.id)
        )
        if task is None:
            raise AppError("task_not_found", "未找到此任务。", status=404)
        return {"task": await task_detail(identity.session, task)}


@router.put("/tasks/{task_id}")
async def update_task(task_id: UUID, data: TaskUpdate, request: Request) -> dict:
    scope = f"tasks.update:{task_id}"
    async with authorized(request, write=True) as identity:
        replay = await replay_operation(
            identity.session, identity.user.id, data.operation_id, scope, data
        )
        if replay is not None:
            return replay
        task = await locked_task(identity.session, identity.user.id, task_id)
        if task.version != data.expected_version:
            raise task_version_conflict(task)
        await require_project(identity.session, identity.user.id, data.project_id)
        changed = (
            task.title != data.title
            or task.description != data.description
            or task.project_id != data.project_id
            or task.estimated_minutes != data.estimated_minutes
            or task.priority != data.priority
        )
        task.title = data.title
        task.description = data.description
        task.project_id = data.project_id
        task.estimated_minutes = data.estimated_minutes
        task.estimate_source = "user" if data.estimated_minutes is not None else None
        task.priority = data.priority
        changed = await replace_task_labels(identity.session, task, data.labels) or changed
        changed = await replace_task_links(identity.session, task, data.links) or changed
        if changed:
            task.version += 1
            task.updated_at = now()
            emit_change(
                identity.session,
                identity.user.id,
                "task",
                task.id,
                "updated",
                task.version,
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


@router.put("/tasks/{task_id}/completion")
async def set_task_completion(
    task_id: UUID, data: CompletionInput, request: Request
) -> dict:
    scope = f"tasks.completion:{task_id}"
    async with authorized(request, write=True) as identity:
        replay = await replay_operation(
            identity.session, identity.user.id, data.operation_id, scope, data
        )
        if replay is not None:
            return replay
        task = await locked_task(identity.session, identity.user.id, task_id)
        if task.version != data.expected_version:
            raise task_version_conflict(task)
        desired = "completed" if data.completed else "open"
        if task.status != desired:
            task.status = desired
            task.completed_at = now() if data.completed else None
            task.version += 1
            task.updated_at = now()
            emit_change(
                identity.session,
                identity.user.id,
                "task",
                task.id,
                "completed" if data.completed else "reopened",
                task.version,
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


@router.post("/tasks/{task_id}/steps", status_code=201)
async def create_step(task_id: UUID, data: StepCreate, request: Request) -> dict:
    scope = f"tasks.steps.create:{task_id}"
    async with authorized(request, write=True) as identity:
        replay = await replay_operation(
            identity.session, identity.user.id, data.operation_id, scope, data
        )
        if replay is not None:
            return replay
        task = await locked_task(identity.session, identity.user.id, task_id)
        if task.version != data.expected_task_version:
            raise task_version_conflict(task)
        last_position = await identity.session.scalar(
            select(func.max(TaskStep.position)).where(TaskStep.task_id == task.id)
        )
        next_position = 0 if last_position is None else last_position + 1
        step = TaskStep(
            task_id=task.id,
            owner_id=identity.user.id,
            title=data.title,
            position=next_position,
        )
        identity.session.add(step)
        task.version += 1
        task.updated_at = now()
        await identity.session.flush()
        emit_change(
            identity.session,
            identity.user.id,
            "step",
            step.id,
            "created",
            task.version,
        )
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


@router.put("/tasks/{task_id}/steps/{step_id}")
async def update_step(
    task_id: UUID,
    step_id: UUID,
    data: StepUpdate,
    request: Request,
) -> dict:
    scope = f"tasks.steps.update:{task_id}:{step_id}"
    async with authorized(request, write=True) as identity:
        replay = await replay_operation(
            identity.session, identity.user.id, data.operation_id, scope, data
        )
        if replay is not None:
            return replay
        task = await locked_task(identity.session, identity.user.id, task_id)
        if task.version != data.expected_task_version:
            raise task_version_conflict(task)
        step = await identity.session.scalar(
            select(TaskStep)
            .where(
                TaskStep.id == step_id,
                TaskStep.task_id == task.id,
                TaskStep.owner_id == identity.user.id,
            )
            .with_for_update()
        )
        if step is None:
            raise AppError("step_not_found", "未找到此步骤。", status=404)
        changed = step.title != data.title or step.completed != data.completed
        if changed:
            step.title = data.title
            step.completed = data.completed
            step.completed_at = now() if data.completed else None
            step.updated_at = now()
            task.version += 1
            task.updated_at = now()
            emit_change(
                identity.session,
                identity.user.id,
                "step",
                step.id,
                "completed" if data.completed else "updated",
                task.version,
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


@router.delete("/tasks/{task_id}/steps/{step_id}")
async def delete_step(
    task_id: UUID,
    step_id: UUID,
    data: StepDelete,
    request: Request,
) -> dict:
    scope = f"tasks.steps.delete:{task_id}:{step_id}"
    async with authorized(request, write=True) as identity:
        replay = await replay_operation(
            identity.session, identity.user.id, data.operation_id, scope, data
        )
        if replay is not None:
            return replay
        task = await locked_task(identity.session, identity.user.id, task_id)
        if task.version != data.expected_task_version:
            raise task_version_conflict(task)
        steps = list(
            await identity.session.scalars(
                select(TaskStep)
                .where(TaskStep.task_id == task.id)
                .order_by(TaskStep.position)
                .with_for_update()
            )
        )
        step = next((candidate for candidate in steps if candidate.id == step_id), None)
        if step is None:
            raise AppError("step_not_found", "未找到此步骤。", status=404)
        await identity.session.delete(step)
        for position, remaining in enumerate(candidate for candidate in steps if candidate != step):
            remaining.position = position
            remaining.updated_at = now()
        task.version += 1
        task.updated_at = now()
        await identity.session.flush()
        emit_change(
            identity.session,
            identity.user.id,
            "step",
            step_id,
            "deleted",
            task.version,
        )
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


@router.put("/tasks/{task_id}/steps")
async def reorder_steps(
    task_id: UUID, data: StepReorder, request: Request
) -> dict:
    scope = f"tasks.steps.reorder:{task_id}"
    async with authorized(request, write=True) as identity:
        replay = await replay_operation(
            identity.session, identity.user.id, data.operation_id, scope, data
        )
        if replay is not None:
            return replay
        task = await locked_task(identity.session, identity.user.id, task_id)
        if task.version != data.expected_task_version:
            raise task_version_conflict(task)
        steps = list(
            await identity.session.scalars(
                select(TaskStep)
                .where(TaskStep.task_id == task.id)
                .order_by(TaskStep.position)
                .with_for_update()
            )
        )
        if set(data.step_ids) != {step.id for step in steps}:
            raise AppError(
                "invalid_step_order",
                "步骤列表已变化，请刷新任务后重新排序。",
                status=409,
                details={"current_version": task.version},
            )
        by_id = {step.id: step for step in steps}
        changed = [step.id for step in steps] != data.step_ids
        if changed:
            changed_at = now()
            for position, step_id in enumerate(data.step_ids):
                by_id[step_id].position = position
                by_id[step_id].updated_at = changed_at
            task.version += 1
            task.updated_at = changed_at
            emit_change(
                identity.session,
                identity.user.id,
                "task",
                task.id,
                "updated",
                task.version,
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


@router.post("/tasks/actions/bulk")
async def bulk_update_tasks(data: TaskBulkInput, request: Request) -> dict:
    scope = "tasks.bulk"
    async with authorized(request, write=True) as identity:
        replay = await replay_operation(
            identity.session, identity.user.id, data.operation_id, scope, data
        )
        if replay is not None:
            return replay
        ids = sorted((item.id for item in data.items), key=str)
        tasks = list(
            await identity.session.scalars(
                select(Task)
                .where(Task.owner_id == identity.user.id, Task.id.in_(ids))
                .order_by(Task.id)
                .with_for_update()
            )
        )
        if len(tasks) != len(ids):
            raise AppError(
                "task_set_changed",
                "部分任务已不存在或不属于当前账号，请刷新列表后重试。",
                status=409,
            )
        expected = {item.id: item.expected_version for item in data.items}
        conflicts = [
            {"id": str(task.id), "current_version": task.version}
            for task in tasks
            if task.version != expected[task.id]
        ]
        if conflicts:
            raise AppError(
                "task_version_conflict",
                "部分任务已在其他页面或设备更新，本次批量操作未保存。",
                status=409,
                details={"conflicts": conflicts},
            )
        if data.action == "set_project":
            await require_project(identity.session, identity.user.id, data.project_id)
        labels = (
            await resolve_labels(
                identity.session,
                identity.user.id,
                data.labels,
                create=data.action == "add_labels",
            )
            if data.action in {"add_labels", "remove_labels"}
            else []
        )
        label_ids = {label.id for label in labels}
        changed_at = now()
        for task in tasks:
            changed = False
            action = "updated"
            if data.action in {"complete", "reopen"}:
                desired = "completed" if data.action == "complete" else "open"
                if task.status != desired:
                    task.status = desired
                    task.completed_at = changed_at if desired == "completed" else None
                    changed = True
                    action = "completed" if desired == "completed" else "reopened"
            elif data.action == "set_priority" and task.priority != data.priority:
                task.priority = data.priority
                changed = True
            elif data.action == "set_project" and task.project_id != data.project_id:
                task.project_id = data.project_id
                changed = True
            elif data.action in {"add_labels", "remove_labels"}:
                current_ids = set(
                    await identity.session.scalars(
                        select(TaskLabel.label_id).where(TaskLabel.task_id == task.id)
                    )
                )
                desired_ids = (
                    current_ids | label_ids
                    if data.action == "add_labels"
                    else current_ids - label_ids
                )
                if current_ids != desired_ids:
                    await identity.session.execute(
                        delete(TaskLabel).where(TaskLabel.task_id == task.id)
                    )
                    identity.session.add_all(
                        TaskLabel(
                            task_id=task.id,
                            label_id=label_id,
                            owner_id=identity.user.id,
                        )
                        for label_id in desired_ids
                    )
                    changed = True
            if changed:
                task.version += 1
                task.updated_at = changed_at
                emit_change(
                    identity.session,
                    identity.user.id,
                    "task",
                    task.id,
                    action,
                    task.version,
                )
        await identity.session.flush()
        result = {
            "operation_id": data.operation_id,
            "items": await task_summaries(identity.session, tasks),
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
