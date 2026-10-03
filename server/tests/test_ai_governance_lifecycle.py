# ruff: noqa: F811

import asyncio
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import psycopg
import pytest

from itime.ai.governance_service import cleanup_expired_governance
from itime.config import Settings
from itime.database import Database
from tests.conftest import SERVER, psycopg_url
from tests.test_ai_conversation_worker import (
    ChatUpstreamState,
    chat_upstream,  # noqa: F401
    conversation_worker_client,  # noqa: F401
    conversation_worker_database_url,  # noqa: F401
    conversation_worker_live_server,  # noqa: F401
    enqueue_conversation_job,
    make_job_due,
    run_process_once,
)

pytestmark = pytest.mark.integration


def governance_snapshot(database_url: str, job_id: str) -> dict:
    with psycopg.connect(psycopg_url(database_url)) as connection:
        logs = connection.execute(
            """
            SELECT id, attempt_number, request_body, response_body, status,
                   error_code, error_stage, provider_request_id, http_status,
                   started_at, request_prepared_at, finished_at, expires_at
            FROM ai_body_logs
            WHERE job_id=%s
            ORDER BY attempt_number
            """,
            (job_id,),
        ).fetchall()
        samples = connection.execute(
            """
            SELECT source_body_log_id, request_body, response_body, status,
                   sampled_at, expires_at
            FROM ai_quality_samples
            WHERE source_job_id=%s
            """,
            (job_id,),
        ).fetchall()
    return {"logs": logs, "samples": samples}


def test_success_logs_sanitized_attempt_and_creates_independent_sample(
    conversation_worker_client,
    conversation_worker_database_url,
    chat_upstream: tuple[ChatUpstreamState, str],
):
    state, endpoint = chat_upstream
    state.reset("split_sse")
    prompt = "记录这一条质量样本"
    submitted = enqueue_conversation_job(
        conversation_worker_client,
        conversation_worker_database_url,
        endpoint,
        prompt=prompt,
    )
    job_id = submitted["job"]["id"]

    assert run_process_once(conversation_worker_database_url)
    snapshot = governance_snapshot(conversation_worker_database_url, job_id)
    assert len(snapshot["logs"]) == 1
    body_log = snapshot["logs"][0]
    assert body_log[1] == 1
    assert body_log[4] == "succeeded"
    assert body_log[5:7] == (None, None)
    assert body_log[7:9] == ("chat-request-1", 200)
    assert body_log[10] is not None
    assert body_log[11] is not None
    assert timedelta(days=6, hours=23) < body_log[12] - body_log[9] < timedelta(
        days=7,
        minutes=1,
    )
    serialized_request = json.dumps(body_log[2], ensure_ascii=False)
    assert prompt in serialized_request
    assert "sk-conversation-secret" not in serialized_request
    assert "Authorization" not in serialized_request
    assert "Cookie" not in serialized_request
    assert body_log[3] == "前半段，后半段"

    assert len(snapshot["samples"]) == 1
    sample = snapshot["samples"][0]
    assert str(sample[0]) == str(body_log[0])
    assert sample[1] == body_log[2]
    assert sample[2] == body_log[3]
    assert sample[3] == "pending"
    assert timedelta(days=29, hours=23) < sample[5] - sample[4] < timedelta(
        days=30,
        minutes=1,
    )


def test_retry_keeps_attempt_logs_and_disabled_setting_blocks_new_sample(
    conversation_worker_client,
    conversation_worker_database_url,
    chat_upstream: tuple[ChatUpstreamState, str],
):
    state, endpoint = chat_upstream
    state.reset("rate_then_success")
    retried = enqueue_conversation_job(
        conversation_worker_client,
        conversation_worker_database_url,
        endpoint,
        prompt="先限流再完成",
    )
    retry_job_id = retried["job"]["id"]

    assert run_process_once(conversation_worker_database_url)
    first = governance_snapshot(
        conversation_worker_database_url,
        retry_job_id,
    )
    assert [(row[1], row[4], row[5]) for row in first["logs"]] == [
        (1, "retryable_failed", "ai_rate_limited")
    ]
    assert first["samples"] == []

    make_job_due(conversation_worker_database_url, retry_job_id)
    assert run_process_once(conversation_worker_database_url)
    completed = governance_snapshot(
        conversation_worker_database_url,
        retry_job_id,
    )
    assert [(row[1], row[4]) for row in completed["logs"]] == [
        (1, "retryable_failed"),
        (2, "succeeded"),
    ]
    assert len(completed["samples"]) == 1
    assert completed["samples"][0][0] == completed["logs"][1][0]

    state.reset("json")
    disabled = enqueue_conversation_job(
        conversation_worker_client,
        conversation_worker_database_url,
        endpoint,
        prompt="关闭后不进入质检",
    )
    disabled_job_id = disabled["job"]["id"]
    with psycopg.connect(
        psycopg_url(conversation_worker_database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            """
            UPDATE user_privacy_settings
            SET quality_improvement_enabled=false,
                version=version + 1,
                updated_at=now()
            WHERE owner_id=(SELECT owner_id FROM ai_jobs WHERE id=%s)
            """,
            (disabled_job_id,),
        )
    assert run_process_once(conversation_worker_database_url)
    blocked = governance_snapshot(
        conversation_worker_database_url,
        disabled_job_id,
    )
    assert [row[4] for row in blocked["logs"]] == ["succeeded"]
    assert blocked["samples"] == []


def test_failure_has_log_without_sample_and_expiry_cleanup_is_concurrent(
    conversation_worker_client,
    conversation_worker_database_url,
    chat_upstream: tuple[ChatUpstreamState, str],
):
    state, endpoint = chat_upstream
    state.reset("authentication")
    failed = enqueue_conversation_job(
        conversation_worker_client,
        conversation_worker_database_url,
        endpoint,
        prompt="鉴权失败也保留技术日志",
    )
    failed_job_id = failed["job"]["id"]
    assert run_process_once(conversation_worker_database_url)
    failure = governance_snapshot(
        conversation_worker_database_url,
        failed_job_id,
    )
    assert [(row[4], row[5], row[6]) for row in failure["logs"]] == [
        ("failed", "ai_authentication_failed", "authentication")
    ]
    assert failure["samples"] == []

    state.reset("json")
    expiring = enqueue_conversation_job(
        conversation_worker_client,
        conversation_worker_database_url,
        endpoint,
        prompt="验证到期清理",
    )
    expiring_job_id = expiring["job"]["id"]
    assert run_process_once(conversation_worker_database_url)
    with psycopg.connect(
        psycopg_url(conversation_worker_database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            """
            UPDATE ai_body_logs
            SET started_at=started_at - interval '8 days',
                request_prepared_at=request_prepared_at - interval '8 days',
                finished_at=finished_at - interval '8 days',
                expires_at=now() - interval '1 second'
            WHERE job_id=%s
            """,
            (expiring_job_id,),
        )
    async def cleanup_body_log() -> tuple[int, int]:
        database = Database(
            Settings(
                database_url=conversation_worker_database_url,
                environment="test",
            )
        )
        try:
            return await cleanup_expired_governance(database)
        finally:
            await database.close()

    assert asyncio.run(cleanup_body_log()) == (1, 0)
    after_body_cleanup = governance_snapshot(
        conversation_worker_database_url,
        expiring_job_id,
    )
    assert after_body_cleanup["logs"] == []
    assert len(after_body_cleanup["samples"]) == 1

    with psycopg.connect(
        psycopg_url(conversation_worker_database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            """
            UPDATE ai_quality_samples
            SET sampled_at=sampled_at - interval '31 days',
                expires_at=now() - interval '1 second'
            WHERE source_job_id=%s
            """,
            (expiring_job_id,),
        )

    async def concurrent_cleanup() -> list[tuple[int, int]]:
        current_settings = Settings(
            database_url=conversation_worker_database_url,
            environment="test",
        )
        first = Database(current_settings)
        second = Database(current_settings)
        try:
            return list(
                await asyncio.gather(
                    cleanup_expired_governance(first),
                    cleanup_expired_governance(second),
                )
            )
        finally:
            await first.close()
            await second.close()

    counts = asyncio.run(concurrent_cleanup())
    assert sum(item[0] for item in counts) == 0
    assert sum(item[1] for item in counts) == 1
    assert governance_snapshot(
        conversation_worker_database_url,
        expiring_job_id,
    ) == {"logs": [], "samples": []}


def test_success_rechecks_quality_setting_at_terminal_commit(
    conversation_worker_client,
    conversation_worker_database_url,
    chat_upstream: tuple[ChatUpstreamState, str],
):
    state, endpoint = chat_upstream
    state.reset("json")
    submitted = enqueue_conversation_job(
        conversation_worker_client,
        conversation_worker_database_url,
        endpoint,
        prompt="终态前关闭质检",
    )
    job_id = submitted["job"]["id"]

    with psycopg.connect(
        psycopg_url(conversation_worker_database_url)
    ) as blocker:
        owner_id = blocker.execute(
            "SELECT owner_id FROM ai_jobs WHERE id=%s",
            (job_id,),
        ).fetchone()[0]
        blocker.execute(
            "SELECT owner_id FROM user_privacy_settings "
            "WHERE owner_id=%s FOR UPDATE",
            (owner_id,),
        )
        blocker.execute(
            """
            UPDATE user_privacy_settings
            SET quality_improvement_enabled=false,
                version=version + 1,
                updated_at=now()
            WHERE owner_id=%s
            """,
            (owner_id,),
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            worker = pool.submit(
                run_process_once,
                conversation_worker_database_url,
            )
            assert state.request_received.wait(timeout=5)
            time.sleep(0.1)
            assert not worker.done()
            blocker.commit()
            assert worker.result(timeout=10)

    snapshot = governance_snapshot(
        conversation_worker_database_url,
        job_id,
    )
    assert [row[4] for row in snapshot["logs"]] == ["succeeded"]
    assert snapshot["samples"] == []


def test_retention_worker_once_cleans_due_rows(
    conversation_worker_client,
    conversation_worker_database_url,
    chat_upstream: tuple[ChatUpstreamState, str],
):
    state, endpoint = chat_upstream
    state.reset("json")
    submitted = enqueue_conversation_job(
        conversation_worker_client,
        conversation_worker_database_url,
        endpoint,
        prompt="独立清理进程",
    )
    job_id = submitted["job"]["id"]
    assert run_process_once(conversation_worker_database_url)
    with psycopg.connect(
        psycopg_url(conversation_worker_database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            """
            UPDATE ai_body_logs
            SET started_at=started_at - interval '8 days',
                request_prepared_at=request_prepared_at - interval '8 days',
                finished_at=finished_at - interval '8 days',
                expires_at=now() - interval '1 second'
            WHERE job_id=%s
            """,
            (job_id,),
        )
        connection.execute(
            """
            UPDATE ai_quality_samples
            SET sampled_at=sampled_at - interval '31 days',
                expires_at=now() - interval '1 second'
            WHERE source_job_id=%s
            """,
            (job_id,),
        )

    process = subprocess.run(
        [sys.executable, "-m", "itime.ai.retention_worker", "--once"],
        cwd=SERVER,
        env={
            **os.environ,
            "ITIME_ENVIRONMENT": "test",
            "ITIME_DATABASE_URL": conversation_worker_database_url,
        },
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    assert "独立清理进程" not in process.stdout + process.stderr
    assert governance_snapshot(
        conversation_worker_database_url,
        job_id,
    ) == {"logs": [], "samples": []}
