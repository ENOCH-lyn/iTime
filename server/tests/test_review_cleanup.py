import asyncio
from datetime import timedelta
from uuid import UUID, uuid4

import psycopg
import pytest

from itime.auth.security import now
from itime.config import Settings
from itime.database import Database
from itime.review.memory_service import cleanup_expired_memories
from tests.conftest import psycopg_url
from tests.test_tasks import create_account

pytestmark = pytest.mark.integration


def test_expired_memory_cleanup_is_bounded_idempotent_and_irreversible(
    client,
    database_url,
):
    _, owner_id_value = create_account(client, database_url)
    owner_id = UUID(owner_id_value)
    due_ids = [uuid4(), uuid4()]
    future_id = uuid4()
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        for memory_id in due_ids:
            connection.execute(
                """
                INSERT INTO memories (
                    id, owner_id, kind, title, content, source_type,
                    deleted_at, purge_after, version
                ) VALUES (
                    %s, %s, 'context', '待清理', '清理后不应保留的正文',
                    'manual', %s, %s, 2
                )
                """,
                (
                    memory_id,
                    owner_id,
                    now() - timedelta(days=31),
                    now() - timedelta(seconds=1),
                ),
            )
        connection.execute(
            """
            INSERT INTO memories (
                id, owner_id, kind, title, content, source_type,
                deleted_at, purge_after, version
            ) VALUES (
                %s, %s, 'context', '仍在恢复期', '仍应保留',
                'manual', %s, %s, 2
            )
            """,
            (
                future_id,
                owner_id,
                now() - timedelta(days=1),
                now() + timedelta(days=29),
            ),
        )

    database = Database(Settings(database_url=database_url, environment="test"))

    async def cleanup() -> tuple[int, int, int]:
        try:
            first = await cleanup_expired_memories(database, limit=1)
            second = await cleanup_expired_memories(database, limit=100)
            third = await cleanup_expired_memories(database, limit=100)
            return first, second, third
        finally:
            await database.close()

    assert asyncio.run(cleanup()) == (1, 1, 0)
    with psycopg.connect(psycopg_url(database_url)) as connection:
        due_rows = connection.execute(
            """
            SELECT title, content, source_proposal_id, purged_at, version
            FROM memories
            WHERE id = ANY(%s)
            """,
            (due_ids,),
        ).fetchall()
        assert all(
            row[0] is None
            and row[1] is None
            and row[2] is None
            and row[3] is not None
            and row[4] == 3
            for row in due_rows
        )
        assert connection.execute(
            "SELECT title, content, purged_at FROM memories WHERE id=%s",
            (future_id,),
        ).fetchone() == ("仍在恢复期", "仍应保留", None)

    purged = client.get(f"/api/v1/memories/{due_ids[0]}")
    assert purged.status_code == 410
    assert purged.json()["error"]["code"] == "memory_purged"
