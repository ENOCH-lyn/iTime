from datetime import datetime, timedelta
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from itime.auth.security import now
from itime.auth.session import authorized
from itime.calendar.models import (
    CalendarSource,
    CalendarSubscription,
    CalendarSubscriptionEvent,
    CalendarSubscriptionException,
    CalendarSubscriptionOverride,
    CalendarSubscriptionRefresh,
    UserTimeSettings,
)
from itime.calendar.series_service import query_occurrences
from itime.calendar.subscription_exception_service import (
    locked_subscription_exception,
    subscription_exception_result,
    subscription_exception_version_conflict,
    validate_subscription_exception,
)
from itime.calendar.subscription_fetcher import subscription_url_metadata
from itime.calendar.subscription_projection import get_subscription_occurrence
from itime.calendar.subscription_schemas import (
    SubscriptionAction,
    SubscriptionCreate,
    SubscriptionExceptionCreate,
    SubscriptionExceptionDelete,
    SubscriptionExceptionUpdate,
    SubscriptionOverrideUpdate,
    SubscriptionRefresh,
    SubscriptionUpdate,
)
from itime.calendar.subscription_service import (
    encrypt_subscription_url,
    event_result,
    event_source_fields,
    locked_subscription,
    refresh_result,
    subscription_result,
    subscription_url_hmac,
    subscription_version_conflict,
)
from itime.errors import AppError
from itime.tasks.service import (
    emit_change,
    replay_operation,
    save_operation,
    validate_cursor,
)

router = APIRouter(prefix="/api/v1/calendar/subscriptions", tags=["日历订阅"])


async def _exception_write_result(
    session: AsyncSession,
    owner_id: UUID,
    source: CalendarSource,
    *,
    master_event_id: UUID,
    source_occurrence_key: str,
    exception: CalendarSubscriptionException | None,
    operation_id: UUID,
) -> dict:
    occurrence = await get_subscription_occurrence(
        session,
        owner_id,
        source_id=source.id,
        master_event_id=master_event_id,
        source_occurrence_key=source_occurrence_key,
    )
    start_at = occurrence["start_at"]
    end_at = occurrence["end_at"]
    conflict_items: list[dict] = []
    has_more = False
    if start_at is not None and end_at is not None:
        user_time_zone = await session.scalar(
            select(UserTimeSettings.time_zone).where(
                UserTimeSettings.owner_id == owner_id
            )
        )
        zone = ZoneInfo(user_time_zone or occurrence["time_zone"])
        range_start = start_at.astimezone(zone).date() - timedelta(days=1)
        range_end = end_at.astimezone(zone).date() + timedelta(days=2)
        cursor = range_start
        seen: set[tuple] = set()
        while cursor < range_end:
            chunk_end = min(cursor + timedelta(days=93), range_end)
            items, _time_zone = await query_occurrences(
                session,
                owner_id,
                cursor,
                chunk_end,
            )
            target = next(
                (
                    item
                    for item in items
                    if item.get("subscription_master_event_id")
                    == master_event_id
                    and item.get("source_occurrence_key")
                    == source_occurrence_key
                ),
                None,
            )
            if target is not None:
                occurrence = target
                for conflict in target["conflict_with"]:
                    key = (
                        conflict["series_id"],
                        conflict.get("subscription_event_id"),
                        conflict["occurrence_key"],
                        conflict["occurrence_type"],
                        conflict["overlap_start"],
                        conflict["overlap_end"],
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    if len(conflict_items) < 100:
                        conflict_items.append(conflict)
                    else:
                        has_more = True
            cursor = chunk_end
    return {
        "operation_id": operation_id,
        "source_version": source.version,
        "exception": (
            subscription_exception_result(exception)
            if exception is not None
            else None
        ),
        "occurrence": occurrence,
        "conflicts": {
            "items": conflict_items,
            "has_more": has_more,
        },
    }


@router.post("", status_code=201)
async def create_subscription(data: SubscriptionCreate, request: Request) -> dict:
    scope = "calendar_subscriptions.create"
    settings = request.app.state.settings
    display_url, display_host = subscription_url_metadata(data.url, settings)
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
        time_zone = await identity.session.scalar(
            select(UserTimeSettings.time_zone).where(
                UserTimeSettings.owner_id == identity.user.id
            )
        )
        if time_zone is None:
            raise AppError(
                "time_settings_required",
                "请先保存当前使用的时区，再创建日历订阅。",
                status=409,
            )
        source = CalendarSource(
            owner_id=identity.user.id,
            kind="ics_subscription",
            name=data.name,
        )
        identity.session.add(source)
        await identity.session.flush()
        subscription = CalendarSubscription(
            source_id=source.id,
            owner_id=identity.user.id,
            encrypted_url=encrypt_subscription_url(settings, data.url),
            url_hmac=subscription_url_hmac(settings, data.url),
            display_url=display_url,
            display_host=display_host,
            default_time_zone=time_zone,
            refresh_interval_minutes=data.refresh_interval_minutes,
            next_refresh_at=now(),
        )
        identity.session.add(subscription)
        emit_change(
            identity.session,
            identity.user.id,
            "calendar_source",
            source.id,
            "created",
            source.version,
        )
        await identity.session.flush()
        result = {
            "operation_id": data.operation_id,
            "subscription": subscription_result(source, subscription),
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


@router.get("")
async def list_subscriptions(request: Request) -> dict:
    async with authorized(request) as identity:
        rows = list(
            (
                await identity.session.execute(
                    select(CalendarSource, CalendarSubscription)
                    .join(
                        CalendarSubscription,
                        CalendarSubscription.source_id == CalendarSource.id,
                    )
                    .where(
                        CalendarSource.owner_id == identity.user.id,
                        CalendarSource.kind == "ics_subscription",
                    )
                    .order_by(CalendarSource.updated_at.desc(), CalendarSource.id.desc())
                    .limit(100)
                )
            ).all()
        )
        source_ids = [source.id for source, _subscription in rows]
        counts: dict[UUID, tuple[int, int]] = {}
        if source_ids:
            count_rows = await identity.session.execute(
                select(
                    CalendarSubscriptionEvent.source_id,
                    func.count(CalendarSubscriptionEvent.id),
                    func.count(CalendarSubscriptionEvent.id).filter(
                        CalendarSubscriptionEvent.parse_status != "valid"
                    ),
                )
                .where(CalendarSubscriptionEvent.source_id.in_(source_ids))
                .group_by(CalendarSubscriptionEvent.source_id)
            )
            counts = {
                source_id: (event_count, error_count)
                for source_id, event_count, error_count in count_rows
            }
        return {
            "items": [
                subscription_result(
                    source,
                    subscription,
                    event_count=counts.get(source.id, (0, 0))[0],
                    error_event_count=counts.get(source.id, (0, 0))[1],
                )
                for source, subscription in rows
            ]
        }


@router.get("/{source_id}")
async def get_subscription(
    source_id: UUID,
    request: Request,
    event_limit: Annotated[int, Query(ge=1, le=200)] = 100,
    before_event_time: Annotated[datetime | None, Query()] = None,
    before_event_id: Annotated[UUID | None, Query()] = None,
) -> dict:
    validate_cursor(before_event_time, before_event_id)
    async with authorized(request) as identity:
        source, subscription = await locked_subscription(
            identity.session,
            identity.user.id,
            source_id,
        )
        statement = (
            select(CalendarSubscriptionEvent)
            .where(
                CalendarSubscriptionEvent.source_id == source_id,
                CalendarSubscriptionEvent.owner_id == identity.user.id,
            )
            .order_by(
                CalendarSubscriptionEvent.updated_at.desc(),
                CalendarSubscriptionEvent.id.desc(),
            )
            .limit(event_limit + 1)
        )
        if before_event_time is not None and before_event_id is not None:
            statement = statement.where(
                (CalendarSubscriptionEvent.updated_at < before_event_time)
                | (
                    (CalendarSubscriptionEvent.updated_at == before_event_time)
                    & (CalendarSubscriptionEvent.id < before_event_id)
                )
            )
        events = list(await identity.session.scalars(statement))
        has_more = len(events) > event_limit
        page = events[:event_limit]
        overrides = {
            override.event_id: override
            for override in await identity.session.scalars(
                select(CalendarSubscriptionOverride).where(
                    CalendarSubscriptionOverride.owner_id == identity.user.id,
                    CalendarSubscriptionOverride.event_id.in_(
                        [event.id for event in page]
                    ),
                )
            )
        } if page else {}
        refreshes = list(
            await identity.session.scalars(
                select(CalendarSubscriptionRefresh)
                .where(
                    CalendarSubscriptionRefresh.source_id == source_id,
                    CalendarSubscriptionRefresh.owner_id == identity.user.id,
                )
                .order_by(CalendarSubscriptionRefresh.started_at.desc())
                .limit(10)
            )
        )
        count_row = (
            await identity.session.execute(
                select(
                    func.count(CalendarSubscriptionEvent.id),
                    func.count(CalendarSubscriptionEvent.id).filter(
                        CalendarSubscriptionEvent.parse_status != "valid"
                    ),
                ).where(CalendarSubscriptionEvent.source_id == source_id)
            )
        ).one()
        return {
            "subscription": subscription_result(
                source,
                subscription,
                event_count=count_row[0],
                error_event_count=count_row[1],
            ),
            "events": [event_result(event, overrides.get(event.id)) for event in page],
            "event_has_more": has_more,
            "next_event_cursor": (
                {
                    "before_event_time": page[-1].updated_at,
                    "before_event_id": page[-1].id,
                }
                if has_more and page
                else None
            ),
            "refreshes": [refresh_result(refresh) for refresh in refreshes],
        }


@router.put("/{source_id}")
async def update_subscription(
    source_id: UUID,
    data: SubscriptionUpdate,
    request: Request,
) -> dict:
    scope = f"calendar_subscriptions.update:{source_id}"
    settings = request.app.state.settings
    url_metadata = (
        subscription_url_metadata(data.url, settings)
        if data.url is not None
        else None
    )
    new_url_hmac = (
        subscription_url_hmac(settings, data.url)
        if data.url is not None
        else None
    )
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
        source, subscription = await locked_subscription(
            identity.session,
            identity.user.id,
            source_id,
            for_update=True,
        )
        if source.version != data.expected_version:
            raise subscription_version_conflict(source)
        current = now()
        url_changed = (
            data.url is not None
            and new_url_hmac is not None
            and subscription.url_hmac != new_url_hmac
        )
        changed = (
            source.name != data.name
            or url_changed
            or subscription.refresh_interval_minutes != data.refresh_interval_minutes
        )
        if changed:
            source.name = data.name
            source.version += 1
            source.updated_at = current
            subscription.refresh_interval_minutes = data.refresh_interval_minutes
            if url_changed:
                if data.url is None or url_metadata is None:
                    raise RuntimeError("changed subscription URL is missing")
                await _stale_running_refresh(identity.session, subscription, current)
                subscription.encrypted_url = encrypt_subscription_url(settings, data.url)
                subscription.url_hmac = new_url_hmac
                subscription.display_url = url_metadata[0]
                subscription.display_host = url_metadata[1]
                subscription.fetch_revision += 1
                subscription.refresh_status = (
                    "queued"
                    if subscription.lifecycle_status == "active"
                    else "idle"
                )
                subscription.next_refresh_at = current
                subscription.lease_id = None
                subscription.lease_until = None
                subscription.etag = None
                subscription.last_modified = None
                subscription.encrypted_final_url = None
                subscription.final_url_hmac = None
                subscription.final_host = None
                subscription.last_error_code = None
                subscription.last_error_message = None
            subscription.version += 1
            subscription.updated_at = current
            emit_change(
                identity.session,
                identity.user.id,
                "calendar_source",
                source.id,
                "updated",
                source.version,
            )
        await identity.session.flush()
        result = {
            "operation_id": data.operation_id,
            "subscription": subscription_result(source, subscription),
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


@router.post("/{source_id}/refresh")
async def refresh_subscription(
    source_id: UUID,
    data: SubscriptionRefresh,
    request: Request,
) -> dict:
    scope = f"calendar_subscriptions.refresh:{source_id}"
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
        source, subscription = await locked_subscription(
            identity.session,
            identity.user.id,
            source_id,
            for_update=True,
        )
        if subscription.lifecycle_status == "paused":
            raise AppError(
                "calendar_subscription_paused",
                "此订阅已暂停，请先恢复后刷新。",
                status=409,
            )
        if subscription.refresh_status not in {"queued", "running"}:
            subscription.refresh_status = "queued"
            subscription.next_refresh_at = now()
            subscription.last_error_code = None
            subscription.last_error_message = None
            subscription.updated_at = now()
            subscription.version += 1
        await identity.session.flush()
        result = {
            "operation_id": data.operation_id,
            "subscription": subscription_result(source, subscription),
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


@router.post("/{source_id}/pause")
async def pause_subscription(
    source_id: UUID,
    data: SubscriptionAction,
    request: Request,
) -> dict:
    return await _set_lifecycle(source_id, data, request, paused=True)


@router.post("/{source_id}/resume")
async def resume_subscription(
    source_id: UUID,
    data: SubscriptionAction,
    request: Request,
) -> dict:
    return await _set_lifecycle(source_id, data, request, paused=False)


@router.put("/{source_id}/events/{event_id}/overrides")
async def update_subscription_override(
    source_id: UUID,
    event_id: UUID,
    data: SubscriptionOverrideUpdate,
    request: Request,
) -> dict:
    scope = f"calendar_subscriptions.override:{source_id}:{event_id}"
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
        source, _subscription = await locked_subscription(
            identity.session,
            identity.user.id,
            source_id,
            for_update=True,
        )
        event = await identity.session.scalar(
            select(CalendarSubscriptionEvent)
            .where(
                CalendarSubscriptionEvent.id == event_id,
                CalendarSubscriptionEvent.source_id == source_id,
                CalendarSubscriptionEvent.owner_id == identity.user.id,
            )
            .with_for_update()
        )
        if event is None:
            raise AppError(
                "calendar_subscription_event_not_found",
                "未找到此订阅事件。",
                status=404,
            )
        if event.baseline is None:
            raise AppError(
                "calendar_subscription_event_invalid",
                "此来源事件尚无可覆盖的有效版本。",
                status=409,
            )
        override = await identity.session.scalar(
            select(CalendarSubscriptionOverride)
            .where(
                CalendarSubscriptionOverride.event_id == event.id,
                CalendarSubscriptionOverride.owner_id == identity.user.id,
            )
            .with_for_update()
        )
        current_version = override.version if override is not None else 0
        if data.expected_version != current_version:
            raise AppError(
                "calendar_subscription_override_version_conflict",
                "此本地覆盖已在其他页面或设备修改，请刷新后比较再保存。",
                status=409,
                details={"current_version": current_version},
            )
        values = dict(override.values) if override is not None else {}
        source_values = dict(override.source_values) if override is not None else {}
        for field_name in data.reset_fields:
            values.pop(field_name, None)
            source_values.pop(field_name, None)
        current_source_fields = event_source_fields(event)
        for field_name, value in data.values.items():
            values[field_name] = value
            source_values[field_name] = current_source_fields[field_name]
        current = now()
        if override is None and values:
            override = CalendarSubscriptionOverride(
                event_id=event.id,
                owner_id=identity.user.id,
                values=values,
                source_values=source_values,
            )
            identity.session.add(override)
        elif override is not None and values:
            override.values = values
            override.source_values = source_values
            override.version += 1
            override.updated_at = current
        elif override is not None:
            await identity.session.delete(override)
            override = None
        event.version += 1
        event.updated_at = current
        source.version += 1
        source.updated_at = current
        emit_change(
            identity.session,
            identity.user.id,
            "calendar_source",
            source.id,
            "updated",
            source.version,
        )
        await identity.session.flush()
        result = {
            "operation_id": data.operation_id,
            "event": event_result(event, override),
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


@router.post("/{source_id}/exceptions", status_code=201)
async def create_subscription_exception(
    source_id: UUID,
    data: SubscriptionExceptionCreate,
    request: Request,
) -> dict:
    scope = f"calendar_subscription_exceptions.create:{source_id}"
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
        source, subscription = await locked_subscription(
            identity.session,
            identity.user.id,
            source_id,
            for_update=True,
        )
        if source.version != data.expected_source_version:
            raise subscription_version_conflict(source)
        master = await identity.session.scalar(
            select(CalendarSubscriptionEvent)
            .where(
                CalendarSubscriptionEvent.id == data.master_event_id,
                CalendarSubscriptionEvent.source_id == source_id,
                CalendarSubscriptionEvent.owner_id == identity.user.id,
                CalendarSubscriptionEvent.recurrence_id == "",
            )
            .with_for_update()
        )
        if master is None:
            raise AppError(
                "calendar_subscription_occurrence_not_found",
                "未找到此订阅安排实例。",
                status=404,
            )
        occurrence = await get_subscription_occurrence(
            identity.session,
            identity.user.id,
            source_id=source_id,
            master_event_id=master.id,
            source_occurrence_key=data.source_occurrence_key,
            include_local_exception=False,
        )
        existing = await identity.session.scalar(
            select(CalendarSubscriptionException).where(
                CalendarSubscriptionException.master_event_id == master.id,
                CalendarSubscriptionException.source_occurrence_key
                == data.source_occurrence_key,
            )
        )
        if existing is not None:
            raise AppError(
                "calendar_subscription_exception_exists",
                "该次订阅安排已有本地修改，请编辑或重置现有例外。",
                status=409,
                details={
                    "exception_id": str(existing.id),
                    "current_version": existing.version,
                },
            )
        values = validate_subscription_exception(
            data,
            occurrence,
            subscription.default_time_zone,
        )
        exception = CalendarSubscriptionException(
            owner_id=identity.user.id,
            master_event_id=master.id,
            source_occurrence_key=data.source_occurrence_key,
            original_start_at=occurrence["start_at"],
            original_end_at=occurrence["end_at"],
            original_all_day=occurrence["all_day"],
            **values,
        )
        identity.session.add(exception)
        current = now()
        source.version += 1
        source.updated_at = current
        emit_change(
            identity.session,
            identity.user.id,
            "calendar_source",
            source.id,
            "updated",
            source.version,
        )
        await identity.session.flush()
        result = await _exception_write_result(
            identity.session,
            identity.user.id,
            source,
            master_event_id=master.id,
            source_occurrence_key=data.source_occurrence_key,
            exception=exception,
            operation_id=data.operation_id,
        )
        save_operation(
            identity.session,
            identity.user.id,
            data.operation_id,
            scope,
            data,
            result,
        )
        return result


@router.put("/{source_id}/exceptions/{exception_id}")
async def update_subscription_exception(
    source_id: UUID,
    exception_id: UUID,
    data: SubscriptionExceptionUpdate,
    request: Request,
) -> dict:
    scope = (
        f"calendar_subscription_exceptions.update:{source_id}:{exception_id}"
    )
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
        source, subscription = await locked_subscription(
            identity.session,
            identity.user.id,
            source_id,
            for_update=True,
        )
        if source.version != data.expected_source_version:
            raise subscription_version_conflict(source)
        exception = await locked_subscription_exception(
            identity.session,
            identity.user.id,
            exception_id,
        )
        master = await identity.session.scalar(
            select(CalendarSubscriptionEvent).where(
                CalendarSubscriptionEvent.id == exception.master_event_id,
                CalendarSubscriptionEvent.source_id == source_id,
                CalendarSubscriptionEvent.owner_id == identity.user.id,
                CalendarSubscriptionEvent.recurrence_id == "",
            )
        )
        if master is None:
            raise AppError(
                "calendar_subscription_exception_not_found",
                "未找到此订阅单次例外。",
                status=404,
            )
        if exception.version != data.expected_version:
            raise subscription_exception_version_conflict(exception)
        occurrence = await get_subscription_occurrence(
            identity.session,
            identity.user.id,
            source_id=source_id,
            master_event_id=master.id,
            source_occurrence_key=exception.source_occurrence_key,
            include_local_exception=False,
        )
        values = validate_subscription_exception(
            data,
            occurrence,
            subscription.default_time_zone,
        )
        changed = any(
            getattr(exception, field) != value
            for field, value in values.items()
        )
        if changed:
            for field, value in values.items():
                setattr(exception, field, value)
            current = now()
            exception.version += 1
            exception.updated_at = current
            source.version += 1
            source.updated_at = current
            emit_change(
                identity.session,
                identity.user.id,
                "calendar_source",
                source.id,
                "updated",
                source.version,
            )
        await identity.session.flush()
        result = await _exception_write_result(
            identity.session,
            identity.user.id,
            source,
            master_event_id=master.id,
            source_occurrence_key=exception.source_occurrence_key,
            exception=exception,
            operation_id=data.operation_id,
        )
        save_operation(
            identity.session,
            identity.user.id,
            data.operation_id,
            scope,
            data,
            result,
        )
        return result


@router.delete("/{source_id}/exceptions/{exception_id}")
async def delete_subscription_exception(
    source_id: UUID,
    exception_id: UUID,
    data: SubscriptionExceptionDelete,
    request: Request,
) -> dict:
    scope = (
        f"calendar_subscription_exceptions.delete:{source_id}:{exception_id}"
    )
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
        source, _subscription = await locked_subscription(
            identity.session,
            identity.user.id,
            source_id,
            for_update=True,
        )
        if source.version != data.expected_source_version:
            raise subscription_version_conflict(source)
        exception = await locked_subscription_exception(
            identity.session,
            identity.user.id,
            exception_id,
        )
        master = await identity.session.scalar(
            select(CalendarSubscriptionEvent).where(
                CalendarSubscriptionEvent.id == exception.master_event_id,
                CalendarSubscriptionEvent.source_id == source_id,
                CalendarSubscriptionEvent.owner_id == identity.user.id,
                CalendarSubscriptionEvent.recurrence_id == "",
            )
        )
        if master is None:
            raise AppError(
                "calendar_subscription_exception_not_found",
                "未找到此订阅单次例外。",
                status=404,
            )
        if exception.version != data.expected_version:
            raise subscription_exception_version_conflict(exception)
        source_occurrence_key = exception.source_occurrence_key
        master_event_id = exception.master_event_id
        await identity.session.delete(exception)
        current = now()
        source.version += 1
        source.updated_at = current
        emit_change(
            identity.session,
            identity.user.id,
            "calendar_source",
            source.id,
            "updated",
            source.version,
        )
        await identity.session.flush()
        result = await _exception_write_result(
            identity.session,
            identity.user.id,
            source,
            master_event_id=master_event_id,
            source_occurrence_key=source_occurrence_key,
            exception=None,
            operation_id=data.operation_id,
        )
        save_operation(
            identity.session,
            identity.user.id,
            data.operation_id,
            scope,
            data,
            result,
        )
        return result


async def _set_lifecycle(
    source_id: UUID,
    data: SubscriptionAction,
    request: Request,
    *,
    paused: bool,
) -> dict:
    action = "pause" if paused else "resume"
    scope = f"calendar_subscriptions.{action}:{source_id}"
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
        source, subscription = await locked_subscription(
            identity.session,
            identity.user.id,
            source_id,
            for_update=True,
        )
        if source.version != data.expected_version:
            raise subscription_version_conflict(source)
        target = "paused" if paused else "active"
        if subscription.lifecycle_status != target:
            current = now()
            if paused:
                await _stale_running_refresh(identity.session, subscription, current)
                subscription.lifecycle_status = "paused"
                subscription.refresh_status = "idle"
                subscription.lease_id = None
                subscription.lease_until = None
            else:
                subscription.lifecycle_status = "active"
                subscription.refresh_status = "queued"
                subscription.next_refresh_at = current
            subscription.version += 1
            subscription.updated_at = current
            source.version += 1
            source.updated_at = current
            emit_change(
                identity.session,
                identity.user.id,
                "calendar_source",
                source.id,
                "updated",
                source.version,
            )
        await identity.session.flush()
        result = {
            "operation_id": data.operation_id,
            "subscription": subscription_result(source, subscription),
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


async def _stale_running_refresh(
    session,
    subscription: CalendarSubscription,
    current: datetime,
) -> None:
    if subscription.refresh_status != "running" or subscription.lease_id is None:
        return
    refresh = await session.scalar(
        select(CalendarSubscriptionRefresh)
        .where(
            CalendarSubscriptionRefresh.source_id == subscription.source_id,
            CalendarSubscriptionRefresh.lease_id == subscription.lease_id,
            CalendarSubscriptionRefresh.status == "running",
        )
        .with_for_update()
    )
    if refresh is not None:
        refresh.status = "stale"
        refresh.error_code = "calendar_subscription_superseded"
        refresh.error_message = "刷新结果已被较新的订阅设置取代。"
        refresh.finished_at = current
