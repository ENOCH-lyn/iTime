from datetime import date, datetime, time, timedelta
from math import ceil
from uuid import UUID

from sqlalchemy import Text, cast, delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from itime.auth.models import User
from itime.auth.security import now
from itime.calendar.models import (
    CalendarAddition,
    CalendarException,
    CalendarImportCandidate,
    CalendarImportIdentity,
    CalendarSeries,
    CalendarSource,
    CalendarSubscription,
    CalendarSubscriptionEvent,
    CalendarSubscriptionRefresh,
)
from itime.calendar.schemas import SeriesLifecycleInput
from itime.calendar.subscription_schemas import SubscriptionAction
from itime.database import Database
from itime.errors import AppError
from itime.tasks.models import UserOperation
from itime.tasks.service import emit_change, replay_operation, save_operation

CALENDAR_TRASH_RETENTION = timedelta(days=30)
PURGE_BATCH_SIZE = 100
PURGED_SERIES_NAME = "已彻底删除的安排"
PURGED_SUBSCRIPTION_NAME = "已彻底删除的订阅"


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


async def expire_calendar_operation_responses(
    session: AsyncSession,
    owner_id: UUID,
    item_id: UUID,
    *,
    current_time: datetime,
) -> int:
    reference = str(item_id)
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
        .values(response=None, response_purged_at=current_time)
    )
    return result.rowcount or 0


async def _purge_series(
    session: AsyncSession,
    series: CalendarSeries,
    *,
    current_time: datetime,
) -> None:
    await expire_calendar_operation_responses(
        session,
        series.owner_id,
        series.id,
        current_time=current_time,
    )
    await session.execute(
        delete(CalendarException).where(
            CalendarException.series_id == series.id
        )
    )
    await session.execute(
        delete(CalendarAddition).where(
            CalendarAddition.series_id == series.id
        )
    )
    await session.execute(
        delete(CalendarImportIdentity).where(
            CalendarImportIdentity.series_id == series.id
        )
    )
    await session.execute(
        update(CalendarImportCandidate)
        .where(
            or_(
                CalendarImportCandidate.confirmed_series_id == series.id,
                CalendarImportCandidate.duplicate_series_id == series.id,
            )
        )
        .values(
            state="skipped",
            raw_data={},
            draft=None,
            identity_key=None,
            content_fingerprint=None,
            external_uid=None,
            external_recurrence_id=None,
            warnings=[],
            error_code=None,
            error_message=None,
            error_details=None,
            confirmed_series_id=None,
            duplicate_series_id=None,
            version=CalendarImportCandidate.version + 1,
            updated_at=current_time,
        )
    )
    series.term_id = None
    series.predecessor_id = None
    series.source_id = None
    series.kind = "fixed"
    series.name = PURGED_SERIES_NAME
    series.description = ""
    series.location = ""
    series.busy = False
    series.source_type = "manual"
    series.time_zone = "UTC"
    series.start_date = date(1970, 1, 1)
    series.starts_at = time.min
    series.ends_at = time.min
    series.end_day_offset = 1
    series.start_period_number = None
    series.end_period_number = None
    series.recurrence_frequency = "none"
    series.recurrence_interval = 1
    series.recurrence_weekdays = []
    series.recurrence_month = None
    series.recurrence_day_of_month = None
    series.recurrence_until = None
    series.recurrence_count = None
    series.course_first_week_monday = None
    series.course_week_numbers = []
    series.ambiguous_offset_minutes = None
    series.purged_at = current_time
    series.version += 1
    series.updated_at = current_time
    emit_change(
        session,
        series.owner_id,
        "calendar_series",
        series.id,
        "purged",
        series.version,
    )


async def _purge_source(
    session: AsyncSession,
    source: CalendarSource,
    *,
    current_time: datetime,
) -> None:
    subscription = await session.scalar(
        select(CalendarSubscription)
        .where(
            CalendarSubscription.source_id == source.id,
            CalendarSubscription.owner_id == source.owner_id,
        )
        .with_for_update()
    )
    if subscription is None:
        raise RuntimeError("calendar subscription aggregate is incomplete")
    await expire_calendar_operation_responses(
        session,
        source.owner_id,
        source.id,
        current_time=current_time,
    )
    await session.execute(
        delete(CalendarSubscriptionRefresh).where(
            CalendarSubscriptionRefresh.source_id == source.id
        )
    )
    await session.execute(
        delete(CalendarSubscriptionEvent).where(
            CalendarSubscriptionEvent.source_id == source.id
        )
    )
    source.name = PURGED_SUBSCRIPTION_NAME
    source.purged_at = current_time
    source.version += 1
    source.updated_at = current_time
    subscription.encrypted_url = b""
    subscription.url_hmac = "0" * 64
    subscription.display_url = "已清理"
    subscription.display_host = "deleted.invalid"
    subscription.default_time_zone = "UTC"
    subscription.lifecycle_status = "paused"
    subscription.refresh_status = "idle"
    subscription.next_refresh_at = current_time
    subscription.fetch_revision += 1
    subscription.attempt_count = 0
    subscription.lease_id = None
    subscription.lease_until = None
    subscription.consecutive_failures = 0
    subscription.last_attempt_at = None
    subscription.last_success_at = None
    subscription.last_change_at = None
    subscription.etag = None
    subscription.last_modified = None
    subscription.encrypted_final_url = None
    subscription.final_url_hmac = None
    subscription.final_host = None
    subscription.content_sha256 = None
    subscription.last_error_code = None
    subscription.last_error_message = None
    subscription.updated_at = current_time
    subscription.version += 1
    emit_change(
        session,
        source.owner_id,
        "calendar_source",
        source.id,
        "purged",
        source.version,
    )


async def cleanup_expired_calendar_items(
    database: Database,
    *,
    limit: int = PURGE_BATCH_SIZE,
) -> dict[str, int]:
    if limit < 1 or limit > PURGE_BATCH_SIZE:
        raise ValueError(f"limit must be between 1 and {PURGE_BATCH_SIZE}")
    current_time = now()
    async with database.transaction() as session:
        series_rows = list(
            await session.scalars(
                select(CalendarSeries)
                .join(User, User.id == CalendarSeries.owner_id)
                .where(
                    User.status == "active",
                    CalendarSeries.deleted_at.is_not(None),
                    CalendarSeries.purge_after <= current_time,
                    CalendarSeries.purged_at.is_(None),
                )
                .order_by(
                    CalendarSeries.owner_id,
                    CalendarSeries.purge_after,
                    CalendarSeries.id,
                )
                .limit(limit)
                .with_for_update(
                    skip_locked=True,
                    of=(User, CalendarSeries),
                )
            )
        )
        for series in series_rows:
            await _purge_series(
                session,
                series,
                current_time=current_time,
            )

        source_rows = list(
            await session.scalars(
                select(CalendarSource)
                .join(User, User.id == CalendarSource.owner_id)
                .where(
                    User.status == "active",
                    CalendarSource.kind == "ics_subscription",
                    CalendarSource.deleted_at.is_not(None),
                    CalendarSource.purge_after <= current_time,
                    CalendarSource.purged_at.is_(None),
                )
                .order_by(
                    CalendarSource.owner_id,
                    CalendarSource.purge_after,
                    CalendarSource.id,
                )
                .limit(limit)
                .with_for_update(
                    skip_locked=True,
                    of=(User, CalendarSource),
                )
            )
        )
        for source in source_rows:
            await _purge_source(
                session,
                source,
                current_time=current_time,
            )
        return {
            "series": len(series_rows),
            "sources": len(source_rows),
        }
