from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import Text, and_, cast, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from itime.database import Database
from itime.errors import AppError
from itime.review.models import Memory, MemorySettings
from itime.review.schemas import (
    MemoryCreateInput,
    MemoryLifecycleInput,
    MemorySettingsUpdateInput,
    MemoryUpdateInput,
)
from itime.tasks.models import UserOperation
from itime.tasks.service import (
    emit_change,
    replay_operation,
    save_operation,
    validate_cursor,
)

MEMORY_RETENTION = timedelta(days=30)
PURGE_BATCH_SIZE = 100


def memory_lifecycle(memory: Memory) -> str:
    if memory.purged_at is not None:
        return "purged"
    if memory.deleted_at is not None:
        return "trashed"
    return "active"


def memory_result(memory: Memory) -> dict:
    return {
        "id": memory.id,
        "kind": memory.kind,
        "title": memory.title,
        "content": memory.content,
        "source_type": memory.source_type,
        "source_proposal_id": memory.source_proposal_id,
        "lifecycle": memory_lifecycle(memory),
        "deleted_at": memory.deleted_at,
        "purge_after": memory.purge_after,
        "purged_at": memory.purged_at,
        "version": memory.version,
        "created_at": memory.created_at,
        "updated_at": memory.updated_at,
    }


def memory_settings_result(settings: MemorySettings) -> dict:
    return {
        "enabled": settings.enabled,
        "version": settings.version,
        "created_at": settings.created_at,
        "updated_at": settings.updated_at,
    }


async def memory_settings(
    session: AsyncSession,
    owner_id: UUID,
    *,
    lock: bool = False,
) -> MemorySettings:
    statement = select(MemorySettings).where(MemorySettings.owner_id == owner_id)
    if lock:
        statement = statement.with_for_update()
    settings = await session.scalar(statement)
    if settings is None:
        raise AppError(
            "memory_settings_unavailable",
            "记忆设置暂时不可用，请保留请求编号并稍后重试。",
            status=503,
            retryable=True,
        )
    return settings


async def update_memory_settings(
    session: AsyncSession,
    owner_id: UUID,
    data: MemorySettingsUpdateInput,
    *,
    current_time: datetime,
) -> dict:
    scope = "memory_settings.update"
    replay = await replay_operation(
        session,
        owner_id,
        data.operation_id,
        scope,
        data,
    )
    if replay is not None:
        return replay
    settings = await memory_settings(session, owner_id, lock=True)
    if settings.version != data.expected_version:
        raise AppError(
            "memory_settings_version_conflict",
            "记忆设置已在其他页面或设备更新，请刷新后比较再保存。",
            status=409,
            details={"current_version": settings.version},
        )
    if settings.enabled != data.enabled:
        settings.enabled = data.enabled
        settings.version += 1
        settings.updated_at = current_time
        emit_change(
            session,
            owner_id,
            "memory_settings",
            owner_id,
            "updated",
            settings.version,
        )
    await session.flush()
    result = {
        "operation_id": data.operation_id,
        "settings": memory_settings_result(settings),
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


async def _owned_memory(
    session: AsyncSession,
    owner_id: UUID,
    memory_id: UUID,
    *,
    lock: bool = False,
    editable: bool = False,
) -> Memory:
    statement = select(Memory).where(
        Memory.id == memory_id,
        Memory.owner_id == owner_id,
    )
    if lock:
        statement = statement.with_for_update()
    memory = await session.scalar(statement)
    if memory is None:
        raise AppError("memory_not_found", "未找到此记忆。", status=404)
    if memory.purged_at is not None:
        raise AppError(
            "memory_purged",
            "此记忆已超过回收站恢复期并完成清理。",
            status=410,
        )
    if editable and memory.deleted_at is not None:
        raise AppError(
            "memory_in_trash",
            "此记忆在回收站中，请先恢复后再修改。",
            status=409,
        )
    return memory


def _version_conflict(memory: Memory) -> AppError:
    return AppError(
        "memory_version_conflict",
        "此记忆已在其他页面或设备更新，请刷新后比较再保存。",
        status=409,
        details={"current_version": memory.version},
    )


async def create_memory(
    session: AsyncSession,
    owner_id: UUID,
    data: MemoryCreateInput,
    *,
    current_time: datetime,
) -> dict:
    scope = "memory.create"
    replay = await replay_operation(
        session,
        owner_id,
        data.operation_id,
        scope,
        data,
    )
    if replay is not None:
        return replay
    memory = Memory(
        owner_id=owner_id,
        kind=data.kind,
        title=data.title,
        content=data.content,
        source_type="manual",
        source_proposal_id=None,
        deleted_at=None,
        purge_after=None,
        purged_at=None,
        version=1,
        created_at=current_time,
        updated_at=current_time,
    )
    session.add(memory)
    await session.flush()
    emit_change(
        session,
        owner_id,
        "memory",
        memory.id,
        "created",
        memory.version,
    )
    result = {
        "operation_id": data.operation_id,
        "memory": memory_result(memory),
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


async def update_memory(
    session: AsyncSession,
    owner_id: UUID,
    memory_id: UUID,
    data: MemoryUpdateInput,
    *,
    current_time: datetime,
) -> dict:
    scope = f"memory.update:{memory_id}"
    replay = await replay_operation(
        session,
        owner_id,
        data.operation_id,
        scope,
        data,
    )
    if replay is not None:
        return replay
    memory = await _owned_memory(
        session,
        owner_id,
        memory_id,
        lock=True,
        editable=True,
    )
    if memory.version != data.expected_version:
        raise _version_conflict(memory)
    changed = (
        memory.kind != data.kind
        or memory.title != data.title
        or memory.content != data.content
    )
    if changed:
        memory.kind = data.kind
        memory.title = data.title
        memory.content = data.content
        memory.version += 1
        memory.updated_at = current_time
        emit_change(
            session,
            owner_id,
            "memory",
            memory.id,
            "updated",
            memory.version,
        )
    await session.flush()
    result = {
        "operation_id": data.operation_id,
        "memory": memory_result(memory),
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


async def trash_memory(
    session: AsyncSession,
    owner_id: UUID,
    memory_id: UUID,
    data: MemoryLifecycleInput,
    *,
    current_time: datetime,
) -> dict:
    scope = f"memory.delete:{memory_id}"
    replay = await replay_operation(
        session,
        owner_id,
        data.operation_id,
        scope,
        data,
    )
    if replay is not None:
        return replay
    memory = await _owned_memory(session, owner_id, memory_id, lock=True)
    if memory.version != data.expected_version:
        raise _version_conflict(memory)
    if memory.deleted_at is not None:
        raise AppError(
            "memory_already_in_trash",
            "此记忆已经在回收站中。",
            status=409,
        )
    memory.deleted_at = current_time
    memory.purge_after = current_time + MEMORY_RETENTION
    memory.version += 1
    memory.updated_at = current_time
    emit_change(
        session,
        owner_id,
        "memory",
        memory.id,
        "deleted",
        memory.version,
    )
    await session.flush()
    result = {
        "operation_id": data.operation_id,
        "memory": memory_result(memory),
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


async def restore_memory(
    session: AsyncSession,
    owner_id: UUID,
    memory_id: UUID,
    data: MemoryLifecycleInput,
    *,
    current_time: datetime,
) -> dict:
    scope = f"memory.restore:{memory_id}"
    replay = await replay_operation(
        session,
        owner_id,
        data.operation_id,
        scope,
        data,
    )
    if replay is not None:
        return replay
    memory = await _owned_memory(session, owner_id, memory_id, lock=True)
    if memory.version != data.expected_version:
        raise _version_conflict(memory)
    if memory.deleted_at is None:
        raise AppError(
            "memory_not_in_trash",
            "此记忆不在回收站。",
            status=409,
        )
    memory.deleted_at = None
    memory.purge_after = None
    memory.version += 1
    memory.updated_at = current_time
    emit_change(
        session,
        owner_id,
        "memory",
        memory.id,
        "restored",
        memory.version,
    )
    await session.flush()
    result = {
        "operation_id": data.operation_id,
        "memory": memory_result(memory),
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


async def memory_detail(
    session: AsyncSession,
    owner_id: UUID,
    memory_id: UUID,
) -> dict:
    return memory_result(await _owned_memory(session, owner_id, memory_id))


async def list_memories(
    session: AsyncSession,
    owner_id: UUID,
    *,
    lifecycle: str,
    before_time: datetime | None,
    before_id: UUID | None,
    limit: int,
) -> dict:
    validate_cursor(before_time, before_id)
    statement = select(Memory).where(
        Memory.owner_id == owner_id,
        Memory.purged_at.is_(None),
    )
    if lifecycle == "active":
        statement = statement.where(Memory.deleted_at.is_(None))
    else:
        statement = statement.where(Memory.deleted_at.is_not(None))
    if before_time is not None and before_id is not None:
        statement = statement.where(
            or_(
                Memory.updated_at < before_time,
                and_(
                    Memory.updated_at == before_time,
                    Memory.id < before_id,
                ),
            )
        )
    rows = list(
        await session.scalars(
            statement.order_by(Memory.updated_at.desc(), Memory.id.desc()).limit(
                limit + 1
            )
        )
    )
    page = rows[:limit]
    return {
        "items": [memory_result(memory) for memory in page],
        "next_cursor": (
            {
                "before_time": page[-1].updated_at,
                "before_id": page[-1].id,
            }
            if len(rows) > limit
            else None
        ),
    }


async def expire_memory_operation_responses(
    session: AsyncSession,
    owner_id: UUID,
    memory_id: UUID,
) -> int:
    reference = str(memory_id)
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


async def _purge_memory(
    session: AsyncSession,
    memory: Memory,
    *,
    current_time: datetime,
) -> None:
    await expire_memory_operation_responses(session, memory.owner_id, memory.id)
    memory.title = None
    memory.content = None
    memory.source_proposal_id = None
    memory.purged_at = current_time
    memory.version += 1
    memory.updated_at = current_time
    emit_change(
        session,
        memory.owner_id,
        "memory",
        memory.id,
        "purged",
        memory.version,
    )


async def cleanup_expired_memories(
    database: Database,
    *,
    limit: int = PURGE_BATCH_SIZE,
) -> int:
    if limit < 1 or limit > PURGE_BATCH_SIZE:
        raise ValueError(f"limit must be between 1 and {PURGE_BATCH_SIZE}")
    current_time = datetime.now(UTC)
    async with database.transaction() as session:
        memories = list(
            await session.scalars(
                select(Memory)
                .where(
                    Memory.deleted_at.is_not(None),
                    Memory.purge_after <= current_time,
                    Memory.purged_at.is_(None),
                )
                .order_by(Memory.purge_after, Memory.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        for memory in memories:
            await _purge_memory(session, memory, current_time=current_time)
        return len(memories)
