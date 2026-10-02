from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier
from uuid import uuid4

import httpx
import psycopg
import pytest

from tests.conftest import isolated_database, migrate, psycopg_url

pytestmark = pytest.mark.integration
AUTH = "/api/v1/auth"
PASSWORD = "Valid-series-password-123"


def address(prefix: str = "series") -> str:
    return f"{prefix}-{uuid4().hex}@example.com"


def login_account(client: httpx.Client, email: str) -> None:
    response = client.post(
        f"{AUTH}/login",
        json={
            "email": email,
            "password": PASSWORD,
            "device_name": "日历测试",
        },
    )
    assert response.status_code == 200, response.text
    client.headers["x-csrf-token"] = response.json()["csrf_token"]


def create_account(
    client: httpx.Client,
    database_url: str,
    *,
    time_zone: str = "Asia/Shanghai",
) -> tuple[str, str]:
    email = address()
    response = client.post(
        f"{AUTH}/register",
        json={
            "email": email,
            "password": PASSWORD,
            "time_zone": time_zone,
        },
    )
    assert response.status_code == 202, response.text
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        user_id = connection.execute(
            "UPDATE users SET email_verified_at=now() WHERE email=%s RETURNING id",
            (email,),
        ).fetchone()[0]
    login_account(client, email)
    return email, str(user_id)


@contextmanager
def browser(live_server):
    with httpx.Client(base_url=live_server.origin, timeout=10, trust_env=False) as client:
        yield client


def fixed_payload(operation_id=None, **changes) -> dict:
    payload = {
        "operation_id": str(operation_id or uuid4()),
        "kind": "fixed",
        "term_id": None,
        "name": "专注学习",
        "description": "关闭通知",
        "location": "图书馆",
        "busy": True,
        "time_zone": None,
        "start_date": "2026-10-05",
        "starts_at": "09:00:00",
        "ends_at": "10:00:00",
        "end_day_offset": 0,
        "start_period_number": None,
        "end_period_number": None,
        "ambiguous_utc_offset_minutes": None,
        "recurrence": {
            "frequency": "none",
            "interval": 1,
            "weekdays": [],
            "month": None,
            "day_of_month": None,
            "until": None,
            "count": None,
            "week_numbers": [],
        },
    }
    payload.update(changes)
    return payload


def create_series(client: httpx.Client, **changes) -> dict:
    response = client.post(
        "/api/v1/calendar/series",
        json=fixed_payload(**changes),
    )
    assert response.status_code == 201, response.text
    return response.json()


def recurrence(
    frequency: str,
    *,
    interval: int = 1,
    weekdays=None,
    month=None,
    day_of_month=None,
    until=None,
    count=None,
    week_numbers=None,
) -> dict:
    return {
        "frequency": frequency,
        "interval": interval,
        "weekdays": weekdays or [],
        "month": month,
        "day_of_month": day_of_month,
        "until": until,
        "count": count,
        "week_numbers": week_numbers or [],
    }


def term_payload(**changes) -> dict:
    payload = {
        "operation_id": str(uuid4()),
        "name": "2026 秋季学期",
        "start_date": "2026-09-01",
        "end_date": "2026-10-31",
        "first_week_monday": "2026-08-31",
        "time_zone": None,
    }
    payload.update(changes)
    return payload


def create_term_with_periods(client: httpx.Client) -> dict:
    created = client.post("/api/v1/terms", json=term_payload())
    assert created.status_code == 201, created.text
    term = created.json()["term"]
    periods = client.put(
        f"/api/v1/terms/{term['id']}/periods",
        json={
            "operation_id": str(uuid4()),
            "expected_version": term["version"],
            "periods": [
                {
                    "period_number": 1,
                    "name": "第一节",
                    "starts_at": "08:00:00",
                    "ends_at": "08:45:00",
                },
                {
                    "period_number": 2,
                    "name": "第二节",
                    "starts_at": "08:45:00",
                    "ends_at": "09:30:00",
                },
            ],
        },
    )
    assert periods.status_code == 200, periods.text
    return periods.json()["term"]


def occurrence_dates(client: httpx.Client, start: str, end: str, name: str) -> list[str]:
    response = client.get(
        "/api/v1/calendar/occurrences",
        params={"start": start, "end": end},
    )
    assert response.status_code == 200, response.text
    return [
        item["local_start_date"]
        for item in response.json()["items"]
        if item["name"] == name
    ]


def test_fixed_series_idempotency_occurrences_cross_midnight_and_conflicts(
    client,
    database_url,
):
    assert client.get(
        "/api/v1/calendar/occurrences",
        params={"start": "2026-10-01", "end": "2026-10-02"},
    ).status_code == 401
    _, user_id = create_account(client, database_url)

    operation_id = uuid4()
    payload = fixed_payload(operation_id)
    created = client.post("/api/v1/calendar/series", json=payload)
    assert created.status_code == 201, created.text
    body = created.json()
    series = body["series"]
    assert series["time_zone"] == "Asia/Shanghai"
    assert series["version"] == 1
    assert body["warnings"] == []
    assert body["conflicts"] == {"items": [], "has_more": False}
    assert client.post("/api/v1/calendar/series", json=payload).json() == body

    reused = client.post(
        "/api/v1/calendar/series",
        json={**payload, "name": "复用操作号"},
    )
    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "operation_id_reused"

    queried = client.get(
        "/api/v1/calendar/occurrences",
        params={"start": "2026-10-05", "end": "2026-10-06"},
    )
    assert queried.status_code == 200, queried.text
    occurrence = queried.json()["items"][0]
    assert occurrence["occurrence_key"] == "2026-10-05T09:00:00"
    assert occurrence["start_at"] == "2026-10-05T01:00:00Z"
    assert occurrence["end_at"] == "2026-10-05T02:00:00Z"
    assert occurrence["conflict_with"] == []

    overlap = create_series(
        client,
        name="重叠会议",
        starts_at="09:30:00",
        ends_at="10:30:00",
    )
    assert len(overlap["conflicts"]["items"]) == 1
    assert overlap["conflicts"]["items"][0]["conflicting_series_id"] == series["id"]
    with_conflicts = client.get(
        "/api/v1/calendar/occurrences",
        params={"start": "2026-10-05", "end": "2026-10-06"},
    ).json()["items"]
    conflict_pairs = {
        (item["name"], item["conflict_with"][0]["series_id"])
        for item in with_conflicts
        if item["conflict_with"]
    }
    assert conflict_pairs == {
        ("专注学习", overlap["series"]["id"]),
        ("重叠会议", series["id"]),
    }

    adjacent = create_series(
        client,
        name="相邻安排",
        starts_at="10:30:00",
        ends_at="11:00:00",
    )
    assert adjacent["conflicts"] == {"items": [], "has_more": False}

    free = create_series(
        client,
        name="不占用时间",
        busy=False,
        starts_at="09:15:00",
        ends_at="09:45:00",
    )
    assert free["conflicts"] == {"items": [], "has_more": False}

    overnight = create_series(
        client,
        name="跨夜值守",
        start_date="2026-10-05",
        starts_at="23:00:00",
        ends_at="01:00:00",
        end_day_offset=1,
    )
    assert overnight["series"]["end_day_offset"] == 1
    next_day = client.get(
        "/api/v1/calendar/occurrences",
        params={"start": "2026-10-06", "end": "2026-10-07"},
    )
    assert next_day.status_code == 200
    assert [item["name"] for item in next_day.json()["items"]] == ["跨夜值守"]

    too_wide = client.get(
        "/api/v1/calendar/occurrences",
        params={"start": "2026-01-01", "end": "2026-04-05"},
    )
    assert too_wide.status_code == 400
    assert too_wide.json()["error"]["code"] == "calendar_range_too_large"

    with psycopg.connect(psycopg_url(database_url)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM calendar_series WHERE owner_id=%s",
            (user_id,),
        ).fetchone()[0] == 5
        assert connection.execute(
            """
            SELECT count(*) FROM change_events
            WHERE owner_id=%s AND entity_type='calendar_series'
            """,
            (user_id,),
        ).fetchone()[0] == 5


def test_structured_recurrence_daily_weekday_weekly_monthly_and_yearly(
    client,
    database_url,
):
    create_account(client, database_url)
    create_series(
        client,
        name="隔日",
        start_date="2026-01-01",
        starts_at="08:00:00",
        ends_at="08:30:00",
        recurrence=recurrence("daily", interval=2, count=3),
    )
    create_series(
        client,
        name="工作日",
        start_date="2026-01-02",
        starts_at="09:00:00",
        ends_at="09:30:00",
        recurrence=recurrence("weekdays", until="2026-01-09"),
    )
    create_series(
        client,
        name="双周",
        start_date="2026-01-05",
        starts_at="10:00:00",
        ends_at="10:30:00",
        recurrence=recurrence(
            "weekly",
            interval=2,
            weekdays=[1, 3],
            until="2026-01-31",
        ),
    )
    create_series(
        client,
        name="月末",
        start_date="2026-01-01",
        starts_at="11:00:00",
        ends_at="11:30:00",
        recurrence=recurrence("monthly", day_of_month=31, count=3),
    )
    create_series(
        client,
        name="闰日",
        start_date="2025-01-01",
        starts_at="12:00:00",
        ends_at="12:30:00",
        recurrence=recurrence(
            "yearly",
            month=2,
            day_of_month=29,
            count=2,
        ),
    )

    assert occurrence_dates(client, "2026-01-01", "2026-01-10", "隔日") == [
        "2026-01-01",
        "2026-01-03",
        "2026-01-05",
    ]
    assert occurrence_dates(client, "2026-01-01", "2026-01-10", "工作日") == [
        "2026-01-02",
        "2026-01-05",
        "2026-01-06",
        "2026-01-07",
        "2026-01-08",
        "2026-01-09",
    ]
    assert occurrence_dates(client, "2026-01-01", "2026-02-01", "双周") == [
        "2026-01-05",
        "2026-01-07",
        "2026-01-19",
        "2026-01-21",
    ]
    assert occurrence_dates(client, "2026-01-01", "2026-04-01", "月末") == [
        "2026-01-31",
        "2026-03-31",
    ]
    assert occurrence_dates(client, "2028-02-01", "2028-03-01", "闰日") == [
        "2028-02-29"
    ]
    assert occurrence_dates(client, "2032-02-01", "2032-03-01", "闰日") == [
        "2032-02-29"
    ]

    excessive = client.post(
        "/api/v1/calendar/series",
        json=fixed_payload(
            name="过多实例",
            start_date="2026-01-01",
            recurrence=recurrence("daily", until="2027-01-02"),
        ),
    )
    assert excessive.status_code == 400
    assert excessive.json()["error"]["code"] == "too_many_occurrences"

    malformed = client.post(
        "/api/v1/calendar/series",
        json=fixed_payload(
            name="错误周规则",
            recurrence=recurrence("weekly", weekdays=[1, 1], count=2),
        ),
    )
    assert malformed.status_code == 400
    assert malformed.json()["error"]["code"] == "invalid_recurrence_weekdays"

    date_overflow = client.post(
        "/api/v1/calendar/series",
        json=fixed_payload(
            name="超出日历上限",
            start_date="9999-12-31",
            recurrence=recurrence("daily", count=2),
        ),
    )
    assert date_overflow.status_code == 400
    assert date_overflow.json()["error"]["code"] == "recurrence_unreachable"


def test_course_series_uses_term_weeks_and_period_snapshot(client, database_url):
    create_account(client, database_url)
    term = create_term_with_periods(client)
    payload = fixed_payload(
        kind="course",
        term_id=term["id"],
        name="数据库系统",
        description="双周课程",
        location="教学楼 201",
        start_date=None,
        starts_at=None,
        ends_at=None,
        start_period_number=1,
        end_period_number=2,
        recurrence=recurrence(
            "weekly",
            weekdays=[1, 3],
            week_numbers=[1, 2, 4],
        ),
    )
    created = client.post("/api/v1/calendar/series", json=payload)
    assert created.status_code == 201, created.text
    series = created.json()["series"]
    assert series["start_date"] == "2026-09-02"
    assert series["starts_at"] == "08:00:00"
    assert series["ends_at"] == "09:30:00"
    assert series["recurrence"]["until"] == "2026-09-23"
    assert series["recurrence"]["week_numbers"] == [1, 2, 4]
    assert occurrence_dates(
        client,
        "2026-09-01",
        "2026-10-01",
        "数据库系统",
    ) == [
        "2026-09-02",
        "2026-09-07",
        "2026-09-09",
        "2026-09-21",
        "2026-09-23",
    ]

    bad_week = client.post(
        "/api/v1/calendar/series",
        json={
            **payload,
            "operation_id": str(uuid4()),
            "recurrence": recurrence(
                "weekly",
                weekdays=[1],
                week_numbers=[10],
            ),
        },
    )
    assert bad_week.status_code == 400
    assert bad_week.json()["error"]["code"] == "invalid_course_weeks"

    missing_period = client.post(
        "/api/v1/calendar/series",
        json={
            **payload,
            "operation_id": str(uuid4()),
            "end_period_number": 3,
        },
    )
    assert missing_period.status_code == 409
    assert missing_period.json()["error"]["code"] == "period_not_found"

    conflicting_time = client.post(
        "/api/v1/calendar/series",
        json={
            **payload,
            "operation_id": str(uuid4()),
            "starts_at": "08:00:00",
        },
    )
    assert conflicting_time.status_code == 400
    assert conflicting_time.json()["error"]["code"] == "period_time_conflict"


def test_dst_missing_and_ambiguous_local_times_are_explicit(client, database_url):
    create_account(client, database_url, time_zone="America/New_York")

    missing = client.post(
        "/api/v1/calendar/series",
        json=fixed_payload(
            name="不存在的本地时刻",
            start_date="2026-03-08",
            starts_at="02:30:00",
            ends_at="03:30:00",
        ),
    )
    assert missing.status_code == 400
    assert missing.json()["error"]["code"] == "invalid_local_time"

    recurring = create_series(
        client,
        name="跨夏令时重复",
        start_date="2026-03-07",
        starts_at="02:30:00",
        ends_at="03:00:00",
        recurrence=recurrence("daily", count=3),
    )
    assert recurring["warnings"] == [
        {
            "code": "invalid_local_time",
            "occurrence_key": "2026-03-08T02:30:00",
            "message": "该次安排位于夏令时跳过的本地时间，未自动平移。",
        }
    ]
    queried = client.get(
        "/api/v1/calendar/occurrences",
        params={"start": "2026-03-07", "end": "2026-03-10"},
    )
    assert queried.status_code == 200
    invalid_occurrence = next(
        item
        for item in queried.json()["items"]
        if item["occurrence_key"] == "2026-03-08T02:30:00"
    )
    assert invalid_occurrence["start_at"] is None
    assert invalid_occurrence["warning_codes"] == ["invalid_local_time"]

    ambiguous_payload = fixed_payload(
        name="重复的本地时刻",
        start_date="2026-11-01",
        starts_at="01:30:00",
        ends_at="01:45:00",
    )
    ambiguous = client.post(
        "/api/v1/calendar/series",
        json=ambiguous_payload,
    )
    assert ambiguous.status_code == 400
    assert ambiguous.json()["error"]["code"] == "ambiguous_local_time"
    assert set(
        ambiguous.json()["error"]["details"]["candidate_offsets_minutes"]
    ) == {-300, -240}

    resolved = client.post(
        "/api/v1/calendar/series",
        json={
            **ambiguous_payload,
            "operation_id": str(uuid4()),
            "ambiguous_utc_offset_minutes": -240,
        },
    )
    assert resolved.status_code == 201, resolved.text
    resolved_items = client.get(
        "/api/v1/calendar/occurrences",
        params={"start": "2026-11-01", "end": "2026-11-02"},
    ).json()["items"]
    chosen = next(item for item in resolved_items if item["name"] == "重复的本地时刻")
    assert chosen["start_at"] == "2026-11-01T05:30:00Z"
    assert chosen["warning_codes"] == []


def test_series_versioning_ownership_pagination_and_concurrency(
    client,
    database_url,
    live_server,
):
    email, _ = create_account(client, database_url)
    first = create_series(client, name="版本安排")["series"]
    create_series(
        client,
        name="第二项",
        start_date="2026-10-06",
    )

    page_one = client.get("/api/v1/calendar/series", params={"limit": 1})
    assert page_one.status_code == 200
    cursor = page_one.json()["next_cursor"]
    assert cursor is not None
    page_two = client.get(
        "/api/v1/calendar/series",
        params={"limit": 1, **cursor},
    )
    assert page_two.status_code == 200
    assert page_one.json()["items"][0]["id"] != page_two.json()["items"][0]["id"]

    no_op = client.put(
        f"/api/v1/calendar/series/{first['id']}",
        json={
            **fixed_payload(name="版本安排"),
            "expected_version": 1,
        },
    )
    assert no_op.status_code == 200
    assert no_op.json()["series"]["version"] == 1

    with browser(live_server) as other:
        create_account(other, database_url)
        assert other.get(
            f"/api/v1/calendar/series/{first['id']}"
        ).status_code == 404
        forbidden = other.put(
            f"/api/v1/calendar/series/{first['id']}",
            json={
                **fixed_payload(name="越权"),
                "expected_version": 1,
            },
        )
        assert forbidden.status_code == 404

    barrier = Barrier(2)

    def update_with(session: httpx.Client, name: str) -> httpx.Response:
        barrier.wait(timeout=5)
        return session.put(
            f"/api/v1/calendar/series/{first['id']}",
            json={
                **fixed_payload(name=name),
                "expected_version": 1,
            },
        )

    with browser(live_server) as left, browser(live_server) as right:
        login_account(left, email)
        login_account(right, email)
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(
                executor.map(
                    lambda pair: update_with(*pair),
                    [
                        (left, "并发更新 A"),
                        (right, "并发更新 B"),
                    ],
                )
            )
    assert sorted(response.status_code for response in responses) == [200, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["error"]["code"] == "calendar_series_version_conflict"
    final = client.get(f"/api/v1/calendar/series/{first['id']}").json()["series"]
    assert final["version"] == 2
    assert final["name"] in {"并发更新 A", "并发更新 B"}


def test_calendar_series_migration_roundtrip_with_data(admin_url):
    with isolated_database(admin_url) as database_url:
        owner_id = uuid4()
        series_id = uuid4()
        with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
            connection.execute(
                """
                INSERT INTO users (id, email, password_hash)
                VALUES (%s, %s, %s)
                """,
                (owner_id, address("series-migration"), "test-password-hash"),
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
                INSERT INTO change_events (
                    owner_id, entity_type, entity_id, action, entity_version
                )
                VALUES (%s, 'calendar_series', %s, 'created', 1)
                """,
                (owner_id, series_id),
            )
            connection.execute(
                """
                INSERT INTO user_operations (
                    owner_id, operation_id, scope, request_hash, response
                )
                VALUES (%s, %s, 'calendar_series.create', %s, '{}'::jsonb)
                """,
                (owner_id, uuid4(), "0" * 64),
            )

        migrate(database_url, "downgrade", "0006_time_terms")
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT to_regclass('calendar_series')"
            ).fetchone()[0] is None
            assert connection.execute(
                """
                SELECT count(*) FROM change_events
                WHERE entity_type='calendar_series'
                """
            ).fetchone()[0] == 0
            assert connection.execute(
                """
                SELECT count(*) FROM user_operations
                WHERE scope='calendar_series.create'
                """
            ).fetchone()[0] == 0

        migrate(database_url, "upgrade", "head")
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT to_regclass('calendar_series')"
            ).fetchone()[0] == "calendar_series"
