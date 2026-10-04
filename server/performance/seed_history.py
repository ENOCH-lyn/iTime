import hashlib
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from itime.calendar.models import (
    CalendarSource,
    CalendarSubscription,
    CalendarSubscriptionEvent,
)
from itime.calendar.subscription_service import (
    encrypt_subscription_url,
    subscription_url_hmac,
)
from itime.config import Settings
from itime.database import Database
from performance.contracts import AccountCredential, SeedManifest, SeedProfile
from performance.seed_core import (
    SYNTHETIC_LOGIN_SECRET,
    TIME_ZONE,
    _insert_batched,
    _prepare_globals,
    _seed_account_base,
    _seed_activity_history,
    _seed_calendar_and_plan,
    _seed_conversations_and_events,
    _seed_tasks,
    stable_uuid,
)


@dataclass(frozen=True)
class HistoryAccountResult:
    counts: dict[str, int]
    credential: AccountCredential


def _subscription_baseline(
    account_index: int,
    event_index: int,
    at: datetime,
) -> dict[str, object]:
    local_date = (at.date() - timedelta(days=365)) + timedelta(
        days=event_index % 365
    )
    starts_at = datetime.combine(
        local_date,
        time(8 + event_index % 10),
        tzinfo=UTC,
    )
    return {
        "uid": f"history-{account_index}-{event_index}",
        "recurrence_id": "",
        "name": f"历史订阅事件 {event_index + 1}",
        "description": "隔离性能测试日历事件",
        "location": f"场地 {event_index % 20 + 1}",
        "busy": event_index % 5 != 0,
        "status": "active",
        "start": {
            "kind": "datetime",
            "semantic": "utc",
            "local": starts_at.replace(tzinfo=None).isoformat(timespec="seconds"),
            "time_zone": TIME_ZONE,
            "utc": starts_at.isoformat().replace("+00:00", "Z"),
        },
        "duration": {"kind": "seconds", "value": 3600},
        "rrule": None,
        "rdates": [],
        "exdates": [],
    }


async def _seed_subscription_events(
    session: AsyncSession,
    settings: Settings,
    profile: SeedProfile,
    *,
    owner_id,
    account_index: int,
    seed: int,
    at: datetime,
) -> Counter[str]:
    counts: Counter[str] = Counter()
    if profile.subscription_events_per_account == 0:
        return counts

    source_id = stable_uuid(
        profile.name,
        seed,
        "subscription-source",
        account_index,
    )
    source_url = (
        f"http://host.docker.internal:18082/calendar/{account_index}.ics"
    )
    source_rows = [
        {
            "id": source_id,
            "owner_id": owner_id,
            "kind": "ics_subscription",
            "name": f"性能订阅 {account_index + 1}",
            "deleted_at": None,
            "purge_after": None,
            "purged_at": None,
            "version": 1,
            "created_at": at - timedelta(days=365),
            "updated_at": at,
        }
    ]
    subscription_rows = [
        {
            "source_id": source_id,
            "owner_id": owner_id,
            "encrypted_url": encrypt_subscription_url(settings, source_url),
            "url_hmac": subscription_url_hmac(settings, source_url),
            "display_url": source_url,
            "display_host": "host.docker.internal",
            "default_time_zone": TIME_ZONE,
            "lifecycle_status": "active",
            "refresh_status": "idle",
            "refresh_interval_minutes": 360,
            "next_refresh_at": at + timedelta(days=365),
            "fetch_revision": 1,
            "generation": 1,
            "attempt_count": 1,
            "lease_id": None,
            "lease_until": None,
            "consecutive_failures": 0,
            "last_attempt_at": at,
            "last_success_at": at,
            "last_change_at": at,
            "etag": '"performance-v1"',
            "last_modified": "Mon, 05 Oct 2026 12:00:00 GMT",
            "encrypted_final_url": encrypt_subscription_url(settings, source_url),
            "final_url_hmac": subscription_url_hmac(settings, source_url),
            "final_host": "host.docker.internal",
            "content_sha256": hashlib.sha256(source_url.encode()).hexdigest(),
            "last_error_code": None,
            "last_error_message": None,
            "version": 1,
            "created_at": at - timedelta(days=365),
            "updated_at": at,
        }
    ]
    event_rows: list[dict[str, object]] = []
    for event_index in range(profile.subscription_events_per_account):
        baseline = _subscription_baseline(account_index, event_index, at)
        event_rows.append(
            {
                "id": stable_uuid(
                    profile.name,
                    seed,
                    "subscription-event",
                    account_index,
                    event_index,
                ),
                "source_id": source_id,
                "owner_id": owner_id,
                "uid": baseline["uid"],
                "recurrence_id": "",
                "baseline": baseline,
                "content_sha256": hashlib.sha256(
                    repr(sorted(baseline.items())).encode()
                ).hexdigest(),
                "parse_status": "valid",
                "source_status": "active",
                "error_code": None,
                "error_message": None,
                "first_seen_generation": 1,
                "last_seen_generation": 1,
                "cancelled_at": None,
                "missing_at": None,
                "version": 1,
                "created_at": at - timedelta(days=365),
                "updated_at": at,
            }
        )
    for model, rows, name in (
        (CalendarSource, source_rows, "calendar_sources"),
        (CalendarSubscription, subscription_rows, "calendar_subscriptions"),
        (
            CalendarSubscriptionEvent,
            event_rows,
            "calendar_subscription_events",
        ),
    ):
        await _insert_batched(session, model, rows)
        counts[name] += len(rows)
    return counts


async def seed_one_history_account(
    session: AsyncSession,
    settings: Settings,
    profile: SeedProfile,
    *,
    account_index: int,
    seed: int,
    at: datetime,
    password_hash: str,
    quota_policy_id,
) -> HistoryAccountResult:
    owner_id, counts = await _seed_account_base(
        session,
        profile=profile,
        account_index=account_index,
        seed=seed,
        at=at,
        password_hash=password_hash,
        quota_policy_id=quota_policy_id,
    )
    task_counts, task_ids, task_titles = await _seed_tasks(
        session,
        profile=profile,
        owner_id=owner_id,
        account_index=account_index,
        seed=seed,
        at=at,
    )
    counts.update(task_counts)
    counts.update(
        await _seed_calendar_and_plan(
            session,
            profile=profile,
            owner_id=owner_id,
            task_ids=task_ids,
            task_titles=task_titles,
            account_index=account_index,
            seed=seed,
            at=at,
            calendar_series_count=profile.calendar_series_per_account,
        )
    )
    counts.update(
        await _seed_subscription_events(
            session,
            settings,
            profile,
            owner_id=owner_id,
            account_index=account_index,
            seed=seed,
            at=at,
        )
    )
    counts.update(
        await _seed_activity_history(
            session,
            profile=profile,
            owner_id=owner_id,
            task_ids=task_ids,
            task_titles=task_titles,
            account_index=account_index,
            seed=seed,
            at=at,
            focus_count=365,
            difficulty_count=180,
            review_count=365,
            memory_count=60,
        )
    )
    counts.update(
        await _seed_conversations_and_events(
            session,
            profile=profile,
            owner_id=owner_id,
            task_ids=task_ids,
            account_index=account_index,
            seed=seed,
            at=at,
            conversation_messages=profile.conversation_messages_per_account,
        )
    )
    return HistoryAccountResult(
        counts=dict(counts),
        credential=AccountCredential(
            email=(
                f"perf-{profile.name}-{seed}-{account_index:03d}"
                "@performance.example.com"
            ),
            password=SYNTHETIC_LOGIN_SECRET,
            dataset=profile.name,
        ),
    )


async def seed_history_accounts(
    database: Database,
    settings: Settings,
    profile: SeedProfile,
    *,
    seed: int,
    at: datetime,
    upstream_origin: str = "http://host.docker.internal:18081",
) -> SeedManifest:
    if at.tzinfo is None:
        raise ValueError("性能数据基准时间必须包含 UTC offset")
    globals_ = await _prepare_globals(
        database,
        settings,
        profile=profile,
        seed=seed,
        at=at,
        upstream_origin=upstream_origin,
    )
    counts: Counter[str] = Counter()
    credentials: list[AccountCredential] = []
    for account_index in range(profile.history_accounts):
        async with database.transaction() as session:
            result = await seed_one_history_account(
                session,
                settings,
                profile,
                account_index=account_index,
                seed=seed,
                at=at,
                password_hash=globals_.password_hash,
                quota_policy_id=globals_.quota_policy_id,
            )
        counts.update(result.counts)
        credentials.append(result.credential)

    async with database.transaction() as session:
        await session.execute(text("ANALYZE"))

    return SeedManifest(
        profile=profile.name,
        seed=seed,
        generated_at=at.isoformat(),
        accounts=profile.accounts,
        active_accounts=profile.active_accounts,
        counts=dict(sorted(counts.items())),
        credentials=tuple(credentials),
    )
