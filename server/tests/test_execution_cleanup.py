import asyncio
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from itime.config import Settings
from itime.database import Database
from itime.execution.difficulty_service import cleanup_expired_difficulties
from tests.conftest import psycopg_url
from tests.test_difficulties import difficulty_payload, lifecycle_payload
from tests.test_task_schedules import initialize_settings
from tests.test_tasks import create_account

pytestmark = pytest.mark.integration
SERVER = Path(__file__).resolve().parents[1]


def _expire(database_url: str, difficulty_ids: list[str]) -> None:
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        connection.execute(
            """
            UPDATE difficulty_records
            SET deleted_at=now() - interval '31 days',
                purge_after=now() - interval '1 second'
            WHERE id = ANY(%s::uuid[])
            """,
            (difficulty_ids,),
        )


def test_difficulty_cleanup_purges_content_and_operation_responses(
    client,
    database_url,
):
    create_account(client, database_url)
    initialize_settings(client)
    create_operation = uuid4()
    created = client.post(
        "/api/v1/difficulties",
        json=difficulty_payload(
            create_operation,
            content="清理后不应保留的困难正文",
            category="资料",
            emotion="焦虑",
        ),
    ).json()["difficulty"]
    deleted = client.request(
        "DELETE",
        f"/api/v1/difficulties/{created['id']}",
        json=lifecycle_payload(created["version"]),
    )
    assert deleted.status_code == 200
    _expire(database_url, [created["id"]])

    database = Database(Settings(database_url=database_url, environment="test"))

    async def cleanup() -> None:
        try:
            assert await cleanup_expired_difficulties(database) == 1
            assert await cleanup_expired_difficulties(database) == 0
        finally:
            await database.close()

    asyncio.run(cleanup())

    detail = client.get(f"/api/v1/difficulties/{created['id']}")
    assert detail.status_code == 410
    assert "清理后不应保留的困难正文" not in detail.text
    replay = client.post(
        "/api/v1/difficulties",
        json=difficulty_payload(
            create_operation,
            content="清理后不应保留的困难正文",
            category="资料",
            emotion="焦虑",
        ),
    )
    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "operation_result_expired"

    with psycopg.connect(psycopg_url(database_url)) as connection:
        row = connection.execute(
            """
            SELECT content, category, emotion, task_id, focus_session_id,
                   occurrence_key, task_title_snapshot, purged_at
            FROM difficulty_records
            WHERE id=%s
            """,
            (created["id"],),
        ).fetchone()
        assert row[:-1] == (None, None, None, None, None, None, None)
        assert row[-1] is not None
        operation = connection.execute(
            """
            SELECT response, response_purged_at
            FROM user_operations
            WHERE operation_id=%s
            """,
            (create_operation,),
        ).fetchone()
        assert operation[0] is None
        assert operation[1] is not None


def test_difficulty_cleanup_workers_skip_locked(client, database_url):
    create_account(client, database_url)
    initialize_settings(client)
    ids = []
    for index in range(3):
        response = client.post(
            "/api/v1/difficulties",
            json=difficulty_payload(content=f"并发清理 {index}"),
        )
        record = response.json()["difficulty"]
        ids.append(record["id"])
        client.request(
            "DELETE",
            f"/api/v1/difficulties/{record['id']}",
            json=lifecycle_payload(record["version"]),
        )
    _expire(database_url, ids)

    databases = [
        Database(Settings(database_url=database_url, environment="test"))
        for _ in range(2)
    ]

    async def cleanup() -> list[int]:
        try:
            return await asyncio.gather(
                *(cleanup_expired_difficulties(db, limit=2) for db in databases)
            )
        finally:
            await asyncio.gather(*(db.close() for db in databases))

    assert sorted(asyncio.run(cleanup())) == [1, 2]


def test_difficulty_cleanup_worker_runs_once(client, database_url):
    create_account(client, database_url)
    initialize_settings(client)
    created = client.post(
        "/api/v1/difficulties",
        json=difficulty_payload(content="独立进程清理"),
    ).json()["difficulty"]
    client.request(
        "DELETE",
        f"/api/v1/difficulties/{created['id']}",
        json=lifecycle_payload(created["version"]),
    )
    _expire(database_url, [created["id"]])

    completed = subprocess.run(
        [sys.executable, "-m", "itime.execution.cleanup_worker", "--once"],
        cwd=SERVER,
        env={
            "PATH": "",
            "ITIME_ENVIRONMENT": "test",
            "ITIME_DATABASE_URL": database_url,
            "ITIME_ENCRYPTION_KEY": Settings().encryption_key.get_secret_value(),
            "ITIME_PUBLIC_ORIGIN": "http://localhost:4173",
        },
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "独立进程清理" not in completed.stdout


def test_cleanup_rejects_invalid_batch_size(database_url):
    database = Database(Settings(database_url=database_url, environment="test"))

    async def cleanup() -> None:
        try:
            with pytest.raises(ValueError, match="between 1 and 100"):
                await cleanup_expired_difficulties(database, limit=0)
        finally:
            await database.close()

    asyncio.run(cleanup())
