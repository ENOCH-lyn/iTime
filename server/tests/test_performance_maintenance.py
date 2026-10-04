import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import SecretStr

from itime.config import Settings
from itime.database import Database
from performance.contracts import RequestStat, ResourceSummary, SeedProfile
from performance.maintenance import (
    CleanupBatchResult,
    MaintenanceState,
    build_maintenance_summary,
    prepare_maintenance_batch,
    read_maintenance_state,
    run_cleanup_batch,
)
from performance.seed import seed_dataset
from tests.conftest import isolated_database


def healthy_resources() -> ResourceSummary:
    return ResourceSummary(
        cpu_mean_ratio=0.5,
        cpu_peak_over_90_seconds=0,
        memory_peak_ratio=0.6,
        memory_growth_ratio=0,
        database_connection_peak_ratio=0.4,
        blocked_over_two_seconds=0,
        pool_timeouts=0,
        server_5xx=0,
        sampling_gap_seconds=0,
    )


def requests(
    *,
    read_p95: float,
    write_p95: float,
) -> tuple[RequestStat, ...]:
    return (
        RequestStat(
            "read::tasks.list",
            100,
            80,
            read_p95,
            min(read_p95 * 1.5, 999),
            0,
        ),
        RequestStat(
            "write::tasks.create",
            10,
            120,
            write_p95,
            min(write_p95 * 1.5, 1499),
            0,
        ),
    )


def test_maintenance_summary_enforces_cleanup_and_degradation():
    initial_values = {
        "tasks_due": 2,
        "tasks_purged": 1,
        "calendar_series_due": 2,
        "calendar_series_purged": 0,
        "difficulties_due": 2,
        "difficulties_purged": 0,
        "memories_due": 2,
        "memories_purged": 0,
        "expired_sync_events": 5,
        "sync_events": 20,
    }
    initial = MaintenanceState(**initial_values)
    final = type(initial)(
        tasks_due=0,
        tasks_purged=3,
        calendar_series_due=0,
        calendar_series_purged=2,
        difficulties_due=0,
        difficulties_purged=2,
        memories_due=0,
        memories_purged=2,
        expired_sync_events=0,
        sync_events=15,
    )
    cleanup = CleanupBatchResult(
        counts={
            "tasks": 2,
            "calendar_series": 2,
            "calendar_sources": 0,
            "difficulties": 2,
            "memories": 2,
            "sync_events": 5,
        },
        durations_seconds={"tasks": 0.1},
        total_seconds=0.5,
    )

    healthy = build_maintenance_summary(
        initial=initial,
        final=final,
        cleanup=cleanup,
        baseline_requests=requests(read_p95=100, write_p95=200),
        maintenance_requests=requests(read_p95=200, write_p95=400),
        baseline_resources=healthy_resources(),
        maintenance_resources=healthy_resources(),
        count=2,
        sync_count=5,
    )
    assert healthy.passed
    assert healthy.degradation_ratios == {"read": 2, "write": 2}

    degraded = build_maintenance_summary(
        initial=initial,
        final=final,
        cleanup=cleanup,
        baseline_requests=requests(read_p95=100, write_p95=200),
        maintenance_requests=requests(read_p95=201, write_p95=401),
        baseline_resources=healthy_resources(),
        maintenance_resources=healthy_resources(),
        count=2,
        sync_count=5,
    )
    assert not degraded.passed
    assert "read p95 degradation 2.010x > 2.0x" in degraded.violations
    assert "write p95 degradation 2.005x > 2.0x" in degraded.violations


@pytest.mark.integration
def test_real_maintenance_batch_cleans_exact_bounded_candidates(
    admin_url: str,
    settings: Settings,
    tmp_path: Path,
):
    with isolated_database(admin_url) as database_url:
        isolated_settings = settings.model_copy(
            update={
                "environment": "test",
                "database_url": SecretStr(database_url),
            }
        )
        asyncio.run(
            seed_dataset(
                isolated_settings,
                SeedProfile.standard(
                    accounts=3,
                    active_accounts=3,
                    tasks_per_active=50,
                    sync_events_per_active=20,
                ),
                seed=20261005,
                at=datetime(2026, 10, 5, 12, tzinfo=UTC),
                credentials_path=tmp_path / "maintenance-accounts.json",
            )
        )

        async def exercise():
            database = Database(isolated_settings)
            try:
                initial = await prepare_maintenance_batch(
                    database,
                    isolated_settings,
                    count=3,
                    sync_count=10,
                )
                cleanup = await run_cleanup_batch(
                    database,
                    isolated_settings,
                    count=3,
                    sync_count=10,
                )
                final = await read_maintenance_state(database)
                return initial, cleanup, final
            finally:
                await database.close()

        initial, cleanup, final = asyncio.run(exercise())

    assert initial.tasks_due == 3
    assert initial.calendar_series_due == 3
    assert initial.difficulties_due == 3
    assert initial.memories_due == 3
    assert initial.expired_sync_events == 10
    assert cleanup.counts == {
        "tasks": 3,
        "calendar_series": 3,
        "calendar_sources": 0,
        "difficulties": 3,
        "memories": 3,
        "sync_events": 10,
    }
    assert final.tasks_due == 0
    assert final.calendar_series_due == 0
    assert final.difficulties_due == 0
    assert final.memories_due == 0
    assert final.expired_sync_events == 0
    assert final.tasks_purged - initial.tasks_purged == 3
    assert final.calendar_series_purged - initial.calendar_series_purged == 3
    assert final.difficulties_purged - initial.difficulties_purged == 3
    assert final.memories_purged - initial.memories_purged == 3
