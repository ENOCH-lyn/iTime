from datetime import datetime, timedelta
from math import ceil
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from itime.auth.security import now
from itime.calendar.models import CalendarSeries, CalendarSource
from itime.calendar.schemas import SeriesLifecycleInput
from itime.errors import AppError
from itime.tasks.service import emit_change, replay_operation, save_operation

CALENDAR_TRASH_RETENTION = timedelta(days=30)


def calendar_lifecycle(item: CalendarSeries | CalendarSource) -> str:
    if item.purged_at is not None:
        return "purged"
    if item.deleted_at is not None:
        return "trashed"
    return "active"


def calendar_trash_remaining_days(
    item: CalendarSeries | CalendarSource,
) -> int | None:
    if item.deleted_at is None or item.purge_after is None:
        return None
    return max(0, ceil((item.purge_after - now()).total_seconds() / 86_400))


def ensure_calendar_readable(item: CalendarSeries | CalendarSource) -> None:
    if item.purged_at is not None:
        raise AppError(
            "calendar_item_purged",
            "此日历内容已超过回收站恢复期并完成清理。",
            status=410,
        )


def ensure_calendar_editable(item: CalendarSeries | CalendarSource) -> None:
    ensure_calendar_readable(item)
    if item.deleted_at is not None:
        raise AppError(
            "calendar_item_trashed",
            "此日历内容在回收站中，请先恢复后再修改。",
            status=409,
            details={
                "lifecycle": "trashed",
                "current_version": item.version,
            },
        )


async def trash_calendar_series(
    session: AsyncSession,
    owner_id: UUID,
    series_id: UUID,
    data: SeriesLifecycleInput,
    *,
    current_time: datetime,
) -> dict:
    from itime.calendar.series_service import (
        locked_series,
        series_result,
        series_version_conflict,
    )

    scope = f"calendar_series.delete:{series_id}"
    replay = await replay_operation(
        session,
        owner_id,
        data.operation_id,
        scope,
        data,
    )
    if replay is not None:
        return replay
    series = await locked_series(
        session,
        owner_id,
        series_id,
        editable=False,
    )
    ensure_calendar_readable(series)
    if series.version != data.expected_version:
        raise series_version_conflict(series)
    if series.deleted_at is None:
        series.deleted_at = current_time
        series.purge_after = current_time + CALENDAR_TRASH_RETENTION
        series.version += 1
        series.updated_at = current_time
        emit_change(
            session,
            owner_id,
            "calendar_series",
            series.id,
            "deleted",
            series.version,
        )
    await session.flush()
    result = {
        "operation_id": data.operation_id,
        "series": series_result(series),
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


async def restore_calendar_series(
    session: AsyncSession,
    owner_id: UUID,
    series_id: UUID,
    data: SeriesLifecycleInput,
    *,
    current_time: datetime,
) -> dict:
    from itime.calendar.series_service import (
        locked_series,
        series_result,
        series_version_conflict,
    )

    scope = f"calendar_series.restore:{series_id}"
    replay = await replay_operation(
        session,
        owner_id,
        data.operation_id,
        scope,
        data,
    )
    if replay is not None:
        return replay
    series = await locked_series(
        session,
        owner_id,
        series_id,
        editable=False,
    )
    ensure_calendar_readable(series)
    if series.version != data.expected_version:
        raise series_version_conflict(series)
    if series.deleted_at is None or series.purge_after is None:
        raise AppError(
            "calendar_item_not_trashed",
            "此日历内容不在回收站，无需恢复。",
            status=409,
            details={
                "lifecycle": "active",
                "current_version": series.version,
            },
        )
    if current_time >= series.purge_after:
        raise AppError(
            "calendar_item_purged",
            "此日历内容已超过回收站恢复期并等待清理。",
            status=410,
        )
    series.deleted_at = None
    series.purge_after = None
    series.version += 1
    series.updated_at = current_time
    emit_change(
        session,
        owner_id,
        "calendar_series",
        series.id,
        "restored",
        series.version,
    )
    await session.flush()
    result = {
        "operation_id": data.operation_id,
        "series": series_result(series),
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
