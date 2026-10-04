import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4

import psycopg
import pytest

from tests.conftest import psycopg_url
from tests.test_calendar_series import (
    browser,
    create_account,
    create_series,
    login_account,
)
from tests.test_calendar_subscriptions import (
    FakeFetcher,
    calendar,
    create_subscription,
    daily_ics,
    event,
    result,
    run_worker,
)

pytestmark = pytest.mark.integration


def occurrences(client, start, end):
    response = client.get(
        "/api/v1/calendar/occurrences",
        params={"start": start.isoformat(), "end": end.isoformat()},
    )
    assert response.status_code == 200, response.text
    return [
        item
        for item in response.json()["items"]
        if item["source_type"] == "subscription"
    ]


def exception_fields(action: str, **changes):
    fields = {
        "action": action,
        "moved_all_day": None,
        "moved_start_date": None,
        "moved_end_date": None,
        "moved_starts_at": None,
        "moved_ends_at": None,
        "moved_time_zone": None,
        "moved_ambiguous_utc_offset_minutes": None,
        "override_name": None,
        "override_description": None,
        "override_location": None,
        "override_busy": None,
    }
    fields.update(changes)
    return fields


def create_exception(client, occurrence, action: str, **changes):
    return client.post(
        f"/api/v1/calendar/subscriptions/{occurrence['source_id']}/exceptions",
        json={
            "operation_id": str(uuid4()),
            "expected_source_version": occurrence["source_version"],
            "master_event_id": occurrence["subscription_master_event_id"],
            "source_occurrence_key": occurrence["source_occurrence_key"],
            **exception_fields(action, **changes),
        },
    )


def update_exception(client, occurrence, action: str, **changes):
    return client.put(
        f"/api/v1/calendar/subscriptions/{occurrence['source_id']}"
        f"/exceptions/{occurrence['exception_id']}",
        json={
            "operation_id": str(uuid4()),
            "expected_source_version": occurrence["source_version"],
            "expected_version": occurrence["exception_version"],
            **exception_fields(action, **changes),
        },
    )


def delete_exception(client, occurrence, *, operation_id=None):
    return client.request(
        "DELETE",
        f"/api/v1/calendar/subscriptions/{occurrence['source_id']}"
        f"/exceptions/{occurrence['exception_id']}",
        json={
            "operation_id": str(operation_id or uuid4()),
            "expected_source_version": occurrence["source_version"],
            "expected_version": occurrence["exception_version"],
        },
    )


def test_subscription_occurrence_cancel_move_reset_and_conflicts(
    client,
    database_url,
):
    _email, owner_id = create_account(client, database_url, time_zone="UTC")
    source_id = create_subscription(client, name="单次例外来源")["id"]
    start = datetime.now(UTC).date() + timedelta(days=1)
    assert asyncio.run(
        run_worker(
            database_url,
            FakeFetcher([result(daily_ics("来源课程", start))]),
        )
    )
    initial = occurrences(client, start, start + timedelta(days=4))
    target = initial[1]
    assert target["subscription_master_event_id"] == target["subscription_event_id"]
    assert target["source_version"] == 2
    assert target["source_status"] == "scheduled"
    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            "DELETE FROM change_events WHERE owner_id=%s",
            (owner_id,),
        )

    cancel_operation = uuid4()
    cancelled_response = client.post(
        f"/api/v1/calendar/subscriptions/{source_id}/exceptions",
        json={
            "operation_id": str(cancel_operation),
            "expected_source_version": target["source_version"],
            "master_event_id": target["subscription_master_event_id"],
            "source_occurrence_key": target["source_occurrence_key"],
            **exception_fields("cancelled"),
        },
    )
    assert cancelled_response.status_code == 201, cancelled_response.text
    cancelled_body = cancelled_response.json()
    assert cancelled_body["occurrence"]["status"] == "cancelled"
    assert cancelled_body["occurrence"]["source_status"] == "scheduled"
    assert cancelled_body["occurrence"]["busy"] is False
    assert cancelled_body["occurrence"]["exception_action"] == "cancelled"
    assert cancelled_body["source_version"] == 3
    assert client.post(
        f"/api/v1/calendar/subscriptions/{source_id}/exceptions",
        json={
            "operation_id": str(cancel_operation),
            "expected_source_version": target["source_version"],
            "master_event_id": target["subscription_master_event_id"],
            "source_occurrence_key": target["source_occurrence_key"],
            **exception_fields("cancelled"),
        },
    ).json() == cancelled_body

    current = next(
        item
        for item in occurrences(client, start, start + timedelta(days=4))
        if item["source_occurrence_key"] == target["source_occurrence_key"]
    )
    duplicate = create_exception(client, current, "cancelled")
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == (
        "calendar_subscription_exception_exists"
    )
    no_change = update_exception(client, current, "cancelled")
    assert no_change.status_code == 200, no_change.text
    assert no_change.json()["source_version"] == current["source_version"]
    assert no_change.json()["exception"]["version"] == current["exception_version"]

    moved_date = start + timedelta(days=5)
    create_series(
        client,
        name="移动后的冲突",
        start_date=moved_date.isoformat(),
        starts_at="11:30:00",
        ends_at="12:30:00",
        time_zone="UTC",
    )
    moved_response = update_exception(
        client,
        current,
        "moved",
        moved_all_day=False,
        moved_start_date=moved_date.isoformat(),
        moved_end_date=moved_date.isoformat(),
        moved_starts_at="11:00:00",
        moved_ends_at="12:00:00",
        moved_time_zone="UTC",
    )
    assert moved_response.status_code == 200, moved_response.text
    moved = moved_response.json()["occurrence"]
    assert moved["local_start_date"] == moved_date.isoformat()
    assert moved["local_start_time"] == "11:00:00"
    assert moved["status"] == "scheduled"
    assert moved["exception_action"] == "moved"
    assert len(moved_response.json()["conflicts"]["items"]) == 1
    assert not any(
        item["source_occurrence_key"] == target["source_occurrence_key"]
        for item in occurrences(client, start, start + timedelta(days=4))
    )
    moved_in = occurrences(
        client,
        moved_date,
        moved_date + timedelta(days=1),
    )
    moved_in_target = next(
        item
        for item in moved_in
        if item["source_occurrence_key"] == target["source_occurrence_key"]
    )
    reset_operation = uuid4()
    reset = delete_exception(
        client,
        moved_in_target,
        operation_id=reset_operation,
    )
    assert reset.status_code == 200, reset.text
    assert reset.json()["occurrence"]["exception_id"] is None
    assert reset.json()["occurrence"]["local_start_date"] == (
        start + timedelta(days=1)
    ).isoformat()
    replay = client.request(
        "DELETE",
        f"/api/v1/calendar/subscriptions/{source_id}"
        f"/exceptions/{moved_in_target['exception_id']}",
        json={
            "operation_id": str(reset_operation),
            "expected_source_version": moved_in_target["source_version"],
            "expected_version": moved_in_target["exception_version"],
        },
    )
    assert replay.status_code == 200
    assert replay.json() == reset.json()

    missing = client.post(
        f"/api/v1/calendar/subscriptions/{source_id}/exceptions",
        json={
            "operation_id": str(uuid4()),
            "expected_source_version": reset.json()["source_version"],
            "master_event_id": target["subscription_master_event_id"],
            "source_occurrence_key": "INSTANT:2020-01-01T09:00:00Z",
            **exception_fields("cancelled"),
        },
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == (
        "calendar_subscription_occurrence_not_found"
    )

    with psycopg.connect(psycopg_url(database_url)) as connection:
        assert connection.execute(
            """
            SELECT count(*) FROM calendar_subscription_exceptions
            WHERE owner_id=%s
            """,
            (owner_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            """
            SELECT entity_type, entity_id, action, entity_version
            FROM change_events
            WHERE owner_id=%s AND entity_type='calendar_source'
            ORDER BY sequence
            """,
            (owner_id,),
        ).fetchall() == [
            ("calendar_source", UUID(source_id), "updated", 3),
            ("calendar_source", UUID(source_id), "updated", 4),
            ("calendar_source", UUID(source_id), "updated", 5),
        ]


def test_subscription_exception_survives_rid_change_and_source_cancellation(
    client,
    database_url,
):
    create_account(client, database_url, time_zone="UTC")
    source_id = create_subscription(client, name="刷新保留例外")["id"]
    start = datetime.now(UTC).date() + timedelta(days=1)
    day_one = start.strftime("%Y%m%d")
    day_two = (start + timedelta(days=1)).strftime("%Y%m%d")
    initial = calendar(
        event(
            "UID:class",
            "SUMMARY:来源课程",
            f"DTSTART:{day_one}T090000Z",
            f"DTEND:{day_one}T100000Z",
            "RRULE:FREQ=DAILY",
        )
    )
    assert asyncio.run(
        run_worker(database_url, FakeFetcher([result(initial)]))
    )
    target = occurrences(client, start, start + timedelta(days=3))[1]
    overridden_response = create_exception(
        client,
        target,
        "overridden",
        override_name="我的单次名称",
        override_description="只影响这一次",
        override_location="自习室",
        override_busy=False,
    )
    assert overridden_response.status_code == 201, overridden_response.text
    overridden = overridden_response.json()["occurrence"]
    assert overridden["name"] == "我的单次名称"
    assert overridden["busy"] is False

    queued = client.post(
        f"/api/v1/calendar/subscriptions/{source_id}/refresh",
        json={"operation_id": str(uuid4())},
    )
    assert queued.status_code == 200
    changed = calendar(
        event(
            "UID:class",
            "SUMMARY:来源课程已更新",
            f"DTSTART:{day_one}T090000Z",
            f"DTEND:{day_one}T100000Z",
            "RRULE:FREQ=DAILY",
        ),
        event(
            "UID:class",
            f"RECURRENCE-ID:{day_two}T090000Z",
            f"DTSTART:{day_two}T130000Z",
            f"DTEND:{day_two}T140000Z",
            "SUMMARY:来源单次移动",
        ),
    )
    assert asyncio.run(
        run_worker(
            database_url,
            FakeFetcher([result(changed, etag='"v2"')]),
        )
    )
    after_refresh = next(
        item
        for item in occurrences(client, start, start + timedelta(days=3))
        if item["source_occurrence_key"] == target["source_occurrence_key"]
    )
    assert after_refresh["name"] == "我的单次名称"
    assert after_refresh["local_start_time"] == "13:00:00"
    assert after_refresh["busy"] is False
    assert after_refresh["exception_action"] == "overridden"
    assert (
        after_refresh["subscription_event_id"]
        != after_refresh["subscription_master_event_id"]
    )

    moved_response = update_exception(
        client,
        after_refresh,
        "moved",
        moved_all_day=False,
        moved_start_date=(start + timedelta(days=4)).isoformat(),
        moved_end_date=(start + timedelta(days=4)).isoformat(),
        moved_starts_at="15:00:00",
        moved_ends_at="16:00:00",
        moved_time_zone="UTC",
    )
    assert moved_response.status_code == 200, moved_response.text

    queued = client.post(
        f"/api/v1/calendar/subscriptions/{source_id}/refresh",
        json={"operation_id": str(uuid4())},
    )
    assert queued.status_code == 200
    assert asyncio.run(
        run_worker(database_url, FakeFetcher([result(calendar(), etag='"v3"')]))
    )
    retained = next(
        item
        for item in occurrences(
            client,
            start + timedelta(days=4),
            start + timedelta(days=5),
        )
        if item["source_occurrence_key"] == target["source_occurrence_key"]
    )
    assert retained["status"] == "scheduled"
    assert retained["source_status"] == "source_cancelled"
    assert retained["local_start_time"] == "15:00:00"
    assert retained["exception_action"] == "moved"


def test_subscription_exception_all_day_and_dst_validation(
    client,
    database_url,
):
    create_account(
        client,
        database_url,
        time_zone="America/New_York",
    )
    create_subscription(client, name="全天例外")
    source = calendar(
        event(
            "UID:all-day",
            "SUMMARY:两天活动",
            "DTSTART;VALUE=DATE:20261005",
            "DTEND;VALUE=DATE:20261007",
        ),
        event(
            "UID:timed",
            "SUMMARY:DST 课程",
            "DTSTART:20261031T130000Z",
            "DTEND:20261031T140000Z",
        ),
    )
    assert asyncio.run(
        run_worker(database_url, FakeFetcher([result(source)]))
    )
    items = occurrences(
        client,
        datetime(2026, 10, 1, tzinfo=UTC).date(),
        datetime(2026, 11, 2, tzinfo=UTC).date(),
    )
    all_day = next(item for item in items if item["name"] == "两天活动")
    moved = create_exception(
        client,
        all_day,
        "moved",
        moved_all_day=True,
        moved_start_date="2026-10-10",
        moved_end_date="2026-10-12",
        moved_time_zone="America/New_York",
    )
    assert moved.status_code == 201, moved.text
    assert moved.json()["occurrence"]["all_day"] is True
    assert moved.json()["occurrence"]["local_start_date"] == "2026-10-10"
    assert moved.json()["occurrence"]["local_end_date"] == "2026-10-12"

    timed = next(item for item in items if item["name"] == "DST 课程")
    stale_source = create_exception(
        client,
        timed,
        "moved",
        moved_all_day=False,
        moved_start_date="2026-03-08",
        moved_end_date="2026-03-08",
        moved_starts_at="02:30:00",
        moved_ends_at="03:30:00",
        moved_time_zone="America/New_York",
    )
    assert stale_source.status_code == 409
    assert stale_source.json()["error"]["code"] == (
        "calendar_subscription_version_conflict"
    )
    current_timed = next(
        item
        for item in occurrences(
            client,
            datetime(2026, 10, 1, tzinfo=UTC).date(),
            datetime(2026, 11, 2, tzinfo=UTC).date(),
        )
        if item["name"] == "DST 课程"
    )
    invalid = create_exception(
        client,
        current_timed,
        "moved",
        moved_all_day=False,
        moved_start_date="2026-03-08",
        moved_end_date="2026-03-08",
        moved_starts_at="02:30:00",
        moved_ends_at="03:30:00",
        moved_time_zone="America/New_York",
    )
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "invalid_local_time"

    ambiguous = create_exception(
        client,
        current_timed,
        "moved",
        moved_all_day=False,
        moved_start_date="2026-11-01",
        moved_end_date="2026-11-01",
        moved_starts_at="01:30:00",
        moved_ends_at="01:45:00",
        moved_time_zone="America/New_York",
    )
    assert ambiguous.status_code == 400
    assert ambiguous.json()["error"]["code"] == "ambiguous_local_time"
    assert ambiguous.json()["error"]["details"][
        "candidate_offsets_minutes"
    ] == [-240, -300]


def test_subscription_exception_create_is_serialized_and_owner_scoped(
    client,
    database_url,
    live_server,
):
    email, _owner_id = create_account(client, database_url, time_zone="UTC")
    create_subscription(client, name="并发例外")
    start = datetime.now(UTC).date() + timedelta(days=1)
    assert asyncio.run(
        run_worker(
            database_url,
            FakeFetcher([result(daily_ics("并发课程", start))]),
        )
    )
    target = occurrences(client, start, start + timedelta(days=2))[0]

    with browser(live_server) as same_user, browser(live_server) as other_user:
        login_account(same_user, email)
        create_account(other_user, database_url, time_zone="UTC")
        foreign = create_exception(other_user, target, "cancelled")
        assert foreign.status_code == 404
        assert foreign.json()["error"]["code"] == "calendar_subscription_not_found"

        barrier = Barrier(2)

        def submit(current_client):
            barrier.wait()
            return create_exception(current_client, target, "cancelled")

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = [
                future.result()
                for future in (
                    pool.submit(submit, client),
                    pool.submit(submit, same_user),
                )
            ]
    assert sorted(response.status_code for response in responses) == [201, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["error"]["code"] in {
        "calendar_subscription_exception_exists",
        "calendar_subscription_version_conflict",
    }
    with psycopg.connect(psycopg_url(database_url)) as connection:
        assert connection.execute(
            """
            SELECT count(*) FROM calendar_subscription_exceptions
            WHERE master_event_id=%s
            """,
            (target["subscription_master_event_id"],),
        ).fetchone()[0] == 1
