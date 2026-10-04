import asyncio
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import psycopg
import pytest
from psycopg import sql

import itime.calendar.lifecycle_service as lifecycle_service
from itime.calendar.lifecycle_service import cleanup_expired_calendar_items
from itime.config import Settings
from itime.database import Database
from tests.conftest import psycopg_url
from tests.test_calendar_additions import addition_payload
from tests.test_calendar_exceptions import exception_payload
from tests.test_calendar_lifecycle import trash_series, trash_subscription
from tests.test_calendar_series import create_account, create_series, recurrence
from tests.test_calendar_subscriptions import (
    FakeFetcher,
    create_subscription,
    daily_ics,
    result,
    run_worker,
)

pytestmark = pytest.mark.integration
SERVER = Path(__file__).resolve().parents[1]


async def cleanup(database_url: str, *, limit: int = 100) -> dict[str, int]:
    database = Database(
        Settings(database_url=database_url, environment="test")
    )
    try:
        return await cleanup_expired_calendar_items(database, limit=limit)
    finally:
        await database.close()


def make_due(connection, table: str, item_id: UUID) -> None:
    if table not in {"calendar_series", "calendar_sources"}:
        raise ValueError("unsupported calendar lifecycle table")
    connection.execute(
        sql.SQL(
            """
        UPDATE {}
        SET deleted_at=now() - interval '31 days',
            purge_after=now() - interval '1 day'
        WHERE id=%s
        """,
        ).format(sql.Identifier(table)),
        (item_id,),
    )


def test_cleanup_redacts_series_and_preserves_tombstone(
    client,
    database_url,
):
    _, owner_id = create_account(client, database_url, time_zone="UTC")
    series = create_series(
        client,
        name="需要彻底清理的安排",
        description="私人说明",
        location="私人地点",
        start_date="2026-10-05",
        recurrence=recurrence("daily", count=3),
    )["series"]
    cancelled = client.post(
        f"/api/v1/calendar/series/{series['id']}/exceptions",
        json=exception_payload(
            expected_series_version=series["version"],
            occurrence_key="2026-10-06T09:00:00",
            action="cancelled",
        ),
    )
    assert cancelled.status_code == 201
    series = cancelled.json()["series"]
    added = client.post(
        f"/api/v1/calendar/series/{series['id']}/additions",
        json=addition_payload(
            expected_series_version=series["version"],
            source_occurrence_key="2026-10-06T09:00:00",
            local_start_date="2026-10-08",
            override_name="私人补课",
        ),
    )
    assert added.status_code == 201
    series = added.json()["series"]
    deleted = trash_series(client, series)
    assert deleted.status_code == 200

    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,
    ) as connection:
        make_due(connection, "calendar_series", UUID(series["id"]))
        assert connection.execute(
            """
            SELECT count(*)
            FROM user_operations
            WHERE owner_id=%s AND response::text LIKE %s
            """,
            (UUID(owner_id), f"%{series['id']}%"),
        ).fetchone()[0] > 0

    assert asyncio.run(cleanup(database_url)) == {
        "series": 1,
        "sources": 0,
    }

    assert client.get(
        f"/api/v1/calendar/series/{series['id']}"
    ).status_code == 410
    with psycopg.connect(psycopg_url(database_url)) as connection:
        tombstone = connection.execute(
            """
            SELECT
                kind, name, description, location, busy, source_type,
                term_id, predecessor_id, source_id, time_zone, start_date,
                starts_at, ends_at, end_day_offset, recurrence_frequency,
                recurrence_weekdays, recurrence_count, course_week_numbers,
                deleted_at, purge_after, purged_at
            FROM calendar_series
            WHERE id=%s
            """,
            (UUID(series["id"]),),
        ).fetchone()
        assert tombstone[:18] == (
            "fixed",
            "已彻底删除的安排",
            "",
            "",
            False,
            "manual",
            None,
            None,
            None,
            "UTC",
            datetime(1970, 1, 1).date(),
            datetime.min.time(),
            datetime.min.time(),
            1,
            "none",
            [],
            None,
            [],
        )
        assert all(value is not None for value in tombstone[18:])
        assert connection.execute(
            "SELECT count(*) FROM calendar_exceptions WHERE series_id=%s",
            (UUID(series["id"]),),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM calendar_additions WHERE series_id=%s",
            (UUID(series["id"]),),
        ).fetchone()[0] == 0
        assert connection.execute(
            """
            SELECT count(*)
            FROM user_operations
            WHERE owner_id=%s
              AND response IS NOT NULL
              AND response::text LIKE %s
            """,
            (UUID(owner_id), f"%{series['id']}%"),
        ).fetchone()[0] == 0
        assert connection.execute(
            """
            SELECT count(*)
            FROM change_events
            WHERE owner_id=%s
              AND entity_type='calendar_series'
              AND entity_id=%s
              AND action='purged'
            """,
            (UUID(owner_id), UUID(series["id"])),
        ).fetchone()[0] == 1


def test_cleanup_redacts_subscription_url_events_and_refreshes(
    client,
    database_url,
):
    _, owner_id = create_account(client, database_url, time_zone="UTC")
    subscription = create_subscription(client, name="需要清理的私人订阅")
    source_id = subscription["id"]
    assert asyncio.run(
        run_worker(
            database_url,
            FakeFetcher(
                [
                    result(
                        daily_ics(
                            "私人课程标题",
                            datetime.now(UTC).date(),
                        )
                    )
                ]
            ),
        )
    )
    detail = client.get(
        f"/api/v1/calendar/subscriptions/{source_id}"
    ).json()["subscription"]
    deleted = trash_subscription(client, detail)
    assert deleted.status_code == 200

    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,
    ) as connection:
        make_due(connection, "calendar_sources", UUID(source_id))

    assert asyncio.run(cleanup(database_url)) == {
        "series": 0,
        "sources": 1,
    }
    assert client.get(
        f"/api/v1/calendar/subscriptions/{source_id}"
    ).status_code == 410

    with psycopg.connect(psycopg_url(database_url)) as connection:
        source = connection.execute(
            """
            SELECT name, deleted_at, purge_after, purged_at
            FROM calendar_sources
            WHERE id=%s
            """,
            (UUID(source_id),),
        ).fetchone()
        assert source[0] == "已彻底删除的订阅"
        assert all(value is not None for value in source[1:])
        subscription_row = connection.execute(
            """
            SELECT
                encrypted_url, url_hmac, display_url, display_host,
                default_time_zone, lifecycle_status, refresh_status,
                lease_id, lease_until, etag, last_modified,
                encrypted_final_url, final_url_hmac, final_host,
                content_sha256, last_error_code, last_error_message
            FROM calendar_subscriptions
            WHERE source_id=%s
            """,
            (UUID(source_id),),
        ).fetchone()
        assert bytes(subscription_row[0]) == b""
        assert subscription_row[1:7] == (
            "0" * 64,
            "已清理",
            "deleted.invalid",
            "UTC",
            "paused",
            "idle",
        )
        assert all(value is None for value in subscription_row[7:])
        assert connection.execute(
            """
            SELECT count(*)
            FROM calendar_subscription_events
            WHERE source_id=%s
            """,
            (UUID(source_id),),
        ).fetchone()[0] == 0
        assert connection.execute(
            """
            SELECT count(*)
            FROM calendar_subscription_refreshes
            WHERE source_id=%s
            """,
            (UUID(source_id),),
        ).fetchone()[0] == 0
        assert connection.execute(
            """
            SELECT count(*)
            FROM user_operations
            WHERE owner_id=%s
              AND response IS NOT NULL
              AND response::text LIKE %s
            """,
            (UUID(owner_id), f"%{source_id}%"),
        ).fetchone()[0] == 0


def test_cleanup_limit_two_workers_and_pending_deletion_skip(
    client,
    database_url,
):
    _, owner_id = create_account(client, database_url, time_zone="UTC")
    series = [
        create_series(client, name=f"并发清理 {index}")["series"]
        for index in range(3)
    ]
    for item in series:
        assert trash_series(client, item).status_code == 200
    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,
    ) as connection:
        for item in series:
            make_due(connection, "calendar_series", UUID(item["id"]))
        connection.execute(
            "UPDATE users SET status='pending_deletion' WHERE id=%s",
            (UUID(owner_id),),
        )

    assert asyncio.run(cleanup(database_url, limit=1)) == {
        "series": 0,
        "sources": 0,
    }

    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            "UPDATE users SET status='active' WHERE id=%s",
            (UUID(owner_id),),
        )

    settings = Settings(database_url=database_url, environment="test")
    databases = [Database(settings), Database(settings)]

    async def run_two():
        try:
            return await asyncio.gather(
                cleanup_expired_calendar_items(databases[0], limit=1),
                cleanup_expired_calendar_items(databases[1], limit=1),
            )
        finally:
            await asyncio.gather(*(database.close() for database in databases))

    results = asyncio.run(run_two())
    assert sum(item["series"] for item in results) == 1
    assert asyncio.run(cleanup(database_url, limit=1)) == {
        "series": 1,
        "sources": 0,
    }
    assert asyncio.run(cleanup(database_url, limit=1)) == {
        "series": 1,
        "sources": 0,
    }
    assert asyncio.run(cleanup(database_url, limit=1)) == {
        "series": 0,
        "sources": 0,
    }


def test_cleanup_failure_rolls_back_and_worker_once_runs(
    client,
    database_url,
    monkeypatch,
):
    create_account(client, database_url, time_zone="UTC")
    series = create_series(client, name="失败回滚安排")["series"]
    assert trash_series(client, series).status_code == 200
    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,
    ) as connection:
        make_due(connection, "calendar_series", UUID(series["id"]))

    original = lifecycle_service._purge_series

    async def fail_after_mutation(session, item, *, current_time):
        await original(session, item, current_time=current_time)
        raise RuntimeError("forced cleanup failure")

    monkeypatch.setattr(lifecycle_service, "_purge_series", fail_after_mutation)
    with pytest.raises(RuntimeError, match="forced cleanup failure"):
        asyncio.run(cleanup(database_url))

    with psycopg.connect(psycopg_url(database_url)) as connection:
        assert connection.execute(
            "SELECT name, purged_at FROM calendar_series WHERE id=%s",
            (UUID(series["id"]),),
        ).fetchone() == ("失败回滚安排", None)

    monkeypatch.setattr(lifecycle_service, "_purge_series", original)
    completed = subprocess.run(
        [sys.executable, "-m", "itime.calendar.cleanup_worker", "--once"],
        cwd=SERVER,
        env={
            **os.environ,
            "ITIME_ENVIRONMENT": "test",
            "ITIME_DATABASE_URL": database_url,
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    with psycopg.connect(psycopg_url(database_url)) as connection:
        assert connection.execute(
            "SELECT purged_at IS NOT NULL FROM calendar_series WHERE id=%s",
            (UUID(series["id"]),),
        ).fetchone() == (True,)
