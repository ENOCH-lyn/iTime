import asyncio
import json
import threading
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import UUID, uuid4

import psycopg
import pytest
from cryptography.fernet import Fernet

from itime.ai.job_service import cancel_job, claim_next_job, locked_job
from itime.ai.worker import process_one
from itime.config import Settings
from itime.database import Database
from itime.errors import AppError
from itime.planning.job_service import enqueue_planning_generation
from itime.planning.schemas import PlanningGenerationInput
from itime.quota.reconciliation import reconcile_one
from itime.quota.service import release_reservation
from tests.conftest import isolated_database, psycopg_url
from tests.quota_helpers import ensure_quota_policy

pytestmark = pytest.mark.integration


@dataclass
class PlanningUpstreamState:
    mode: str = "success"
    requests: list[dict] = field(default_factory=list)
    authorizations: list[str | None] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)


def _tool_arguments(request_body: dict, *, invalid: bool) -> dict:
    snapshot = json.loads(request_body["messages"][-1]["content"])[
        "planning_snapshot"
    ]
    candidate_key = (
        "task:00000000-0000-0000-0000-000000000000"
        if invalid
        else snapshot["task_candidates"][0]["candidate_key"]
    )
    return {
        "summary": "按优先级安排了一小时任务。",
        "assumptions": ["沿用任务已有估时"],
        "estimates": [],
        "blocks": [
            {
                "candidate_key": candidate_key,
                "local_date": snapshot["target_date"],
                "starts_at": "09:00:00",
                "ends_at": "10:00:00",
                "step_ids": [],
            }
        ],
        "unscheduled_items": [],
    }


def planning_upstream_handler(state: PlanningUpstreamState):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = json.loads(
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
            )
            with state.lock:
                state.requests.append(body)
                state.authorizations.append(self.headers.get("Authorization"))
                mode = state.mode
            if mode == "rate_limit":
                status = 429
                response = {"error": "busy"}
            else:
                arguments = _tool_arguments(body, invalid=mode == "invalid_plan")
                status = 200
                response = {
                    "choices": [
                        {
                            "message": {
                                "content": "计划草案已生成。",
                                "tool_calls": [
                                    {
                                        "id": "planning-call-1",
                                        "type": "function",
                                        "function": {
                                            "name": "submit_itime_plan",
                                            "arguments": json.dumps(arguments),
                                        },
                                    }
                                ],
                            }
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 120,
                        "completion_tokens": 80,
                        "total_tokens": 200,
                    },
                }
            encoded = json.dumps(response).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("x-request-id", "planning-worker-request")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args) -> None:
            pass

    return Handler


@pytest.fixture
def planning_upstream():
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


def _insert_published_config(
    database_url: str,
    *,
    capability: str,
    endpoint: str,
    key: str,
    version: int = 1,
) -> UUID:
    settings = Settings(database_url=database_url, environment="test")
    cipher = Fernet(
        (settings.ai_encryption_key or settings.encryption_key).get_secret_value()
    )
    config_id = uuid4()
    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,
    ) as connection:
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
                %s, %s, %s, %s, 'planning-model', %s, 10, 512,
                'CNY', 1000000000, 500000000, 2000000000,
                'published', %s, now(), now(), now()
            )
            """,
            (
                config_id,
                capability,
                version,
                endpoint,
                cipher.encrypt(key.encode()),
                uuid4(),
            ),
        )
        connection.execute(
            """
            UPDATE ai_config_capabilities
            SET current_version_id=%s, next_version=%s, updated_at=now()
            WHERE capability=%s
            """,
            (config_id, version + 1, capability),
        )
    return config_id


def seed_planning_context(
    database_url: str,
    endpoint: str,
    *,
    with_task: bool = True,
) -> tuple[UUID, UUID | None, UUID]:
    owner_id = uuid4()
    task_id = uuid4() if with_task else None
    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            """
            INSERT INTO users (id, email, password_hash, email_verified_at)
            VALUES (%s, %s, 'test-password-hash', now())
            """,
            (owner_id, f"{owner_id}@example.com"),
        )
        connection.execute(
            """
            INSERT INTO user_time_settings (owner_id, time_zone)
            VALUES (%s, 'Asia/Shanghai')
            """,
            (owner_id,),
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
        if task_id is not None:
            connection.execute(
                """
                INSERT INTO tasks (
                    id, owner_id, title, estimated_minutes, estimate_source
                ) VALUES (%s, %s, '编写规划测试', 60, 'user')
                """,
                (task_id, owner_id),
            )
    ensure_quota_policy(database_url)
    config_id = _insert_published_config(
        database_url,
        capability="planning",
        endpoint=endpoint,
        key="sk-planning-frozen",
    )
    return owner_id, task_id, config_id


def planning_input(
    *,
    operation_id: UUID | None = None,
    target_date: date | None = None,
) -> PlanningGenerationInput:
    return PlanningGenerationInput(
        operation_id=operation_id or uuid4(),
        scope="day",
        target_date=target_date or datetime.now(UTC).date(),
        density="balanced",
        buffer_minutes=0,
        availability_start="08:00:00",
        availability_end="22:00:00",
        task_ids=[],
        instruction="先处理高优先级任务",
    )


async def enqueue(
    database: Database,
    owner_id: UUID,
    data: PlanningGenerationInput,
) -> dict:
    async with database.transaction() as session:
        return await enqueue_planning_generation(
            session,
            owner_id,
            data,
            timeout_seconds=120,
        )


async def cancel_planning_job(
    database: Database,
    owner_id: UUID,
    job_id: UUID,
) -> None:
    async with database.transaction() as session:
        job = await locked_job(session, owner_id, job_id)
        assert await cancel_job(session, job)
        await release_reservation(
            session,
            job=job,
            reason="cancelled",
        )


def database_settings(database_url: str) -> Settings:
    return Settings(
        database_url=database_url,
        environment="test",
        ai_job_lease_seconds=30,
    )


def test_empty_candidates_create_no_snapshot_job_or_reservation(
    admin_url,
    planning_upstream,
):
    state, endpoint = planning_upstream
    with isolated_database(admin_url) as database_url:
        owner_id, _, _ = seed_planning_context(
            database_url,
            endpoint,
            with_task=False,
        )
        database = Database(database_settings(database_url))
        try:
            with pytest.raises(AppError) as caught:
                asyncio.run(enqueue(database, owner_id, planning_input()))
            assert caught.value.code == "planning_candidates_empty"
        finally:
            asyncio.run(database.close())

        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT count(*) FROM planning_input_snapshots"
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT count(*) FROM ai_jobs"
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT count(*) FROM ai_quota_reservations"
            ).fetchone()[0] == 0
        assert state.requests == []


def test_claim_includes_snapshot_and_capacity_counts_other_capabilities(
    admin_url,
    planning_upstream,
):
    _, endpoint = planning_upstream
    with isolated_database(admin_url) as database_url:
        owner_id, _, planning_config_id = seed_planning_context(
            database_url,
            endpoint,
        )
        database = Database(database_settings(database_url))
        first = asyncio.run(enqueue(database, owner_id, planning_input()))
        claim = asyncio.run(claim_next_job(database, capability="planning"))
        assert claim is not None
        assert claim.id == UUID(str(first["job"]["id"]))
        assert claim.planning_snapshot_id is not None
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                """
                SELECT payload->>'status'
                FROM ai_job_events
                WHERE job_id=%s
                ORDER BY sequence
                """,
                (claim.id,),
            ).fetchall() == [("queued",), ("running",)]
        asyncio.run(database.close())

    with isolated_database(admin_url) as database_url:
        owner_id, _, planning_config_id = seed_planning_context(
            database_url,
            endpoint,
        )
        screenshot_config_id = _insert_published_config(
            database_url,
            capability="calendar_screenshot",
            endpoint=endpoint,
            key="sk-screenshot",
        )
        with psycopg.connect(
            psycopg_url(database_url),
        ) as connection:
            daily_id = uuid4()
            connection.execute(
                """
                INSERT INTO ai_daily_allowances (
                    id, owner_id, activity_date, window_start_at, window_end_at,
                    time_zone, activity_day_start_minute, policy_version_id,
                    currency, base_amount_nanos
                )
                SELECT
                    %s, %s, current_date, now() - interval '1 hour',
                    now() + interval '23 hours', 'Asia/Shanghai', 240,
                    current_version_id, 'CNY', 1000000000
                FROM ai_quota_policy_state
                WHERE id=1
                """,
                (daily_id, owner_id),
            )
            for index in range(3):
                media_id = uuid4()
                active_job_id = uuid4()
                connection.execute(
                    """
                    INSERT INTO temporary_media (
                        id, owner_id, purpose, original_filename,
                        content_type, content_sha256, byte_size,
                        width, height, encrypted_content, expires_at
                    ) VALUES (
                        %s, %s, 'calendar_screenshot', %s, 'image/png',
                        %s, 4, 1, 1, %s, now() + interval '1 day'
                    )
                    """,
                    (
                        media_id,
                        owner_id,
                        f"active-{index}.png",
                        str(index) * 64,
                        b"encrypted",
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO ai_jobs (
                        id, owner_id, capability, config_version_id,
                        input_media_id, quota_mode, status, input_summary,
                        available_at, deadline_at
                    ) VALUES (
                        %s, %s, 'calendar_screenshot', %s, %s, 'user',
                        'queued', '{}'::jsonb, now(), now() + interval '2 minutes'
                    )
                    """,
                    (
                        active_job_id,
                        owner_id,
                        screenshot_config_id,
                        media_id,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO ai_quota_reservations (
                        id, job_id, owner_id, daily_allowance_id,
                        reserved_nanos, estimator_version, accepted_at
                    ) VALUES (
                        %s, %s, %s, %s, 0,
                        'reservation-utf8-vision-v1', now()
                    )
                    """,
                    (
                        uuid4(),
                        active_job_id,
                        owner_id,
                        daily_id,
                    ),
                )
        database = Database(database_settings(database_url))
        try:
            with pytest.raises(AppError) as caught:
                asyncio.run(enqueue(database, owner_id, planning_input()))
            assert caught.value.code == "ai_job_limit_reached"
        finally:
            asyncio.run(database.close())
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                """
                SELECT count(*) FROM ai_jobs
                WHERE owner_id=%s AND config_version_id=%s
                """,
                (owner_id, planning_config_id),
            ).fetchone()[0] == 0


def test_success_rebuilds_versioned_request_and_atomically_writes_terminal_facts(
    admin_url,
    planning_upstream,
):
    state, endpoint = planning_upstream
    with isolated_database(admin_url) as database_url:
        owner_id, _, config_id = seed_planning_context(database_url, endpoint)
        settings = database_settings(database_url)
        database = Database(settings)
        submitted = asyncio.run(enqueue(database, owner_id, planning_input()))
        job_id = UUID(str(submitted["job"]["id"]))

        with psycopg.connect(psycopg_url(database_url)) as connection:
            accepted = connection.execute(
                """
                SELECT input_summary, planning_snapshot_id, config_version_id
                FROM ai_jobs
                WHERE id=%s
                """,
                (job_id,),
            ).fetchone()
        assert accepted[0] == submitted["job"]["input_summary"]
        assert accepted[0]["request_schema_version"] == 1
        assert "_frozen_request_body" not in accepted[0]
        serialized_summary = json.dumps(accepted[0], ensure_ascii=False)
        assert "编写规划测试" not in serialized_summary
        assert "先处理高优先级任务" not in serialized_summary
        assert "submit_itime_plan" not in serialized_summary
        assert "tools" not in serialized_summary
        assert accepted[1] is not None
        assert accepted[2] == config_id

        assert asyncio.run(process_one(database, settings))
        asyncio.run(database.close())
        assert state.authorizations == ["Bearer sk-planning-frozen"]
        assert len(state.requests) == 1
        provider_request = state.requests[0]
        assert (
            provider_request["tool_choice"]["function"]["name"]
            == "submit_itime_plan"
        )

        with psycopg.connect(psycopg_url(database_url)) as connection:
            job = connection.execute(
                """
                SELECT status, output, error_code, last_event_sequence
                FROM ai_jobs WHERE id=%s
                """,
                (job_id,),
            ).fetchone()
            proposal = connection.execute(
                """
                SELECT id, status, payload, input_snapshot_id
                FROM ai_proposals WHERE source_job_id=%s
                """,
                (job_id,),
            ).fetchone()
            changes = connection.execute(
                """
                SELECT action, entity_type
                FROM ai_proposal_changes
                WHERE proposal_id=%s
                ORDER BY position
                """,
                (proposal[0],),
            ).fetchall()
            body_log = connection.execute(
                """
                SELECT status, request_body, response_body
                FROM ai_body_logs WHERE job_id=%s
                """,
                (job_id,),
            ).fetchone()
            cost = connection.execute(
                """
                SELECT attempt_status, usage_source, total_cost_nanos
                FROM ai_provider_costs WHERE job_id=%s
                """,
                (job_id,),
            ).fetchone()
            reservation = connection.execute(
                """
                SELECT status, actual_cost_nanos, charged_nanos
                FROM ai_quota_reservations WHERE job_id=%s
                """,
                (job_id,),
            ).fetchone()
            ledger_count = connection.execute(
                """
                SELECT count(*) FROM ai_quota_ledger_entries
                WHERE job_id=%s AND kind='usage_charge'
                """,
                (job_id,),
            ).fetchone()[0]
            sample = connection.execute(
                """
                SELECT capability, status
                FROM ai_quality_samples WHERE source_job_id=%s
                """,
                (job_id,),
            ).fetchone()
            events = connection.execute(
                """
                SELECT payload->>'status'
                FROM ai_job_events
                WHERE job_id=%s AND kind='job_state'
                ORDER BY sequence
                """,
                (job_id,),
            ).fetchall()

        assert job[0] == "succeeded"
        assert job[1] == {
            "resource_type": "planning_proposal",
            "proposal_id": str(proposal[0]),
        }
        assert job[2] is None
        assert job[3] == 3
        assert proposal[1] == "pending"
        assert proposal[2]["blocks"][0]["candidate_key"].startswith("task:")
        assert proposal[3] == accepted[1]
        assert changes[0][1] == "plan"
        assert ("create", "scheduled_block") in changes
        assert body_log[0] == "succeeded"
        assert body_log[1] == provider_request
        assert json.loads(body_log[2])["tool_call"]["name"] == "submit_itime_plan"
        assert cost[0:2] == ("succeeded", "provider")
        assert cost[2] > 0
        assert reservation[0] == "settled"
        assert reservation[1] == cost[2]
        assert reservation[2] <= reservation[1]
        assert ledger_count == 1
        assert sample == ("planning", "pending")
        assert events == [("queued",), ("running",), ("succeeded",)]


def test_invalid_plan_fails_without_proposal_and_releases_reservation(
    admin_url,
    planning_upstream,
):
    state, endpoint = planning_upstream
    state.mode = "invalid_plan"
    with isolated_database(admin_url) as database_url:
        owner_id, _, _ = seed_planning_context(database_url, endpoint)
        settings = database_settings(database_url)
        database = Database(settings)
        submitted = asyncio.run(enqueue(database, owner_id, planning_input()))
        job_id = submitted["job"]["id"]
        assert asyncio.run(process_one(database, settings))
        asyncio.run(database.close())

        with psycopg.connect(psycopg_url(database_url)) as connection:
            job = connection.execute(
                "SELECT status, error_code FROM ai_jobs WHERE id=%s",
                (job_id,),
            ).fetchone()
            proposal_count = connection.execute(
                "SELECT count(*) FROM ai_proposals WHERE source_job_id=%s",
                (job_id,),
            ).fetchone()[0]
            reservation = connection.execute(
                """
                SELECT status, release_reason
                FROM ai_quota_reservations WHERE job_id=%s
                """,
                (job_id,),
            ).fetchone()
            cost = connection.execute(
                """
                SELECT attempt_status, usage_source
                FROM ai_provider_costs WHERE job_id=%s
                """,
                (job_id,),
            ).fetchone()
            body_log = connection.execute(
                "SELECT status, response_body FROM ai_body_logs WHERE job_id=%s",
                (job_id,),
            ).fetchone()
        assert job == ("failed", "unknown_plan_candidate")
        assert proposal_count == 0
        assert reservation == ("released", "failed")
        assert cost == ("failed", "provider")
        assert json.loads(body_log[1])["tool_call"]["name"] == "submit_itime_plan"
        assert body_log[0] == "failed"


def test_retry_keeps_reservation_then_succeeds(
    admin_url,
    planning_upstream,
):
    state, endpoint = planning_upstream
    state.mode = "rate_limit"
    with isolated_database(admin_url) as database_url:
        owner_id, _, _ = seed_planning_context(database_url, endpoint)
        settings = database_settings(database_url)
        database = Database(settings)
        submitted = asyncio.run(enqueue(database, owner_id, planning_input()))
        job_id = submitted["job"]["id"]
        assert asyncio.run(process_one(database, settings))
        with psycopg.connect(
            psycopg_url(database_url),
            autocommit=True,
        ) as connection:
            assert connection.execute(
                "SELECT status FROM ai_jobs WHERE id=%s",
                (job_id,),
            ).fetchone()[0] == "queued"
            assert connection.execute(
                "SELECT status FROM ai_quota_reservations WHERE job_id=%s",
                (job_id,),
            ).fetchone()[0] == "reserved"
            connection.execute(
                "UPDATE ai_jobs SET available_at=now() WHERE id=%s",
                (job_id,),
            )
        state.mode = "success"
        assert asyncio.run(process_one(database, settings))
        asyncio.run(database.close())
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT status, attempt_count FROM ai_jobs WHERE id=%s",
                (job_id,),
            ).fetchone() == ("succeeded", 2)
            assert connection.execute(
                """
                SELECT attempt_status FROM ai_provider_costs
                WHERE job_id=%s ORDER BY attempt_number
                """,
                (job_id,),
            ).fetchall() == [("retryable_failed",), ("succeeded",)]


def test_cancel_and_reconciliation_release_and_emit_terminal_event(
    admin_url,
    planning_upstream,
):
    _, endpoint = planning_upstream
    for terminal in ("cancelled", "timed_out"):
        with isolated_database(admin_url) as database_url:
            owner_id, _, _ = seed_planning_context(database_url, endpoint)
            settings = database_settings(database_url)
            database = Database(settings)
            submitted = asyncio.run(enqueue(database, owner_id, planning_input()))
            job_id = UUID(str(submitted["job"]["id"]))

            if terminal == "cancelled":
                asyncio.run(cancel_planning_job(database, owner_id, job_id))
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
                assert asyncio.run(reconcile_one(database, settings))
            asyncio.run(database.close())

            with psycopg.connect(psycopg_url(database_url)) as connection:
                assert connection.execute(
                    "SELECT status FROM ai_jobs WHERE id=%s",
                    (job_id,),
                ).fetchone()[0] == terminal
                assert connection.execute(
                    """
                    SELECT status, release_reason
                    FROM ai_quota_reservations WHERE job_id=%s
                    """,
                    (job_id,),
                ).fetchone() == ("released", terminal)
                assert connection.execute(
                    """
                    SELECT payload->>'status'
                    FROM ai_job_events
                    WHERE job_id=%s
                    ORDER BY sequence DESC
                    LIMIT 1
                    """,
                    (job_id,),
                ).fetchone()[0] == terminal
                assert connection.execute(
                    "SELECT count(*) FROM ai_proposals WHERE source_job_id=%s",
                    (job_id,),
                ).fetchone()[0] == 0
