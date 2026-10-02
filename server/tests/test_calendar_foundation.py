from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime
from threading import Barrier
from uuid import uuid4

import httpx
import psycopg
import pytest

from itime.calendar.service import activity_day_at
from tests.conftest import isolated_database, migrate, psycopg_url

pytestmark = pytest.mark.integration
AUTH = "/api/v1/auth"
PASSWORD = "Valid-calendar-password-123"


def address(prefix: str = "calendar") -> str:
    return f"{prefix}-{uuid4().hex}@example.com"


def create_account(client: httpx.Client, database_url: str) -> tuple[str, str]:
    email = address()
    registered = client.post(
        f"{AUTH}/register",
        json={"email": email, "password": PASSWORD},
    )
    assert registered.status_code == 202, registered.text
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        user_id = connection.execute(
            "UPDATE users SET email_verified_at=now() WHERE email=%s RETURNING id",
            (email,),
        ).fetchone()[0]
    login_account(client, email)
    return email, str(user_id)


def login_account(client: httpx.Client, email: str) -> None:
    logged_in = client.post(
        f"{AUTH}/login",
        json={
            "email": email,
            "password": PASSWORD,
            "device_name": "课表测试",
        },
    )
    assert logged_in.status_code == 200, logged_in.text
    client.headers["x-csrf-token"] = logged_in.json()["csrf_token"]


@contextmanager
def browser(live_server):
    with httpx.Client(base_url=live_server.origin, timeout=10, trust_env=False) as client:
        yield client


def settings_payload(operation_id=None, **changes) -> dict:
    payload = {
        "operation_id": str(operation_id or uuid4()),
        "expected_version": 0,
        "time_zone": "Asia/Shanghai",
        "week_starts_on": 1,
        "activity_day_start_minute": 240,
    }
    payload.update(changes)
    return payload


def term_payload(operation_id=None, **changes) -> dict:
    payload = {
        "operation_id": str(operation_id or uuid4()),
        "name": "2026 秋季学期",
        "start_date": "2026-09-01",
        "end_date": "2027-01-31",
        "first_week_monday": "2026-08-31",
        "time_zone": None,
    }
    payload.update(changes)
    return payload


def initialize_settings(client: httpx.Client, **changes) -> dict:
    response = client.put(
        "/api/v1/time-settings",
        json=settings_payload(**changes),
    )
    assert response.status_code == 200, response.text
    return response.json()["settings"]


def create_term(client: httpx.Client, **changes) -> dict:
    response = client.post("/api/v1/terms", json=term_payload(**changes))
    assert response.status_code == 201, response.text
    return response.json()["term"]


def period(
    number: int,
    name: str,
    starts_at: str,
    ends_at: str,
) -> dict:
    return {
        "period_number": number,
        "name": name,
        "starts_at": starts_at,
        "ends_at": ends_at,
    }


def test_calendar_migration_roundtrip_with_existing_feature_data(admin_url):
    with isolated_database(admin_url) as database_url:
        owner_id = uuid4()
        term_id = uuid4()
        with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
            connection.execute(
                """
                INSERT INTO users (id, email, password_hash)
                VALUES (%s, %s, %s)
                """,
                (owner_id, address("migration"), "test-only-password-hash"),
            )
            connection.execute(
                """
                INSERT INTO user_time_settings (owner_id, time_zone)
                VALUES (%s, 'Asia/Shanghai')
                """,
                (owner_id,),
            )
            connection.execute(
                """
                INSERT INTO academic_terms (
                    id, owner_id, name, start_date, end_date,
                    first_week_monday, time_zone
                )
                VALUES (
                    %s, %s, '迁移测试学期', '2026-09-01', '2027-01-31',
                    '2026-08-31', 'Asia/Shanghai'
                )
                """,
                (term_id, owner_id),
            )
            connection.execute(
                """
                INSERT INTO change_events (
                    owner_id, entity_type, entity_id, action, entity_version
                )
                VALUES
                    (%s, 'time_settings', %s, 'created', 1),
                    (%s, 'term', %s, 'created', 1)
                """,
                (owner_id, owner_id, owner_id, term_id),
            )
            connection.execute(
                """
                INSERT INTO user_operations (
                    owner_id, operation_id, scope, request_hash, response
                )
                VALUES (%s, %s, 'terms.create', %s, '{}'::jsonb)
                """,
                (owner_id, uuid4(), "0" * 64),
            )

        migrate(database_url, "downgrade", "0005_tasks")
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT to_regclass('user_time_settings')"
            ).fetchone()[0] is None
            assert connection.execute(
                """
                SELECT count(*) FROM change_events
                WHERE entity_type IN ('time_settings', 'term')
                """
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT count(*) FROM user_operations WHERE scope='terms.create'"
            ).fetchone()[0] == 0

        migrate(database_url, "upgrade", "head")
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT to_regclass('user_time_settings')"
            ).fetchone()[0] == "user_time_settings"


def test_time_settings_initialization_validation_idempotency_and_versioning(
    client,
    database_url,
    live_server,
):
    assert client.get("/api/v1/time-settings").status_code == 401
    _, user_id = create_account(client, database_url)

    initial = client.get("/api/v1/time-settings")
    assert initial.status_code == 200
    assert initial.json()["settings"] == {
        "initialized": False,
        "time_zone": None,
        "week_starts_on": 1,
        "activity_day_start_minute": 240,
        "version": 0,
        "created_at": None,
        "updated_at": None,
    }

    csrf = client.headers.pop("x-csrf-token")
    denied = client.put(
        "/api/v1/time-settings",
        json=settings_payload(),
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "invalid_csrf"
    client.headers["x-csrf-token"] = csrf

    invalid_zone = "Invalid/Private-Time-Zone"
    invalid = client.put(
        "/api/v1/time-settings",
        json=settings_payload(time_zone=invalid_zone),
    )
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "invalid_time_zone"
    assert invalid.json()["error"]["saved"] == "not_saved"
    assert invalid_zone not in invalid.text

    operation_id = uuid4()
    payload = settings_payload(operation_id)
    created = client.put("/api/v1/time-settings", json=payload)
    assert created.status_code == 200, created.text
    settings = created.json()["settings"]
    assert settings["initialized"] is True
    assert settings["time_zone"] == "Asia/Shanghai"
    assert settings["version"] == 1
    replayed = client.put("/api/v1/time-settings", json=payload)
    assert replayed.status_code == 200
    assert replayed.json() == created.json()

    reused = client.put(
        "/api/v1/time-settings",
        json={**payload, "time_zone": "America/New_York"},
    )
    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "operation_id_reused"

    stale = client.put(
        "/api/v1/time-settings",
        json=settings_payload(time_zone="America/New_York"),
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "time_settings_version_conflict"
    assert stale.json()["error"]["details"]["current_version"] == 1

    updated = client.put(
        "/api/v1/time-settings",
        json=settings_payload(
            expected_version=1,
            time_zone="America/New_York",
        ),
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["settings"]["version"] == 2
    assert updated.json()["settings"]["time_zone"] == "America/New_York"
    assert client.get("/api/v1/time-settings").json()["settings"]["version"] == 2

    with psycopg.connect(psycopg_url(database_url)) as connection:
        row = connection.execute(
            """
            SELECT time_zone, week_starts_on, activity_day_start_minute, version
            FROM user_time_settings
            WHERE owner_id=%s
            """,
            (user_id,),
        ).fetchone()
        assert row == ("America/New_York", 1, 240, 2)
        actions = connection.execute(
            """
            SELECT action, entity_version
            FROM change_events
            WHERE owner_id=%s AND entity_type='time_settings'
            ORDER BY sequence
            """,
            (user_id,),
        ).fetchall()
        assert actions == [("created", 1), ("updated", 2)]

    with browser(live_server) as registration:
        invalid_email = address("invalid-zone")
        invalid_registration = registration.post(
            f"{AUTH}/register",
            json={
                "email": invalid_email,
                "password": PASSWORD,
                "time_zone": "Private/Invalid-Zone",
            },
        )
        assert invalid_registration.status_code == 400
        assert invalid_registration.json()["error"]["code"] == "invalid_time_zone"

        registered_email = address("registered-zone")
        registered = registration.post(
            f"{AUTH}/register",
            json={
                "email": registered_email,
                "password": PASSWORD,
                "time_zone": "Europe/Paris",
            },
        )
        assert registered.status_code == 202, registered.text

    with psycopg.connect(psycopg_url(database_url)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM users WHERE email=%s",
            (invalid_email,),
        ).fetchone()[0] == 0
        registered_row = connection.execute(
            """
            SELECT settings.time_zone, settings.version
            FROM user_time_settings AS settings
            JOIN users ON users.id = settings.owner_id
            WHERE users.email=%s
            """,
            (registered_email,),
        ).fetchone()
        assert registered_row == ("Europe/Paris", 1)


def test_activity_day_uses_local_four_am_boundary_including_dst():
    assert activity_day_at(
        datetime(2026, 10, 1, 19, 59, tzinfo=UTC),
        "Asia/Shanghai",
    ).isoformat() == "2026-10-01"
    assert activity_day_at(
        datetime(2026, 10, 1, 20, 0, tzinfo=UTC),
        "Asia/Shanghai",
    ).isoformat() == "2026-10-02"
    assert activity_day_at(
        datetime(2026, 3, 8, 7, 59, tzinfo=UTC),
        "America/New_York",
    ).isoformat() == "2026-03-07"
    assert activity_day_at(
        datetime(2026, 3, 8, 8, 0, tzinfo=UTC),
        "America/New_York",
    ).isoformat() == "2026-03-08"


def test_term_validation_defaults_idempotency_pagination_and_versioning(
    client,
    database_url,
):
    create_account(client, database_url)

    missing_settings = client.post("/api/v1/terms", json=term_payload())
    assert missing_settings.status_code == 409
    assert missing_settings.json()["error"]["code"] == "time_settings_required"
    initialize_settings(client)

    invalid_zone = client.post(
        "/api/v1/terms",
        json=term_payload(time_zone="Not/AZone"),
    )
    assert invalid_zone.status_code == 400
    assert invalid_zone.json()["error"]["code"] == "invalid_time_zone"

    too_long = client.post(
        "/api/v1/terms",
        json=term_payload(
            start_date="2026-01-01",
            end_date="2027-02-05",
            first_week_monday="2025-12-29",
        ),
    )
    assert too_long.status_code == 400
    assert too_long.json()["error"]["code"] == "invalid_term_dates"

    invalid_week = client.post(
        "/api/v1/terms",
        json=term_payload(first_week_monday="2026-08-30"),
    )
    assert invalid_week.status_code == 400
    assert invalid_week.json()["error"]["code"] == "invalid_first_week"

    operation_id = uuid4()
    payload = term_payload(operation_id)
    created = client.post("/api/v1/terms", json=payload)
    assert created.status_code == 201, created.text
    term = created.json()["term"]
    assert term["time_zone"] == "Asia/Shanghai"
    assert term["periods"] == []
    assert term["version"] == 1
    assert client.post("/api/v1/terms", json=payload).json() == created.json()

    reused = client.post(
        "/api/v1/terms",
        json={**payload, "name": "复用操作号"},
    )
    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "operation_id_reused"

    overlapping = create_term(
        client,
        name="重叠的交换学期",
        start_date="2026-10-01",
        end_date="2026-12-31",
        first_week_monday="2026-09-28",
    )
    assert overlapping["id"] != term["id"]

    first_page = client.get("/api/v1/terms", params={"limit": 1})
    assert first_page.status_code == 200
    first_body = first_page.json()
    assert len(first_body["items"]) == 1
    assert first_body["next_cursor"] is not None
    second_page = client.get(
        "/api/v1/terms",
        params={
            "limit": 1,
            **first_body["next_cursor"],
        },
    )
    assert second_page.status_code == 200
    assert len(second_page.json()["items"]) == 1
    assert (
        second_page.json()["items"][0]["id"]
        != first_body["items"][0]["id"]
    )

    update_payload = {
        **term_payload(
            name="2026-2027 秋季学期",
            time_zone="Asia/Tokyo",
        ),
        "expected_version": 1,
    }
    updated = client.put(f"/api/v1/terms/{term['id']}", json=update_payload)
    assert updated.status_code == 200, updated.text
    assert updated.json()["term"]["version"] == 2
    assert updated.json()["term"]["time_zone"] == "Asia/Tokyo"

    stale = client.put(
        f"/api/v1/terms/{term['id']}",
        json={
            **update_payload,
            "operation_id": str(uuid4()),
            "name": "陈旧修改",
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "term_version_conflict"
    assert stale.json()["error"]["details"]["current_version"] == 2
    fetched = client.get(f"/api/v1/terms/{term['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["term"]["name"] == "2026-2027 秋季学期"


def test_period_table_replacement_is_atomic_and_versioned(client, database_url):
    _, user_id = create_account(client, database_url)
    initialize_settings(client)
    term = create_term(client)
    endpoint = f"/api/v1/terms/{term['id']}/periods"
    periods = [
        period(1, "第一节", "08:00:00", "08:45:00"),
        period(2, "第二节", "08:45:00", "09:30:00"),
        period(5, "午间研讨", "12:30:00", "13:15:00"),
    ]

    operation_id = uuid4()
    payload = {
        "operation_id": str(operation_id),
        "expected_version": 1,
        "periods": periods,
    }
    created = client.put(endpoint, json=payload)
    assert created.status_code == 200, created.text
    assert created.json()["term"]["version"] == 2
    assert created.json()["term"]["periods"] == periods
    assert client.put(endpoint, json=payload).json() == created.json()

    reused = client.put(
        endpoint,
        json={**payload, "periods": periods[:2]},
    )
    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "operation_id_reused"

    invalid_cases = [
        (
            "periods_overlap",
            [
                period(1, "第一节", "08:00:00", "09:00:00"),
                period(2, "第二节", "08:59:00", "09:30:00"),
            ],
        ),
        (
            "duplicate_period_number",
            [
                period(1, "第一节", "08:00:00", "08:45:00"),
                period(1, "第二节", "09:00:00", "09:45:00"),
            ],
        ),
        (
            "duplicate_period_name",
            [
                period(1, "Morning", "08:00:00", "08:45:00"),
                period(2, "morning", "09:00:00", "09:45:00"),
            ],
        ),
        (
            "invalid_period_time",
            [
                period(1, "第一节", "08:00:00+08:00", "08:45:00+08:00"),
            ],
        ),
        (
            "too_many_periods",
            [
                period(
                    number,
                    f"第 {number} 节",
                    f"{number // 2:02d}:{(number % 2) * 30:02d}:00",
                    f"{number // 2:02d}:{(number % 2) * 30 + 20:02d}:00",
                )
                for number in range(1, 32)
            ],
        ),
    ]
    for expected_code, invalid_periods in invalid_cases:
        rejected = client.put(
            endpoint,
            json={
                "operation_id": str(uuid4()),
                "expected_version": 2,
                "periods": invalid_periods,
            },
        )
        assert rejected.status_code == 400, rejected.text
        assert rejected.json()["error"]["code"] == expected_code
        assert rejected.json()["error"]["saved"] == "not_saved"

    unchanged = client.get(f"/api/v1/terms/{term['id']}")
    assert unchanged.status_code == 200
    assert unchanged.json()["term"]["version"] == 2
    assert unchanged.json()["term"]["periods"] == periods

    no_op = client.put(
        endpoint,
        json={
            "operation_id": str(uuid4()),
            "expected_version": 2,
            "periods": periods,
        },
    )
    assert no_op.status_code == 200
    assert no_op.json()["term"]["version"] == 2

    replacement = [
        period(1, "第一节", "08:10:00", "08:55:00"),
        period(2, "第二节", "09:00:00", "09:45:00"),
    ]
    replaced = client.put(
        endpoint,
        json={
            "operation_id": str(uuid4()),
            "expected_version": 2,
            "periods": replacement,
        },
    )
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["term"]["version"] == 3
    assert replaced.json()["term"]["periods"] == replacement

    stale = client.put(
        endpoint,
        json={
            "operation_id": str(uuid4()),
            "expected_version": 2,
            "periods": [],
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["details"]["current_version"] == 3

    with psycopg.connect(psycopg_url(database_url)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM term_periods WHERE term_id=%s",
            (term["id"],),
        ).fetchone()[0] == 2
        actions = connection.execute(
            """
            SELECT entity_type, action, entity_version
            FROM change_events
            WHERE owner_id=%s AND entity_type='term'
            ORDER BY sequence
            """,
            (user_id,),
        ).fetchall()
        assert actions == [
            ("term", "created", 1),
            ("term", "updated", 2),
            ("term", "updated", 3),
        ]


def test_term_ownership_and_concurrent_updates(
    client,
    database_url,
    live_server,
):
    email, _ = create_account(client, database_url)
    initialize_settings(client)
    term = create_term(client)

    with browser(live_server) as other:
        create_account(other, database_url)
        assert other.get(f"/api/v1/terms/{term['id']}").status_code == 404
        assert other.get("/api/v1/terms").json()["items"] == []
        cross_update = other.put(
            f"/api/v1/terms/{term['id']}",
            json={
                **term_payload(name="越权修改"),
                "expected_version": 1,
            },
        )
        assert cross_update.status_code == 404
        assert cross_update.json()["error"]["code"] == "term_not_found"
        cross_periods = other.put(
            f"/api/v1/terms/{term['id']}/periods",
            json={
                "operation_id": str(uuid4()),
                "expected_version": 1,
                "periods": [],
            },
        )
        assert cross_periods.status_code == 404

    barrier = Barrier(2)

    def update_with(client_for_update: httpx.Client, name: str) -> httpx.Response:
        barrier.wait(timeout=5)
        return client_for_update.put(
            f"/api/v1/terms/{term['id']}",
            json={
                **term_payload(name=name),
                "expected_version": 1,
            },
        )

    with browser(live_server) as first, browser(live_server) as second:
        login_account(first, email)
        login_account(second, email)
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(
                executor.map(
                    lambda pair: update_with(*pair),
                    [
                        (first, "并发版本 A"),
                        (second, "并发版本 B"),
                    ],
                )
            )

    assert sorted(response.status_code for response in responses) == [200, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["error"]["code"] == "term_version_conflict"
    assert conflict.json()["error"]["details"]["current_version"] == 2
    final = client.get(f"/api/v1/terms/{term['id']}").json()["term"]
    assert final["version"] == 2
    assert final["name"] in {"并发版本 A", "并发版本 B"}
