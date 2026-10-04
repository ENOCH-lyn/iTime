import asyncio
import json
import stat
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest

from itime.config import Settings
from performance.contracts import SeedProfile
from performance.seed import seed_dataset
from performance.seed_verify import verify_dataset
from tests.conftest import isolated_database, psycopg_url

BASE_TIME = datetime(2026, 10, 5, 12, tzinfo=UTC)


def performance_settings(database_url: str) -> Settings:
    return Settings(
        environment="test",
        database_url=database_url,
        db_pool_size=2,
        db_max_overflow=0,
    )


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
                item.email.endswith("@performance.invalid")
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
                ("%@performance.invalid",),
            ).fetchone()[0] == 0
        assert not credentials_path.exists()
