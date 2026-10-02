import asyncio
import json
import time
from datetime import datetime
from uuid import UUID

import psycopg
import pytest
from psycopg import sql
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from itime.config import Settings
from itime.database import Database
from tests.conftest import isolated_database, migrate, psycopg_url

pytestmark = pytest.mark.integration


def test_health_and_schema_ready_over_http(client):
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["status"] == "alive"
    assert datetime.fromisoformat(health.json()["server_time"]).utcoffset().total_seconds() == 0
    assert UUID(health.headers["x-request-id"]).version == 4
    assert health.headers["cache-control"] == "no-store"
    assert client.get("/api/ready").json()["status"] == "ready"
    schema = client.get("/api/openapi.json").json()
    assert "/api/health" in schema["paths"]


def test_error_contract_does_not_echo_password_or_query(client, live_server):
    secret = "password-must-not-appear"
    response = client.post(
        "/__test/input?token=query-must-not-appear",
        json={"count": secret, "password": secret},
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["request_id"] == response.headers["x-request-id"]
    assert error["saved"] == "not_saved"
    assert error["details"]["fields"][0]["path"] == "body.count"
    assert secret not in response.text
    assert client.get("/unknown/private-path-must-not-appear").status_code == 404
    unexpected = client.get("/__test/error")
    assert unexpected.status_code == 500
    assert unexpected.json()["error"]["saved"] == "unknown"
    assert "sensitive-exception-content" not in unexpected.text
    # 发一个后续请求，确保之前 finally 的访问日志已经写入。
    client.get("/api/health")
    logs = live_server.log_path.read_text()
    for sensitive in (secret, "query-must-not-appear", "private-path-must-not-appear",
                      "sensitive-exception-content"):
        assert sensitive not in logs
    events = [json.loads(line) for line in logs.splitlines() if line.startswith("{")]
    assert any(e.get("request_id") == error["request_id"] for e in events)


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"host": "untrusted.example"}, 400),
        ({"origin": "https://untrusted.example"}, 403),
        ({"sec-fetch-site": "cross-site"}, 403),
        ({"origin": "http://localhost:5173"}, 200),
    ],
)
def test_host_and_origin_boundaries(client, headers, expected):
    response = client.post("/__test/input", headers=headers, json={"count": 1, "password": "p"})
    assert response.status_code == expected
    if expected != 200:
        assert response.json()["error"]["saved"] == "not_saved"


def test_upload_limits_for_content_length_and_chunked_body(client):
    assert client.post("/__test/body", content=b"x" * 4097).status_code == 413
    # 生成器触发真正的 Transfer-Encoding: chunked，不依赖 Content-Length。
    response = client.post("/__test/body", content=iter([b"x" * 2048] * 3))
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"
    assert client.post("/__test/body", content=b"x" * 4096).json()["bytes"] == 4096


def test_live_database_outage_and_recovery(client, admin_url, database_url):
    name = make_url(database_url).database
    assert name.startswith("itime_test_")
    with psycopg.connect(psycopg_url(admin_url), autocommit=True) as admin:
        admin.execute(
            sql.SQL("ALTER DATABASE {} ALLOW_CONNECTIONS false").format(sql.Identifier(name))
        )
        admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s", (name,)
        )
        try:
            start = time.monotonic()
            unavailable = client.get("/api/ready")
            assert unavailable.status_code == 503
            assert unavailable.json()["error"]["code"] == "service_not_ready"
            assert time.monotonic() - start < 3
            assert client.get("/api/health").status_code == 200
            failed_read = client.get("/__test/database")
            assert failed_read.status_code == 503
            assert failed_read.json()["error"]["code"] == "database_unavailable"
            assert failed_read.json()["error"]["saved"] == "unknown"
        finally:
            admin.execute(
                sql.SQL("ALTER DATABASE {} ALLOW_CONNECTIONS true").format(sql.Identifier(name))
            )
    assert client.get("/api/ready").status_code == 200
    assert client.get("/__test/database").json()["value"] == 1


def test_migrations_round_trip_in_disposable_database(admin_url):
    with isolated_database(admin_url) as url:
        migrate(url, "downgrade", "base")
        with psycopg.connect(psycopg_url(url)) as connection:
            assert connection.execute("SELECT count(*) FROM alembic_version").fetchone()[0] == 0
        migrate(url, "upgrade", "head")
        migrate(url, "upgrade", "head")
        migrate(url, "check")
        with psycopg.connect(psycopg_url(url)) as connection:
            assert connection.execute("SELECT count(*) FROM alembic_version").fetchone()[0] == 1


def test_readiness_rejects_schema_mismatch(client, database_url):
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        original = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        connection.execute("UPDATE alembic_version SET version_num = 'test_old_revision'")
        try:
            response = client.get("/api/ready")
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "schema_not_ready"
        finally:
            connection.execute("UPDATE alembic_version SET version_num = %s", (original,))
    assert client.get("/api/ready").status_code == 200


async def test_cancelled_transaction_releases_connection_and_rolls_back(settings):
    database = Database(settings)
    started = asyncio.Event()
    try:
        async with database.transaction() as session:
            await session.execute(text("CREATE TABLE cancellation_probe (value integer)"))

        async def interrupted_write() -> None:
            async with database.transaction() as session:
                await session.execute(text("INSERT INTO cancellation_probe VALUES (1)"))
                started.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(interrupted_write())
        await asyncio.wait_for(started.wait(), timeout=3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with database.transaction() as session:
            assert await session.scalar(text("SELECT count(*) FROM cancellation_probe")) == 0
            await session.execute(text("DROP TABLE cancellation_probe"))
    finally:
        await database.close()


async def test_transactions_rollback_commit_timeout_and_pool_bound(settings):
    database = Database(settings)
    try:
        async with database.transaction() as session:
            await session.execute(text("CREATE TABLE foundation_probe (value integer PRIMARY KEY)"))
            await session.execute(text("INSERT INTO foundation_probe VALUES (1)"))
        with pytest.raises(RuntimeError, match="rollback"):
            async with database.transaction() as session:
                await session.execute(text("INSERT INTO foundation_probe VALUES (2)"))
                raise RuntimeError("rollback")
        async with database.transaction() as session:
            assert list(await session.scalars(text("SELECT value FROM foundation_probe"))) == [1]
        with pytest.raises(DBAPIError):
            async with database.transaction() as session:
                await session.execute(text("SET LOCAL statement_timeout = '100ms'"))
                await session.execute(text("SELECT pg_sleep(1)"))
        # 超时后连接可复用；20 个真实事务同时竞争 2 个连接，无额外连接。
        async def query() -> int:
            async with database.transaction() as session:
                await session.execute(text("SELECT pg_sleep(0.02)"))
                return await session.scalar(text("SELECT pg_backend_pid()"))

        pids = await asyncio.gather(*(query() for _ in range(20)))
        assert len(set(pids)) <= 2
        async with database.transaction() as session:
            assert await session.scalar(text("SELECT count(*) FROM foundation_probe")) == 1
            await session.execute(text("DROP TABLE foundation_probe"))
    finally:
        await database.close()


def test_configuration_rejects_unsafe_settings_and_hides_credentials():
    database_url = "postgresql+psycopg://itime:config-secret@localhost/itime"
    settings = Settings(database_url=database_url)
    assert "config-secret" not in repr(settings)
    with pytest.raises(ValidationError) as error:
        Settings(database_url="sqlite://config-secret")
    assert "config-secret" not in str(error.value)
    with pytest.raises(ValidationError):
        Settings(database_url=database_url, environment="production", public_origin="http://example.com")
    with pytest.raises(ValidationError):
        Settings(database_url=database_url, allowed_hosts=["*"])
