import asyncio
import os
import smtplib
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
import psycopg
import pytest

from itime.config import Settings
from itime.database import Database
from performance.contracts import SeedProfile
from performance.seed import seed_dataset
from performance.synthetic_workers import run_public_link, run_subscription
from tests.conftest import isolated_database, psycopg_url

BASE_TIME = datetime(2026, 10, 5, 12, tzinfo=UTC)
SERVER = Path(__file__).resolve().parents[1]


def unused_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@contextmanager
def upstream_process(
    tmp_path: Path,
    *,
    mode: str,
    smtp: bool = False,
) -> Iterator[tuple[str, int, Path]]:
    http_port = unused_port()
    smtp_port = unused_port() if smtp else 0
    log_path = tmp_path / f"upstream-{mode}.log"
    with log_path.open("w") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "performance.upstream",
                "--host",
                "127.0.0.1",
                "--port",
                str(http_port),
                "--smtp-port",
                str(smtp_port),
                "--mode",
                mode,
                "--initial-delay",
                "0.01",
                "--chunk-delay",
                "0.01",
            ],
            cwd=SERVER,
            env={**os.environ, "ITIME_ENVIRONMENT": "test"},
            stdout=log,
            stderr=log,
        )
        origin = f"http://127.0.0.1:{http_port}"
        try:
            deadline = time.monotonic() + 10
            with httpx.Client(base_url=origin, timeout=1, trust_env=False) as client:
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        pytest.fail(f"协议桩启动失败，请检查 {log_path}")
                    try:
                        if client.get("/health").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.05)
                else:
                    pytest.fail(f"协议桩启动超时，请检查 {log_path}")
            yield origin, smtp_port, log_path
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def test_upstream_stream_failures_and_smtp_sink_do_not_echo_credentials(
    tmp_path: Path,
):
    secret = "synthetic-authorization-value"
    with upstream_process(tmp_path, mode="normal", smtp=True) as (
        origin,
        smtp_port,
        log_path,
    ):
        response = httpx.post(
            f"{origin}/v1/chat/completions",
            json={
                "stream": True,
                "messages": [{"role": "user", "content": "容量测试"}],
            },
            headers={"Authorization": f"Bearer {secret}"},
            timeout=5,
            trust_env=False,
        )
        assert response.status_code == 200
        assert "data:" in response.text
        assert "[DONE]" in response.text
        transcription = httpx.post(
            f"{origin}/v1/audio/transcriptions",
            files={"file": ("sample.wav", b"synthetic", "audio/wav")},
            data={"model": "performance-transcription"},
            timeout=5,
            trust_env=False,
        )
        assert transcription.status_code == 200
        assert transcription.json()["text"] == "合成语音转写完成"
        calendar = httpx.get(f"{origin}/calendar.ics", timeout=5, trust_env=False)
        assert calendar.status_code == 200
        assert calendar.text.startswith("BEGIN:VCALENDAR")
        public_page = httpx.get(
            f"{origin}/public.html",
            timeout=5,
            trust_env=False,
        )
        assert public_page.status_code == 200
        assert "Synthetic performance document." in public_page.text

        with smtplib.SMTP("127.0.0.1", smtp_port, timeout=5) as smtp_client:
            smtp_client.sendmail(
                "sender@performance.example.com",
                ["recipient@performance.example.com"],
                "Subject: synthetic\r\n\r\nSynthetic body",
            )
        metrics = httpx.get(f"{origin}/metrics", timeout=5, trust_env=False).json()
        assert metrics["smtp_messages"] == 1

    assert secret not in log_path.read_text()

    for mode, expected in (("rate-limit", 429), ("unavailable", 503)):
        with upstream_process(tmp_path, mode=mode) as (origin, _, _):
            response = httpx.post(
                f"{origin}/v1/chat/completions",
                json={"stream": True, "messages": []},
                timeout=5,
                trust_env=False,
            )
            assert response.status_code == expected

    with upstream_process(tmp_path, mode="disconnect") as (origin, _, _):
        with pytest.raises(httpx.RemoteProtocolError):
            httpx.post(
                f"{origin}/v1/chat/completions",
                json={"stream": True, "messages": []},
                timeout=5,
                trust_env=False,
            )

    with upstream_process(tmp_path, mode="timeout") as (origin, _, _):
        with pytest.raises(httpx.ReadTimeout):
            httpx.post(
                f"{origin}/v1/chat/completions",
                json={"stream": True, "messages": []},
                timeout=0.1,
                trust_env=False,
            )


@pytest.mark.integration
def test_synthetic_workers_keep_production_lease_and_writeback_semantics(
    admin_url: str,
    tmp_path: Path,
):
    with isolated_database(admin_url) as database_url:
        settings = Settings(
            environment="test",
            database_url=database_url,
            db_pool_size=2,
            db_max_overflow=0,
        )
        asyncio.run(
            seed_dataset(
                settings,
                SeedProfile.history(
                    accounts=1,
                    tasks_per_account=20,
                    calendar_series_per_account=10,
                    subscription_events_per_account=5,
                    conversation_messages_per_account=20,
                    sync_events_per_account=20,
                ),
                seed=20261005,
                at=BASE_TIME,
                credentials_path=tmp_path / "accounts.json",
            )
        )
        with psycopg.connect(
            psycopg_url(database_url),
            autocommit=True,
        ) as connection:
            connection.execute(
                """
                UPDATE calendar_subscriptions
                SET refresh_status='queued', next_refresh_at=now(),
                    lease_id=NULL, lease_until=NULL
                """
            )
            connection.execute(
                """
                UPDATE task_links
                SET fetch_status='queued', available_at=now(),
                    lease_id=NULL, lease_until=NULL
                WHERE id=(SELECT id FROM task_links ORDER BY id LIMIT 1)
                """
            )

        async def process() -> tuple[bool, bool]:
            database = Database(settings)
            try:
                return (
                    await run_subscription(
                        database,
                        settings,
                        delay_seconds=0,
                    ),
                    await run_public_link(
                        database,
                        settings,
                        delay_seconds=0,
                    ),
                )
            finally:
                await database.close()

        assert asyncio.run(process()) == (True, True)
        with psycopg.connect(psycopg_url(database_url)) as connection:
            subscription = connection.execute(
                """
                SELECT refresh_status, lease_id, lease_until, last_error_code
                FROM calendar_subscriptions
                """
            ).fetchone()
            link = connection.execute(
                """
                SELECT fetch_status, lease_id, lease_until,
                       last_error_code, summary
                FROM task_links
                ORDER BY id
                LIMIT 1
                """
            ).fetchone()
        assert subscription == ("idle", None, None, None)
        assert link[:4] == ("ready", None, None, None)
        assert link[4] == "Synthetic performance document."


def test_synthetic_workers_refuse_non_test_environment(settings: Settings):
    unsafe = settings.model_copy(update={"environment": "production"})
    database = Database(unsafe)
    try:
        with pytest.raises(RuntimeError, match="test"):
            asyncio.run(run_subscription(database, unsafe, delay_seconds=0))
    finally:
        asyncio.run(database.close())
