import asyncio
from contextlib import contextmanager
from datetime import timedelta
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest

from itime.auth.security import now
from itime.config import Settings
from itime.database import Database
from itime.sync.service import cleanup_expired_events
from tests.conftest import psycopg_url

pytestmark = pytest.mark.integration
AUTH = "/api/v1/auth"
PASSWORD = "Valid-sync-password-123"


def create_account(client: httpx.Client, database_url: str) -> tuple[str, UUID]:
    email = f"sync-{uuid4().hex}@example.com"
    registered = client.post(
        f"{AUTH}/register",
        json={"email": email, "password": PASSWORD},
    )
    assert registered.status_code == 202, registered.text
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        owner_id = connection.execute(
            "UPDATE users SET email_verified_at=now() WHERE email=%s RETURNING id",
            (email,),
        ).fetchone()[0]
    logged_in = client.post(
        f"{AUTH}/login",
        json={"email": email, "password": PASSWORD, "device_name": "同步测试"},
    )
    assert logged_in.status_code == 200, logged_in.text
    client.headers["x-csrf-token"] = logged_in.json()["csrf_token"]
    return email, owner_id


@contextmanager
def browser(live_server):
    with httpx.Client(
        base_url=live_server.origin,
        timeout=10,
        trust_env=False,
    ) as client:
        yield client


def insert_event(
    connection,
    owner_id: UUID,
    entity_type: str,
    action: str,
    *,
    entity_version: int,
) -> tuple[int, UUID]:
    entity_id = uuid4()
    sequence = connection.execute(
        """
        INSERT INTO change_events (
            owner_id, entity_type, entity_id, action, entity_version
        ) VALUES (%s, %s, %s, %s, %s)
        RETURNING sequence
        """,
        (owner_id, entity_type, entity_id, action, entity_version),
    ).fetchone()[0]
    return sequence, entity_id


def test_sync_initial_incremental_pagination_and_owner_isolation(
    client,
    database_url,
    live_server,
):
    assert client.get("/api/v1/sync/changes").status_code == 401
    _, owner_id = create_account(client, database_url)

    baseline_response = client.get("/api/v1/sync/changes")
    assert baseline_response.status_code == 200, baseline_response.text
    baseline = baseline_response.json()
    assert baseline["events"] == []
    assert baseline["has_more"] is False
    assert baseline["reset_required"] is True
    assert baseline["reset_reason"] == "initial"
    assert isinstance(baseline["next_cursor"], str)

    with browser(live_server) as other:
        _, other_owner_id = create_account(other, database_url)
        with psycopg.connect(
            psycopg_url(database_url),
            autocommit=True,
        ) as connection:
            first = insert_event(
                connection,
                owner_id,
                "task",
                "created",
                entity_version=1,
            )
            insert_event(
                connection,
                other_owner_id,
                "task",
                "created",
                entity_version=1,
            )
            second = insert_event(
                connection,
                owner_id,
                "step",
                "completed",
                entity_version=2,
            )
            third = insert_event(
                connection,
                owner_id,
                "conversation",
                "updated",
                entity_version=3,
            )

        first_page_response = client.get(
            "/api/v1/sync/changes",
            params={"cursor": baseline["next_cursor"], "limit": 2},
        )
        assert first_page_response.status_code == 200, first_page_response.text
        first_page = first_page_response.json()
        assert first_page["reset_required"] is False
        assert first_page["reset_reason"] is None
        assert first_page["has_more"] is True
        assert [
            (item["sequence"], item["entity_type"], item["entity_id"])
            for item in first_page["events"]
        ] == [
            (first[0], "task", str(first[1])),
            (second[0], "step", str(second[1])),
        ]

        second_page_response = client.get(
            "/api/v1/sync/changes",
            params={"cursor": first_page["next_cursor"], "limit": 2},
        )
        assert second_page_response.status_code == 200
        second_page = second_page_response.json()
        assert second_page["has_more"] is False
        assert [
            (item["sequence"], item["entity_type"], item["entity_id"])
            for item in second_page["events"]
        ] == [(third[0], "conversation", str(third[1]))]

        empty = client.get(
            "/api/v1/sync/changes",
            params={"cursor": second_page["next_cursor"]},
        ).json()
        assert empty["events"] == []
        assert empty["next_cursor"] == second_page["next_cursor"]
        assert empty["reset_required"] is False
        assert "sync-" not in str(first_page)

        cross_owner = other.get(
            "/api/v1/sync/changes",
            params={"cursor": second_page["next_cursor"]},
        )
        assert cross_owner.status_code == 400
        assert cross_owner.json()["error"]["code"] == "invalid_sync_cursor"

        tampered_cursor = (
            second_page["next_cursor"][:-1]
            + ("0" if second_page["next_cursor"][-1] != "0" else "1")
        )
        tampered = client.get(
            "/api/v1/sync/changes",
            params={"cursor": tampered_cursor},
        )
        assert tampered.status_code == 400
        assert tampered.json()["error"]["code"] == "invalid_sync_cursor"

    status = client.get("/api/v1/sync/status")
    assert status.status_code == 200
    assert status.json()["retention_days"] == 30
    assert status.json()["latest_change_at"] is not None


def test_sync_cursor_expired_ahead_and_missing_state(client, database_url):
    _, owner_id = create_account(client, database_url)
    baseline = client.get("/api/v1/sync/changes").json()
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        sequence, _ = insert_event(
            connection,
            owner_id,
            "task",
            "created",
            entity_version=1,
        )

    current = client.get(
        "/api/v1/sync/changes",
        params={"cursor": baseline["next_cursor"]},
    ).json()
    assert len(current["events"]) == 1

    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        connection.execute(
            """
            UPDATE sync_states
            SET discarded_through_sequence=%s, updated_at=now()
            WHERE owner_id=%s
            """,
            (sequence, owner_id),
        )
    expired = client.get(
        "/api/v1/sync/changes",
        params={"cursor": baseline["next_cursor"]},
    )
    assert expired.status_code == 200
    assert expired.json()["events"] == []
    assert expired.json()["reset_required"] is True
    assert expired.json()["reset_reason"] == "cursor_expired"

    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        connection.execute(
            "DELETE FROM change_events WHERE owner_id=%s",
            (owner_id,),
        )
        connection.execute(
            """
            UPDATE sync_states
            SET discarded_through_sequence=0, updated_at=now()
            WHERE owner_id=%s
            """,
            (owner_id,),
        )
    ahead = client.get(
        "/api/v1/sync/changes",
        params={"cursor": current["next_cursor"]},
    )
    assert ahead.status_code == 200
    assert ahead.json()["reset_required"] is True
    assert ahead.json()["reset_reason"] == "cursor_ahead"

    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        connection.execute("DELETE FROM sync_states WHERE owner_id=%s", (owner_id,))
    missing = client.get("/api/v1/sync/changes")
    assert missing.status_code == 503
    assert missing.json()["error"]["code"] == "sync_state_unavailable"


def test_sync_event_cleanup_is_bounded_concurrent_and_advances_each_owner(
    client,
    database_url,
    live_server,
):
    _, owner_id = create_account(client, database_url)
    baseline = client.get("/api/v1/sync/changes").json()["next_cursor"]
    with browser(live_server) as other:
        _, other_owner_id = create_account(other, database_url)

    old = now() - timedelta(days=31)
    future = now() - timedelta(days=1)
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        owner_old = [
            insert_event(
                connection,
                owner_id,
                "task",
                "updated",
                entity_version=index,
            )[0]
            for index in (1, 2, 3)
        ]
        other_old = insert_event(
            connection,
            other_owner_id,
            "task",
            "updated",
            entity_version=1,
        )[0]
        future_event = insert_event(
            connection,
            owner_id,
            "task",
            "updated",
            entity_version=4,
        )[0]
        connection.execute(
            """
            UPDATE change_events SET changed_at=%s
            WHERE sequence = ANY(%s)
            """,
            (old, [*owner_old, other_old]),
        )
        connection.execute(
            "UPDATE change_events SET changed_at=%s WHERE sequence=%s",
            (future, future_event),
        )

    async def clean() -> tuple[list[int], int]:
        database = Database(
            Settings(environment="test", database_url=database_url)
        )
        try:
            first = await asyncio.gather(
                cleanup_expired_events(
                    database,
                    retention_days=30,
                    limit=2,
                ),
                cleanup_expired_events(
                    database,
                    retention_days=30,
                    limit=2,
                ),
            )
            final = await cleanup_expired_events(
                database,
                retention_days=30,
                limit=2,
            )
            return first, final
        finally:
            await database.close()

    first, final = asyncio.run(clean())
    assert sum(first) == 4
    assert final == 0

    with psycopg.connect(psycopg_url(database_url)) as connection:
        assert connection.execute(
            "SELECT sequence FROM change_events WHERE owner_id=%s",
            (owner_id,),
        ).fetchall() == [(future_event,)]
        floors = dict(
            connection.execute(
                """
                SELECT owner_id, discarded_through_sequence
                FROM sync_states
                WHERE owner_id = ANY(%s)
                """,
                ([owner_id, other_owner_id],),
            ).fetchall()
        )
        assert floors[owner_id] == max(owner_old)
        assert floors[other_owner_id] == other_old

    expired = client.get(
        "/api/v1/sync/changes",
        params={"cursor": baseline},
    )
    assert expired.status_code == 200
    assert expired.json()["reset_required"] is True
    assert expired.json()["reset_reason"] == "cursor_expired"
