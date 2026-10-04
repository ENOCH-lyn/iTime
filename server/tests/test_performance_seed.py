import asyncio
import json
import os
import socket
import stat
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
from performance.contracts import SeedProfile
from performance.seed import seed_dataset
from performance.seed_verify import verify_dataset
from tests.conftest import isolated_database, psycopg_url

BASE_TIME = datetime(2026, 10, 5, 12, tzinfo=UTC)
SERVER = Path(__file__).resolve().parents[1]


def performance_settings(database_url: str) -> Settings:
    return Settings(
        environment="test",
        database_url=database_url,
        db_pool_size=2,
        db_max_overflow=0,
    )


@contextmanager
def live_performance_app(
    database_url: str,
    log_path: Path,
) -> Iterator[str]:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    with log_path.open("w") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "itime.main:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--no-access-log",
            ],
            cwd=SERVER,
            env={
                **os.environ,
                "ITIME_ENVIRONMENT": "test",
                "ITIME_DATABASE_URL": database_url,
            },
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 15
            with httpx.Client(base_url=origin, timeout=2, trust_env=False) as client:
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        pytest.fail(f"性能读取 API 启动失败，请检查 {log_path}")
                    try:
                        if client.get("/api/ready").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.05)
                else:
                    pytest.fail(f"性能读取 API 启动超时，请检查 {log_path}")
            yield origin
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def test_seed_profile_rejects_invalid_counts():
    with pytest.raises(ValueError):
        SeedProfile.standard(accounts=0)
    with pytest.raises(ValueError):
        SeedProfile.standard(accounts=2, active_accounts=3)
    with pytest.raises(ValueError):
        SeedProfile.standard(tasks_per_active=1001)
    with pytest.raises(ValueError):
        SeedProfile.history(subscription_events_per_account=5001)


@pytest.mark.integration
def test_standard_seed_is_deterministic_private_and_valid(
    admin_url: str,
    tmp_path: Path,
):
    profile = SeedProfile.standard()
    manifests = []

    for run_number in range(2):
        with isolated_database(admin_url) as database_url:
            settings = performance_settings(database_url)
            credentials_path = tmp_path / f"standard-accounts-{run_number}.json"
            manifest = asyncio.run(
                seed_dataset(
                    settings,
                    profile,
                    seed=20261005,
                    at=BASE_TIME,
                    credentials_path=credentials_path,
                )
            )
            verification = asyncio.run(verify_dataset(settings, manifest))

            assert verification.ok, verification.violations
            assert manifest.accounts == 100
            assert manifest.active_accounts == 50
            assert manifest.counts["tasks"] == 15_000
            assert manifest.counts["change_events"] >= 150_000
            assert manifest.counts["task_lifecycle_active"] == 12_000
            assert manifest.counts["task_lifecycle_archived"] == 1_500
            assert manifest.counts["task_lifecycle_trashed"] == 1_200
            assert manifest.counts["task_lifecycle_purged"] == 300
            assert len(manifest.credentials) == 50
            assert all(
                item.email.endswith("@performance.example.com")
                for item in manifest.credentials
            )

            credentials = json.loads(credentials_path.read_text())
            assert len(credentials) == 50
            assert stat.S_IMODE(credentials_path.stat().st_mode) == 0o600
            assert {item["email"] for item in credentials} == {
                item.email for item in manifest.credentials
            }
            public_manifest = json.dumps(manifest.to_public_dict()).casefold()
            assert "password" not in public_manifest
            assert "cookie" not in public_manifest
            assert "api_key" not in public_manifest
            assert "content" not in public_manifest

            with psycopg.connect(psycopg_url(database_url)) as connection:
                distinct_hashes, plaintext_hashes = connection.execute(
                    """
                    SELECT count(DISTINCT password_hash),
                           count(*) FILTER (
                               WHERE password_hash = 'itime-performance-only'
                           )
                    FROM users
                    """
                ).fetchone()
            assert distinct_hashes == 1
            assert plaintext_hashes == 0
            manifests.append(manifest)

    assert manifests[0].to_public_dict() == manifests[1].to_public_dict()


@pytest.mark.integration
def test_standard_seed_rolls_back_an_account_and_does_not_write_credentials(
    admin_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from performance import seed_core

    original = seed_core._seed_account_data

    async def fail_after_account_rows(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("injected seed failure")

    monkeypatch.setattr(seed_core, "_seed_account_data", fail_after_account_rows)
    credentials_path = tmp_path / "failed-accounts.json"

    with isolated_database(admin_url) as database_url:
        with pytest.raises(RuntimeError, match="injected seed failure"):
            asyncio.run(
                seed_dataset(
                    performance_settings(database_url),
                    SeedProfile.standard(
                        accounts=1,
                        active_accounts=1,
                        tasks_per_active=10,
                        sync_events_per_active=20,
                    ),
                    seed=20261005,
                    at=BASE_TIME,
                    credentials_path=credentials_path,
                )
            )

        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT count(*) FROM users WHERE email LIKE %s",
                ("%@performance.example.com",),
            ).fetchone()[0] == 0
        assert not credentials_path.exists()


@pytest.mark.integration
def test_history_seed_supports_real_http_reads(
    admin_url: str,
    tmp_path: Path,
):
    profile = SeedProfile.history(
        accounts=1,
        tasks_per_account=100,
        calendar_series_per_account=100,
        subscription_events_per_account=200,
        conversation_messages_per_account=200,
        sync_events_per_account=500,
    )
    with isolated_database(admin_url) as database_url:
        settings = performance_settings(database_url)
        manifest = asyncio.run(
            seed_dataset(
                settings,
                profile,
                seed=20261005,
                at=BASE_TIME,
                credentials_path=tmp_path / "history-accounts.json",
            )
        )
        assert manifest.counts["tasks"] == 100
        assert manifest.counts["calendar_series"] == 100
        assert manifest.counts["calendar_subscription_events"] == 200
        assert manifest.counts["conversation_messages"] == 200
        assert manifest.counts["change_events"] >= 500
        verification = asyncio.run(verify_dataset(settings, manifest))
        assert verification.ok, verification.violations

        with (
            live_performance_app(database_url, tmp_path / "history-api.log") as origin,
            httpx.Client(base_url=origin, timeout=10, trust_env=False) as client,
        ):
            credential = manifest.credentials[0]
            logged_in = client.post(
                "/api/v1/auth/login",
                json={
                    "email": credential.email,
                    "password": credential.password,
                    "device_name": "性能读取验证",
                },
            )
            assert logged_in.status_code == 200, logged_in.text
            client.headers["x-csrf-token"] = logged_in.json()["csrf_token"]

            tasks = client.get("/api/v1/tasks", params={"limit": 100})
            assert tasks.status_code == 200, tasks.text
            assert len(tasks.json()["items"]) == 80

            occurrences = client.get(
                "/api/v1/calendar/occurrences",
                params={"start": "2026-01-01", "end": "2026-04-04"},
            )
            assert occurrences.status_code == 200, occurrences.text
            assert occurrences.json()["items"]

            statistics = client.get(
                "/api/v1/statistics",
                params={"scope": "month", "anchor_date": "2026-10-04"},
            )
            assert statistics.status_code == 200, statistics.text

            initial = client.get(
                "/api/v1/sync/changes",
                params={"limit": 200},
            )
            assert initial.status_code == 200, initial.text
            assert initial.json()["reset_required"] is True


@pytest.mark.performance
@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("ITIME_RUN_PERFORMANCE") != "1",
    reason="设置 ITIME_RUN_PERFORMANCE=1 执行完整性能数据测试",
)
def test_full_history_profile(admin_url: str, tmp_path: Path):
    with isolated_database(admin_url) as database_url:
        settings = performance_settings(database_url)
        manifest = asyncio.run(
            seed_dataset(
                settings,
                SeedProfile.history(),
                seed=20261005,
                at=BASE_TIME,
                credentials_path=tmp_path / "full-history-accounts.json",
            )
        )
        assert manifest.accounts == 5
        assert manifest.counts["tasks"] == 5000
        assert manifest.counts["calendar_series"] == 5000
        assert manifest.counts["calendar_subscription_events"] == 25_000
        assert manifest.counts["conversation_messages"] == 25_000
        assert manifest.counts["change_events"] >= 50_000
        verification = asyncio.run(verify_dataset(settings, manifest))
        assert verification.ok, verification.violations
