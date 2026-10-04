from datetime import datetime, timedelta
from math import ceil
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from itime.auth.security import now
from itime.calendar.models import (
    CalendarSeries,
    CalendarSource,
)
from itime.calendar.schemas import SeriesLifecycleInput
from itime.calendar.subscription_schemas import SubscriptionAction
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


async def trash_subscription_source(
    session: AsyncSession,
    owner_id: UUID,
    source_id: UUID,
    data: SubscriptionAction,
    *,
    current_time: datetime,
) -> dict:
    from itime.calendar.subscription_service import (
        locked_subscription,
        stale_running_refresh,
        subscription_result,
        subscription_version_conflict,
    )
    from itime.planning.source_conflict_service import (
        supersede_open_source_conflicts,
    )

    scope = f"calendar_subscriptions.delete:{source_id}"
    replay = await replay_operation(
        session,
        owner_id,
        data.operation_id,
        scope,
        data,
    )
    if replay is not None:
        return replay
    source, subscription = await locked_subscription(
        session,
        owner_id,
        source_id,
        for_update=True,
        editable=False,
    )
    ensure_calendar_readable(source)
    if source.version != data.expected_version:
        raise subscription_version_conflict(source)
    if source.deleted_at is None:
        await stale_running_refresh(
            session,
            subscription,
            current_time,
            error_code="calendar_subscription_deleted",
            error_message="订阅已移入回收站，刷新结果不会应用。",
        )
        source.deleted_at = current_time
        source.purge_after = current_time + CALENDAR_TRASH_RETENTION
        source.version += 1
        source.updated_at = current_time
        subscription.fetch_revision += 1
        subscription.refresh_status = "idle"
        subscription.lease_id = None
        subscription.lease_until = None
        subscription.updated_at = current_time
        subscription.version += 1
        await supersede_open_source_conflicts(
            session,
            owner_id,
            source.id,
            current_time,
        )
        emit_change(
            session,
            owner_id,
            "calendar_source",
            source.id,
            "deleted",
            source.version,
        )
    await session.flush()
    result = {
        "operation_id": data.operation_id,
        "subscription": subscription_result(source, subscription),
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


async def restore_subscription_source(
    session: AsyncSession,
    owner_id: UUID,
    source_id: UUID,
    data: SubscriptionAction,
    *,
    current_time: datetime,
) -> dict:
    from itime.calendar.subscription_service import (
        locked_subscription,
        subscription_result,
        subscription_version_conflict,
    )
    from itime.planning.source_conflict_service import (
        capture_source_plan_baselines,
        record_source_plan_conflicts,
    )

    scope = f"calendar_subscriptions.restore:{source_id}"
    replay = await replay_operation(
        session,
        owner_id,
        data.operation_id,
        scope,
        data,
    )
    if replay is not None:
        return replay
    source, subscription = await locked_subscription(
        session,
        owner_id,
        source_id,
        for_update=True,
        editable=False,
    )
    ensure_calendar_readable(source)
    if source.version != data.expected_version:
        raise subscription_version_conflict(source)
    if source.deleted_at is None or source.purge_after is None:
        raise AppError(
            "calendar_item_not_trashed",
            "此日历内容不在回收站，无需恢复。",
            status=409,
            details={
                "lifecycle": "active",
                "current_version": source.version,
            },
        )
    if current_time >= source.purge_after:
        raise AppError(
            "calendar_item_purged",
            "此日历内容已超过回收站恢复期并等待清理。",
            status=410,
        )
    baselines = await capture_source_plan_baselines(
        session,
        owner_id,
        source.id,
        current_time,
    )
    source.deleted_at = None
    source.purge_after = None
    source.version += 1
    source.updated_at = current_time
    subscription.generation += 1
    subscription.last_error_code = None
    subscription.last_error_message = None
    if subscription.lifecycle_status == "active":
        subscription.refresh_status = "queued"
        subscription.next_refresh_at = current_time
    else:
        subscription.refresh_status = "idle"
    subscription.updated_at = current_time
    subscription.version += 1
    await session.flush()
    await record_source_plan_conflicts(
        session,
        owner_id,
        source,
        subscription.generation,
        baselines,
        current_time,
    )
    emit_change(
        session,
        owner_id,
        "calendar_source",
        source.id,
        "restored",
        source.version,
    )
    await session.flush()
    result = {
        "operation_id": data.operation_id,
        "subscription": subscription_result(source, subscription),
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
