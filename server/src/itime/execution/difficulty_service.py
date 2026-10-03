from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from sqlalchemy import Text, and_, cast, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from itime.calendar.models import UserTimeSettings
from itime.database import Database
from itime.errors import AppError
from itime.execution.models import DifficultyRecord, FocusSession
from itime.execution.schemas import (
    DifficultyCreateInput,
    DifficultyLifecycleInput,
    DifficultyUpdateInput,
)
from itime.tasks.models import UserOperation
from itime.tasks.service import (
    emit_change,
    replay_operation,
    save_operation,
    validate_cursor,
)

DIFFICULTY_RETENTION = timedelta(days=30)
FUTURE_TOLERANCE = timedelta(minutes=5)
MAX_DIFFICULTY_RANGE = timedelta(days=93)
PURGE_BATCH_SIZE = 100


def difficulty_lifecycle(record: DifficultyRecord) -> str:
    if record.purged_at is not None:
        return "purged"
    if record.deleted_at is not None:
        return "trashed"
    return "active"


def difficulty_result(record: DifficultyRecord) -> dict:
    return {
        "id": record.id,
        "task_id": record.task_id,
        "focus_session_id": record.focus_session_id,
        "occurrence_key": record.occurrence_key,
        "task_title": record.task_title_snapshot,
        "content": record.content,
        "category": record.category,
        "emotion": record.emotion,
        "occurred_at": record.occurred_at,
        "time_zone": record.time_zone,
        "lifecycle": difficulty_lifecycle(record),
        "deleted_at": record.deleted_at,
        "purge_after": record.purge_after,
        "purged_at": record.purged_at,
        "version": record.version,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


async def _owned_difficulty(
    session: AsyncSession,
    owner_id: UUID,
    difficulty_id: UUID,
    *,
    lock: bool = False,
) -> DifficultyRecord:
    statement = select(DifficultyRecord).where(
        DifficultyRecord.id == difficulty_id,
        DifficultyRecord.owner_id == owner_id,
    )
    if lock:
        statement = statement.with_for_update()
    record = await session.scalar(statement)
    if record is None:
        raise AppError(
            "difficulty_not_found",
            "未找到此困难记录。",
            status=404,
        )
    if record.purged_at is not None:
        raise AppError(
            "difficulty_purged",
            "此困难记录已超过回收站恢复期并完成清理。",
            status=410,
        )
    return record


async def _resolve_links(
    session: AsyncSession,
    owner_id: UUID,
    *,
    task_id: UUID | None,
    focus_session_id: UUID | None,
    occurrence_key: str | None,
) -> tuple[UUID | None, UUID | None, str | None, str | None, str]:
    focus = None
    if focus_session_id is not None:
        focus = await session.scalar(
            select(FocusSession).where(
                FocusSession.id == focus_session_id,
                FocusSession.owner_id == owner_id,
            )
        )
        if focus is None:
            raise AppError(
                "focus_session_not_found",
                "未找到此专注记录。",
                status=404,
            )
        if task_id is not None and task_id != focus.task_id:
            raise AppError(
                "difficulty_focus_task_mismatch",
                "所选任务与专注记录不一致。",
                status=409,
            )
        if occurrence_key is not None and occurrence_key != focus.occurrence_key:
            raise AppError(
                "difficulty_focus_task_mismatch",
                "所选任务实例与专注记录不一致。",
                status=409,
            )
        task_id = focus.task_id
        occurrence_key = focus.occurrence_key
    from itime.execution.service import _resolve_historical_task

    task = await _resolve_historical_task(
        session,
        owner_id,
        task_id,
        occurrence_key,
    )
    if focus is not None:
        time_zone = focus.time_zone
    else:
        time_zone = await session.scalar(
            select(UserTimeSettings.time_zone).where(
                UserTimeSettings.owner_id == owner_id
            )
        )
        if time_zone is None:
            raise AppError(
                "time_settings_required",
                "请先保存当前使用的时区，再记录困难。",
                status=409,
            )
    return (
        task.id if task is not None else None,
        focus.id if focus is not None else None,
        occurrence_key,
        task.title if task is not None else None,
        time_zone,
    )


def _occurred_at(value: datetime | None, current_time: datetime) -> datetime:
    if value is None:
        return current_time
    occurred_at = value.astimezone(UTC)
    if occurred_at > current_time + FUTURE_TOLERANCE:
        raise AppError(
            "invalid_difficulty_time",
            "困难发生时间晚于服务端时间过多，请核对后重试。",
            status=422,
        )
    return occurred_at


async def create_difficulty(
    session: AsyncSession,
    owner_id: UUID,
    data: DifficultyCreateInput,
    *,
    current_time: datetime,
) -> dict:
    scope = "difficulty.create"
    replay = await replay_operation(
        session,
        owner_id,
        data.operation_id,
        scope,
        data,
    )
    if replay is not None:
        return replay
    task_id, focus_id, occurrence_key, task_title, time_zone = await _resolve_links(
        session,
        owner_id,
        task_id=data.task_id,
        focus_session_id=data.focus_session_id,
        occurrence_key=data.occurrence_key,
    )
    record = DifficultyRecord(
        owner_id=owner_id,
        task_id=task_id,
        focus_session_id=focus_id,
        occurrence_key=occurrence_key,
        task_title_snapshot=task_title,
        content=data.content,
        category=data.category,
        emotion=data.emotion,
        occurred_at=_occurred_at(data.occurred_at, current_time),
        time_zone=time_zone,
        deleted_at=None,
        purge_after=None,
        purged_at=None,
    )
    session.add(record)
    await session.flush()
    emit_change(
        session,
        owner_id,
        "difficulty",
        record.id,
        "created",
        record.version,
    )
    result = {
        "operation_id": data.operation_id,
        "difficulty": difficulty_result(record),
    }
    save_operation(
        session,
        owner_id,
        data.operation_id,
        scope,
        data,
        result,
    )
    return result


async def update_difficulty(
    session: AsyncSession,
    owner_id: UUID,
    difficulty_id: UUID,
    data: DifficultyUpdateInput,
    *,
    current_time: datetime,
) -> dict:
    scope = f"difficulty.update:{difficulty_id}"
    replay = await replay_operation(
        session,
        owner_id,
        data.operation_id,
        scope,
        data,
    )
    if replay is not None:
        return replay
    record = await _owned_difficulty(
        session,
        owner_id,
        difficulty_id,
        lock=True,
    )
    if record.deleted_at is not None:
        raise AppError(
            "difficulty_in_trash",
            "此困难记录位于回收站，请先恢复后再修改。",
            status=409,
        )
    if record.version != data.expected_version:
        raise AppError(
            "difficulty_version_conflict",
            "此困难记录已在其他页面或设备更新，请刷新后比较再保存。",
            status=409,
            details={
                "current_difficulty": jsonable_encoder(
                    difficulty_result(record)
                )
            },
        )
    task_id, focus_id, occurrence_key, task_title, time_zone = await _resolve_links(
        session,
        owner_id,
        task_id=data.task_id,
        focus_session_id=data.focus_session_id,
        occurrence_key=data.occurrence_key,
    )
    record.task_id = task_id
    record.focus_session_id = focus_id
    record.occurrence_key = occurrence_key
    record.task_title_snapshot = task_title
    record.content = data.content
    record.category = data.category
    record.emotion = data.emotion
    if data.occurred_at is not None:
        record.occurred_at = _occurred_at(data.occurred_at, current_time)
    if data.focus_session_id is not None:
        record.time_zone = time_zone
    record.version += 1
    record.updated_at = current_time
    emit_change(
        session,
        owner_id,
        "difficulty",
        record.id,
        "updated",
        record.version,
    )
    await session.flush()
    result = {
        "operation_id": data.operation_id,
        "difficulty": difficulty_result(record),
    }
    save_operation(
        session,
        owner_id,
        data.operation_id,
        scope,
        data,
        result,
    )
    return result


async def trash_difficulty(
    session: AsyncSession,
    owner_id: UUID,
    difficulty_id: UUID,
    data: DifficultyLifecycleInput,
    *,
    current_time: datetime,
) -> dict:
    scope = f"difficulty.trash:{difficulty_id}"
    replay = await replay_operation(
        session,
        owner_id,
        data.operation_id,
        scope,
        data,
    )
    if replay is not None:
        return replay
    record = await _owned_difficulty(
        session,
        owner_id,
        difficulty_id,
        lock=True,
    )
    if record.version != data.expected_version:
        raise AppError(
            "difficulty_version_conflict",
            "此困难记录已在其他页面或设备更新，请刷新后比较再保存。",
            status=409,
            details={"current_version": record.version},
        )
    if record.deleted_at is None:
        record.deleted_at = current_time
        record.purge_after = current_time + DIFFICULTY_RETENTION
        record.version += 1
        record.updated_at = current_time
        emit_change(
            session,
            owner_id,
            "difficulty",
            record.id,
            "deleted",
            record.version,
        )
    await session.flush()
    result = {
        "operation_id": data.operation_id,
        "difficulty": difficulty_result(record),
    }
    save_operation(
        session,
        owner_id,
        data.operation_id,
        scope,
        data,
        result,
    )
    return result


async def restore_difficulty(
    session: AsyncSession,
    owner_id: UUID,
    difficulty_id: UUID,
    data: DifficultyLifecycleInput,
    *,
    current_time: datetime,
) -> dict:
    scope = f"difficulty.restore:{difficulty_id}"
    replay = await replay_operation(
        session,
        owner_id,
        data.operation_id,
        scope,
        data,
    )
    if replay is not None:
        return replay
    record = await _owned_difficulty(
        session,
        owner_id,
        difficulty_id,
        lock=True,
    )
    if record.version != data.expected_version:
        raise AppError(
            "difficulty_version_conflict",
            "此困难记录已在其他页面或设备更新，请刷新后比较再保存。",
            status=409,
            details={"current_version": record.version},
        )
    if record.deleted_at is None:
        raise AppError(
            "difficulty_not_in_trash",
            "此困难记录不在回收站。",
            status=409,
        )
    record.deleted_at = None
    record.purge_after = None
    record.version += 1
    record.updated_at = current_time
    emit_change(
        session,
        owner_id,
        "difficulty",
        record.id,
        "restored",
        record.version,
    )
    await session.flush()
    result = {
        "operation_id": data.operation_id,
        "difficulty": difficulty_result(record),
    }
    save_operation(
        session,
        owner_id,
        data.operation_id,
        scope,
        data,
        result,
    )
    return result


async def difficulty_detail(
    session: AsyncSession,
    owner_id: UUID,
    difficulty_id: UUID,
) -> dict:
    return difficulty_result(
        await _owned_difficulty(session, owner_id, difficulty_id)
    )


async def list_difficulties(
    session: AsyncSession,
    owner_id: UUID,
    *,
    scope: str,
    start: datetime | None,
    end: datetime | None,
    task_id: UUID | None,
    category: str | None,
    before_time: datetime | None,
    before_id: UUID | None,
    limit: int,
    current_time: datetime,
) -> dict:
    validate_cursor(before_time, before_id)
    if (start is None) != (end is None):
        raise AppError(
            "invalid_difficulty_range",
            "困难查询的开始和结束时间必须同时提供。",
        )
    if start is None or end is None:
        start = current_time - timedelta(days=30)
        end = current_time + FUTURE_TOLERANCE
    if start.tzinfo is None or end.tzinfo is None or end <= start:
        raise AppError(
            "invalid_difficulty_range",
            "困难查询时间范围无效。",
        )
    if end - start > MAX_DIFFICULTY_RANGE:
        raise AppError(
            "difficulty_range_too_large",
            "困难查询范围不能超过 93 天。",
        )
    statement = select(DifficultyRecord).where(
        DifficultyRecord.owner_id == owner_id,
        DifficultyRecord.purged_at.is_(None),
        DifficultyRecord.occurred_at >= start.astimezone(UTC),
        DifficultyRecord.occurred_at < end.astimezone(UTC),
    )
    if scope == "active":
        statement = statement.where(DifficultyRecord.deleted_at.is_(None))
    else:
        statement = statement.where(DifficultyRecord.deleted_at.is_not(None))
    if task_id is not None:
        statement = statement.where(DifficultyRecord.task_id == task_id)
    if category:
        statement = statement.where(DifficultyRecord.category == category)
    if before_time is not None and before_id is not None:
        statement = statement.where(
            or_(
                DifficultyRecord.occurred_at < before_time,
                and_(
                    DifficultyRecord.occurred_at == before_time,
                    DifficultyRecord.id < before_id,
                ),
            )
        )
    rows = list(
        await session.scalars(
            statement.order_by(
                DifficultyRecord.occurred_at.desc(),
                DifficultyRecord.id.desc(),
            ).limit(limit + 1)
        )
    )
    page = rows[:limit]
    return {
        "items": [difficulty_result(record) for record in page],
        "next_cursor": (
            {
                "before_time": page[-1].occurred_at,
                "before_id": page[-1].id,
            }
            if len(rows) > limit
            else None
        ),
    }


async def expire_difficulty_operation_responses(
    session: AsyncSession,
    owner_id: UUID,
    difficulty_id: UUID,
) -> int:
    reference = str(difficulty_id)
    result = await session.execute(
        update(UserOperation)
        .where(
            UserOperation.owner_id == owner_id,
            UserOperation.response.is_not(None),
            or_(
                UserOperation.scope.contains(reference, autoescape=True),
                cast(UserOperation.response, Text).contains(
                    reference,
                    autoescape=True,
                ),
            ),
        )
        .values(response=None, response_purged_at=datetime.now(UTC))
    )
    return result.rowcount or 0


async def _purge_difficulty(
    session: AsyncSession,
    record: DifficultyRecord,
    *,
    current_time: datetime,
) -> None:
    await expire_difficulty_operation_responses(
        session,
        record.owner_id,
        record.id,
    )
    record.task_id = None
    record.focus_session_id = None
    record.occurrence_key = None
    record.task_title_snapshot = None
    record.content = None
    record.category = None
    record.emotion = None
    record.purged_at = current_time
    record.version += 1
    record.updated_at = current_time
    emit_change(
        session,
        record.owner_id,
        "difficulty",
        record.id,
        "purged",
        record.version,
    )


async def cleanup_expired_difficulties(
    database: Database,
    *,
    limit: int = PURGE_BATCH_SIZE,
) -> int:
    if limit < 1 or limit > PURGE_BATCH_SIZE:
        raise ValueError(f"limit must be between 1 and {PURGE_BATCH_SIZE}")
    current_time = datetime.now(UTC)
    async with database.transaction() as session:
        records = list(
            await session.scalars(
                select(DifficultyRecord)
                .where(
                    DifficultyRecord.deleted_at.is_not(None),
                    DifficultyRecord.purge_after <= current_time,
                    DifficultyRecord.purged_at.is_(None),
                )
                .order_by(DifficultyRecord.purge_after, DifficultyRecord.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        for record in records:
            await _purge_difficulty(
                session,
                record,
                current_time=current_time,
            )
        return len(records)
