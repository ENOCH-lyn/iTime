import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

import performance.sampler as sampler
from itime.config import Settings
from performance.contracts import SeedProfile
from performance.seed import seed_dataset
from tests.conftest import isolated_database, psycopg_url

BASE_TIME = datetime(2026, 10, 5, 12, tzinfo=UTC)
DOCKER_SAMPLE = {
    "Name": "itime-perf-abc-api-1",
    "CPUPerc": "37.50%",
    "MemUsage": "512MiB / 1.25GiB",
    "NetIO": "1.2MB / 820kB",
    "BlockIO": "4.1MB / 2.0MB",
    "PIDs": "3",
}


def test_parse_docker_stats_with_iec_and_si_units():
    sample = sampler.parse_docker_stat(DOCKER_SAMPLE, at=BASE_TIME)

    assert sample.cpu_percent == 37.5
    assert sample.memory_bytes == 512 * 1024 * 1024
    assert sample.memory_limit_bytes == int(1.25 * 1024**3)
    assert sample.network_rx_bytes == 1_200_000
    assert sample.network_tx_bytes == 820_000
    assert sample.block_read_bytes == 4_100_000
    assert sample.block_write_bytes == 2_000_000
    assert sample.pids == 3


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1e+03kB", 1_000_000),
        ("1.25E+02MiB", 125 * 1024 * 1024),
    ],
)
def test_parse_bytes_accepts_docker_scientific_notation(
    value: str,
    expected: int,
):
    assert sampler.parse_bytes(value) == expected


@pytest.mark.parametrize("value", ["", "unknown", "1XB", "-1MiB"])
def test_parse_bytes_rejects_unknown_or_negative_values(value: str):
    with pytest.raises(ValueError):
        sampler.parse_bytes(value)


@pytest.mark.integration
def test_sample_postgres_observes_connections_queue_and_statements(
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
        manifest = asyncio.run(
            seed_dataset(
                settings,
                SeedProfile.standard(
                    accounts=1,
                    active_accounts=1,
                    tasks_per_active=10,
                    sync_events_per_active=20,
                ),
                seed=20261005,
                at=BASE_TIME,
                credentials_path=tmp_path / "accounts.json",
            )
        )
        with psycopg.connect(
            psycopg_url(database_url),
        ) as setup:
            setup.execute("CREATE EXTENSION IF NOT EXISTS pg_stat_statements")
            owner_id, conversation_id, request_message_id, response_message_id = (
                setup.execute(
                """
                SELECT u.id, c.id, request.id, response.id
                FROM users u
                JOIN ai_conversations c ON c.owner_id=u.id
                JOIN ai_conversation_messages request
                  ON request.conversation_id=c.id AND request.sequence=1
                JOIN ai_conversation_messages response
                  ON response.conversation_id=c.id AND response.sequence=2
                WHERE u.email=%s
                ORDER BY c.created_at
                LIMIT 1
                """,
                (manifest.credentials[0].email,),
                ).fetchone()
            )
            config_id = setup.execute(
                """
                SELECT current_version_id
                FROM ai_config_capabilities
                WHERE capability='conversation'
                """
            ).fetchone()[0]
            job_id = uuid4()
            setup.execute(
                """
                INSERT INTO ai_jobs (
                    id, owner_id, capability, config_version_id,
                    input_media_id, conversation_id, planning_snapshot_id,
                    review_session_id, review_snapshot_id, quota_mode,
                    status, input_summary, output, error_code, error_message,
                    attempt_count, max_attempts, last_event_sequence,
                    available_at, deadline_at, lease_id, lease_until, version,
                    created_at, updated_at, finished_at
                ) VALUES (
                    %s, %s, 'conversation', %s,
                    NULL, %s, NULL,
                    NULL, NULL, 'platform',
                    'queued', '{}'::jsonb, NULL, NULL, NULL,
                    0, 3, 0,
                    %s, %s, NULL, NULL, 1,
                    %s, %s, NULL
                )
                """,
                (
                    job_id,
                    owner_id,
                    config_id,
                    conversation_id,
                    BASE_TIME,
                    BASE_TIME + timedelta(minutes=3),
                    BASE_TIME,
                    BASE_TIME,
                ),
            )
            setup.execute(
                """
                INSERT INTO ai_chat_generations (
                    job_id, owner_id, conversation_id,
                    request_message_id, response_message_id,
                    history_through_sequence, context_message_ids,
                    public_source_context, stream_generation, created_at
                ) VALUES (
                    %s, %s, %s,
                    %s, %s,
                    2, '[]'::jsonb,
                    '[]'::jsonb, 0, %s
                )
                """,
                (
                    job_id,
                    owner_id,
                    conversation_id,
                    request_message_id,
                    response_message_id,
                    BASE_TIME,
                ),
            )
            setup.commit()

        first = psycopg.connect(psycopg_url(database_url), autocommit=True)
        second = psycopg.connect(psycopg_url(database_url), autocommit=True)
        try:
            first.execute("SELECT 1")
            second.execute("SELECT 1")
            sample = asyncio.run(
                sampler.sample_postgres(
                    database_url,
                    at=BASE_TIME,
                    include_statements=False,
                )
            )
        finally:
            first.close()
            second.close()

        assert sample.connections_total >= 3
        assert sample.connections_idle >= 2
        assert sample.connections_idle_in_transaction == 0
        assert sample.blocked_over_two_seconds == 0
        assert sample.queues["ai"]["queued"] >= 1
        assert sample.slow_queries == ()


def test_sampler_loop_never_overlaps_slow_samples(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    active = 0
    maximum_active = 0
    calls = 0
    stop = asyncio.Event()

    async def fake_write_sample(config, *, sampling_gap_seconds=0):
        nonlocal active, maximum_active, calls
        active += 1
        maximum_active = max(maximum_active, active)
        calls += 1
        await asyncio.sleep(0.02)
        active -= 1
        if calls == 3:
            stop.set()

    monkeypatch.setattr(sampler, "write_sample", fake_write_sample)
    config = sampler.SamplerConfig(
        docker_context="colima-itime-perf",
        compose_project="itime-perf-abc12345",
        database_url="postgresql+psycopg://itime:test@127.0.0.1/itime_perf_abc12345",
        output_dir=tmp_path,
        interval_seconds=0.005,
    )

    asyncio.run(sampler.sample_until_stopped(config, stop))

    assert calls == 3
    assert maximum_active == 1


def test_write_sample_flushes_aggregate_container_database_and_queue_csv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    docker_sample = sampler.parse_docker_stat(DOCKER_SAMPLE, at=BASE_TIME)
    postgres_sample = sampler.PostgresSample(
        at=BASE_TIME,
        connections_total=10,
        connections_active=2,
        connections_idle=8,
        connections_idle_in_transaction=0,
        connections_waiting=0,
        blocked_over_two_seconds=0,
        queues={
            "ai": {
                "queued": 2,
                "running": 1,
                "oldest": BASE_TIME.isoformat(),
                "terminal": 3,
            }
        },
        slow_queries=(),
    )

    async def fake_docker(*args, **kwargs):
        return (docker_sample,)

    async def fake_postgres(*args, **kwargs):
        return postgres_sample

    monkeypatch.setattr(sampler, "sample_docker", fake_docker)
    monkeypatch.setattr(sampler, "sample_postgres", fake_postgres)
    config = sampler.SamplerConfig(
        docker_context="colima-itime-perf",
        compose_project="itime-perf-abc12345",
        database_url="postgresql+psycopg://itime:test@127.0.0.1/itime_perf_abc12345",
        output_dir=tmp_path,
    )

    asyncio.run(sampler.write_sample(config))
    asyncio.run(sampler.write_sample(config, sampling_gap_seconds=2))

    assert len((tmp_path / "resources.csv").read_text().splitlines()) == 3
    assert len((tmp_path / "containers.csv").read_text().splitlines()) == 3
    assert len((tmp_path / "postgres.csv").read_text().splitlines()) == 3
    assert len((tmp_path / "queues.csv").read_text().splitlines()) == 3
    assert ",2\n" in (tmp_path / "resources.csv").read_text()
