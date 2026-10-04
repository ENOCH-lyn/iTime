import asyncio
import io
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4
from zipfile import ZipFile

import psycopg
import pytest

from itime.config import Settings
from itime.data_export.archive import ExportArchive, ExportBuildError
from itime.data_export.service import (
    claim_next_export,
    cleanup_expired_exports,
    complete_export,
    export_cipher,
)
from itime.data_export.worker import process_one
from itime.database import Database
from tests.conftest import psycopg_url

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def reset_exports(database_url):
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        connection.execute("DELETE FROM data_export_jobs")


def seed_user(connection) -> UUID:
    owner_id = uuid4()
    connection.execute(
        """
        INSERT INTO users (id, email, password_hash, email_verified_at)
        VALUES (%s, %s, 'test-password-hash', now())
        """,
        (owner_id, f"{owner_id}@example.com"),
    )
    return owner_id


def seed_job(connection, owner_id: UUID, *, available_at: datetime | None = None) -> UUID:
    job_id = uuid4()
    connection.execute(
        """
        INSERT INTO data_export_jobs (
            id, owner_id, status, schema_version,
            attempt_count, max_attempts, available_at
        ) VALUES (%s, %s, 'queued', 1, 0, 3, %s)
        """,
        (job_id, owner_id, available_at or datetime.now(UTC)),
    )
    return job_id


def job_row(database_url: str, job_id: UUID):
    with psycopg.connect(psycopg_url(database_url)) as connection:
        return connection.execute(
            """
            SELECT status, attempt_count, lease_id, lease_until,
                   snapshot_at, artifact_ciphertext, artifact_sha256,
                   artifact_byte_size, filename, expires_at,
                   error_code, error_message, finished_at
            FROM data_export_jobs
            WHERE id=%s
            """,
            (job_id,),
        ).fetchone()


def run_process_once(database_url: str) -> bool:
    settings = Settings(database_url=database_url, environment="test")
    database = Database(settings)

    async def execute() -> bool:
        try:
            return await process_one(database, settings)
        finally:
            await database.close()

    return asyncio.run(execute())


def run_cleanup_once(database_url: str) -> tuple[int, int]:
    settings = Settings(database_url=database_url, environment="test")
    database = Database(settings)

    async def execute() -> tuple[int, int]:
        try:
            return await cleanup_expired_exports(database)
        finally:
            await database.close()

    return asyncio.run(execute())


def test_two_workers_claim_once_and_stale_lease_recovery(
    database_url,
    settings,
):
    with psycopg.connect(psycopg_url(database_url)) as connection:
        owner_id = seed_user(connection)
        job_id = seed_job(connection, owner_id)
        connection.commit()
    database = Database(settings)

    async def exercise():
        first, second = await asyncio.gather(
            claim_next_export(database, lease_seconds=60),
            claim_next_export(database, lease_seconds=60),
        )
        claims = [claim for claim in (first, second) if claim is not None]
        assert len(claims) == 1
        assert claims[0].id == job_id
        with psycopg.connect(
            psycopg_url(database_url),
            autocommit=True,
        ) as connection:
            connection.execute(
                """
                UPDATE data_export_jobs
                SET lease_until=now()-interval '1 second'
                WHERE id=%s
                """,
                (job_id,),
            )
        recovered = await claim_next_export(database, lease_seconds=60)
        assert recovered is not None
        assert recovered.id == job_id
        assert recovered.lease_id != claims[0].lease_id
        archive = ExportArchive(
            content=b"new-worker",
            sha256="a" * 64,
            byte_size=10,
            filename="itime-export-20261004-000000Z.zip",
            manifest={},
        )
        assert not await complete_export(
            database,
            settings,
            claims[0],
            snapshot_at=datetime.now(UTC),
            archive=archive,
            ciphertext=b"old-worker",
        )
        assert await complete_export(
            database,
            settings,
            recovered,
            snapshot_at=datetime.now(UTC),
            archive=archive,
            ciphertext=b"new-worker-encrypted",
        )
        await database.close()

    asyncio.run(exercise())
    row = job_row(database_url, job_id)
    assert row[0] == "succeeded"
    assert row[1] == 2
    assert row[5] == b"new-worker-encrypted"


def test_worker_builds_encrypted_archive_and_process_restart(
    database_url,
    settings,
):
    marker = f"private-export-{uuid4().hex}"
    with psycopg.connect(psycopg_url(database_url)) as connection:
        owner_id = seed_user(connection)
        job_id = seed_job(connection, owner_id)
        connection.execute(
            """
            INSERT INTO tasks (
                id, owner_id, title, description, priority, status, version
            ) VALUES (%s, %s, %s, '', 0, 'open', 1)
            """,
            (uuid4(), owner_id, marker),
        )
        connection.commit()

    assert run_process_once(database_url)
    row = job_row(database_url, job_id)
    assert row[0] == "succeeded"
    assert row[5] is not None
    assert marker.encode() not in row[5]
    plaintext = export_cipher(settings).decrypt(row[5])
    with ZipFile(io.BytesIO(plaintext)) as package:
        assert package.testzip() is None
        assert marker.encode() in package.read("itime-data.json")

    with psycopg.connect(psycopg_url(database_url)) as connection:
        process_job = seed_job(connection, owner_id)
        connection.commit()
    result = subprocess.run(
        [sys.executable, "-m", "itime.data_export.worker", "--once"],
        cwd=os.fspath(Path(__file__).resolve().parents[1]),
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
    assert result.returncode == 0, result.stderr
    assert job_row(database_url, process_job)[0] == "succeeded"


def test_worker_retries_unknown_failure_and_stops_on_build_error(
    database_url,
    settings,
    monkeypatch,
):
    with psycopg.connect(psycopg_url(database_url)) as connection:
        owner_id = seed_user(connection)
        retry_job = seed_job(connection, owner_id)
        connection.commit()

    async def broken_snapshot(*_args, **_kwargs):
        raise RuntimeError("sensitive failure")

    monkeypatch.setattr(
        "itime.data_export.worker.read_consistent_snapshot",
        broken_snapshot,
    )
    for expected_attempt in range(1, 4):
        assert run_process_once(database_url)
        row = job_row(database_url, retry_job)
        assert row[1] == expected_attempt
        assert row[0] == ("failed" if expected_attempt == 3 else "queued")
        if expected_attempt < 3:
            with psycopg.connect(
                psycopg_url(database_url),
                autocommit=True,
            ) as connection:
                connection.execute(
                                """
                                UPDATE data_export_jobs
                                SET available_at=now()-interval '1 second'
                                WHERE id=%s
                                """,
                    (retry_job,),
                )
    assert row[10] == "data_export_generation_failed"
    assert "sensitive failure" not in row[11]

    with psycopg.connect(psycopg_url(database_url)) as connection:
        deterministic_job = seed_job(connection, owner_id)
        connection.commit()

    async def valid_snapshot(*_args, **_kwargs):
        return {"snapshot_at": datetime.now(UTC).isoformat()}

    def too_large(*_args, **_kwargs):
        raise ExportBuildError("data_export_too_large", "导出超过容量。")

    monkeypatch.setattr(
        "itime.data_export.worker.read_consistent_snapshot",
        valid_snapshot,
    )
    monkeypatch.setattr("itime.data_export.worker.build_export_archive", too_large)
    assert run_process_once(database_url)
    deterministic = job_row(database_url, deterministic_job)
    assert deterministic[0] == "failed"
    assert deterministic[1] == 1
    assert deterministic[10:12] == (
        "data_export_too_large",
        "导出超过容量。",
    )


def test_expiry_cleanup_is_idempotent_and_concurrent(
    database_url,
    settings,
):
    current = datetime.now(UTC)
    with psycopg.connect(psycopg_url(database_url)) as connection:
        owner_id = seed_user(connection)
        jobs = []
        for offset in range(3):
            job_id = uuid4()
            jobs.append(job_id)
            connection.execute(
                """
                INSERT INTO data_export_jobs (
                    id, owner_id, status, schema_version,
                    attempt_count, max_attempts, available_at,
                    snapshot_at, artifact_ciphertext, artifact_sha256,
                    artifact_byte_size, filename, expires_at, finished_at
                ) VALUES (
                    %s, %s, 'succeeded', 1, 1, 3, %s,
                    %s, %s, %s, 10, %s, %s, %s
                )
                """,
                (
                    job_id,
                    owner_id,
                    current - timedelta(days=2),
                    current - timedelta(days=2),
                    f"cipher-{offset}".encode(),
                    f"{offset}" * 64,
                    f"itime-export-{offset}.zip",
                    current - timedelta(days=1),
                    current - timedelta(days=2),
                ),
            )
        connection.commit()

    async def cleanup():
        databases = [Database(settings), Database(settings)]
        try:
            return await asyncio.gather(
                *(cleanup_expired_exports(item, limit=2) for item in databases)
            )
        finally:
            await asyncio.gather(*(item.close() for item in databases))

    results = asyncio.run(cleanup())
    assert sum(expired for expired, _ in results) == 3
    with psycopg.connect(psycopg_url(database_url)) as connection:
        assert connection.execute(
            """
            SELECT count(*) FROM data_export_jobs
            WHERE id = ANY(%s) AND status='expired'
              AND artifact_ciphertext IS NULL
            """,
            (jobs,),
        ).fetchone() == (3,)
    assert run_cleanup_once(database_url) == (0, 0)
