import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from itime.auth.models import User
from itime.auth.security import now
from itime.calendar.models import (
    CalendarSource,
    CalendarSubscription,
    CalendarSubscriptionEvent,
    CalendarSubscriptionOverride,
    CalendarSubscriptionRefresh,
)
from itime.calendar.subscription_fetcher import CalendarFetchResult
from itime.calendar.subscription_parser import SubscriptionSnapshot
from itime.config import Settings
from itime.database import Database
from itime.errors import AppError
from itime.planning.source_conflict_service import (
    capture_source_plan_baselines,
    record_source_plan_conflicts,
)
from itime.tasks.service import emit_change

MAX_SUBSCRIPTION_EVENTS_PER_OWNER = 5000


@dataclass(frozen=True)
class ClaimedSubscription:
    source_id: UUID
    owner_id: UUID
    encrypted_url: bytes
    encrypted_final_url: bytes | None
    etag: str | None
    last_modified: str | None
    default_time_zone: str
    fetch_revision: int
    attempt_number: int
    lease_id: UUID
    refresh_id: UUID


def subscription_cipher(settings: Settings) -> Fernet:
    key = settings.calendar_subscription_encryption_key or settings.encryption_key
    return Fernet(key.get_secret_value())


def encrypt_subscription_url(settings: Settings, url: str) -> bytes:
    return subscription_cipher(settings).encrypt(url.encode())


def decrypt_subscription_url(settings: Settings, value: bytes) -> str:
    try:
        return subscription_cipher(settings).decrypt(value).decode()
    except (InvalidToken, UnicodeDecodeError) as exc:
        raise AppError(
            "calendar_subscription_url_unavailable",
            "订阅地址无法解密，请重新保存地址。",
            status=409,
        ) from exc


def subscription_url_hmac(settings: Settings, url: str) -> str:
    key = settings.calendar_subscription_encryption_key or settings.encryption_key
    return hmac.new(
        key.get_secret_value().encode(),
        url.encode(),
        hashlib.sha256,
    ).hexdigest()


def subscription_result(
    source: CalendarSource,
    subscription: CalendarSubscription,
    *,
    event_count: int | None = None,
    error_event_count: int | None = None,
) -> dict:
    result = {
        "id": source.id,
        "name": source.name,
        "url_display": subscription.display_url,
        "host": subscription.display_host,
        "final_host": subscription.final_host,
        "default_time_zone": subscription.default_time_zone,
        "lifecycle_status": subscription.lifecycle_status,
        "refresh_status": subscription.refresh_status,
        "refresh_interval_minutes": subscription.refresh_interval_minutes,
        "next_refresh_at": subscription.next_refresh_at,
        "last_attempt_at": subscription.last_attempt_at,
        "last_success_at": subscription.last_success_at,
        "last_change_at": subscription.last_change_at,
        "consecutive_failures": subscription.consecutive_failures,
        "last_error": (
            {
                "code": subscription.last_error_code,
                "message": subscription.last_error_message,
            }
            if subscription.last_error_code
            else None
        ),
        "generation": subscription.generation,
        "version": source.version,
        "state_version": subscription.version,
        "created_at": source.created_at,
        "updated_at": source.updated_at,
    }
    if event_count is not None:
        result["event_count"] = event_count
        result["error_event_count"] = error_event_count or 0
    return result


def refresh_result(refresh: CalendarSubscriptionRefresh) -> dict:
    return {
        "id": refresh.id,
        "source_id": refresh.source_id,
        "attempt_number": refresh.attempt_number,
        "trigger": refresh.trigger,
        "status": refresh.status,
        "http_status": refresh.http_status,
        "final_host": refresh.final_host,
        "event_count": refresh.event_count,
        "changed_count": refresh.changed_count,
        "cancelled_count": refresh.cancelled_count,
        "error_count": refresh.error_count,
        "item_errors": refresh.item_errors,
        "error": (
            {"code": refresh.error_code, "message": refresh.error_message}
            if refresh.error_code
            else None
        ),
        "started_at": refresh.started_at,
        "finished_at": refresh.finished_at,
    }


def event_source_fields(event: CalendarSubscriptionEvent) -> dict:
    if event.baseline is None:
        return {}
    return {
        field: event.baseline.get(field)
        for field in ("name", "description", "location", "busy")
    }


def event_result(
    event: CalendarSubscriptionEvent,
    override: CalendarSubscriptionOverride | None,
) -> dict:
    source_fields = event_source_fields(event)
    override_values = override.values if override is not None else {}
    source_values = override.source_values if override is not None else {}
    effective_fields = {**source_fields, **override_values}
    differences = [
        {
            "field": field,
            "source_at_override": source_values.get(field),
            "source_current": source_fields.get(field),
            "local_value": value,
        }
        for field, value in override_values.items()
        if source_values.get(field) != source_fields.get(field)
    ]
    return {
        "id": event.id,
        "uid": event.uid,
        "recurrence_id": event.recurrence_id or None,
        "parse_status": event.parse_status,
        "source_status": event.source_status,
        "source_fields": source_fields,
        "effective_fields": effective_fields,
        "override_fields": sorted(override_values),
        "differences": differences,
        "error": (
            {"code": event.error_code, "message": event.error_message}
            if event.error_code
            else None
        ),
        "version": event.version,
        "override_version": override.version if override is not None else 0,
        "updated_at": event.updated_at,
    }


async def locked_subscription(
    session: AsyncSession,
    owner_id: UUID,
    source_id: UUID,
    *,
    for_update: bool = False,
) -> tuple[CalendarSource, CalendarSubscription]:
    source_statement = select(CalendarSource).where(
        CalendarSource.id == source_id,
        CalendarSource.owner_id == owner_id,
        CalendarSource.kind == "ics_subscription",
    )
    if for_update:
        source_statement = source_statement.with_for_update()
    source = await session.scalar(source_statement)
    if source is None:
        raise AppError(
            "calendar_subscription_not_found",
            "未找到此日历订阅。",
            status=404,
        )
    subscription_statement = select(CalendarSubscription).where(
        CalendarSubscription.source_id == source_id,
        CalendarSubscription.owner_id == owner_id,
    )
    if for_update:
        subscription_statement = subscription_statement.with_for_update()
    subscription = await session.scalar(subscription_statement)
    if subscription is None:
        raise RuntimeError("calendar subscription aggregate is incomplete")
    return source, subscription


def subscription_version_conflict(source: CalendarSource) -> AppError:
    return AppError(
        "calendar_subscription_version_conflict",
        "此订阅已在其他页面或设备更新，请刷新后比较再保存。",
        status=409,
        details={"current_version": source.version},
    )


async def claim_next_subscription(
    database: Database,
    *,
    lease_seconds: int,
) -> ClaimedSubscription | None:
    current = now()
    async with database.transaction() as session:
        subscription = await session.scalar(
            select(CalendarSubscription)
            .join(User, User.id == CalendarSubscription.owner_id)
            .where(
                User.status == "active",
                CalendarSubscription.lifecycle_status == "active",
                or_(
                    CalendarSubscription.refresh_status == "queued",
                    and_(
                        CalendarSubscription.refresh_status.in_(["idle", "error"]),
                        CalendarSubscription.next_refresh_at <= current,
                    ),
                    and_(
                        CalendarSubscription.refresh_status == "running",
                        CalendarSubscription.lease_until <= current,
                    ),
                ),
            )
            .order_by(
                CalendarSubscription.next_refresh_at,
                CalendarSubscription.source_id,
            )
            .limit(1)
            .with_for_update(skip_locked=True, of=CalendarSubscription)
        )
        if subscription is None:
            return None
        stale_lease_id = subscription.lease_id
        if subscription.refresh_status == "running" and stale_lease_id is not None:
            stale_refresh = await session.scalar(
                select(CalendarSubscriptionRefresh)
                .where(
                    CalendarSubscriptionRefresh.source_id == subscription.source_id,
                    CalendarSubscriptionRefresh.lease_id == stale_lease_id,
                    CalendarSubscriptionRefresh.status == "running",
                )
                .with_for_update()
            )
            if stale_refresh is not None:
                stale_refresh.status = "stale"
                stale_refresh.error_code = "calendar_subscription_lease_expired"
                stale_refresh.error_message = "刷新进程租约已过期，结果不会应用。"
                stale_refresh.finished_at = current

        trigger = (
            "initial"
            if subscription.generation == 0
            else (
                "manual"
                if subscription.refresh_status == "queued"
                else "automatic"
            )
        )
        lease_id = uuid4()
        subscription.refresh_status = "running"
        subscription.attempt_count += 1
        subscription.lease_id = lease_id
        subscription.lease_until = current + timedelta(seconds=lease_seconds)
        subscription.last_attempt_at = current
        subscription.last_error_code = None
        subscription.last_error_message = None
        subscription.updated_at = current
        subscription.version += 1
        refresh = CalendarSubscriptionRefresh(
            source_id=subscription.source_id,
            owner_id=subscription.owner_id,
            lease_id=lease_id,
            fetch_revision=subscription.fetch_revision,
            attempt_number=subscription.attempt_count,
            trigger=trigger,
            status="running",
            started_at=current,
        )
        session.add(refresh)
        await session.flush()
        return ClaimedSubscription(
            source_id=subscription.source_id,
            owner_id=subscription.owner_id,
            encrypted_url=subscription.encrypted_url,
            encrypted_final_url=subscription.encrypted_final_url,
            etag=subscription.etag,
            last_modified=subscription.last_modified,
            default_time_zone=subscription.default_time_zone,
            fetch_revision=subscription.fetch_revision,
            attempt_number=subscription.attempt_count,
            lease_id=lease_id,
            refresh_id=refresh.id,
        )


async def invalidate_subscription_refreshes_for_account_deletion(
    session: AsyncSession,
    owner_id: UUID,
    at: datetime,
) -> int:
    subscriptions = list(
        await session.scalars(
            select(CalendarSubscription)
            .where(
                CalendarSubscription.owner_id == owner_id,
                CalendarSubscription.refresh_status == "running",
            )
            .order_by(CalendarSubscription.source_id)
            .with_for_update()
        )
    )
    for subscription in subscriptions:
        if subscription.lease_id is not None:
            refresh = await session.scalar(
                select(CalendarSubscriptionRefresh)
                .where(
                    CalendarSubscriptionRefresh.source_id
                    == subscription.source_id,
                    CalendarSubscriptionRefresh.lease_id
                    == subscription.lease_id,
                    CalendarSubscriptionRefresh.status == "running",
                )
                .with_for_update()
            )
            if refresh is not None:
                refresh.status = "stale"
                refresh.error_code = (
                    "calendar_subscription_account_deletion"
                )
                refresh.error_message = (
                    "账号已申请注销，订阅刷新结果不会应用。"
                )
                refresh.finished_at = at
        subscription.refresh_status = "idle"
        subscription.lease_id = None
        subscription.lease_until = None
        subscription.updated_at = at
        subscription.version += 1
    return len(subscriptions)


async def complete_not_modified(
    database: Database,
    claim: ClaimedSubscription,
    result: CalendarFetchResult,
) -> bool:
    current = now()
    async with database.transaction() as session:
        subscription = await _locked_claim_subscription(session, claim)
        refresh = await _locked_refresh(session, claim.refresh_id)
        if subscription is None or refresh is None:
            return False
        subscription.refresh_status = "idle"
        subscription.next_refresh_at = _next_successful_refresh(
            subscription.source_id,
            current,
            subscription.refresh_interval_minutes,
        )
        subscription.consecutive_failures = 0
        subscription.last_success_at = current
        subscription.last_error_code = None
        subscription.last_error_message = None
        subscription.lease_id = None
        subscription.lease_until = None
        subscription.updated_at = current
        subscription.version += 1
        refresh.status = "not_modified"
        refresh.http_status = result.status
        refresh.final_host = urlsplit(result.final_url).hostname
        refresh.finished_at = current
        return True


async def renew_subscription_lease(
    database: Database,
    claim: ClaimedSubscription,
    *,
    lease_seconds: int,
) -> bool:
    async with database.transaction() as session:
        subscription = await _locked_claim_subscription(session, claim)
        if subscription is None:
            return False
        subscription.lease_until = now() + timedelta(seconds=lease_seconds)
        return True


async def apply_subscription_snapshot(
    database: Database,
    settings: Settings,
    claim: ClaimedSubscription,
    result: CalendarFetchResult,
    snapshot: SubscriptionSnapshot,
) -> bool:
    current = now()
    async with database.transaction() as session:
        source = await session.scalar(
            select(CalendarSource)
            .where(
                CalendarSource.id == claim.source_id,
                CalendarSource.owner_id == claim.owner_id,
            )
            .with_for_update()
        )
        if source is None:
            return False
        subscription = await _locked_claim_subscription(session, claim)
        refresh = await _locked_refresh(session, claim.refresh_id)
        if subscription is None or refresh is None:
            return False
        plan_baselines = await capture_source_plan_baselines(
            session,
            claim.owner_id,
            claim.source_id,
            current,
        )
        events = list(
            await session.scalars(
                select(CalendarSubscriptionEvent)
                .where(CalendarSubscriptionEvent.source_id == claim.source_id)
                .with_for_update()
            )
        )
        existing = {(event.uid, event.recurrence_id): event for event in events}
        other_event_count = await session.scalar(
            select(func.count(CalendarSubscriptionEvent.id)).where(
                CalendarSubscriptionEvent.owner_id == claim.owner_id,
                CalendarSubscriptionEvent.source_id != claim.source_id,
            )
        )
        projected_event_count = int(other_event_count or 0) + len(
            set(existing).union(
                (item.uid, item.recurrence_id) for item in snapshot.items
            )
        )
        if projected_event_count > MAX_SUBSCRIPTION_EVENTS_PER_OWNER:
            raise AppError(
                "calendar_subscription_account_event_limit",
                "账号订阅事件总数不能超过 5000 条，请移除不需要的来源。",
                status=422,
            )
        generation = subscription.generation + 1
        seen: set[tuple[str, str]] = set()
        changed_count = 0
        cancelled_count = 0
        for item in snapshot.items:
            key = (item.uid, item.recurrence_id)
            seen.add(key)
            event = existing.get(key)
            if item.baseline is None:
                if event is None:
                    event = CalendarSubscriptionEvent(
                        source_id=claim.source_id,
                        owner_id=claim.owner_id,
                        uid=item.uid,
                        recurrence_id=item.recurrence_id,
                        baseline=None,
                        content_sha256=item.content_sha256,
                        parse_status="error",
                        source_status="active",
                        error_code=item.error_code,
                        error_message=item.error_message,
                        first_seen_generation=generation,
                        last_seen_generation=generation,
                    )
                    session.add(event)
                    existing[key] = event
                else:
                    was_missing = event.missing_at is not None
                    event.parse_status = (
                        "stale_error" if event.baseline is not None else "error"
                    )
                    event.error_code = item.error_code
                    event.error_message = item.error_message
                    event.last_seen_generation = generation
                    event.missing_at = None
                    if was_missing and (
                        event.baseline is None
                        or event.baseline.get("status") != "cancelled"
                    ):
                        event.source_status = "active"
                        event.cancelled_at = None
                    event.updated_at = current
                    event.version += 1
                continue

            incoming_cancelled = item.baseline.get("status") == "cancelled"
            if event is None:
                baseline = item.baseline
                event = CalendarSubscriptionEvent(
                    source_id=claim.source_id,
                    owner_id=claim.owner_id,
                    uid=item.uid,
                    recurrence_id=item.recurrence_id,
                    baseline=baseline,
                    content_sha256=item.content_sha256,
                    parse_status="valid",
                    source_status="cancelled" if incoming_cancelled else "active",
                    first_seen_generation=generation,
                    last_seen_generation=generation,
                    cancelled_at=current if incoming_cancelled else None,
                    missing_at=None,
                )
                session.add(event)
                existing[key] = event
                changed_count += 1
                cancelled_count += int(incoming_cancelled)
                continue

            previous_digest = event.content_sha256
            previous_status = event.source_status
            previous_parse_status = event.parse_status
            previous_error = (event.error_code, event.error_message)
            if incoming_cancelled and event.baseline is not None:
                baseline = {**event.baseline, "status": "cancelled"}
            else:
                baseline = item.baseline
            event.baseline = baseline
            event.content_sha256 = item.content_sha256
            event.parse_status = "valid"
            event.source_status = "cancelled" if incoming_cancelled else "active"
            event.error_code = None
            event.error_message = None
            event.last_seen_generation = generation
            event.cancelled_at = (
                event.cancelled_at or current
                if incoming_cancelled
                else None
            )
            event.missing_at = None
            semantic_event_change = (
                previous_digest != item.content_sha256
                or previous_status != event.source_status
            )
            row_changed = (
                semantic_event_change
                or previous_parse_status != "valid"
                or previous_error != (None, None)
            )
            if row_changed:
                event.version += 1
                event.updated_at = current
            if semantic_event_change:
                changed_count += 1
                cancelled_count += int(incoming_cancelled)

        if snapshot.absence_complete:
            for key, event in existing.items():
                if key in seen:
                    continue
                if event.recurrence_id:
                    if event.missing_at is not None:
                        continue
                    event.source_status = "cancelled"
                    event.missing_at = current
                    event.last_seen_generation = generation
                    event.version += 1
                    event.updated_at = current
                    changed_count += 1
                    cancelled_count += 1
                    continue
                if event.source_status == "cancelled":
                    continue
                event.source_status = "cancelled"
                event.cancelled_at = current
                event.last_seen_generation = generation
                event.version += 1
                event.updated_at = current
                changed_count += 1
                cancelled_count += 1

        semantic_change = changed_count > 0
        if semantic_change and plan_baselines:
            await session.flush()
            await record_source_plan_conflicts(
                session,
                claim.owner_id,
                source,
                generation,
                plan_baselines,
                current,
            )
        subscription.refresh_status = "idle"
        subscription.next_refresh_at = _next_successful_refresh(
            subscription.source_id,
            current,
            subscription.refresh_interval_minutes,
        )
        subscription.generation = generation
        subscription.consecutive_failures = 0
        subscription.last_success_at = current
        subscription.last_change_at = current if semantic_change else subscription.last_change_at
        subscription.etag = result.etag
        subscription.last_modified = result.last_modified
        subscription.encrypted_final_url = encrypt_subscription_url(
            settings,
            result.final_url,
        )
        subscription.final_url_hmac = subscription_url_hmac(settings, result.final_url)
        subscription.final_host = urlsplit(result.final_url).hostname
        subscription.content_sha256 = result.content_sha256
        subscription.last_error_code = None
        subscription.last_error_message = None
        subscription.lease_id = None
        subscription.lease_until = None
        subscription.updated_at = current
        subscription.version += 1
        if semantic_change:
            source.version += 1
            source.updated_at = current
            emit_change(
                session,
                claim.owner_id,
                "calendar_source",
                source.id,
                "updated",
                source.version,
            )

        refresh.status = "succeeded"
        refresh.http_status = result.status
        refresh.final_host = subscription.final_host
        refresh.content_sha256 = result.content_sha256
        refresh.event_count = len(snapshot.items)
        refresh.changed_count = changed_count
        refresh.cancelled_count = cancelled_count
        refresh.error_count = len(snapshot.item_errors)
        refresh.item_errors = snapshot.item_errors
        refresh.finished_at = current
        return True


async def fail_subscription_refresh(
    database: Database,
    claim: ClaimedSubscription,
    *,
    error_code: str,
    error_message: str,
    http_status: int | None = None,
) -> bool:
    current = now()
    async with database.transaction() as session:
        subscription = await _locked_claim_subscription(session, claim)
        refresh = await _locked_refresh(session, claim.refresh_id)
        if subscription is None or refresh is None:
            return False
        subscription.consecutive_failures += 1
        subscription.refresh_status = "error"
        subscription.next_refresh_at = current + _failure_backoff(
            subscription.consecutive_failures,
            subscription.refresh_interval_minutes,
        )
        subscription.last_error_code = error_code
        subscription.last_error_message = error_message
        subscription.lease_id = None
        subscription.lease_until = None
        subscription.updated_at = current
        subscription.version += 1
        refresh.status = "failed"
        refresh.http_status = http_status
        refresh.error_code = error_code
        refresh.error_message = error_message
        refresh.finished_at = current
        return True


async def _locked_claim_subscription(
    session: AsyncSession,
    claim: ClaimedSubscription,
) -> CalendarSubscription | None:
    return await session.scalar(
        select(CalendarSubscription)
        .where(
            CalendarSubscription.source_id == claim.source_id,
            CalendarSubscription.owner_id == claim.owner_id,
            CalendarSubscription.refresh_status == "running",
            CalendarSubscription.fetch_revision == claim.fetch_revision,
            CalendarSubscription.attempt_count == claim.attempt_number,
            CalendarSubscription.lease_id == claim.lease_id,
            CalendarSubscription.lease_until > now(),
        )
        .with_for_update()
    )


async def _locked_refresh(
    session: AsyncSession,
    refresh_id: UUID,
) -> CalendarSubscriptionRefresh | None:
    return await session.scalar(
        select(CalendarSubscriptionRefresh)
        .where(
            CalendarSubscriptionRefresh.id == refresh_id,
            CalendarSubscriptionRefresh.status == "running",
        )
        .with_for_update()
    )


def _next_successful_refresh(
    source_id: UUID,
    current: datetime,
    interval_minutes: int,
) -> datetime:
    jitter_ceiling = min(300, max(1, interval_minutes * 6))
    jitter_seconds = int.from_bytes(source_id.bytes[:4]) % jitter_ceiling
    return current + timedelta(minutes=interval_minutes, seconds=jitter_seconds)


def _failure_backoff(failure_count: int, interval_minutes: int) -> timedelta:
    seconds = min(interval_minutes * 60, 60 * (2 ** min(failure_count - 1, 8)))
    return timedelta(seconds=max(60, seconds))
