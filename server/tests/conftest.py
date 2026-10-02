import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
import pytest
from psycopg import sql
from sqlalchemy.engine import make_url

from itime.config import Settings

SERVER = Path(__file__).resolve().parents[1]


def psycopg_url(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


@pytest.fixture(scope="session")
def admin_url() -> str:
    settings = Settings()
    url = make_url(settings.database_url.get_secret_value())
    if settings.environment == "production" or url.host not in {"localhost", "127.0.0.1", "::1"}:
        pytest.fail("集成测试只允许本地开发数据库，拒绝连接生产或外部数据库。")
    return url.set(database="postgres").render_as_string(hide_password=False)


def migrate(database_url: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=SERVER,
        env={
            **os.environ,
            "ITIME_ENVIRONMENT": "test",
            "ITIME_DATABASE_URL": database_url,
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )


@contextmanager
def isolated_database(admin_url: str) -> Iterator[str]:
    # 名称只能由测试生成；绝不接受任意输入作为 DROP DATABASE 的目标。
    name = "itime_test_" + uuid4().hex
    with psycopg.connect(psycopg_url(admin_url), autocommit=True, connect_timeout=3) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    url = make_url(admin_url).set(database=name).render_as_string(hide_password=False)
    try:
        migrate(url, "upgrade", "head")
        yield url
    finally:
        with psycopg.connect(psycopg_url(admin_url), autocommit=True, connect_timeout=3) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture(scope="session")
def database_url(admin_url: str) -> Iterator[str]:
    with isolated_database(admin_url) as url:
        yield url


@pytest.fixture
def settings(database_url: str) -> Settings:
    return Settings(
        environment="test",
        database_url=database_url,
        db_pool_size=2,
        db_max_overflow=0,
    )


@dataclass
class LiveServer:
    origin: str
    log_path: Path


@pytest.fixture(scope="session")
def live_server(database_url: str, tmp_path_factory) -> Iterator[LiveServer]:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    log_path = tmp_path_factory.mktemp("api") / "server.log"
    with log_path.open("w") as log:
        process = subprocess.Popen(
            [
                sys.executable, "-m", "uvicorn", "tests.support_app:create_test_app",
                "--factory", "--host", "127.0.0.1", "--port", str(port), "--no-access-log",
            ],
            cwd=SERVER,
            env={
                **os.environ,
                "ITIME_ENVIRONMENT": "test",
                "ITIME_DATABASE_URL": database_url,
                "ITIME_MAX_REQUEST_BYTES": "4096",
            },
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 15
            with httpx.Client(base_url=origin, timeout=1, trust_env=False) as client:
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        pytest.fail(f"测试 API 启动失败，请检查 {log_path}")
                    try:
                        if client.get("/api/ready").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.05)
                else:
                    pytest.fail(f"测试 API 启动超时，请检查 {log_path}")
            yield LiveServer(origin, log_path)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@pytest.fixture
def client(live_server: LiveServer) -> Iterator[httpx.Client]:
    with httpx.Client(base_url=live_server.origin, timeout=5, trust_env=False) as client:
        yield client
