import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import psycopg
import pytest
from sqlalchemy import text

from itime.ai.job_service import (
    claim_next_job,
    cleanup_expired_media,
    complete_job,
    reject_or_retry_job,
)
from itime.config import Settings
from itime.database import Database
from tests.conftest import isolated_database, migrate, psycopg_url
from tests.test_calendar_series import create_account

pytestmark = pytest.mark.integration


@contextmanager
def browser(live_server):
    with httpx.Client(base_url=live_server.origin, timeout=10, trust_env=False) as client:
        yield client


def seed_config(connection) -> str:
    version = connection.execute(
        "UPDATE ai_config_capabilities"
        " SET next_version=next_version + 1, updated_at=now()"
        " WHERE capability='calendar_screenshot'"
        " RETURNING next_version - 1"
    ).fetchone()[0]
    config_id = uuid4()
    connection.execute(
        """
        INSERT INTO ai_config_versions (
            id, capability, version, endpoint_url, model, api_key_ciphertext,
            timeout_seconds, max_output_tokens, status
        ) VALUES (%s, 'calendar_screenshot', %s, 'https://ai.example.com/v1',
                  'vision-model', %s, 60, 1024, 'draft')
        """,
        (config_id, version, b"encrypted-key"),
    )
    return str(config_id)


def seed_media(connection, owner_id: str, *, expired: bool = False) -> str:
    media_id = uuid4()
    expires_at = datetime.now(UTC) + timedelta(hours=-1 if expired else 24)
    connection.execute(
        """
        INSERT INTO temporary_media (
            id, owner_id, purpose, original_filename, content_type,
            content_sha256, byte_size, width, height, encrypted_content, expires_at
        ) VALUES (%s, %s, 'calendar_screenshot', 'schedule.png', 'image/png',
                  %s, 16, 2, 2, %s, %s)
        """,
        (media_id, owner_id, "a" * 64, b"encrypted-image", expires_at),
    )
    return str(media_id)


def seed_job(
    connection,
    owner_id: str,
    config_id: str,
    media_id: str,
    *,
    status: str = "queued",
    deadline_delta: timedelta = timedelta(minutes=3),
    lease_delta: timedelta | None = None,
) -> str:
    job_id = uuid4()
    current = datetime.now(UTC)
    lease_id = uuid4() if status == "running" else None
    attempt_count = 1 if status == "running" else 0
    connection.execute(
        """
        INSERT INTO ai_jobs (
            id, owner_id, capability, config_version_id, input_media_id,
            status, input_summary, attempt_count, max_attempts,
            available_at, deadline_at, lease_id, lease_until
        ) VALUES (%s, %s, 'calendar_screenshot', %s, %s, %s, %s, %s, 3,
                  %s, %s, %s, %s)
        """,
        (
            job_id,
            owner_id,
            config_id,
            media_id,
            status,
            '{"filename":"schedule.png"}',
            attempt_count,
            current - timedelta(seconds=1),
            current + deadline_delta,
            lease_id,
            current + lease_delta if lease_delta is not None else None,
        ),
    )
    if status == "running":
        connection.execute(
            """
            INSERT INTO ai_job_attempts (
                id, job_id, owner_id, attempt_number, lease_id, status, started_at
            ) VALUES (%s, %s, %s, 1, %s, 'running', %s)
            """,
            (uuid4(), job_id, owner_id, lease_id, current),
        )
    return str(job_id)


def test_job_api_owner_isolation_cancel_and_replay(client, live_server, database_url):
    _, owner_id = create_account(client, database_url)
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        config_id = seed_config(connection)
        media_id = seed_media(connection, owner_id)
        job_id = seed_job(
            connection,
            owner_id,
            config_id,
            media_id,
            status="running",
            lease_delta=timedelta(minutes=2),
        )

    listing = client.get("/api/v1/ai/jobs")
    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()["items"]] == [job_id]
    assert listing.json()["items"][0]["status"] == "running"
    assert client.get(f"/api/v1/ai/jobs/{job_id}").status_code == 200

    operation_id = str(uuid4())
    cancelled = client.post(
        f"/api/v1/ai/jobs/{job_id}/cancel",
        json={"operation_id": operation_id},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["job"]["status"] == "cancelled"
    assert (
        client.post(
            f"/api/v1/ai/jobs/{job_id}/cancel",
            json={"operation_id": operation_id},
        ).json()
        == cancelled.json()
    )

    with psycopg.connect(psycopg_url(database_url)) as connection:
        attempt = connection.execute(
            "SELECT status, finished_at FROM ai_job_attempts WHERE job_id=%s",
            (job_id,),
        ).fetchone()
        assert attempt[0] == "cancelled"
        assert attempt[1] is not None

    with browser(live_server) as other:
        create_account(other, database_url)
        assert other.get(f"/api/v1/ai/jobs/{job_id}").status_code == 404
        assert (
            other.post(
                f"/api/v1/ai/jobs/{job_id}/cancel",
                json={"operation_id": str(uuid4())},
            ).status_code
            == 404
        )


async def exercise_claims(database_url: str) -> None:
    database = Database(Settings(database_url=database_url, environment="test"))
    try:
        first, second = await asyncio.gather(
            claim_next_job(database),
            claim_next_job(database),
        )
        assert first is not None and second is not None
        assert first.id != second.id
        assert first.attempt_number == second.attempt_number == 1

        assert await complete_job(database, first, {"batch_id": str(uuid4())})
        assert (
            await reject_or_retry_job(
                database,
                second,
                error_code="ai_upstream_unavailable",
                error_message="上游暂时不可用。",
                error_stage="upstream",
                retryable=True,
                http_status=503,
            )
            == "queued"
        )
        async with database.transaction() as session:
            await session.execute(
                text(
                    "UPDATE ai_jobs SET available_at=now() - interval '1 second'"
                    " WHERE id=:job_id"
                ),
                {"job_id": second.id},
            )
        retried = await claim_next_job(database)
        assert retried is not None
        assert retried.id == second.id
        assert retried.attempt_number == 2
        assert (
            await reject_or_retry_job(
                database,
                retried,
                error_code="ai_output_invalid",
                error_message="模型输出无法校验。",
                error_stage="validation",
                retryable=False,
            )
            == "failed"
        )
    finally:
        await database.close()


def test_concurrent_claim_retry_terminal_and_late_result(database_url, client):
    _, owner_id = create_account(client, database_url)
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        config_id = seed_config(connection)
        jobs = []
        for _ in range(2):
            jobs.append(
                seed_job(
                    connection,
                    owner_id,
                    config_id,
                    seed_media(connection, owner_id),
                )
            )
    asyncio.run(exercise_claims(database_url))

    with psycopg.connect(psycopg_url(database_url)) as connection:
        rows = connection.execute(
            "SELECT id, status, attempt_count, lease_id, output, error_code"
            " FROM ai_jobs WHERE id = ANY(%s) ORDER BY status",
            (jobs,),
        ).fetchall()
        assert {row[1] for row in rows} == {"failed", "succeeded"}
        succeeded = next(row for row in rows if row[1] == "succeeded")
        failed = next(row for row in rows if row[1] == "failed")
        assert succeeded[2] == 1 and succeeded[3] is None and succeeded[4] is not None
        assert failed[2] == 2 and failed[3] is None
        assert failed[5] == "ai_output_invalid"
        attempts = connection.execute(
            "SELECT status FROM ai_job_attempts WHERE job_id=%s ORDER BY attempt_number",
            (failed[0],),
        ).fetchall()
        assert [row[0] for row in attempts] == ["retryable_failed", "failed"]


def test_expired_media_cleanup_and_expired_job_sweep(database_url, client):
    _, owner_id = create_account(client, database_url)
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        config_id = seed_config(connection)
        expired_media = seed_media(connection, owner_id, expired=True)
        expired_job = seed_job(
            connection,
            owner_id,
            config_id,
            seed_media(connection, owner_id),
            deadline_delta=timedelta(seconds=-1),
        )

    async def run_checks():
        database = Database(Settings(database_url=database_url, environment="test"))
        try:
            assert await cleanup_expired_media(database) == 1
            assert await claim_next_job(database) is None
        finally:
            await database.close()

    asyncio.run(run_checks())
    with psycopg.connect(psycopg_url(database_url)) as connection:
        media = connection.execute(
            "SELECT encrypted_content, deletion_reason FROM temporary_media WHERE id=%s",
            (expired_media,),
        ).fetchone()
        job = connection.execute(
            "SELECT status, error_code, finished_at FROM ai_jobs WHERE id=%s",
            (expired_job,),
        ).fetchone()
        assert media == (None, "expired")
        assert job[0] == "timed_out"
        assert job[1] == "ai_job_deadline_exceeded"
        assert job[2] is not None


def test_stale_lease_is_reclaimed_without_taking_active_job(database_url, client):
    _, owner_id = create_account(client, database_url)
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        config_id = seed_config(connection)
        stale_job = seed_job(
            connection,
            owner_id,
            config_id,
            seed_media(connection, owner_id),
            status="running",
            lease_delta=timedelta(seconds=-1),
        )
        active_job = seed_job(
            connection,
            owner_id,
            config_id,
            seed_media(connection, owner_id),
            status="running",
            lease_delta=timedelta(minutes=2),
        )

    async def reclaim():
        database = Database(Settings(database_url=database_url, environment="test"))
        try:
            claimed = await claim_next_job(database)
            assert claimed is not None
            assert str(claimed.id) == stale_job
            assert claimed.attempt_number == 2
            assert (
                await reject_or_retry_job(
                    database,
                    claimed,
                    error_code="ai_output_invalid",
                    error_message="模型输出无法校验。",
                    error_stage="validation",
                    retryable=False,
                )
                == "failed"
            )
            assert await claim_next_job(database) is None
        finally:
            await database.close()

    asyncio.run(reclaim())
    with psycopg.connect(psycopg_url(database_url)) as connection:
        attempts = connection.execute(
            "SELECT status FROM ai_job_attempts WHERE job_id=%s ORDER BY attempt_number",
            (stale_job,),
        ).fetchall()
        assert [row[0] for row in attempts] == ["abandoned", "failed"]
        assert connection.execute(
            "SELECT status FROM ai_jobs WHERE id=%s",
            (active_job,),
        ).fetchone()[0] == "running"


def test_ai_job_migration_round_trip(admin_url):
    with isolated_database(admin_url) as database_url:
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT to_regclass('ai_job_attempts')"
            ).fetchone()[0] == "ai_job_attempts"
        migrate(database_url, "downgrade", "0012_ai_config_versions")
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT to_regclass('temporary_media')"
            ).fetchone()[0] is None
            assert connection.execute(
                "SELECT to_regclass('ai_jobs')"
            ).fetchone()[0] is None
        migrate(database_url, "upgrade", "head")
