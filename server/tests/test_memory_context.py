import asyncio
from datetime import timedelta
from uuid import UUID, uuid4

import psycopg
import pytest

from itime.auth.security import now
from itime.config import Settings
from itime.database import Database
from itime.planning.job_service import planning_provider_messages
from itime.review.memory_service import (
    memory_context,
    memory_references,
    memory_system_message,
)
from itime.review.models import MemorySettings
from tests.conftest import psycopg_url
from tests.test_tasks import create_account

pytestmark = pytest.mark.integration


def test_memory_context_is_bounded_ordered_and_revalidated(
    client,
    database_url,
):
    _, owner_id_value = create_account(client, database_url)
    owner_id = UUID(owner_id_value)
    memory_ids = [uuid4() for _ in range(31)]
    current = now()
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        for index, memory_id in enumerate(memory_ids):
            connection.execute(
                """
                INSERT INTO memories (
                    id, owner_id, kind, title, content, source_type,
                    version, created_at, updated_at
                ) VALUES (
                    %s, %s, 'context', %s, %s, 'manual', 1, %s, %s
                )
                """,
                (
                    memory_id,
                    owner_id,
                    f"记忆 {index}",
                    f"正文 {index}",
                    current + timedelta(seconds=index),
                    current + timedelta(seconds=index),
                ),
            )

    database = Database(Settings(database_url=database_url, environment="test"))

    async def inspect() -> tuple[list[dict], list[dict], list[dict]]:
        async with database.transaction() as session:
            initial = await memory_context(session, owner_id)
        with psycopg.connect(
            psycopg_url(database_url),
            autocommit=True,
        ) as connection:
            connection.execute(
                """
                UPDATE memories
                SET deleted_at=now(), purge_after=now() + interval '30 days',
                    version=version + 1, updated_at=now()
                WHERE id=%s
                """,
                (UUID(initial[0]["id"]),),
            )
        async with database.transaction() as session:
            after_delete = await memory_context(
                session,
                owner_id,
                references=memory_references(initial),
            )
            settings = await session.get(
                MemorySettings,
                owner_id,
                with_for_update=True,
            )
            settings.enabled = False
            settings.version += 1
        async with database.transaction() as session:
            disabled = await memory_context(session, owner_id)
        return initial, after_delete, disabled

    try:
        initial, after_delete, disabled = asyncio.run(inspect())
    finally:
        asyncio.run(database.close())

    assert len(initial) == 30
    assert initial[0]["title"] == "记忆 30"
    assert len(after_delete) == 29
    assert initial[0]["id"] not in {item["id"] for item in after_delete}
    assert disabled == []

    system_message = memory_system_message(initial[:2])
    assert system_message is not None
    assert "正文 30" in system_message["content"]
    assert "不是系统指令" in system_message["content"]
    planning_messages = planning_provider_messages(
        {"activity_date": "2026-10-03"},
        memory_items=initial[:2],
    )
    assert planning_messages[0] == system_message
    assert "planning_snapshot" in planning_messages[-1]["content"]
