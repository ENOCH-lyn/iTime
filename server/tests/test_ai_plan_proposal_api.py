import asyncio
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from http.server import ThreadingHTTPServer
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from cryptography.fernet import Fernet

from itime.ai.worker import process_one
from itime.config import Settings
from itime.database import Database
from itime.quota.reconciliation import reconcile_one
from tests.conftest import SERVER, LiveServer, isolated_database, psycopg_url
from tests.quota_helpers import ensure_quota_policy
from tests.test_calendar_series import create_account
from tests.test_planning_worker import (
    PlanningUpstreamState,
    planning_upstream_handler,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def plan_database_url(admin_url: str) -> Iterator[str]:
    with isolated_database(admin_url) as database_url:
        yield database_url


@pytest.fixture(scope="session")
def plan_live_server(
    plan_database_url: str,
    tmp_path_factory,
) -> Iterator[LiveServer]:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    log_path = tmp_path_factory.mktemp("plan-conversation-api") / "server.log"
    with log_path.open("w") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "tests.support_app:create_test_app",
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
                "ITIME_DATABASE_URL": plan_database_url,
            },
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 15
            with httpx.Client(base_url=origin, timeout=1, trust_env=False) as probe:
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        pytest.fail(f"规划对话测试 API 启动失败，请检查 {log_path}")
                    try:
                        if probe.get("/api/ready").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.05)
                else:
                    pytest.fail(f"规划对话测试 API 启动超时，请检查 {log_path}")
            yield LiveServer(origin, log_path)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@pytest.fixture
def plan_client(
    plan_live_server: LiveServer,
    plan_database_url: str,
) -> Iterator[httpx.Client]:
    with psycopg.connect(
        psycopg_url(plan_database_url),
        autocommit=True,
    ) as connection:
        connection.execute("DELETE FROM auth_rate_buckets")
    with httpx.Client(
        base_url=plan_live_server.origin,
        timeout=10,
        trust_env=False,
    ) as client:
        yield client


@pytest.fixture
def plan_upstream() -> Iterator[tuple[PlanningUpstreamState, str]]:
    state = PlanningUpstreamState()
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        planning_upstream_handler(state),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state, f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def publish_planning_config(database_url: str, endpoint: str) -> UUID:
    settings = Settings(database_url=database_url, environment="test")
    cipher = Fernet(
        (settings.ai_encryption_key or settings.encryption_key).get_secret_value()
    )
    config_id = uuid4()
    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,
    ) as connection:
        current_id, version = connection.execute(
            """
            SELECT current_version_id, next_version
            FROM ai_config_capabilities
            WHERE capability='planning'
            FOR UPDATE
            """
        ).fetchone()
        if current_id is not None:
            connection.execute(
                """
                UPDATE ai_config_versions
                SET status='retired', retired_at=now(), updated_at=now()
                WHERE id=%s AND status='published'
                """,
                (current_id,),
            )
        connection.execute(
            """
            INSERT INTO ai_config_versions (
                id, capability, version, endpoint_url, model,
                api_key_ciphertext, timeout_seconds, max_output_tokens,
                price_currency, input_price_nanos_per_million_tokens,
                cached_input_price_nanos_per_million_tokens,
                output_price_nanos_per_million_tokens, status,
                last_test_id, last_test_started_at, last_test_completed_at,
                published_at
            ) VALUES (
                %s, 'planning', %s, %s, 'planning-chat-model', %s, 10, 512,
                'CNY', 1000000000, 500000000, 2000000000,
                'published', %s, now(), now(), now()
            )
            """,
            (
                config_id,
                version,
                endpoint,
                cipher.encrypt(b"sk-planning-conversation"),
                uuid4(),
            ),
        )
        connection.execute(
            """
            UPDATE ai_config_capabilities
            SET current_version_id=%s, next_version=%s, updated_at=now()
            WHERE capability='planning'
            """,
            (config_id, version + 1),
        )
    ensure_quota_policy(database_url)
    return config_id


def seed_task(database_url: str, owner_id: str, *, title: str = "整理项目计划") -> UUID:
    task_id = uuid4()
    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            """
            INSERT INTO tasks (
                id, owner_id, title, estimated_minutes, estimate_source
            ) VALUES (%s, %s, %s, 60, 'user')
            """,
            (task_id, owner_id, title),
        )
        connection.execute(
            """
            INSERT INTO user_privacy_settings (
                owner_id, quality_improvement_enabled
            ) VALUES (%s, true)
            ON CONFLICT (owner_id) DO UPDATE
            SET quality_improvement_enabled=true
            """,
            (owner_id,),
        )
    return task_id


def seed_related_conversation(database_url: str, owner_id: str) -> None:
    conversation_id = uuid4()
    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            """
            INSERT INTO ai_conversations (
                id, owner_id, title, title_source
            ) VALUES (%s, %s, '项目背景', 'user')
            """,
            (conversation_id, owner_id),
        )
        connection.execute(
            """
            INSERT INTO ai_conversation_messages (
                id, owner_id, conversation_id, sequence, role, status,
                content, completed_at
            ) VALUES
                (%s, %s, %s, 1, 'user', 'complete',
                 '当前对话要优先完成项目计划', now()),
                (%s, %s, %s, 2, 'assistant', 'complete',
                 '已记录项目计划的优先级。', now())
            """,
            (
                uuid4(),
                owner_id,
                conversation_id,
                uuid4(),
                owner_id,
                conversation_id,
            ),
        )


def create_conversation(client: httpx.Client) -> dict:
    response = client.post(
        "/api/v1/ai/conversations",
        json={"operation_id": str(uuid4())},
    )
    assert response.status_code == 201, response.text
    return response.json()["conversation"]


def planning_message_payload(
    conversation: dict,
    task_id: UUID,
    *,
    operation_id: UUID | None = None,
) -> dict:
    return {
        "operation_id": str(operation_id or uuid4()),
        "expected_conversation_version": conversation["version"],
        "content": "请结合当前对话，为今天生成可确认的计划。",
        "response_mode": "planning",
        "planning": {
            "scope": "day",
            "target_date": datetime.now(UTC).date().isoformat(),
            "density": "balanced",
            "buffer_minutes": 0,
            "availability_start": "08:00:00",
            "availability_end": "22:00:00",
            "task_ids": [str(task_id)],
            "instruction": "优先安排当前对话提到的项目",
        },
    }


def submit_planning_message(
    client: httpx.Client,
    conversation: dict,
    task_id: UUID,
    *,
    operation_id: UUID | None = None,
) -> httpx.Response:
    return client.post(
        f"/api/v1/ai/conversations/{conversation['id']}/messages",
        json=planning_message_payload(
            conversation,
            task_id,
            operation_id=operation_id,
        ),
    )


def run_worker_once(database_url: str) -> bool:
    async def execute() -> bool:
        settings = Settings(
            database_url=database_url,
            environment="test",
            ai_job_lease_seconds=30,
        )
        database = Database(settings)
        try:
            return await process_one(database, settings)
        finally:
            await database.close()

    return asyncio.run(execute())


def run_reconciliation_once(database_url: str) -> bool:
    async def execute() -> bool:
        settings = Settings(database_url=database_url, environment="test")
        database = Database(settings)
        try:
            return await reconcile_one(database, settings)
        finally:
            await database.close()

    return asyncio.run(execute())


def test_planning_mode_requires_fields_and_text_mode_rejects_planning(
    plan_client: httpx.Client,
    plan_database_url: str,
) -> None:
    client = plan_client
    database_url = plan_database_url
    create_account(client, database_url)
    conversation = create_conversation(client)
    common = {
        "operation_id": str(uuid4()),
        "expected_conversation_version": conversation["version"],
        "content": "生成计划",
    }

    missing = client.post(
        f"/api/v1/ai/conversations/{conversation['id']}/messages",
        json={**common, "response_mode": "planning"},
    )
    assert missing.status_code == 422

    unexpected = client.post(
        f"/api/v1/ai/conversations/{conversation['id']}/messages",
        json={
            **common,
            "operation_id": str(uuid4()),
            "response_mode": "text",
            "planning": {
                "scope": "day",
                "target_date": datetime.now(UTC).date().isoformat(),
            },
        },
    )
    assert unexpected.status_code == 422


def test_planning_admission_is_atomic_replayable_and_cross_capability_bounded(
    plan_client: httpx.Client,
    plan_database_url: str,
    plan_upstream: tuple[PlanningUpstreamState, str],
) -> None:
    client = plan_client
    database_url = plan_database_url
    _, endpoint = plan_upstream
    _, owner_id = create_account(client, database_url)
    publish_planning_config(database_url, endpoint)
    task_id = seed_task(database_url, owner_id)
    conversation = create_conversation(client)
    operation_id = uuid4()

    submitted = submit_planning_message(
        client,
        conversation,
        task_id,
        operation_id=operation_id,
    )
    assert submitted.status_code == 202, submitted.text
    body = submitted.json()
    assert body["job"]["capability"] == "planning"
    assert body["job"]["conversation_id"] == conversation["id"]
    assert body["messages"][1]["status"] == "queued"
    assert (
        submit_planning_message(
            client,
            conversation,
            task_id,
            operation_id=operation_id,
        ).json()
        == body
    )

    with psycopg.connect(psycopg_url(database_url)) as connection:
        stored = connection.execute(
            """
            SELECT
                job.planning_snapshot_id IS NOT NULL,
                generation.history_through_sequence,
                generation.context_message_ids,
                (SELECT count(*) FROM ai_conversation_messages
                 WHERE conversation_id=%s),
                (SELECT count(*) FROM ai_job_events WHERE job_id=job.id),
                (SELECT count(*) FROM ai_quota_reservations
                 WHERE job_id=job.id),
                (SELECT count(*) FROM user_operations
                 WHERE owner_id=job.owner_id AND operation_id=%s)
            FROM ai_jobs AS job
            JOIN ai_chat_generations AS generation
              ON generation.job_id=job.id
            WHERE job.id=%s
            """,
            (conversation["id"], operation_id, body["job"]["id"]),
        ).fetchone()
    assert stored == (True, 1, [], 2, 1, 1, 1)

    active_same_conversation = client.post(
        f"/api/v1/ai/conversations/{conversation['id']}/messages",
        json={
            "operation_id": str(uuid4()),
            "expected_conversation_version": body["conversation"]["version"],
            "content": "普通回复也必须互斥",
        },
    )
    assert active_same_conversation.status_code == 409
    assert (
        active_same_conversation.json()["error"]["code"]
        == "conversation_generation_active"
    )

    for _ in range(2):
        another = create_conversation(client)
        response = submit_planning_message(client, another, task_id)
        assert response.status_code == 202, response.text
    fourth = create_conversation(client)
    limited = client.post(
        f"/api/v1/ai/conversations/{fourth['id']}/messages",
        json={
            "operation_id": str(uuid4()),
            "expected_conversation_version": fourth["version"],
            "content": "跨能力第四个用户额度作业",
        },
    )
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "ai_job_limit_reached"
    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,
    ) as connection:
        connection.execute("DELETE FROM users WHERE id=%s", (owner_id,))


def test_planning_worker_streams_only_text_and_commits_conversation_proposal(
    plan_client: httpx.Client,
    plan_database_url: str,
    plan_upstream: tuple[PlanningUpstreamState, str],
) -> None:
    client = plan_client
    database_url = plan_database_url
    state, endpoint = plan_upstream
    state.mode = "success"
    _, owner_id = create_account(client, database_url)
    publish_planning_config(database_url, endpoint)
    task_id = seed_task(database_url, owner_id, title="完成规划集成测试")
    seed_related_conversation(database_url, owner_id)
    conversation = create_conversation(client)
    submitted = submit_planning_message(client, conversation, task_id)
    assert submitted.status_code == 202, submitted.text
    job_id = submitted.json()["job"]["id"]
    response_message_id = submitted.json()["messages"][1]["id"]

    assert run_worker_once(database_url)

    with psycopg.connect(psycopg_url(database_url)) as connection:
        terminal = connection.execute(
            """
            SELECT
                job.status, job.output,
                message.status, message.content, message.completed_at,
                proposal.id, proposal.conversation_id,
                proposal.source_message_id,
                reservation.status,
                (SELECT count(*) FROM ai_quota_ledger_entries
                 WHERE job_id=job.id AND kind='usage_charge'),
                (SELECT status FROM ai_quality_samples
                 WHERE source_job_id=job.id)
            FROM ai_jobs AS job
            JOIN ai_chat_generations AS generation
              ON generation.job_id=job.id
            JOIN ai_conversation_messages AS message
              ON message.id=generation.response_message_id
            JOIN ai_proposals AS proposal
              ON proposal.source_job_id=job.id
            JOIN ai_quota_reservations AS reservation
              ON reservation.job_id=job.id
            WHERE job.id=%s
            """,
            (job_id,),
        ).fetchone()
        events = connection.execute(
            """
            SELECT kind, payload
            FROM ai_job_events
            WHERE job_id=%s
            ORDER BY sequence
            """,
            (job_id,),
        ).fetchall()
    assert terminal[0] == "succeeded"
    assert terminal[1] == {
        "resource_type": "planning_proposal",
        "message_id": response_message_id,
        "proposal_id": str(terminal[5]),
    }
    assert terminal[2:5] == ("complete", "计划草案已生成。", terminal[4])
    assert terminal[4] is not None
    assert str(terminal[6]) == conversation["id"]
    assert str(terminal[7]) == response_message_id
    assert terminal[8:] == ("settled", 1, "pending")
    assert [event[1]["status"] for event in events if event[0] == "job_state"] == [
        "queued",
        "running",
        "succeeded",
    ]
    deltas = [event[1]["text"] for event in events if event[0] == "content_delta"]
    assert deltas == ["计划草案已生成。"]
    assert all("submit_itime_plan" not in delta for delta in deltas)

    assert len(state.requests) == 1
    provider_messages = state.requests[0]["messages"]
    assert any(
        message.get("role") == "user"
        and "请结合当前对话" in str(message.get("content"))
        for message in provider_messages[:-1]
    )
    assert any(
        message.get("role") == "system"
        and "当前对话要优先完成项目计划" in str(message.get("content"))
        for message in provider_messages[:-1]
    )
    assert "planning_snapshot" in str(provider_messages[-1]["content"])

    stream = client.get(f"/api/v1/ai/jobs/{job_id}/events")
    assert stream.status_code == 200
    assert "计划草案已生成。" in stream.text
    assert "submit_itime_plan" not in stream.text


def test_planning_conversation_retry_resets_then_completes_message(
    plan_client: httpx.Client,
    plan_database_url: str,
    plan_upstream: tuple[PlanningUpstreamState, str],
) -> None:
    client = plan_client
    database_url = plan_database_url
    state, endpoint = plan_upstream
    state.mode = "rate_limit"
    _, owner_id = create_account(client, database_url)
    publish_planning_config(database_url, endpoint)
    task_id = seed_task(database_url, owner_id)
    conversation = create_conversation(client)
    submitted = submit_planning_message(client, conversation, task_id)
    assert submitted.status_code == 202, submitted.text
    job_id = submitted.json()["job"]["id"]

    assert run_worker_once(database_url)
    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,
    ) as connection:
        retrying = connection.execute(
            """
            SELECT job.status, generation.stream_generation,
                   message.status, message.content, reservation.status
            FROM ai_jobs AS job
            JOIN ai_chat_generations AS generation
              ON generation.job_id=job.id
            JOIN ai_conversation_messages AS message
              ON message.id=generation.response_message_id
            JOIN ai_quota_reservations AS reservation
              ON reservation.job_id=job.id
            WHERE job.id=%s
            """,
            (job_id,),
        ).fetchone()
        connection.execute(
            """
            UPDATE ai_jobs
            SET available_at=now() - interval '1 second'
            WHERE id=%s
            """,
            (job_id,),
        )
    assert retrying == ("queued", 1, "queued", "", "reserved")

    state.mode = "success"
    assert run_worker_once(database_url)
    with psycopg.connect(psycopg_url(database_url)) as connection:
        completed = connection.execute(
            """
            SELECT job.status, generation.stream_generation,
                   message.status, message.content, reservation.status
            FROM ai_jobs AS job
            JOIN ai_chat_generations AS generation
              ON generation.job_id=job.id
            JOIN ai_conversation_messages AS message
              ON message.id=generation.response_message_id
            JOIN ai_quota_reservations AS reservation
              ON reservation.job_id=job.id
            WHERE job.id=%s
            """,
            (job_id,),
        ).fetchone()
        resets = connection.execute(
            """
            SELECT count(*) FROM ai_job_events
            WHERE job_id=%s AND kind='content_reset'
            """,
            (job_id,),
        ).fetchone()[0]
    assert completed == ("succeeded", 2, "complete", "计划草案已生成。", "settled")
    assert resets == 1


def test_invalid_planning_result_fails_message_without_proposal_or_charge(
    plan_client: httpx.Client,
    plan_database_url: str,
    plan_upstream: tuple[PlanningUpstreamState, str],
) -> None:
    client = plan_client
    database_url = plan_database_url
    state, endpoint = plan_upstream
    state.mode = "invalid_plan"
    _, owner_id = create_account(client, database_url)
    publish_planning_config(database_url, endpoint)
    task_id = seed_task(database_url, owner_id)
    conversation = create_conversation(client)
    submitted = submit_planning_message(client, conversation, task_id)
    assert submitted.status_code == 202, submitted.text
    job_id = submitted.json()["job"]["id"]

    assert run_worker_once(database_url)
    with psycopg.connect(psycopg_url(database_url)) as connection:
        failed = connection.execute(
            """
            SELECT job.status, job.error_code,
                   message.status, message.error_code, message.completed_at,
                   reservation.status, reservation.release_reason,
                   (SELECT count(*) FROM ai_proposals
                    WHERE source_job_id=job.id),
                   (SELECT count(*) FROM ai_quota_ledger_entries
                    WHERE job_id=job.id)
            FROM ai_jobs AS job
            JOIN ai_chat_generations AS generation
              ON generation.job_id=job.id
            JOIN ai_conversation_messages AS message
              ON message.id=generation.response_message_id
            JOIN ai_quota_reservations AS reservation
              ON reservation.job_id=job.id
            WHERE job.id=%s
            """,
            (job_id,),
        ).fetchone()
    assert failed[0:4] == (
        "failed",
        "unknown_plan_candidate",
        "failed",
        "unknown_plan_candidate",
    )
    assert failed[4] is not None
    assert failed[5:] == ("released", "failed", 0, 0)


@pytest.mark.parametrize(
    ("terminal", "expected_status"),
    [("cancel", "cancelled"), ("reconcile", "timed_out")],
)
def test_planning_conversation_cancel_and_reconciliation_update_message(
    plan_client: httpx.Client,
    plan_database_url: str,
    plan_upstream: tuple[PlanningUpstreamState, str],
    terminal: str,
    expected_status: str,
) -> None:
    client = plan_client
    database_url = plan_database_url
    _, endpoint = plan_upstream
    _, owner_id = create_account(client, database_url)
    publish_planning_config(database_url, endpoint)
    task_id = seed_task(database_url, owner_id)
    conversation = create_conversation(client)
    submitted = submit_planning_message(client, conversation, task_id)
    assert submitted.status_code == 202, submitted.text
    job_id = submitted.json()["job"]["id"]

    if terminal == "cancel":
        response = client.post(
            f"/api/v1/ai/jobs/{job_id}/cancel",
            json={"operation_id": str(uuid4())},
        )
        assert response.status_code == 200, response.text
    else:
        with psycopg.connect(
            psycopg_url(database_url),
            autocommit=True,
        ) as connection:
            connection.execute(
                """
                UPDATE ai_jobs
                SET deadline_at=now() - interval '1 second'
                WHERE id=%s
                """,
                (job_id,),
            )
        assert run_reconciliation_once(database_url)

    with psycopg.connect(psycopg_url(database_url)) as connection:
        projection = connection.execute(
            """
            SELECT job.status, message.status, message.error_code,
                   message.completed_at, reservation.status,
                   reservation.release_reason
            FROM ai_jobs AS job
            JOIN ai_chat_generations AS generation
              ON generation.job_id=job.id
            JOIN ai_conversation_messages AS message
              ON message.id=generation.response_message_id
            JOIN ai_quota_reservations AS reservation
              ON reservation.job_id=job.id
            WHERE job.id=%s
            """,
            (job_id,),
        ).fetchone()
        terminal_event = connection.execute(
            """
            SELECT payload
            FROM ai_job_events
            WHERE job_id=%s AND payload->>'status'=%s
            ORDER BY sequence DESC
            LIMIT 1
            """,
            (job_id, expected_status),
        ).fetchone()[0]
    assert projection[0:2] == (expected_status, expected_status)
    assert projection[2] == (
        "ai_job_deadline_exceeded" if expected_status == "timed_out" else None
    )
    assert projection[3] is not None
    assert projection[4:] == ("released", expected_status)
    assert terminal_event["message_id"] == submitted.json()["messages"][1]["id"]
