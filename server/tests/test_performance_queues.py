import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import SecretStr

from itime.config import Settings
from itime.database import Database
from performance.contracts import RequestStat, ResourceSummary, SeedProfile
from performance.queues import (
    QueueDrainObservation,
    QueueKind,
    QueueState,
    QueueTypeResult,
    build_queue_run_summary,
    build_queue_type_result,
    prepare_queue_backlog,
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


def healthy_requests() -> tuple[RequestStat, ...]:
    return (
        RequestStat("read::tasks.list", 100, 100, 300, 600, 0),
        RequestStat("write::tasks.create", 10, 200, 500, 900, 0),
    )


def queue_result(kind: QueueKind) -> QueueTypeResult:
    started = datetime(2026, 10, 5, 12, tzinfo=UTC)
    initial = QueueState(queued=50, running=0, succeeded=10, failed=0, oldest=None)
    observation = QueueDrainObservation(
        final=QueueState(
            queued=0,
            running=0,
            succeeded=60,
            failed=0,
            oldest=None,
        ),
        drained=True,
        drain_seconds=25,
    )
    return build_queue_type_result(
        kind=kind,
        initial=initial,
        observation=observation,
        started_at=started,
        ended_at=started.replace(second=30),
        requests=healthy_requests(),
        resources=healthy_resources(),
    )


def test_queue_summary_requires_each_queue_and_preserves_throughput():
    summary = build_queue_run_summary(
        [queue_result(kind) for kind in QueueKind]
    )

    assert summary.passed
    assert summary.violations == ()
    assert [result.kind for result in summary.queues] == [
        "subscriptions",
        "public_links",
        "exports",
    ]
    assert all(result.throughput_per_second == 2 for result in summary.queues)
    assert sum(item.count for item in summary.requests) == 330


def test_queue_result_rejects_timeout_remaining_and_failed_terminal():
    started = datetime(2026, 10, 5, 12, tzinfo=UTC)
    initial = QueueState(queued=50, running=0, succeeded=0, failed=0, oldest=None)
    result = build_queue_type_result(
        kind=QueueKind.EXPORTS,
        initial=initial,
        observation=QueueDrainObservation(
            final=QueueState(
                queued=2,
                running=1,
                succeeded=46,
                failed=1,
                oldest="2026-10-05T11:59:00+00:00",
            ),
            drained=False,
            drain_seconds=180,
        ),
        started_at=started,
        ended_at=started.replace(minute=3),
        requests=healthy_requests(),
        resources=healthy_resources(),
    )

    assert not result.drained
    assert result.oldest_remaining_age_seconds == 240
    assert "queue did not drain within 180s" in result.violations
    assert "queue remaining queued=2 running=1" in result.violations
    assert "successful terminal delta 46, expected 50" in result.violations
    assert "failed terminal delta 1" in result.violations


@pytest.mark.integration
def test_prepare_each_queue_creates_an_isolated_backlog(
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
                    tasks_per_active=20,
                    sync_events_per_active=20,
                ),
                seed=20261005,
                at=datetime(2026, 10, 5, 12, tzinfo=UTC),
                credentials_path=tmp_path / "queue-accounts.json",
            )
        )

        async def prepare() -> dict[QueueKind, QueueState]:
            database = Database(isolated_settings)
            try:
                return {
                    kind: await prepare_queue_backlog(
                        database,
                        isolated_settings,
                        kind,
                        count=3,
                        upstream_origin="http://host.docker.internal:18081",
                    )
                    for kind in QueueKind
                }
            finally:
                await database.close()

        states = asyncio.run(prepare())

    assert set(states) == set(QueueKind)
    assert all(state.queued == 3 for state in states.values())
    assert all(state.running == 0 for state in states.values())
