from datetime import date, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from itime.auth.security import now
from itime.auth.session import Identity, authorized
from itime.calendar.recurrence import occurrence_dates
from itime.errors import AppError
from itime.tasks.models import Task, TaskOccurrenceState, TaskSchedule, TaskStep
from itime.tasks.schedule_service import (
    MAX_MAKEUP_OCCURRENCES,
    clone_task_template,
    find_task_occurrence_date,
    occurrence_state_result,
    project_task_occurrence,
    query_task_occurrences,
    resolve_effective_due,
    resolve_task_schedule,
    task_occurrence_cutoff,
    task_plan,
    task_schedule_result,
    task_schedule_values,
    validate_completed_steps,
)
from itime.tasks.schemas import (
    TaskMakeupCreate,
    TaskOccurrenceReschedule,
    TaskOccurrenceUpdate,
    TaskScheduleSplit,
    TaskScheduleUpdate,
)
from itime.tasks.service import (
    emit_change,
    locked_task,
    replay_operation,
    save_operation,
    task_detail,
    task_version_conflict,
)

router = APIRouter(prefix="/api/v1", tags=["任务日期"])


def schedule_warnings(occurrence_keys: tuple[str, ...]) -> list[dict]:
    return [
        {
            "code": "invalid_local_time",
            "occurrence_key": key,
            "message": "该次任务截止位于夏令时跳过的本地时间，未自动平移。",
        }
        for key in occurrence_keys
    ]


async def _locked_schedule(
    identity: Identity,
    task_id: UUID,
    expected_task_version: int,
):
    task = await locked_task(identity.session, identity.user.id, task_id)
    if task.version != expected_task_version:
        raise task_version_conflict(task)
    schedule = await identity.session.scalar(
        select(TaskSchedule)
        .where(
            TaskSchedule.task_id == task.id,
            TaskSchedule.owner_id == identity.user.id,
        )
        .with_for_update()
    )
    if schedule is None or schedule.due_kind == "none":
        raise AppError(
            "task_schedule_not_found",
            "此任务尚未设置截止时间和重复规则。",
            status=404,
        )
    return identity, task, schedule


async def _base_state(
    identity,
    task_id: UUID,
    occurrence_date: date,
) -> TaskOccurrenceState | None:
    return await identity.session.scalar(
        select(TaskOccurrenceState)
        .where(
            TaskOccurrenceState.task_id == task_id,
            TaskOccurrenceState.owner_id == identity.user.id,
            TaskOccurrenceState.kind == "base",
            TaskOccurrenceState.original_date == occurrence_date,
        )
        .with_for_update()
    )


async def _state_by_key(
    identity: Identity,
    task_id: UUID,
    occurrence_key: str,
) -> TaskOccurrenceState | None:
    return await identity.session.scalar(
        select(TaskOccurrenceState)
        .where(
            TaskOccurrenceState.task_id == task_id,
            TaskOccurrenceState.owner_id == identity.user.id,
            TaskOccurrenceState.occurrence_key == occurrence_key,
        )
        .with_for_update()
    )


def _check_occurrence_version(
    state: TaskOccurrenceState | None,
    expected_version: int,
) -> None:
    current_version = state.version if state is not None else 0
    if current_version != expected_version:
        raise AppError(
            "task_occurrence_version_conflict",
            "此任务实例已在其他页面或设备更新，请刷新后比较再保存。",
            status=409,
            details={"current_version": current_version},
        )


@router.get("/tasks/{task_id}/schedule")
async def get_task_schedule(task_id: UUID, request: Request) -> dict:
    async with authorized(request) as identity:
        task = await identity.session.scalar(
            select(Task).where(
                Task.id == task_id,
                Task.owner_id == identity.user.id,
            )
        )
        if task is None:
            raise AppError("task_not_found", "未找到此任务。", status=404)
        schedule = await identity.session.scalar(
            select(TaskSchedule).where(
                TaskSchedule.task_id == task_id,
                TaskSchedule.owner_id == identity.user.id,
            )
        )
        return {
            "task_version": task.version,
            "schedule": task_schedule_result(schedule),
        }


@router.put("/tasks/{task_id}/schedule")
async def update_task_schedule(
    task_id: UUID,
    data: TaskScheduleUpdate,
    request: Request,
) -> dict:
    scope = f"tasks.schedule:{task_id}"
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
        task = await locked_task(identity.session, identity.user.id, task_id)
        if task.version != data.expected_task_version:
            raise task_version_conflict(task)
        resolved = await resolve_task_schedule(
            identity.session,
            identity.user.id,
            task,
            data,
        )
        if resolved.plan is not None and resolved.plan.frequency != "none":
            template_step_completed = await identity.session.scalar(
                select(TaskStep.id).where(
                    TaskStep.task_id == task.id,
                    TaskStep.completed.is_(True),
                )
            )
            if template_step_completed is not None:
                raise AppError(
                    "task_progress_requires_reset",
                    "已有步骤进度的任务不能直接改为重复任务。",
                    status=409,
                )
        existing = await identity.session.scalar(
            select(TaskSchedule)
            .where(TaskSchedule.task_id == task.id)
            .with_for_update()
        )
        states = list(
            await identity.session.scalars(
                select(TaskOccurrenceState)
                .where(TaskOccurrenceState.task_id == task.id)
                .with_for_update()
            )
        )
        if states:
            if (
                existing is not None
                and existing.recurrence_frequency != "none"
                and resolved.plan is not None
                and resolved.plan.frequency == "none"
            ):
                raise AppError(
                    "task_occurrences_require_recurring_schedule",
                    "已有实例状态时不能把重复任务直接改为不重复任务。",
                    status=409,
                )
            if (
                existing is not None
                and existing.due_kind != resolved.due_kind
                and any(state.kind == "makeup" or state.rescheduled for state in states)
            ):
                raise AppError(
                    "task_occurrence_due_kind_conflict",
                    "已有调期或补做记录时不能切换截止类型。",
                    status=409,
                )
            if resolved.plan is None:
                raise AppError(
                    "task_occurrences_would_be_orphaned",
                    "已有任务实例状态时不能移除截止和重复规则。",
                    status=409,
                )
            valid_dates = set(occurrence_dates(resolved.plan))
            orphaned = [
                state
                for state in states
                if (
                    state.kind == "base"
                    and state.original_date not in valid_dates
                )
                or (
                    state.kind == "makeup"
                    and state.makeup_for_date not in valid_dates
                )
            ]
            if orphaned:
                raise AppError(
                    "task_occurrences_would_be_orphaned",
                    "新的重复规则会移除已有实例状态，请先处理这些记录。",
                    status=409,
                    details={
                        "occurrence_state_ids": [
                            str(state.id) for state in orphaned[:20]
                        ],
                        "has_more": len(orphaned) > 20,
                    },
                )
        values = task_schedule_values(data, resolved)
        clear_default = (
            resolved.due_kind == "none"
            and data.splittable
            and data.minimum_block_minutes == 15
        )
        changed = (
            existing is not None
            if clear_default
            else existing is None
            or any(
                getattr(existing, field) != value
                for field, value in values.items()
            )
        )
        if clear_default and existing is not None:
            await identity.session.delete(existing)
            existing = None
        elif existing is None and not clear_default:
            existing = TaskSchedule(
                task_id=task.id,
                owner_id=identity.user.id,
                predecessor_task_id=None,
                **values,
            )
            identity.session.add(existing)
        elif existing is not None and changed:
            for field, value in values.items():
                setattr(existing, field, value)
            existing.updated_at = now()
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
            "warnings": schedule_warnings(resolved.warning_occurrence_keys),
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


@router.get("/task-occurrences")
async def list_task_occurrences(
    request: Request,
    start: date,
    end: date,
) -> dict:
    async with authorized(request) as identity:
        items, time_zone = await query_task_occurrences(
            identity.session,
            identity.user.id,
            start,
            end,
        )
        return {
            "start": start,
            "end": end,
            "time_zone": time_zone,
            "items": items,
        }


@router.put("/tasks/{task_id}/occurrences/status")
async def update_task_occurrence_status(
    task_id: UUID,
    data: TaskOccurrenceUpdate,
    request: Request,
) -> dict:
    scope = f"task_occurrences.status:{task_id}:{data.occurrence_key}"
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
        _, task, schedule = await _locked_schedule(
            identity,
            task_id,
            data.expected_task_version,
        )
        if schedule.recurrence_frequency == "none":
            raise AppError(
                "task_not_recurring",
                "非重复任务请使用任务完成操作。",
            )
        state = await _state_by_key(identity, task.id, data.occurrence_key)
        original_date = (
            state.original_date
            if state is not None
            else await find_task_occurrence_date(
                identity.session,
                identity.user.id,
                schedule,
                data.occurrence_key,
            )
        )
        if state is None:
            state = await _base_state(identity, task.id, original_date)
        _check_occurrence_version(state, data.expected_occurrence_version)
        completed_step_ids = await validate_completed_steps(
            identity.session,
            task.id,
            data.completed_step_ids,
        )
        step_count = await identity.session.scalar(
            select(func.count(TaskStep.id)).where(TaskStep.task_id == task.id)
        )
        changed = (
            state is None
            or state.status != data.status
            or state.completed_step_ids != completed_step_ids
        )
        if state is None:
            state = TaskOccurrenceState(
                task_id=task.id,
                owner_id=identity.user.id,
                occurrence_key=data.occurrence_key,
                original_date=original_date,
                kind="base",
                effective_date=original_date,
                effective_local_time=None,
                effective_at=None,
                effective_time_zone=None,
                ambiguous_offset_minutes=None,
                rescheduled=False,
                status=data.status,
                completed_at=now() if data.status == "completed" else None,
                completed_step_ids=completed_step_ids,
                step_count_snapshot=step_count,
                makeup_for_key=None,
                makeup_for_date=None,
            )
            identity.session.add(state)
            await identity.session.flush()
        elif changed:
            if state.status != data.status:
                state.completed_at = (
                    now() if data.status == "completed" else None
                )
            state.status = data.status
            state.completed_step_ids = completed_step_ids
            state.version += 1
            state.updated_at = now()
        if changed:
            task.version += 1
            task.updated_at = now()
            emit_change(
                identity.session,
                identity.user.id,
                "task_occurrence",
                state.id,
                {
                    "completed": "completed",
                    "open": "reopened",
                    "skipped": "skipped",
                }[data.status],
                state.version,
            )
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
            "occurrence": project_task_occurrence(
                task,
                schedule,
                original_date,
                state,
                step_count=step_count,
            ),
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


@router.put("/tasks/{task_id}/occurrences/schedule")
async def reschedule_task_occurrence(
    task_id: UUID,
    data: TaskOccurrenceReschedule,
    request: Request,
) -> dict:
    scope = f"task_occurrences.reschedule:{task_id}:{data.occurrence_key}"
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
        _, task, schedule = await _locked_schedule(
            identity,
            task_id,
            data.expected_task_version,
        )
        if schedule.recurrence_frequency == "none":
            raise AppError(
                "task_not_recurring",
                "非重复任务请直接编辑任务截止时间。",
            )
        original_date = await find_task_occurrence_date(
            identity.session,
            identity.user.id,
            schedule,
            data.occurrence_key,
        )
        state = await _base_state(identity, task.id, original_date)
        _check_occurrence_version(state, data.expected_occurrence_version)
        effective_at, _ = resolve_effective_due(
            schedule,
            data.due_date,
            data.due_time,
            data.ambiguous_utc_offset_minutes,
        )
        step_count = await identity.session.scalar(
            select(func.count(TaskStep.id)).where(TaskStep.task_id == task.id)
        )
        if state is None:
            state = TaskOccurrenceState(
                task_id=task.id,
                owner_id=identity.user.id,
                occurrence_key=data.occurrence_key,
                original_date=original_date,
                kind="base",
                effective_date=data.due_date,
                effective_local_time=data.due_time,
                effective_at=effective_at,
                effective_time_zone=schedule.time_zone,
                ambiguous_offset_minutes=data.ambiguous_utc_offset_minutes,
                rescheduled=True,
                status="open",
                completed_at=None,
                completed_step_ids=[],
                step_count_snapshot=step_count,
                makeup_for_key=None,
                makeup_for_date=None,
            )
            identity.session.add(state)
        else:
            state.effective_date = data.due_date
            state.effective_local_time = data.due_time
            state.effective_at = effective_at
            state.effective_time_zone = schedule.time_zone
            state.ambiguous_offset_minutes = data.ambiguous_utc_offset_minutes
            state.rescheduled = True
            state.version += 1
            state.updated_at = now()
        task.version += 1
        task.updated_at = now()
        await identity.session.flush()
        emit_change(
            identity.session,
            identity.user.id,
            "task_occurrence",
            state.id,
            "rescheduled",
            state.version,
        )
        emit_change(
            identity.session,
            identity.user.id,
            "task",
            task.id,
            "updated",
            task.version,
        )
        result = {
            "operation_id": data.operation_id,
            "task": await task_detail(identity.session, task),
            "occurrence": occurrence_state_result(state),
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


@router.post("/tasks/{task_id}/occurrences/makeup", status_code=201)
async def create_task_makeup(
    task_id: UUID,
    data: TaskMakeupCreate,
    request: Request,
) -> dict:
    scope = f"task_occurrences.makeup:{task_id}"
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
        _, task, schedule = await _locked_schedule(
            identity,
            task_id,
            data.expected_task_version,
        )
        if schedule.recurrence_frequency == "none":
            raise AppError(
                "task_not_recurring",
                "只有重复任务可以创建关联补做。",
            )
        original_date = await find_task_occurrence_date(
            identity.session,
            identity.user.id,
            schedule,
            data.original_occurrence_key,
        )
        makeup_count = await identity.session.scalar(
            select(func.count(TaskOccurrenceState.id)).where(
                TaskOccurrenceState.task_id == task.id,
                TaskOccurrenceState.kind == "makeup",
            )
        )
        if makeup_count >= MAX_MAKEUP_OCCURRENCES:
            raise AppError(
                "too_many_task_makeups",
                "单个重复任务最多保留 366 个补做实例。",
            )
        effective_at, _ = resolve_effective_due(
            schedule,
            data.due_date,
            data.due_time,
            data.ambiguous_utc_offset_minutes,
        )
        step_count = await identity.session.scalar(
            select(func.count(TaskStep.id)).where(TaskStep.task_id == task.id)
        )
        state = TaskOccurrenceState(
            task_id=task.id,
            owner_id=identity.user.id,
            occurrence_key=f"makeup:{data.operation_id}",
            original_date=data.due_date,
            kind="makeup",
            effective_date=data.due_date,
            effective_local_time=data.due_time,
            effective_at=effective_at,
            effective_time_zone=schedule.time_zone,
            ambiguous_offset_minutes=data.ambiguous_utc_offset_minutes,
            rescheduled=False,
            status="open",
            completed_at=None,
            completed_step_ids=[],
            step_count_snapshot=step_count,
            makeup_for_key=data.original_occurrence_key,
            makeup_for_date=original_date,
        )
        identity.session.add(state)
        task.version += 1
        task.updated_at = now()
        await identity.session.flush()
        emit_change(
            identity.session,
            identity.user.id,
            "task_occurrence",
            state.id,
            "created",
            state.version,
        )
        emit_change(
            identity.session,
            identity.user.id,
            "task",
            task.id,
            "updated",
            task.version,
        )
        result = {
            "operation_id": data.operation_id,
            "task": await task_detail(identity.session, task),
            "occurrence": occurrence_state_result(state),
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


@router.post("/tasks/{task_id}/schedule/split", status_code=201)
async def split_task_schedule(
    task_id: UUID,
    data: TaskScheduleSplit,
    request: Request,
) -> dict:
    scope = f"tasks.schedule:{task_id}:split"
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
        _, task, schedule = await _locked_schedule(
            identity,
            task_id,
            data.expected_task_version,
        )
        if schedule.recurrence_frequency == "none":
            raise AppError(
                "task_not_recurring",
                "不重复任务不能按“本次及以后”拆分。",
            )
        existing_successor = await identity.session.scalar(
            select(TaskSchedule.task_id).where(
                TaskSchedule.owner_id == identity.user.id,
                TaskSchedule.predecessor_task_id == task.id,
            )
        )
        if existing_successor is not None:
            raise AppError(
                "task_series_already_split",
                "此任务系列已经拆分；请从当前有效的后继任务继续修改。",
                status=409,
                details={"successor_task_id": str(existing_successor)},
            )
        split_date = await find_task_occurrence_date(
            identity.session,
            identity.user.id,
            schedule,
            data.occurrence_key,
        )
        split_cutoff = task_occurrence_cutoff(schedule, split_date)
        current_time = now()
        historical = (
            split_date
            <= current_time.astimezone(ZoneInfo(schedule.time_zone)).date()
            if split_cutoff is None
            else split_cutoff <= current_time
        )
        if historical:
            raise AppError(
                "historical_task_occurrence_immutable",
                "已经到期或时间无效的任务实例不能作为未来系列分界点。",
                status=409,
            )
        old_dates = occurrence_dates(task_plan(schedule))
        split_index = old_dates.index(split_date)
        if split_index == 0:
            raise AppError(
                "split_at_first_occurrence",
                "从首个实例开始修改等同于修改整个任务重复规则。",
            )
        resolved = await resolve_task_schedule(
            identity.session,
            identity.user.id,
            task,
            data.replacement,
        )
        if resolved.plan is None or resolved.plan.start_date != split_date:
            raise AppError(
                "invalid_task_split_start",
                "拆分后的任务必须从所选实例日期开始。",
                details={"expected_start_date": split_date.isoformat()},
            )
        successor_dates = set(occurrence_dates(resolved.plan))
        states = list(
            await identity.session.scalars(
                select(TaskOccurrenceState)
                .where(TaskOccurrenceState.task_id == task.id)
                .order_by(TaskOccurrenceState.id)
                .with_for_update()
            )
        )
        future_states = [
            state
            for state in states
            if (
                state.kind == "base"
                and state.original_date >= split_date
            )
            or (
                state.kind == "makeup"
                and state.makeup_for_date >= split_date
            )
        ]
        orphaned = [
            state
            for state in future_states
            if (
                state.original_date
                if state.kind == "base"
                else state.makeup_for_date
            )
            not in successor_dates
        ]
        if orphaned:
            raise AppError(
                "task_occurrences_would_be_orphaned",
                "拆分后的重复规则不包含已有的未来实例状态。",
                status=409,
                details={
                    "occurrence_state_ids": [
                        str(state.id) for state in orphaned[:20]
                    ],
                    "has_more": len(orphaned) > 20,
                },
            )
        if (
            schedule.due_kind != resolved.due_kind
            and any(state.kind == "makeup" or state.rescheduled for state in future_states)
        ):
            raise AppError(
                "task_occurrence_due_kind_conflict",
                "未来已有调期或补做记录时不能切换截止类型。",
                status=409,
            )

        successor, step_map = await clone_task_template(identity.session, task)
        successor_schedule = TaskSchedule(
            task_id=successor.id,
            owner_id=identity.user.id,
            predecessor_task_id=task.id,
            **task_schedule_values(data.replacement, resolved),
        )
        identity.session.add(successor_schedule)
        if schedule.recurrence_count is not None:
            schedule.recurrence_count = split_index
        else:
            schedule.recurrence_until = split_date - timedelta(days=1)
        schedule.last_occurrence_date = old_dates[split_index - 1]
        schedule.updated_at = now()
        task.version += 1
        task.updated_at = now()
        await identity.session.flush()

        for state in future_states:
            state.task_id = successor.id
            state.completed_step_ids = [
                step_map[step_id] for step_id in state.completed_step_ids
            ]
            if state.kind == "base" and not state.rescheduled:
                state.effective_date = state.original_date
                state.effective_local_time = None
                state.effective_at = None
                state.effective_time_zone = None
                state.ambiguous_offset_minutes = None
            state.version += 1
            state.updated_at = now()
            emit_change(
                identity.session,
                identity.user.id,
                "task_occurrence",
                state.id,
                "updated",
                state.version,
            )
        emit_change(
            identity.session,
            identity.user.id,
            "task",
            task.id,
            "updated",
            task.version,
        )
        emit_change(
            identity.session,
            identity.user.id,
            "task",
            successor.id,
            "created",
            successor.version,
        )
        await identity.session.flush()
        result = {
            "operation_id": data.operation_id,
            "predecessor": await task_detail(identity.session, task),
            "successor": await task_detail(identity.session, successor),
            "moved_occurrence_state_count": len(future_states),
            "warnings": schedule_warnings(resolved.warning_occurrence_keys),
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
