from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest

from tests.conftest import isolated_database, migrate, psycopg_url
from tests.test_calendar_series import (
    address,
    browser,
    create_account,
    create_series,
    fixed_payload,
    login_account,
    recurrence,
)

pytestmark = pytest.mark.integration


def exception_payload(
    *,
    expected_series_version: int,
    occurrence_key: str,
    action: str,
    operation_id=None,
    **changes,
) -> dict:
    payload = {
        "operation_id": str(operation_id or uuid4()),
        "expected_series_version": expected_series_version,
        "occurrence_key": occurrence_key,
        "action": action,
        "moved_start_date": None,
        "moved_starts_at": None,
        "moved_ends_at": None,
        "moved_end_day_offset": None,
        "moved_ambiguous_utc_offset_minutes": None,
        "override_name": None,
        "override_description": None,
        "override_location": None,
        "override_busy": None,
    }
    payload.update(changes)
    return payload


def exception_update_payload(
    *,
    expected_series_version: int,
    expected_version: int,
    action: str,
    operation_id=None,
    **changes,
) -> dict:
    payload = exception_payload(
        expected_series_version=expected_series_version,
        occurrence_key="unused-on-update",
        action=action,
        operation_id=operation_id,
        **changes,
    )
    payload.pop("occurrence_key")
    payload["expected_version"] = expected_version
    return payload


def get_occurrences(client, start: str, end: str) -> list[dict]:
    response = client.get(
        "/api/v1/calendar/occurrences",
        params={"start": start, "end": end},
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


def test_cancel_move_override_and_exception_updates(client, database_url):
    _, user_id = create_account(client, database_url)
    series = create_series(
        client,
        name="每日站会",
        start_date="2026-10-05",
        starts_at="09:00:00",
        ends_at="10:00:00",
        recurrence=recurrence("daily", count=3),
    )["series"]

    cancel_operation = uuid4()
    cancel_payload = exception_payload(
        expected_series_version=1,
        occurrence_key="2026-10-06T09:00:00",
        action="cancelled",
        operation_id=cancel_operation,
    )
    cancelled = client.post(
        f"/api/v1/calendar/series/{series['id']}/exceptions",
        json=cancel_payload,
    )
    assert cancelled.status_code == 201, cancelled.text
    assert cancelled.json()["series"]["version"] == 2
    cancel_exception = cancelled.json()["exception"]
    assert cancel_exception["version"] == 1
    replayed = client.post(
        f"/api/v1/calendar/series/{series['id']}/exceptions",
        json=cancel_payload,
    )
    assert replayed.json() == cancelled.json()

    duplicate = client.post(
        f"/api/v1/calendar/series/{series['id']}/exceptions",
        json=exception_payload(
            expected_series_version=2,
            occurrence_key="2026-10-06T09:00:00",
            action="overridden",
            override_name="重复例外",
        ),
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "calendar_exception_exists"

    moved = client.post(
        f"/api/v1/calendar/series/{series['id']}/exceptions",
        json=exception_payload(
            expected_series_version=2,
            occurrence_key="2026-10-07T09:00:00",
            action="moved",
            moved_start_date="2026-10-08",
            moved_starts_at="11:00:00",
            moved_ends_at="12:00:00",
            moved_end_day_offset=0,
        ),
    )
    assert moved.status_code == 201, moved.text
    assert moved.json()["series"]["version"] == 3
    moved_exception = moved.json()["exception"]

    overridden = client.post(
        f"/api/v1/calendar/series/{series['id']}/exceptions",
        json=exception_payload(
            expected_series_version=3,
            occurrence_key="2026-10-05T09:00:00",
            action="overridden",
            override_name="异步站会",
            override_description="改为文档同步",
            override_location="线上",
            override_busy=False,
        ),
    )
    assert overridden.status_code == 201, overridden.text
    assert overridden.json()["series"]["version"] == 4

    items = get_occurrences(client, "2026-10-05", "2026-10-09")
    by_key = {item["occurrence_key"]: item for item in items}
    assert by_key["2026-10-05T09:00:00"]["name"] == "异步站会"
    assert by_key["2026-10-05T09:00:00"]["busy"] is False
    assert by_key["2026-10-05T09:00:00"]["exception_action"] == "overridden"
    assert by_key["2026-10-06T09:00:00"]["status"] == "cancelled"
    assert by_key["2026-10-06T09:00:00"]["busy"] is False
    assert by_key["2026-10-07T09:00:00"]["local_start_date"] == "2026-10-08"
    assert by_key["2026-10-07T09:00:00"]["local_start_time"] == "11:00:00"
    assert by_key["2026-10-07T09:00:00"]["exception_action"] == "moved"

    conflicting = create_series(
        client,
        name="调课后的冲突",
        start_date="2026-10-08",
        starts_at="11:30:00",
        ends_at="12:30:00",
    )
    assert conflicting["conflicts"]["items"][0]["conflicting_series_id"] == series["id"]

    changed_to_cancel = client.put(
        f"/api/v1/calendar/series/{series['id']}/exceptions/{moved_exception['id']}",
        json=exception_update_payload(
            expected_series_version=4,
            expected_version=1,
            action="cancelled",
        ),
    )
    assert changed_to_cancel.status_code == 200, changed_to_cancel.text
    assert changed_to_cancel.json()["series"]["version"] == 5
    assert changed_to_cancel.json()["exception"]["version"] == 2
    after_update = get_occurrences(client, "2026-10-05", "2026-10-09")
    changed = next(
        item
        for item in after_update
        if item["occurrence_key"] == "2026-10-07T09:00:00"
    )
    assert changed["status"] == "cancelled"
    assert changed["local_start_date"] == "2026-10-07"

    exception_list = client.get(
        f"/api/v1/calendar/series/{series['id']}/exceptions"
    )
    assert exception_list.status_code == 200
    assert exception_list.json()["series_version"] == 5
    assert len(exception_list.json()["items"]) == 3

    missing = client.post(
        f"/api/v1/calendar/series/{series['id']}/exceptions",
        json=exception_payload(
            expected_series_version=5,
            occurrence_key="2026-10-20T09:00:00",
            action="cancelled",
        ),
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "occurrence_not_found"

    free_series = create_series(
        client,
        name="默认空闲",
        busy=False,
        start_date="2026-10-09",
        starts_at="14:00:00",
        ends_at="15:00:00",
    )["series"]
    made_busy = client.post(
        f"/api/v1/calendar/series/{free_series['id']}/exceptions",
        json=exception_payload(
            expected_series_version=1,
            occurrence_key="2026-10-09T14:00:00",
            action="overridden",
            override_busy=True,
        ),
    )
    assert made_busy.status_code == 201
    conflicts_with_override = create_series(
        client,
        name="与单次占用冲突",
        start_date="2026-10-09",
        starts_at="14:30:00",
        ends_at="15:30:00",
    )
    assert conflicts_with_override["conflicts"]["items"][0][
        "conflicting_series_id"
    ] == free_series["id"]

    with psycopg.connect(psycopg_url(database_url)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM calendar_exceptions WHERE owner_id=%s",
            (user_id,),
        ).fetchone()[0] == 4


def test_moved_exception_validates_dst_and_new_conflicts(client, database_url):
    create_account(client, database_url, time_zone="America/New_York")
    series = create_series(
        client,
        name="可调课课程",
        start_date="2026-03-07",
        starts_at="09:00:00",
        ends_at="10:00:00",
        recurrence=recurrence("daily", count=2),
    )["series"]
    endpoint = f"/api/v1/calendar/series/{series['id']}/exceptions"

    invalid = client.post(
        endpoint,
        json=exception_payload(
            expected_series_version=1,
            occurrence_key="2026-03-07T09:00:00",
            action="moved",
            moved_start_date="2026-03-08",
            moved_starts_at="02:30:00",
            moved_ends_at="03:30:00",
            moved_end_day_offset=0,
        ),
    )
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "invalid_local_time"

    ambiguous = client.post(
        endpoint,
        json=exception_payload(
            expected_series_version=1,
            occurrence_key="2026-03-07T09:00:00",
            action="moved",
            moved_start_date="2026-11-01",
            moved_starts_at="01:30:00",
            moved_ends_at="01:45:00",
            moved_end_day_offset=0,
        ),
    )
    assert ambiguous.status_code == 400
    assert ambiguous.json()["error"]["code"] == "ambiguous_local_time"

    moved = client.post(
        endpoint,
        json=exception_payload(
            expected_series_version=1,
            occurrence_key="2026-03-07T09:00:00",
            action="moved",
            moved_start_date="2026-11-01",
            moved_starts_at="01:30:00",
            moved_ends_at="01:45:00",
            moved_end_day_offset=0,
            moved_ambiguous_utc_offset_minutes=-300,
        ),
    )
    assert moved.status_code == 201, moved.text
    november = get_occurrences(client, "2026-11-01", "2026-11-02")
    assert len(november) == 1
    assert november[0]["occurrence_key"] == "2026-03-07T09:00:00"
    assert november[0]["original_local_start_date"] == "2026-03-07"
    assert november[0]["start_at"] == "2026-11-01T06:30:00Z"
    assert moved.json()["exception"]["moved_time_zone"] == "America/New_York"

    changed_series_zone = client.put(
        f"/api/v1/calendar/series/{series['id']}",
        json={
            **fixed_payload(
                name="改为上海时区的系列",
                time_zone="Asia/Shanghai",
                start_date="2026-03-07",
                starts_at="09:00:00",
                ends_at="10:00:00",
                recurrence=recurrence("daily", count=2),
            ),
            "expected_version": 2,
        },
    )
    assert changed_series_zone.status_code == 200, changed_series_zone.text
    unchanged_move = get_occurrences(client, "2026-11-01", "2026-11-02")
    assert len(unchanged_move) == 1
    assert unchanged_move[0]["start_at"] == "2026-11-01T06:30:00Z"
    assert unchanged_move[0]["time_zone"] == "America/New_York"

    future = create_series(
        client,
        name="未来 DST 系列",
        start_date="2027-03-13",
        starts_at="02:30:00",
        ends_at="03:00:00",
        recurrence=recurrence("daily", count=3),
    )["series"]
    replacement = fixed_payload(
        name="DST 后继系列",
        start_date="2027-03-14",
        starts_at="03:30:00",
        ends_at="04:00:00",
        recurrence=recurrence("daily", count=2),
    )
    replacement.pop("operation_id")
    split = client.post(
        f"/api/v1/calendar/series/{future['id']}/split",
        json={
            "operation_id": str(uuid4()),
            "expected_version": 1,
            "occurrence_key": "2027-03-14T02:30:00",
            "replacement": replacement,
        },
    )
    assert split.status_code == 201, split.text


def test_series_update_rejects_orphaned_exception_but_preserves_date_binding(
    client,
    database_url,
):
    create_account(client, database_url)
    series = create_series(
        client,
        name="重复复习",
        start_date="2026-10-05",
        starts_at="09:00:00",
        ends_at="10:00:00",
        recurrence=recurrence("daily", count=3),
    )["series"]
    exception = client.post(
        f"/api/v1/calendar/series/{series['id']}/exceptions",
        json=exception_payload(
            expected_series_version=1,
            occurrence_key="2026-10-07T09:00:00",
            action="cancelled",
        ),
    )
    assert exception.status_code == 201

    orphaning = client.put(
        f"/api/v1/calendar/series/{series['id']}",
        json={
            **fixed_payload(
                name="缩短后的复习",
                start_date="2026-10-05",
                starts_at="09:00:00",
                ends_at="10:00:00",
                recurrence=recurrence("daily", count=2),
            ),
            "expected_version": 2,
        },
    )
    assert orphaning.status_code == 409
    assert orphaning.json()["error"]["code"] == "series_exceptions_would_be_orphaned"

    time_change = client.put(
        f"/api/v1/calendar/series/{series['id']}",
        json={
            **fixed_payload(
                name="改时刻后的复习",
                start_date="2026-10-05",
                starts_at="10:00:00",
                ends_at="11:00:00",
                recurrence=recurrence("daily", count=3),
            ),
            "expected_version": 2,
        },
    )
    assert time_change.status_code == 200, time_change.text
    assert time_change.json()["series"]["version"] == 3
    items = get_occurrences(client, "2026-10-05", "2026-10-08")
    cancelled = next(
        item
        for item in items
        if item["original_local_start_date"] == "2026-10-07"
    )
    assert cancelled["status"] == "cancelled"
    assert cancelled["occurrence_key"] == "2026-10-07T09:00:00"


def test_split_series_moves_future_exceptions_atomically(client, database_url):
    _, user_id = create_account(client, database_url)
    series = create_series(
        client,
        name="晨间复习",
        start_date="2026-10-05",
        starts_at="09:00:00",
        ends_at="10:00:00",
        recurrence=recurrence("daily", count=5),
    )["series"]
    first_exception = client.post(
        f"/api/v1/calendar/series/{series['id']}/exceptions",
        json=exception_payload(
            expected_series_version=1,
            occurrence_key="2026-10-06T09:00:00",
            action="overridden",
            override_name="晨间复习（资料整理）",
        ),
    ).json()["exception"]
    future_exception = client.post(
        f"/api/v1/calendar/series/{series['id']}/exceptions",
        json=exception_payload(
            expected_series_version=2,
            occurrence_key="2026-10-08T09:00:00",
            action="cancelled",
        ),
    ).json()["exception"]

    operation_id = uuid4()
    payload = {
        "operation_id": str(operation_id),
        "expected_version": 3,
        "occurrence_key": "2026-10-07T09:00:00",
        "replacement": fixed_payload(
            name="午后复习",
            start_date="2026-10-07",
            starts_at="14:00:00",
            ends_at="15:00:00",
            recurrence=recurrence("daily", count=3),
        ),
    }
    payload["replacement"].pop("operation_id")
    split = client.post(
        f"/api/v1/calendar/series/{series['id']}/split",
        json=payload,
    )
    assert split.status_code == 201, split.text
    body = split.json()
    successor = body["successor"]
    assert body["predecessor"]["recurrence"]["count"] == 2
    assert body["predecessor"]["version"] == 4
    assert successor["predecessor_id"] == series["id"]
    assert successor["start_date"] == "2026-10-07"
    assert body["moved_exception_count"] == 1
    assert client.post(
        f"/api/v1/calendar/series/{series['id']}/split",
        json=payload,
    ).json() == body

    items = get_occurrences(client, "2026-10-05", "2026-10-10")
    assert [
        (item["local_start_date"], item["local_start_time"], item["status"])
        for item in items
    ] == [
        ("2026-10-05", "09:00:00", "scheduled"),
        ("2026-10-06", "09:00:00", "scheduled"),
        ("2026-10-07", "14:00:00", "scheduled"),
        ("2026-10-08", "14:00:00", "cancelled"),
        ("2026-10-09", "14:00:00", "scheduled"),
    ]
    old_exceptions = client.get(
        f"/api/v1/calendar/series/{series['id']}/exceptions"
    ).json()["items"]
    new_exceptions = client.get(
        f"/api/v1/calendar/series/{successor['id']}/exceptions"
    ).json()["items"]
    assert [item["id"] for item in old_exceptions] == [first_exception["id"]]
    assert [item["id"] for item in new_exceptions] == [future_exception["id"]]
    assert new_exceptions[0]["version"] == 2

    with psycopg.connect(psycopg_url(database_url)) as connection:
        series_count = connection.execute(
            "SELECT count(*) FROM calendar_series WHERE owner_id=%s",
            (user_id,),
        ).fetchone()[0]
        assert series_count == 2


def test_split_rejects_first_occurrence_or_orphaned_future_exception(
    client,
    database_url,
):
    create_account(client, database_url)
    series = create_series(
        client,
        name="不可错误拆分",
        start_date="2026-10-05",
        recurrence=recurrence("daily", count=4),
    )["series"]
    first = client.post(
        f"/api/v1/calendar/series/{series['id']}/split",
        json={
            "operation_id": str(uuid4()),
            "expected_version": 1,
            "occurrence_key": "2026-10-05T09:00:00",
            "replacement": {
                key: value
                for key, value in fixed_payload(
                    name="从首日修改",
                    recurrence=recurrence("daily", count=4),
                ).items()
                if key != "operation_id"
            },
        },
    )
    assert first.status_code == 400
    assert first.json()["error"]["code"] == "split_at_first_occurrence"

    exception = client.post(
        f"/api/v1/calendar/series/{series['id']}/exceptions",
        json=exception_payload(
            expected_series_version=1,
            occurrence_key="2026-10-08T09:00:00",
            action="cancelled",
        ),
    )
    assert exception.status_code == 201
    replacement = fixed_payload(
        name="只保留一次",
        start_date="2026-10-07",
        recurrence=recurrence("none"),
    )
    replacement.pop("operation_id")
    orphaned = client.post(
        f"/api/v1/calendar/series/{series['id']}/split",
        json={
            "operation_id": str(uuid4()),
            "expected_version": 2,
            "occurrence_key": "2026-10-07T09:00:00",
            "replacement": replacement,
        },
    )
    assert orphaned.status_code == 409
    assert orphaned.json()["error"]["code"] == "series_exceptions_would_be_orphaned"
    assert client.get(f"/api/v1/calendar/series/{series['id']}").json()["series"][
        "version"
    ] == 2

    historical = create_series(
        client,
        name="历史系列",
        start_date="2020-01-01",
        recurrence=recurrence("daily", count=3),
    )["series"]
    historical_replacement = fixed_payload(
        name="不应改写历史",
        start_date="2020-01-02",
        recurrence=recurrence("daily", count=2),
    )
    historical_replacement.pop("operation_id")
    past_split = client.post(
        f"/api/v1/calendar/series/{historical['id']}/split",
        json={
            "operation_id": str(uuid4()),
            "expected_version": 1,
            "occurrence_key": "2020-01-02T09:00:00",
            "replacement": historical_replacement,
        },
    )
    assert past_split.status_code == 409
    assert past_split.json()["error"]["code"] == "historical_occurrence_immutable"


def test_exception_ownership_and_concurrent_updates(
    client,
    database_url,
    live_server,
):
    email, _ = create_account(client, database_url)
    series = create_series(
        client,
        start_date="2026-10-05",
        recurrence=recurrence("daily", count=2),
    )["series"]
    created = client.post(
        f"/api/v1/calendar/series/{series['id']}/exceptions",
        json=exception_payload(
            expected_series_version=1,
            occurrence_key="2026-10-05T09:00:00",
            action="overridden",
            override_name="第一次覆盖",
        ),
    ).json()
    exception = created["exception"]

    with browser(live_server) as other:
        create_account(other, database_url)
        assert other.get(
            f"/api/v1/calendar/series/{series['id']}/exceptions"
        ).status_code == 404
        cross = other.put(
            f"/api/v1/calendar/series/{series['id']}/exceptions/{exception['id']}",
            json=exception_update_payload(
                expected_series_version=2,
                expected_version=1,
                action="cancelled",
            ),
        )
        assert cross.status_code == 404

    barrier = Barrier(2)

    def update_with(session, name):
        barrier.wait(timeout=5)
        return session.put(
            f"/api/v1/calendar/series/{series['id']}/exceptions/{exception['id']}",
            json=exception_update_payload(
                expected_series_version=2,
                expected_version=1,
                action="overridden",
                override_name=name,
            ),
        )

    with browser(live_server) as left, browser(live_server) as right:
        login_account(left, email)
        login_account(right, email)
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(
                executor.map(
                    lambda pair: update_with(*pair),
                    [(left, "并发覆盖 A"), (right, "并发覆盖 B")],
                )
            )
    assert sorted(response.status_code for response in responses) == [200, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["error"]["code"] == "calendar_series_version_conflict"


def test_concurrent_series_split_creates_only_one_successor(
    client,
    database_url,
    live_server,
):
    email, user_id = create_account(client, database_url)
    series = create_series(
        client,
        name="并发拆分",
        start_date="2026-10-05",
        recurrence=recurrence("daily", count=4),
    )["series"]
    barrier = Barrier(2)

    def split_with(session, name):
        replacement = fixed_payload(
            name=name,
            start_date="2026-10-07",
            recurrence=recurrence("daily", count=2),
        )
        replacement.pop("operation_id")
        barrier.wait(timeout=5)
        return session.post(
            f"/api/v1/calendar/series/{series['id']}/split",
            json={
                "operation_id": str(uuid4()),
                "expected_version": 1,
                "occurrence_key": "2026-10-07T09:00:00",
                "replacement": replacement,
            },
        )

    with browser(live_server) as left, browser(live_server) as right:
        login_account(left, email)
        login_account(right, email)
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(
                executor.map(
                    lambda pair: split_with(*pair),
                    [
                        (left, "继任 A"),
                        (right, "继任 B"),
                    ],
                )
            )

    assert sorted(response.status_code for response in responses) == [201, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["error"]["code"] == "calendar_series_version_conflict"
    with psycopg.connect(psycopg_url(database_url)) as connection:
        rows = connection.execute(
            """
            SELECT name, predecessor_id
            FROM calendar_series
            WHERE owner_id=%s
            ORDER BY predecessor_id NULLS FIRST
            """,
            (user_id,),
        ).fetchall()
        assert len(rows) == 2
        assert rows[0] == ("并发拆分", None)
        assert rows[1][1] is not None
        assert rows[1][0] in {"继任 A", "继任 B"}


def test_calendar_exception_migration_roundtrip_with_data(admin_url):
    with isolated_database(admin_url) as database_url:
        owner_id = uuid4()
        series_id = uuid4()
        exception_id = uuid4()
        with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
            connection.execute(
                """
                INSERT INTO users (id, email, password_hash)
                VALUES (%s, %s, %s)
                """,
                (owner_id, address("exception-migration"), "test-password-hash"),
            )
            connection.execute(
                """
                INSERT INTO calendar_series (
                    id, owner_id, kind, name, time_zone, start_date,
                    starts_at, ends_at, recurrence_frequency,
                    recurrence_weekdays, course_week_numbers
                )
                VALUES (
                    %s, %s, 'fixed', '迁移安排', 'Asia/Shanghai', '2026-10-05',
                    '09:00', '10:00', 'none', '[]'::jsonb, '[]'::jsonb
                )
                """,
                (series_id, owner_id),
            )
            connection.execute(
                """
                INSERT INTO calendar_exceptions (
                    id, series_id, owner_id, occurrence_key,
                    original_start_date, action
                )
                VALUES (
                    %s, %s, %s, '2026-10-05T09:00:00',
                    '2026-10-05', 'cancelled'
                )
                """,
                (exception_id, series_id, owner_id),
            )
            connection.execute(
                """
                INSERT INTO change_events (
                    owner_id, entity_type, entity_id, action, entity_version
                )
                VALUES (%s, 'calendar_exception', %s, 'created', 1)
                """,
                (owner_id, exception_id),
            )
            connection.execute(
                """
                INSERT INTO user_operations (
                    owner_id, operation_id, scope, request_hash, response
                )
                VALUES (
                    %s, %s, 'calendar_exceptions.create:test', %s, '{}'::jsonb
                )
                """,
                (owner_id, uuid4(), "0" * 64),
            )

        migrate(database_url, "downgrade", "0007_calendar_series")
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT to_regclass('calendar_exceptions')"
            ).fetchone()[0] is None
            assert connection.execute(
                """
                SELECT count(*) FROM change_events
                WHERE entity_type='calendar_exception'
                """
            ).fetchone()[0] == 0
            assert connection.execute(
                """
                SELECT count(*) FROM user_operations
                WHERE scope LIKE 'calendar_exceptions.%'
                """
            ).fetchone()[0] == 0

        migrate(database_url, "upgrade", "head")
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT to_regclass('calendar_exceptions')"
            ).fetchone()[0] == "calendar_exceptions"
