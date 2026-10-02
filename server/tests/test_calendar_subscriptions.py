import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import uuid4

import psycopg
import pytest

import itime.calendar.subscription_service as subscription_service
from itime.calendar.subscription_fetcher import (
    CalendarFetchResult,
    FetchValidators,
    SubscriptionFetchError,
)
from itime.calendar.subscription_parser import parse_subscription_ics
from itime.calendar.subscription_service import (
    apply_subscription_snapshot,
    claim_next_subscription,
)
from itime.calendar.subscription_worker import process_one
from itime.config import Settings
from itime.database import Database
from tests.conftest import isolated_database, migrate, psycopg_url
from tests.test_calendar_series import create_account, fixed_payload

pytestmark = pytest.mark.integration


def calendar(*events: str) -> bytes:
    return (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//iTime Subscription Test//EN\r\n"
        + "".join(events)
        + "END:VCALENDAR\r\n"
    ).encode()


def event(*lines: str) -> str:
    return "BEGIN:VEVENT\r\n" + "\r\n".join(lines) + "\r\nEND:VEVENT\r\n"


def daily_ics(name: str, start_date, *, uid: str = "daily-event") -> bytes:
    stamp = start_date.strftime("%Y%m%d")
    return calendar(
        event(
            f"UID:{uid}",
            f"SUMMARY:{name}",
            f"DTSTART:{stamp}T090000Z",
            f"DTEND:{stamp}T100000Z",
            "RRULE:FREQ=DAILY",
        )
    )


def result(
    content: bytes | None,
    *,
    status: int = 200,
    etag: str | None = '"v1"',
    final_url: str = "https://cdn.example/calendar.ics?token=secret",
) -> CalendarFetchResult:
    return CalendarFetchResult(
        status=status,
        final_url=final_url,
        etag=etag,
        last_modified="Wed, 21 Oct 2015 07:28:00 GMT",
        content_sha256="a" * 64 if content is not None else None,
        content=content,
        redirect_count=1,
    )


@dataclass
class FakeFetcher:
    outcomes: list[CalendarFetchResult | SubscriptionFetchError]
    calls: list[tuple[str, FetchValidators | None]] = field(default_factory=list)

    async def fetch(
        self,
        url: str,
        *,
        validators: FetchValidators | None = None,
    ) -> CalendarFetchResult:
        self.calls.append((url, validators))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, SubscriptionFetchError):
            raise outcome
        return outcome


@dataclass
class BlockingFetcher:
    outcome: CalendarFetchResult
    received: Event = field(default_factory=Event)
    release: Event = field(default_factory=Event)

    async def fetch(
        self,
        url: str,
        *,
        validators: FetchValidators | None = None,
    ) -> CalendarFetchResult:
        self.received.set()
        released = await asyncio.to_thread(self.release.wait, 10)
        assert released
        return self.outcome


def create_subscription(client, *, name: str = "课程订阅") -> dict:
    response = client.post(
        "/api/v1/calendar/subscriptions",
        json={
            "operation_id": str(uuid4()),
            "name": name,
            "url": "http://calendar.test/feed.ics?token=do-not-display",
            "refresh_interval_minutes": 60,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["subscription"]


def worker_settings(database_url: str) -> Settings:
    return Settings(database_url=database_url, environment="test")


async def run_worker(
    database_url: str,
    fetcher: FakeFetcher,
) -> bool:
    database = Database(worker_settings(database_url))
    try:
        return await process_one(
            database,
            worker_settings(database_url),
            fetcher=fetcher,
        )
    finally:
        await database.close()


def test_subscription_refresh_projection_304_failure_and_cancellation(
    client,
    database_url,
):
    create_account(client, database_url, time_zone="UTC")
    subscription = create_subscription(client)
    source_id = subscription["id"]
    assert subscription["url_display"] == "http://calendar.test/..."
    assert "token" not in str(subscription)
    with psycopg.connect(psycopg_url(database_url)) as connection:
        encrypted_url, url_hmac = connection.execute(
            "SELECT encrypted_url, url_hmac FROM calendar_subscriptions WHERE source_id=%s",
            (source_id,),
        ).fetchone()
        assert b"do-not-display" not in bytes(encrypted_url)
        assert len(url_hmac) == 64
    today = datetime.now(UTC).date()
    first_fetcher = FakeFetcher([result(daily_ics("Source title", today - timedelta(days=2)))])
    assert asyncio.run(run_worker(database_url, first_fetcher))
    detail = client.get(f"/api/v1/calendar/subscriptions/{source_id}")
    assert detail.status_code == 200, detail.text
    first_detail = detail.json()
    assert first_detail["subscription"]["refresh_status"] == "idle"
    assert first_detail["subscription"]["generation"] == 1
    assert first_detail["subscription"]["event_count"] == 1
    assert first_detail["refreshes"][0]["status"] == "succeeded"
    assert first_detail["refreshes"][0]["changed_count"] == 1

    occurrences = client.get(
        "/api/v1/calendar/occurrences",
        params={
            "start": (today - timedelta(days=2)).isoformat(),
            "end": (today + timedelta(days=3)).isoformat(),
        },
    )
    assert occurrences.status_code == 200, occurrences.text
    subscription_items = [
        item
        for item in occurrences.json()["items"]
        if item["source_type"] == "subscription"
    ]
    assert len(subscription_items) == 5
    assert {item["name"] for item in subscription_items} == {"Source title"}
    manual = client.post(
        "/api/v1/calendar/series",
        json=fixed_payload(
            name="Overlapping manual event",
            start_date=today.isoformat(),
            starts_at="09:30:00",
            ends_at="10:30:00",
            time_zone="UTC",
        ),
    )
    assert manual.status_code == 201, manual.text
    conflict = manual.json()["conflicts"]["items"][0]
    assert conflict["conflicting_series_id"] is None
    assert conflict["conflicting_subscription_event_id"] is not None

    queued = client.post(
        f"/api/v1/calendar/subscriptions/{source_id}/refresh",
        json={"operation_id": str(uuid4())},
    )
    assert queued.status_code == 200
    not_modified_fetcher = FakeFetcher([result(None, status=304, etag=None)])
    assert asyncio.run(run_worker(database_url, not_modified_fetcher))
    assert not_modified_fetcher.calls[0][1] == FetchValidators(
        final_url="https://cdn.example/calendar.ics?token=secret",
        etag='"v1"',
        last_modified="Wed, 21 Oct 2015 07:28:00 GMT",
    )
    after_304 = client.get(f"/api/v1/calendar/subscriptions/{source_id}").json()
    assert after_304["subscription"]["generation"] == 1
    assert after_304["refreshes"][0]["status"] == "not_modified"

    client.post(
        f"/api/v1/calendar/subscriptions/{source_id}/refresh",
        json={"operation_id": str(uuid4())},
    )
    failed_fetcher = FakeFetcher(
        [
            SubscriptionFetchError(
                "calendar_subscription_timeout",
                "订阅服务器响应超时，请稍后重试。",
                retryable=True,
            )
        ]
    )
    assert asyncio.run(run_worker(database_url, failed_fetcher))
    failed = client.get(f"/api/v1/calendar/subscriptions/{source_id}").json()
    assert failed["subscription"]["refresh_status"] == "error"
    assert failed["subscription"]["generation"] == 1
    assert failed["subscription"]["last_success_at"] is not None
    assert failed["refreshes"][0]["status"] == "failed"

    client.post(
        f"/api/v1/calendar/subscriptions/{source_id}/refresh",
        json={"operation_id": str(uuid4())},
    )
    assert asyncio.run(run_worker(database_url, FakeFetcher([result(calendar())])))
    cancelled_occurrences = client.get(
        "/api/v1/calendar/occurrences",
        params={
            "start": (today - timedelta(days=2)).isoformat(),
            "end": (today + timedelta(days=3)).isoformat(),
        },
    ).json()["items"]
    subscription_items = [
        item
        for item in cancelled_occurrences
        if item["source_type"] == "subscription"
    ]
    assert any(
        item["status"] == "scheduled"
        and datetime.fromisoformat(item["start_at"].replace("Z", "+00:00"))
        < datetime.now(UTC)
        for item in subscription_items
    )
    assert any(item["status"] == "source_cancelled" for item in subscription_items)
    assert all(
        not item["busy"]
        for item in subscription_items
        if item["status"] == "source_cancelled"
    )


def test_subscription_local_override_survives_source_change_and_can_reset(
    client,
    database_url,
):
    create_account(client, database_url, time_zone="UTC")
    subscription = create_subscription(client, name="Override source")
    source_id = subscription["id"]
    today = datetime.now(UTC).date()
    assert asyncio.run(
        run_worker(
            database_url,
            FakeFetcher([result(daily_ics("Source A", today))]),
        )
    )
    detail = client.get(f"/api/v1/calendar/subscriptions/{source_id}").json()
    event_row = detail["events"][0]
    override = client.put(
        f"/api/v1/calendar/subscriptions/{source_id}/events/{event_row['id']}/overrides",
        json={
            "operation_id": str(uuid4()),
            "expected_version": 0,
            "values": {"name": "Local title", "location": "Local room"},
            "reset_fields": [],
        },
    )
    assert override.status_code == 200, override.text
    assert override.json()["event"]["effective_fields"]["name"] == "Local title"
    override_version = override.json()["event"]["override_version"]

    client.post(
        f"/api/v1/calendar/subscriptions/{source_id}/refresh",
        json={"operation_id": str(uuid4())},
    )
    assert asyncio.run(
        run_worker(
            database_url,
            FakeFetcher([result(daily_ics("Source B", today), etag='"v2"')]),
        )
    )
    changed = client.get(f"/api/v1/calendar/subscriptions/{source_id}").json()
    changed_event = changed["events"][0]
    assert changed_event["source_fields"]["name"] == "Source B"
    assert changed_event["effective_fields"]["name"] == "Local title"
    assert changed_event["differences"] == [
        {
            "field": "name",
            "source_at_override": "Source A",
            "source_current": "Source B",
            "local_value": "Local title",
        }
    ]
    occurrences = client.get(
        "/api/v1/calendar/occurrences",
        params={
            "start": today.isoformat(),
            "end": (today + timedelta(days=2)).isoformat(),
        },
    ).json()["items"]
    assert occurrences[0]["name"] == "Local title"

    reset = client.put(
        f"/api/v1/calendar/subscriptions/{source_id}/events/{event_row['id']}/overrides",
        json={
            "operation_id": str(uuid4()),
            "expected_version": override_version,
            "values": {},
            "reset_fields": ["name", "location"],
        },
    )
    assert reset.status_code == 200, reset.text
    assert reset.json()["event"]["effective_fields"]["name"] == "Source B"
    assert reset.json()["event"]["override_version"] == 0


def test_subscription_revision_applies_rid_and_preserves_invalid_known_uid(
    client,
    database_url,
):
    create_account(client, database_url, time_zone="UTC")
    source_id = create_subscription(client, name="Revision source")["id"]
    today = datetime.now(UTC).date()
    day_one = today.strftime("%Y%m%d")
    day_two = (today + timedelta(days=1)).strftime("%Y%m%d")
    day_three = (today + timedelta(days=2)).strftime("%Y%m%d")
    initial = calendar(
        event(
            "UID:class",
            "SUMMARY:Class",
            f"DTSTART:{day_one}T090000Z",
            f"DTEND:{day_one}T100000Z",
            "RRULE:FREQ=DAILY",
        ),
        event(
            "UID:known-invalid-next",
            "SUMMARY:Keep old",
            f"DTSTART:{day_two}T140000Z",
            f"DTEND:{day_two}T150000Z",
        ),
        event(
            "UID:removed",
            "SUMMARY:Removed future",
            f"DTSTART:{day_two}T160000Z",
            f"DTEND:{day_two}T170000Z",
        ),
    )
    assert asyncio.run(run_worker(database_url, FakeFetcher([result(initial)])))

    client.post(
        f"/api/v1/calendar/subscriptions/{source_id}/refresh",
        json={"operation_id": str(uuid4())},
    )
    revision = calendar(
        event(
            "UID:class",
            "SUMMARY:Class",
            f"DTSTART:{day_one}T090000Z",
            f"DTEND:{day_one}T100000Z",
            "RRULE:FREQ=DAILY",
        ),
        event(
            "UID:class",
            f"RECURRENCE-ID:{day_two}T090000Z",
            f"DTSTART:{day_two}T110000Z",
            f"DTEND:{day_two}T120000Z",
            "SUMMARY:Moved class",
        ),
        event(
            "UID:class",
            f"RECURRENCE-ID:{day_three}T090000Z",
            "STATUS:CANCELLED",
        ),
        event(
            "UID:known-invalid-next",
            "SUMMARY:Broken update",
            f"DTSTART;TZID=Unknown/Zone:{day_two}T150000",
            f"DTEND;TZID=Unknown/Zone:{day_two}T160000",
        ),
    )
    assert asyncio.run(
        run_worker(database_url, FakeFetcher([result(revision, etag='"v2"')]))
    )

    detail = client.get(f"/api/v1/calendar/subscriptions/{source_id}").json()
    events = {item["uid"]: item for item in detail["events"] if not item["recurrence_id"]}
    assert events["known-invalid-next"]["parse_status"] == "stale_error"
    assert events["known-invalid-next"]["source_fields"]["name"] == "Keep old"
    assert events["removed"]["source_status"] == "cancelled"
    assert detail["refreshes"][0]["error_count"] == 1
    with psycopg.connect(psycopg_url(database_url)) as connection:
        first_cancelled_at = connection.execute(
            """
            SELECT cancelled_at
            FROM calendar_subscription_events
            WHERE source_id=%s AND uid='removed' AND recurrence_id=''
            """,
            (source_id,),
        ).fetchone()[0]
        first_rid_cancelled_at = connection.execute(
            """
            SELECT cancelled_at
            FROM calendar_subscription_events
            WHERE source_id=%s AND uid='class' AND recurrence_id=%s
            """,
            (
                source_id,
                f"INSTANT:{day_three[:4]}-{day_three[4:6]}-{day_three[6:]}T09:00:00Z",
            ),
        ).fetchone()[0]

    occurrences = client.get(
        "/api/v1/calendar/occurrences",
        params={
            "start": today.isoformat(),
            "end": (today + timedelta(days=4)).isoformat(),
        },
    )
    assert occurrences.status_code == 200, occurrences.text
    items = occurrences.json()["items"]
    moved = next(item for item in items if item["name"] == "Moved class")
    assert moved["local_start_time"] == "11:00:00"
    cancelled = next(
        item
        for item in items
        if item["source_occurrence_key"] == f"INSTANT:{day_three[:4]}-{day_three[4:6]}-"
        f"{day_three[6:]}T09:00:00Z"
    )
    assert cancelled["status"] == "source_cancelled"
    assert not cancelled["busy"]
    assert any(item["name"] == "Keep old" for item in items)
    assert any(
        item["name"] == "Removed future" and item["status"] == "source_cancelled"
        for item in items
    )

    client.post(
        f"/api/v1/calendar/subscriptions/{source_id}/refresh",
        json={"operation_id": str(uuid4())},
    )
    assert asyncio.run(
        run_worker(
            database_url,
            FakeFetcher([result(revision, etag='"v2-again"')]),
        )
    )
    with psycopg.connect(psycopg_url(database_url)) as connection:
        repeated_rid_cancelled_at = connection.execute(
            """
            SELECT cancelled_at
            FROM calendar_subscription_events
            WHERE source_id=%s AND uid='class' AND recurrence_id=%s
            """,
            (
                source_id,
                f"INSTANT:{day_three[:4]}-{day_three[4:6]}-{day_three[6:]}T09:00:00Z",
            ),
        ).fetchone()[0]
    assert repeated_rid_cancelled_at == first_rid_cancelled_at

    client.post(
        f"/api/v1/calendar/subscriptions/{source_id}/refresh",
        json={"operation_id": str(uuid4())},
    )
    without_detached = calendar(
        event(
            "UID:class",
            "SUMMARY:Class",
            f"DTSTART:{day_one}T090000Z",
            f"DTEND:{day_one}T100000Z",
            "RRULE:FREQ=DAILY",
        ),
        event(
            "UID:known-invalid-next",
            "SUMMARY:Broken update",
            f"DTSTART;TZID=Unknown/Zone:{day_two}T150000",
            f"DTEND;TZID=Unknown/Zone:{day_two}T160000",
        ),
    )
    assert asyncio.run(
        run_worker(
            database_url,
            FakeFetcher([result(without_detached, etag='"v3"')]),
        )
    )
    restored = client.get(
        "/api/v1/calendar/occurrences",
        params={
            "start": today.isoformat(),
            "end": (today + timedelta(days=4)).isoformat(),
        },
    ).json()["items"]
    class_items = [item for item in restored if item["name"] == "Class"]
    assert not any(item["name"] == "Moved class" for item in restored)
    assert {
        (item["local_start_date"], item["local_start_time"], item["status"])
        for item in class_items
        if item["local_start_date"]
        in {
            (today + timedelta(days=1)).isoformat(),
            (today + timedelta(days=2)).isoformat(),
        }
    } == {
        ((today + timedelta(days=1)).isoformat(), "09:00:00", "scheduled"),
        ((today + timedelta(days=2)).isoformat(), "09:00:00", "scheduled"),
    }
    with psycopg.connect(psycopg_url(database_url)) as connection:
        repeated_cancelled_at = connection.execute(
            """
            SELECT cancelled_at
            FROM calendar_subscription_events
            WHERE source_id=%s AND uid='removed' AND recurrence_id=''
            """,
            (source_id,),
        ).fetchone()[0]
    assert repeated_cancelled_at == first_cancelled_at


def test_subscription_all_day_duration_uses_local_calendar_days(
    client,
    database_url,
):
    create_account(client, database_url, time_zone="America/New_York")
    create_subscription(client, name="All day source")
    all_day = calendar(
        event(
            "UID:dst-day",
            "SUMMARY:DST day",
            "DTSTART;VALUE=DATE:20261101",
            "DTEND;VALUE=DATE:20261102",
        )
    )
    assert asyncio.run(run_worker(database_url, FakeFetcher([result(all_day)])))

    occurrences = client.get(
        "/api/v1/calendar/occurrences",
        params={"start": "2026-11-01", "end": "2026-11-02"},
    )
    assert occurrences.status_code == 200, occurrences.text
    item = next(
        value
        for value in occurrences.json()["items"]
        if value["source_type"] == "subscription"
    )
    start_at = datetime.fromisoformat(item["start_at"].replace("Z", "+00:00"))
    end_at = datetime.fromisoformat(item["end_at"].replace("Z", "+00:00"))
    assert end_at - start_at == timedelta(hours=25)
    assert item["local_end_date"] == "2026-11-02"


def test_subscription_network_wait_does_not_hold_database_transaction(
    client,
    database_url,
):
    create_account(client, database_url, time_zone="UTC")
    create_subscription(client, name="No idle transaction")
    fetcher = BlockingFetcher(
        result(daily_ics("Network wait", datetime.now(UTC).date()))
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, run_worker(database_url, fetcher))
        assert fetcher.received.wait(timeout=5)
        with psycopg.connect(psycopg_url(database_url)) as connection:
            idle_transactions = connection.execute(
                """
                SELECT count(*)
                FROM pg_stat_activity
                WHERE datname = current_database()
                  AND pid <> pg_backend_pid()
                  AND state = 'idle in transaction'
                """
            ).fetchone()[0]
        fetcher.release.set()
        assert future.result(timeout=10)
    assert idle_transactions == 0


def test_subscription_account_event_limit_fails_without_partial_snapshot(
    client,
    database_url,
    monkeypatch,
):
    create_account(client, database_url, time_zone="UTC")
    source_id = create_subscription(client, name="Bounded source")["id"]
    today = datetime.now(UTC).date()
    stamp = today.strftime("%Y%m%d")
    oversized_for_test = calendar(
        event(
            "UID:first",
            "SUMMARY:First",
            f"DTSTART:{stamp}T090000Z",
            f"DTEND:{stamp}T100000Z",
        ),
        event(
            "UID:second",
            "SUMMARY:Second",
            f"DTSTART:{stamp}T110000Z",
            f"DTEND:{stamp}T120000Z",
        ),
    )
    monkeypatch.setattr(
        subscription_service,
        "MAX_SUBSCRIPTION_EVENTS_PER_OWNER",
        1,
    )

    assert asyncio.run(
        run_worker(database_url, FakeFetcher([result(oversized_for_test)]))
    )

    detail = client.get(f"/api/v1/calendar/subscriptions/{source_id}").json()
    assert detail["subscription"]["refresh_status"] == "error"
    assert detail["subscription"]["generation"] == 0
    assert detail["subscription"]["event_count"] == 0
    assert detail["subscription"]["last_error"]["code"] == (
        "calendar_subscription_account_event_limit"
    )


async def exercise_claim_and_stale_result(
    database_url: str,
    source_id: str,
):
    settings = worker_settings(database_url)
    database = Database(settings)
    try:
        first, second = await asyncio.gather(
            claim_next_subscription(
                database,
                lease_seconds=settings.calendar_subscription_lease_seconds,
            ),
            claim_next_subscription(
                database,
                lease_seconds=settings.calendar_subscription_lease_seconds,
            ),
        )
        claims = [claim for claim in (first, second) if claim is not None]
        assert len(claims) == 1
        assert str(claims[0].source_id) == source_id
        return claims[0]
    finally:
        await database.close()


async def apply_stale_result(database_url: str, claim, snapshot) -> bool:
    settings = worker_settings(database_url)
    database = Database(settings)
    try:
        return await apply_subscription_snapshot(
            database,
            settings,
            claim,
            result(daily_ics("Stale", datetime.now(UTC).date())),
            snapshot,
        )
    finally:
        await database.close()


def test_url_update_supersedes_running_refresh_and_pause_resume(
    client,
    database_url,
):
    create_account(client, database_url, time_zone="UTC")
    subscription = create_subscription(client, name="Mutable")
    source_id = subscription["id"]
    claim = asyncio.run(
        exercise_claim_and_stale_result(database_url, source_id)
    )
    updated = client.put(
        f"/api/v1/calendar/subscriptions/{source_id}",
        json={
            "operation_id": str(uuid4()),
            "expected_version": subscription["version"],
            "name": "Mutable",
            "url": "http://calendar.test/new.ics?token=new-secret",
            "refresh_interval_minutes": 120,
        },
    )
    assert updated.status_code == 200, updated.text
    updated_body = updated.json()["subscription"]
    assert updated_body["refresh_status"] == "queued"
    assert updated_body["url_display"] == "http://calendar.test/..."
    stale_snapshot = parse_subscription_ics(
        daily_ics("Stale", datetime.now(UTC).date()),
        default_time_zone="UTC",
    )
    assert not asyncio.run(apply_stale_result(database_url, claim, stale_snapshot))

    detail = client.get(f"/api/v1/calendar/subscriptions/{source_id}").json()
    assert detail["subscription"]["event_count"] == 0
    assert detail["refreshes"][0]["status"] == "stale"
    paused = client.post(
        f"/api/v1/calendar/subscriptions/{source_id}/pause",
        json={
            "operation_id": str(uuid4()),
            "expected_version": detail["subscription"]["version"],
        },
    )
    assert paused.status_code == 200, paused.text
    assert paused.json()["subscription"]["lifecycle_status"] == "paused"
    assert (
        client.post(
            f"/api/v1/calendar/subscriptions/{source_id}/refresh",
            json={"operation_id": str(uuid4())},
        ).json()["error"]["code"]
        == "calendar_subscription_paused"
    )
    paused_update = client.put(
        f"/api/v1/calendar/subscriptions/{source_id}",
        json={
            "operation_id": str(uuid4()),
            "expected_version": paused.json()["subscription"]["version"],
            "name": "Paused URL update",
            "url": "http://calendar.test/private/path-token.ics?token=hidden",
            "refresh_interval_minutes": 120,
        },
    )
    assert paused_update.status_code == 200, paused_update.text
    assert paused_update.json()["subscription"]["refresh_status"] == "idle"
    assert "path-token" not in str(paused_update.json())
    resumed = client.post(
        f"/api/v1/calendar/subscriptions/{source_id}/resume",
        json={
            "operation_id": str(uuid4()),
            "expected_version": paused_update.json()["subscription"]["version"],
        },
    )
    assert resumed.status_code == 200
    assert resumed.json()["subscription"]["refresh_status"] == "queued"
    renamed = client.put(
        f"/api/v1/calendar/subscriptions/{source_id}",
        json={
            "operation_id": str(uuid4()),
            "expected_version": resumed.json()["subscription"]["version"],
            "name": "Renamed without URL",
            "refresh_interval_minutes": 180,
        },
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["subscription"]["name"] == "Renamed without URL"
    assert renamed.json()["subscription"]["url_display"] == "http://calendar.test/..."


def test_calendar_subscription_migration_roundtrip(admin_url):
    with isolated_database(admin_url) as database_url:
        migrate(database_url, "downgrade", "0014_calendar_screenshot_imports")
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT to_regclass('calendar_subscriptions')"
            ).fetchone()[0] is None
        migrate(database_url, "upgrade", "head")
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT to_regclass('calendar_subscriptions')"
            ).fetchone()[0] == "calendar_subscriptions"
