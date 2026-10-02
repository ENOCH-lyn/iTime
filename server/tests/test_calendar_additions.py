import subprocess
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest

from tests.conftest import isolated_database, migrate, psycopg_url
from tests.test_calendar_exceptions import exception_payload, get_occurrences
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


def addition_payload(
    *,
    expected_series_version: int,
    source_occurrence_key: str,
    operation_id=None,
    **changes,
) -> dict:
    payload = {
        "operation_id": str(operation_id or uuid4()),
        "expected_series_version": expected_series_version,
        "source_occurrence_key": source_occurrence_key,
        "status": "scheduled",
        "local_start_date": "2026-10-07",
        "starts_at": "09:30:00",
        "ends_at": "10:30:00",
        "end_day_offset": 0,
        "time_zone": None,
        "ambiguous_utc_offset_minutes": None,
        "override_name": None,
        "override_description": None,
        "override_location": None,
        "override_busy": None,
    }
    payload.update(changes)
    return payload


def addition_update_payload(
    *,
    expected_series_version: int,
    expected_version: int,
    operation_id=None,
    **changes,
) -> dict:
    payload = addition_payload(
        expected_series_version=expected_series_version,
        source_occurrence_key="unused-on-update",
        operation_id=operation_id,
        **changes,
    )
    payload.pop("source_occurrence_key")
    payload["expected_version"] = expected_version
    return payload


def test_addition_coexists_with_cancel_and_has_lifecycle(client, database_url):
    _, user_id = create_account(client, database_url)
    series = create_series(
        client,
        name="算法课",
        start_date="2026-10-05",
        starts_at="09:00:00",
        ends_at="10:00:00",
        recurrence=recurrence("daily", count=3),
    )["series"]
    cancelled = client.post(
        f"/api/v1/calendar/series/{series['id']}/exceptions",
        json=exception_payload(
            expected_series_version=1,
            occurrence_key="2026-10-06T09:00:00",
            action="cancelled",
        ),
    )
    assert cancelled.status_code == 201, cancelled.text

    operation_id = uuid4()
    payload = addition_payload(
        expected_series_version=2,
        source_occurrence_key="2026-10-06T09:00:00",
        operation_id=operation_id,
    )
    created = client.post(
        f"/api/v1/calendar/series/{series['id']}/additions",
        json=payload,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    addition = body["addition"]
    assert body["series"]["version"] == 3
    assert addition["occurrence_key"].startswith("makeup:")
    assert addition["time_zone"] == "Asia/Shanghai"
    assert body["conflicts"]["items"][0]["conflicting_series_id"] == series["id"]
    assert {
        body["conflicts"]["items"][0]["occurrence_type"],
        body["conflicts"]["items"][0]["conflicting_occurrence_type"],
    } == {"addition", "base"}
    replayed = client.post(
        f"/api/v1/calendar/series/{series['id']}/additions",
        json=payload,
    )
    assert replayed.json() == body
    reused = client.post(
        f"/api/v1/calendar/series/{series['id']}/additions",
        json={**payload, "starts_at": "09:15:00"},
    )
    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "operation_id_reused"

    duplicate = client.post(
        f"/api/v1/calendar/series/{series['id']}/additions",
        json=addition_payload(
            expected_series_version=3,
            source_occurrence_key="2026-10-06T09:00:00",
        ),
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "calendar_addition_exists"

    items = get_occurrences(client, "2026-10-06", "2026-10-08")
    source = next(
        item
        for item in items
        if item["occurrence_key"] == "2026-10-06T09:00:00"
    )
    makeup = next(item for item in items if item["occurrence_type"] == "addition")
    base = next(
        item
        for item in items
        if item["occurrence_key"] == "2026-10-07T09:00:00"
    )
    assert source["status"] == "cancelled"
    assert makeup["source_occurrence_key"] == source["occurrence_key"]
    assert makeup["addition_id"] == addition["id"]
    assert makeup["conflict_with"][0]["occurrence_key"] == base["occurrence_key"]
    assert base["conflict_with"][0]["addition_id"] == addition["id"]

    changed_zone = client.put(
        "/api/v1/time-settings",
        json={
            "operation_id": str(uuid4()),
            "expected_version": 1,
            "time_zone": "America/New_York",
        },
    )
    assert changed_zone.status_code == 200, changed_zone.text
    cancelled_addition = client.put(
        f"/api/v1/calendar/series/{series['id']}/additions/{addition['id']}",
        json=addition_update_payload(
            expected_series_version=3,
            expected_version=1,
            status="cancelled",
        ),
    )
    assert cancelled_addition.status_code == 200, cancelled_addition.text
    assert cancelled_addition.json()["addition"]["time_zone"] == "Asia/Shanghai"
    assert cancelled_addition.json()["conflicts"]["items"] == []

    restored = client.put(
        f"/api/v1/calendar/series/{series['id']}/additions/{addition['id']}",
        json=addition_update_payload(
            expected_series_version=4,
            expected_version=2,
            status="scheduled",
            override_name="算法课补课",
            override_location="新教室",
        ),
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["addition"]["version"] == 3
    listed = client.get(
        f"/api/v1/calendar/series/{series['id']}/additions"
    )
    assert listed.status_code == 200
    assert listed.json()["series_version"] == 5
    assert listed.json()["items"] == [restored.json()["addition"]]

    no_op = client.put(
        f"/api/v1/calendar/series/{series['id']}/additions/{addition['id']}",
        json=addition_update_payload(
            expected_series_version=5,
            expected_version=3,
            status="scheduled",
            override_name="算法课补课",
            override_location="新教室",
        ),
    )
    assert no_op.status_code == 200, no_op.text
    assert no_op.json()["series"]["version"] == 5
    assert no_op.json()["addition"]["version"] == 3

    with psycopg.connect(psycopg_url(database_url)) as connection:
        stored = connection.execute(
            """
            SELECT status, time_zone, version
            FROM calendar_additions
            WHERE id=%s AND owner_id=%s
            """,
            (addition["id"], user_id),
        ).fetchone()
        assert stored == ("scheduled", "Asia/Shanghai", 3)
        assert connection.execute(
            """
            SELECT count(*) FROM change_events
            WHERE owner_id=%s AND entity_type='calendar_addition'
            """,
            (user_id,),
        ).fetchone()[0] == 3
        assert connection.execute(
            """
            SELECT count(*) FROM user_operations
            WHERE owner_id=%s AND scope LIKE 'calendar_additions.%%'
            """,
            (user_id,),
        ).fetchone()[0] == 4


def test_addition_dst_cross_midnight_and_range_reentry(client, database_url):
    create_account(client, database_url, time_zone="America/New_York")
    series = create_series(
        client,
        name="DST 课程",
        start_date="2026-03-07",
        starts_at="09:00:00",
        ends_at="10:00:00",
        time_zone="Asia/Shanghai",
        recurrence=recurrence("daily", count=2),
    )["series"]
    endpoint = f"/api/v1/calendar/series/{series['id']}/additions"

    invalid = client.post(
        endpoint,
        json=addition_payload(
            expected_series_version=1,
            source_occurrence_key="2026-03-07T09:00:00",
            local_start_date="2026-03-08",
            starts_at="02:30:00",
            ends_at="03:30:00",
        ),
    )
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "invalid_local_time"

    ambiguous = client.post(
        endpoint,
        json=addition_payload(
            expected_series_version=1,
            source_occurrence_key="2026-03-07T09:00:00",
            local_start_date="2026-11-01",
            starts_at="01:30:00",
            ends_at="01:45:00",
        ),
    )
    assert ambiguous.status_code == 400
    assert ambiguous.json()["error"]["code"] == "ambiguous_local_time"

    created = client.post(
        endpoint,
        json=addition_payload(
            expected_series_version=1,
            source_occurrence_key="2026-03-07T09:00:00",
            local_start_date="2026-11-01",
            starts_at="01:30:00",
            ends_at="01:45:00",
            ambiguous_utc_offset_minutes=-300,
        ),
    )
    assert created.status_code == 201, created.text
    addition = created.json()["addition"]
    assert addition["time_zone"] == "America/New_York"
    november = get_occurrences(client, "2026-11-01", "2026-11-02")
    assert len(november) == 1
    assert november[0]["addition_id"] == addition["id"]
    assert november[0]["start_at"] == "2026-11-01T06:30:00Z"

    overnight = client.put(
        f"{endpoint}/{addition['id']}",
        json=addition_update_payload(
            expected_series_version=2,
            expected_version=1,
            local_start_date="2027-01-10",
            starts_at="23:00:00",
            ends_at="01:00:00",
            end_day_offset=1,
            time_zone="America/New_York",
        ),
    )
    assert overnight.status_code == 200, overnight.text
    next_day = get_occurrences(client, "2027-01-11", "2027-01-12")
    assert len(next_day) == 1
    assert next_day[0]["addition_id"] == addition["id"]
    assert next_day[0]["local_start_date"] == "2027-01-10"


def test_addition_ownership_and_concurrent_writes(
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
    barrier = Barrier(2)

    def create_with(session):
        barrier.wait(timeout=5)
        return session.post(
            f"/api/v1/calendar/series/{series['id']}/additions",
            json=addition_payload(
                expected_series_version=1,
                source_occurrence_key="2026-10-05T09:00:00",
            ),
        )

    with browser(live_server) as left, browser(live_server) as right:
        login_account(left, email)
        login_account(right, email)
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(create_with, [left, right]))
    assert sorted(response.status_code for response in responses) == [201, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["error"]["code"] == "calendar_series_version_conflict"
    addition = next(
        response.json()["addition"]
        for response in responses
        if response.status_code == 201
    )

    with browser(live_server) as other:
        create_account(other, database_url)
        assert other.get(
            f"/api/v1/calendar/series/{series['id']}/additions"
        ).status_code == 404
        assert other.put(
            f"/api/v1/calendar/series/{series['id']}/additions/{addition['id']}",
            json=addition_update_payload(
                expected_series_version=2,
                expected_version=1,
                status="cancelled",
            ),
        ).status_code == 404

    update_barrier = Barrier(2)

    def update_with(session):
        update_barrier.wait(timeout=5)
        return session.put(
            f"/api/v1/calendar/series/{series['id']}/additions/{addition['id']}",
            json=addition_update_payload(
                expected_series_version=2,
                expected_version=1,
                status="cancelled",
            ),
        )

    with browser(live_server) as left, browser(live_server) as right:
        login_account(left, email)
        login_account(right, email)
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(update_with, [left, right]))
    assert sorted(response.status_code for response in responses) == [200, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["error"]["code"] == "calendar_series_version_conflict"


def test_series_update_and_split_protect_addition_sources(client, database_url):
    create_account(client, database_url)
    series = create_series(
        client,
        name="未来课程",
        start_date="2026-12-01",
        recurrence=recurrence("daily", count=5),
    )["series"]
    created = client.post(
        f"/api/v1/calendar/series/{series['id']}/additions",
        json=addition_payload(
            expected_series_version=1,
            source_occurrence_key="2026-12-04T09:00:00",
            local_start_date="2027-01-10",
            starts_at="10:00:00",
            ends_at="11:00:00",
        ),
    )
    assert created.status_code == 201, created.text
    addition = created.json()["addition"]

    shortened = client.put(
        f"/api/v1/calendar/series/{series['id']}",
        json={
            **fixed_payload(
                name="错误缩短",
                start_date="2026-12-01",
                recurrence=recurrence("daily", count=2),
            ),
            "expected_version": 2,
        },
    )
    assert shortened.status_code == 409
    assert shortened.json()["error"]["code"] == "series_additions_would_be_orphaned"

    replacement = fixed_payload(
        name="未来课程新时段",
        start_date="2026-12-03",
        starts_at="14:00:00",
        ends_at="15:00:00",
        recurrence=recurrence("daily", count=3),
    )
    replacement.pop("operation_id")
    split = client.post(
        f"/api/v1/calendar/series/{series['id']}/split",
        json={
            "operation_id": str(uuid4()),
            "expected_version": 2,
            "occurrence_key": "2026-12-03T09:00:00",
            "replacement": replacement,
        },
    )
    assert split.status_code == 201, split.text
    body = split.json()
    successor = body["successor"]
    assert body["moved_addition_count"] == 1
    assert client.get(
        f"/api/v1/calendar/series/{series['id']}/additions"
    ).json()["items"] == []
    moved = client.get(
        f"/api/v1/calendar/series/{successor['id']}/additions"
    ).json()["items"]
    assert moved[0]["id"] == addition["id"]
    assert moved[0]["version"] == 2
    assert moved[0]["source_occurrence_key"] == "2026-12-04T09:00:00"
    january = get_occurrences(client, "2027-01-10", "2027-01-11")
    assert len(january) == 1
    assert january[0]["series_id"] == successor["id"]
    assert january[0]["addition_id"] == addition["id"]

    second_replacement = fixed_payload(
        name="不应创建第二后继",
        start_date="2026-12-02",
        recurrence=recurrence("none"),
    )
    second_replacement.pop("operation_id")
    second_split = client.post(
        f"/api/v1/calendar/series/{series['id']}/split",
        json={
            "operation_id": str(uuid4()),
            "expected_version": body["predecessor"]["version"],
            "occurrence_key": "2026-12-02T09:00:00",
            "replacement": second_replacement,
        },
    )
    assert second_split.status_code == 409
    assert second_split.json()["error"]["code"] == "calendar_series_already_split"


def test_calendar_addition_migration_roundtrip_with_data(admin_url):
    with isolated_database(admin_url) as database_url:
        owner_id = uuid4()
        series_id = uuid4()
        exception_id = uuid4()
        addition_id = uuid4()
        with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
            connection.execute(
                """
                INSERT INTO users (id, email, password_hash)
                VALUES (%s, %s, %s)
                """,
                (owner_id, address("addition-migration"), "test-password-hash"),
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
                    original_start_date, action, moved_start_date,
                    moved_starts_at, moved_ends_at, moved_end_day_offset,
                    moved_time_zone
                )
                VALUES (
                    %s, %s, %s, '2026-10-05T09:00:00',
                    '2026-10-05', 'moved', '2026-10-06',
                    '11:00', '12:00', 0, 'Asia/Shanghai'
                )
                """,
                (exception_id, series_id, owner_id),
            )
            connection.execute(
                """
                INSERT INTO calendar_additions (
                    id, series_id, owner_id, occurrence_key,
                    source_occurrence_key, source_original_start_date,
                    local_start_date, starts_at, ends_at, time_zone
                )
                VALUES (
                    %s, %s, %s, %s, '2026-10-05T09:00:00',
                    '2026-10-05', '2026-10-07', '13:00', '14:00',
                    'Asia/Shanghai'
                )
                """,
                (addition_id, series_id, owner_id, f"makeup:{addition_id}"),
            )
            connection.execute(
                """
                INSERT INTO change_events (
                    owner_id, entity_type, entity_id, action, entity_version
                )
                VALUES (%s, 'calendar_addition', %s, 'created', 1)
                """,
                (owner_id, addition_id),
            )
            connection.execute(
                """
                INSERT INTO user_operations (
                    owner_id, operation_id, scope, request_hash, response
                )
                VALUES (
                    %s, %s, 'calendar_additions.create:test', %s, '{}'::jsonb
                )
                """,
                (owner_id, uuid4(), "0" * 64),
            )

        with pytest.raises(subprocess.CalledProcessError) as error:
            migrate(database_url, "downgrade", "0009_task_schedules")
        assert "while additions exist" in error.value.stderr
        with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
            connection.execute("DELETE FROM calendar_additions")
            connection.execute(
                """
                UPDATE calendar_exceptions
                SET moved_time_zone='America/New_York'
                WHERE id=%s
                """,
                (exception_id,),
            )

        with pytest.raises(subprocess.CalledProcessError) as error:
            migrate(database_url, "downgrade", "0009_task_schedules")
        assert "while moved time zones differ" in error.value.stderr
        with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
            connection.execute(
                """
                UPDATE calendar_exceptions
                SET moved_time_zone='Asia/Shanghai'
                WHERE id=%s
                """,
                (exception_id,),
            )
        migrate(database_url, "downgrade", "0009_task_schedules")
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT to_regclass('calendar_additions')"
            ).fetchone()[0] is None
            assert connection.execute(
                """
                SELECT count(*) FROM information_schema.columns
                WHERE table_name='calendar_exceptions'
                  AND column_name='moved_time_zone'
                """
            ).fetchone()[0] == 0
            assert connection.execute(
                """
                SELECT count(*) FROM change_events
                WHERE entity_type='calendar_addition'
                """
            ).fetchone()[0] == 0
            assert connection.execute(
                """
                SELECT count(*) FROM user_operations
                WHERE scope LIKE 'calendar_additions.%'
                """
            ).fetchone()[0] == 0

        migrate(database_url, "upgrade", "head")
        migrate(database_url, "check")
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT to_regclass('calendar_additions')"
            ).fetchone()[0] == "calendar_additions"
            assert connection.execute(
                """
                SELECT moved_time_zone
                FROM calendar_exceptions
                WHERE id=%s
                """,
                (exception_id,),
            ).fetchone()[0] == "Asia/Shanghai"


def test_calendar_addition_upgrade_preserves_existing_split_branches(admin_url):
    with isolated_database(admin_url) as database_url:
        migrate(database_url, "downgrade", "0009_task_schedules")
        owner_id = uuid4()
        predecessor_id = uuid4()
        first_successor_id = uuid4()
        second_successor_id = uuid4()
        with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
            connection.execute(
                """
                INSERT INTO users (id, email, password_hash)
                VALUES (%s, %s, %s)
                """,
                (owner_id, address("branched-upgrade"), "test-password-hash"),
            )
            connection.execute(
                """
                INSERT INTO calendar_series (
                    id, owner_id, predecessor_id, kind, name, time_zone,
                    start_date, starts_at, ends_at, recurrence_frequency,
                    recurrence_weekdays, course_week_numbers
                )
                VALUES
                    (
                        %s, %s, NULL, 'fixed', '前序', 'Asia/Shanghai',
                        '2026-10-05', '09:00', '10:00', 'none',
                        '[]'::jsonb, '[]'::jsonb
                    ),
                    (
                        %s, %s, %s, 'fixed', '旧后继 A', 'Asia/Shanghai',
                        '2026-10-06', '09:00', '10:00', 'none',
                        '[]'::jsonb, '[]'::jsonb
                    ),
                    (
                        %s, %s, %s, 'fixed', '旧后继 B', 'Asia/Shanghai',
                        '2026-10-07', '09:00', '10:00', 'none',
                        '[]'::jsonb, '[]'::jsonb
                    )
                """,
                (
                    predecessor_id,
                    owner_id,
                    first_successor_id,
                    owner_id,
                    predecessor_id,
                    second_successor_id,
                    owner_id,
                    predecessor_id,
                ),
            )

        migrate(database_url, "upgrade", "head")
        migrate(database_url, "check")
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                """
                SELECT count(*)
                FROM calendar_series
                WHERE predecessor_id=%s
                """,
                (predecessor_id,),
            ).fetchone()[0] == 2
