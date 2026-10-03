import asyncio
import json
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import httpx
import psycopg
import pytest
from cryptography.fernet import Fernet
from psycopg.rows import dict_row

from itime.ai.conversation_service import cleanup_expired_job_events
from itime.ai.worker import process_one
from itime.config import Settings
from itime.database import Database
from tests.conftest import SERVER, LiveServer, isolated_database, psycopg_url
from tests.quota_helpers import ensure_quota_policy
from tests.test_calendar_series import create_account

pytestmark = pytest.mark.integration

PARTIAL_REPLY = "已经保存的片段"
LATE_REPLY = "不应保存的晚到内容"
CHAT_USAGE = {
    "prompt_tokens": 100,
    "completion_tokens": 20,
    "total_tokens": 120,
    "prompt_tokens_details": {"cached_tokens": 10},
}


def sse_delta(text: str) -> bytes:
    payload = {
        "id": "chat-completion-test",
        "choices": [{"index": 0, "delta": {"content": text}}],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


def sse_usage(usage: dict | None = None) -> bytes:
    payload = {
        "id": "chat-completion-test",
        "choices": [],
        "usage": usage or CHAT_USAGE,
    }
    return f"data: {json.dumps(payload)}\n\n".encode()


@dataclass
class ChatUpstreamState:
    mode: str = "split_sse"
    requests: list[dict] = field(default_factory=list)
    request_received: threading.Event = field(default_factory=threading.Event)
    first_delta_sent: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def reset(self, mode: str) -> None:
        with self.lock:
            self.mode = mode
            self.requests.clear()
        self.request_received.clear()
        self.first_delta_sent.clear()
        self.release.clear()

    def request_snapshot(self) -> list[dict]:
        with self.lock:
            return list(self.requests)


def chat_handler(state: ChatUpstreamState):
    class Handler(BaseHTTPRequestHandler):
        def _start_response(self, content_type: str, request_id: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.send_header("x-request-id", request_id)
            self.end_headers()

        def _write_chunks(self, chunks: list[bytes], *, pause: float = 0.02) -> None:
            try:
                for chunk in chunks:
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    time.sleep(pause)
            except OSError:
                pass
            self.close_connection = True

        def _json_response(
            self,
            status: int,
            payload: dict,
            request_id: str,
        ) -> None:
            encoded = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("x-request-id", request_id)
            self.end_headers()
            try:
                self.wfile.write(encoded)
            except OSError:
                pass

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length))
            with state.lock:
                state.requests.append(
                    {
                        "path": self.path,
                        "authorization": self.headers.get("Authorization"),
                        "body": body,
                    }
                )
                attempt = len(state.requests)
                mode = state.mode
            request_id = f"chat-request-{attempt}"
            state.request_received.set()

            if mode == "authentication":
                self._json_response(
                    401,
                    {"error": {"message": "private authentication detail"}},
                    request_id,
                )
                return
            if mode == "rate_then_success" and attempt == 1:
                self._json_response(
                    429,
                    {"error": {"message": "private rate limit detail"}},
                    request_id,
                )
                return
            if mode == "invalid_with_usage":
                self._json_response(
                    200,
                    {"choices": [], "usage": CHAT_USAGE},
                    request_id,
                )
                return
            if mode in {"json", "json_invalid_usage"}:
                self._json_response(
                    200,
                    {
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": "一次性 JSON 回复",
                                }
                            }
                        ],
                        "usage": (
                            CHAT_USAGE if mode == "json" else "invalid-usage"
                        ),
                    },
                    request_id,
                )
                return

            self._start_response("text/event-stream; charset=utf-8", request_id)
            if mode == "invalid":
                self._write_chunks([b"data: {not-json}\n\n", b"data: [DONE]\n\n"])
                return
            if mode == "empty":
                self._write_chunks([sse_usage(), b"data: [DONE]\n\n"])
                return
            if mode == "idle":
                state.release.wait(timeout=10)
                self._write_chunks(
                    [
                        sse_delta("空闲续租后完成"),
                        sse_usage(),
                        b"data: [DONE]\n\n",
                    ]
                )
                return
            if mode == "cancel":
                self._write_chunks([sse_delta(PARTIAL_REPLY)], pause=0)
                state.first_delta_sent.set()
                state.release.wait(timeout=10)
                self._write_chunks(
                    [sse_delta(LATE_REPLY), sse_usage(), b"data: [DONE]\n\n"],
                    pause=0,
                )
                return

            first_frame = sse_delta("前半段")
            split_at = first_frame.index("半".encode()) + 1
            usage = (
                {
                    "prompt_tokens": 100_000,
                    "completion_tokens": 100_000,
                    "total_tokens": 200_000,
                }
                if mode == "overage"
                else CHAT_USAGE
            )
            self._write_chunks(
                [
                    first_frame[:17],
                    first_frame[17:split_at],
                    first_frame[split_at:],
                    sse_delta("，后半段"),
                    sse_usage(usage),
                    b"data: [DONE]\n\n",
                ]
            )

        def log_message(self, format: str, *args) -> None:
            pass

    return Handler


@pytest.fixture(scope="session")
def conversation_worker_database_url(admin_url: str) -> Iterator[str]:
    with isolated_database(admin_url) as database_url:
        yield database_url


@pytest.fixture(scope="session")
def conversation_worker_live_server(
    conversation_worker_database_url: str,
    tmp_path_factory,
) -> Iterator[LiveServer]:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    log_path = tmp_path_factory.mktemp("conversation-worker-api") / "server.log"
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
                "ITIME_DATABASE_URL": conversation_worker_database_url,
            },
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 15
            with httpx.Client(base_url=origin, timeout=1, trust_env=False) as probe:
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        pytest.fail(f"对话测试 API 启动失败，请检查 {log_path}")
                    try:
                        if probe.get("/api/ready").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.05)
                else:
                    pytest.fail(f"对话测试 API 启动超时，请检查 {log_path}")
            yield LiveServer(origin, log_path)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@pytest.fixture
def conversation_worker_client(
    conversation_worker_live_server: LiveServer,
    conversation_worker_database_url: str,
) -> Iterator[httpx.Client]:
    with psycopg.connect(
        psycopg_url(conversation_worker_database_url),
        autocommit=True,
    ) as connection:
        connection.execute("DELETE FROM auth_rate_buckets")
    with httpx.Client(
        base_url=conversation_worker_live_server.origin,
        timeout=10,
        trust_env=False,
    ) as client:
        yield client


@pytest.fixture(scope="session")
def chat_upstream() -> Iterator[tuple[ChatUpstreamState, str]]:
    state = ChatUpstreamState()
    server = ThreadingHTTPServer(("127.0.0.1", 0), chat_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state, f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        state.release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def publish_conversation_config(
    database_url: str,
    endpoint: str,
) -> str:
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
            WHERE capability='conversation'
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
                output_price_nanos_per_million_tokens,
                status, last_test_id, last_test_started_at,
                last_test_completed_at, published_at
            ) VALUES (
                %s, 'conversation', %s, %s, 'chat-model', %s, 10, 4096,
                'CNY', 1000000000, 500000000, 2000000000,
                'published', %s, now(), now(), now()
            )
            """,
            (
                config_id,
                version,
                endpoint,
                cipher.encrypt(b"sk-conversation-secret"),
                uuid4(),
            ),
        )
        connection.execute(
            """
            UPDATE ai_config_capabilities
            SET current_version_id=%s, next_version=%s, updated_at=now()
            WHERE capability='conversation'
            """,
            (config_id, version + 1),
        )
    ensure_quota_policy(database_url)
    return str(config_id)


def enqueue_conversation_job(
    client: httpx.Client,
    database_url: str,
    endpoint: str,
    *,
    prompt: str,
) -> dict:
    publish_conversation_config(database_url, endpoint)
    create_account(client, database_url)
    created = client.post(
        "/api/v1/ai/conversations",
        json={"operation_id": str(uuid4())},
    )
    assert created.status_code == 201, created.text
    conversation = created.json()["conversation"]
    submitted = client.post(
        f"/api/v1/ai/conversations/{conversation['id']}/messages",
        json={
            "operation_id": str(uuid4()),
            "expected_conversation_version": conversation["version"],
            "content": prompt,
        },
    )
    assert submitted.status_code == 202, submitted.text
    return submitted.json()


async def process_once(
    database_url: str,
    *,
    lease_seconds: int = 30,
) -> bool:
    settings = Settings(
        database_url=database_url,
        environment="test",
    ).model_copy(update={"ai_job_lease_seconds": lease_seconds})
    database = Database(settings)
    try:
        return await process_one(database, settings)
    finally:
        await database.close()


def run_process_once(
    database_url: str,
    *,
    lease_seconds: int = 30,
) -> bool:
    return asyncio.run(
        process_once(database_url, lease_seconds=lease_seconds)
    )


def make_job_due(database_url: str, job_id: str) -> None:
    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            """
            UPDATE ai_jobs
            SET available_at=now() - interval '1 second'
            WHERE id=%s AND status='queued'
            """,
            (job_id,),
        )


def job_snapshot(database_url: str, job_id: str) -> dict:
    with psycopg.connect(
        psycopg_url(database_url),
        row_factory=dict_row,
    ) as connection:
        job = connection.execute(
            """
            SELECT id, status, output, error_code, error_message,
                   attempt_count, last_event_sequence, lease_id,
                   lease_until, finished_at
            FROM ai_jobs
            WHERE id=%s
            """,
            (job_id,),
        ).fetchone()
        generation = connection.execute(
            """
            SELECT stream_generation, response_message_id
            FROM ai_chat_generations
            WHERE job_id=%s
            """,
            (job_id,),
        ).fetchone()
        message = connection.execute(
            """
            SELECT message.id, message.status, message.content,
                   message.error_code, message.error_message,
                   message.completed_at
            FROM ai_conversation_messages AS message
            JOIN ai_chat_generations AS generation
              ON generation.response_message_id=message.id
            WHERE generation.job_id=%s
            """,
            (job_id,),
        ).fetchone()
        attempts = connection.execute(
            """
            SELECT attempt_number, status, provider_request_id, http_status,
                   error_code, error_stage, finished_at
            FROM ai_job_attempts
            WHERE job_id=%s
            ORDER BY attempt_number
            """,
            (job_id,),
        ).fetchall()
        events = connection.execute(
            """
            SELECT sequence, generation, kind, payload
            FROM ai_job_events
            WHERE job_id=%s
            ORDER BY sequence
            """,
            (job_id,),
        ).fetchall()
        costs = connection.execute(
            """
            SELECT attempt_number, attempt_status, usage_source,
                   input_tokens, cached_input_tokens, output_tokens,
                   total_tokens, price_currency, total_cost_nanos
            FROM ai_provider_costs
            WHERE job_id=%s
            ORDER BY attempt_number
            """,
            (job_id,),
        ).fetchall()
        reservation = connection.execute(
            """
            SELECT id, status, reserved_nanos, actual_cost_nanos,
                   charged_nanos, platform_overage_nanos,
                   usage_source, release_reason
            FROM ai_quota_reservations
            WHERE job_id=%s
            """,
            (job_id,),
        ).fetchone()
        ledger = connection.execute(
            """
            SELECT kind, delta_nanos, currency, usage_source
            FROM ai_quota_ledger_entries
            WHERE job_id=%s
            ORDER BY created_at, id
            """,
            (job_id,),
        ).fetchall()
    assert job is not None
    assert generation is not None
    assert message is not None
    return {
        "job": job,
        "generation": generation,
        "message": message,
        "attempts": attempts,
        "events": events,
        "costs": costs,
        "reservation": reservation,
        "ledger": ledger,
    }


def wait_for_message_content(
    database_url: str,
    job_id: str,
    expected: str,
) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if job_snapshot(database_url, job_id)["message"]["content"] == expected:
            return
        time.sleep(0.03)
    pytest.fail("worker 未在取消前持久化首个对话分段")


def assert_event_consistency(snapshot: dict) -> None:
    job = snapshot["job"]
    events = snapshot["events"]
    assert [event["sequence"] for event in events] == list(
        range(1, len(events) + 1)
    )
    assert job["last_event_sequence"] == len(events)
    assert events[0]["kind"] == "job_state"
    assert events[0]["generation"] == 0
    assert events[0]["payload"]["status"] == "queued"


def assert_success(
    snapshot: dict,
    *,
    expected_content: str,
    expected_generation: int,
    expected_attempts: int,
    expected_usage_source: str = "provider",
) -> None:
    job = snapshot["job"]
    generation = snapshot["generation"]
    message = snapshot["message"]
    attempts = snapshot["attempts"]
    events = snapshot["events"]
    costs = snapshot["costs"]
    reservation = snapshot["reservation"]
    ledger = snapshot["ledger"]

    assert job["status"] == "succeeded"
    assert job["output"] is not None
    assert job["error_code"] is None
    assert job["attempt_count"] == expected_attempts
    assert job["lease_id"] is None and job["lease_until"] is None
    assert job["finished_at"] is not None
    assert generation["stream_generation"] == expected_generation
    assert message["status"] == "complete"
    assert message["content"] == expected_content
    assert message["error_code"] is None
    assert message["completed_at"] is not None
    assert len(attempts) == expected_attempts
    assert attempts[-1]["status"] == "succeeded"
    assert attempts[-1]["finished_at"] is not None
    assert len(costs) == expected_attempts
    assert costs[-1]["attempt_status"] == "succeeded"
    assert costs[-1]["usage_source"] == expected_usage_source
    if expected_usage_source == "provider":
        assert costs[-1]["input_tokens"] == 100
        assert costs[-1]["cached_input_tokens"] == 10
        assert costs[-1]["output_tokens"] == 20
        assert costs[-1]["total_tokens"] == 120
        assert costs[-1]["total_cost_nanos"] == 135_000
    else:
        assert costs[-1]["input_tokens"] > 0
        assert costs[-1]["output_tokens"] > 0
        assert costs[-1]["total_cost_nanos"] > 0
    assert costs[-1]["price_currency"] == "CNY"
    assert reservation["status"] == "settled"
    assert reservation["actual_cost_nanos"] == costs[-1]["total_cost_nanos"]
    assert reservation["charged_nanos"] == min(
        reservation["actual_cost_nanos"],
        reservation["reserved_nanos"],
    )
    assert reservation["platform_overage_nanos"] == (
        reservation["actual_cost_nanos"] - reservation["charged_nanos"]
    )
    assert ledger == [
        {
            "kind": "usage_charge",
            "delta_nanos": -reservation["charged_nanos"],
            "currency": "CNY",
            "usage_source": expected_usage_source,
        }
    ]

    assert_event_consistency(snapshot)
    deltas = [event for event in events if event["kind"] == "content_delta"]
    assert "".join(event["payload"]["text"] for event in deltas) == expected_content
    assert all(event["generation"] == expected_generation for event in deltas)
    assert events[-1]["kind"] == "job_state"
    assert events[-1]["generation"] == expected_generation
    assert events[-1]["payload"]["status"] == "succeeded"
    assert events[-1]["payload"]["message_id"] == str(message["id"])


def parse_sse(body: str) -> list[dict]:
    events = []
    for block in body.strip().split("\n\n"):
        if not block or block.startswith(":"):
            continue
        fields = {
            line.split(":", 1)[0]: line.split(":", 1)[1].strip()
            for line in block.splitlines()
            if ":" in line
        }
        events.append(json.loads(fields["data"]))
    return events


@pytest.mark.parametrize(
    ("mode", "expected_content", "expected_usage_source"),
    [
        ("split_sse", "前半段，后半段", "provider"),
        ("json", "一次性 JSON 回复", "provider"),
        ("json_invalid_usage", "一次性 JSON 回复", "estimated"),
    ],
)
def test_conversation_worker_accepts_split_sse_and_json_fallback(
    conversation_worker_client: httpx.Client,
    conversation_worker_database_url: str,
    chat_upstream: tuple[ChatUpstreamState, str],
    mode: str,
    expected_content: str,
    expected_usage_source: str,
) -> None:
    state, endpoint = chat_upstream
    state.reset(mode)
    submitted = enqueue_conversation_job(
        conversation_worker_client,
        conversation_worker_database_url,
        endpoint,
        prompt=f"请验证 {mode} 响应",
    )
    job_id = submitted["job"]["id"]

    assert run_process_once(conversation_worker_database_url)
    snapshot = job_snapshot(conversation_worker_database_url, job_id)
    assert_success(
        snapshot,
        expected_content=expected_content,
        expected_generation=1,
        expected_attempts=1,
        expected_usage_source=expected_usage_source,
    )
    assert snapshot["attempts"][0]["provider_request_id"] == "chat-request-1"
    assert snapshot["attempts"][0]["http_status"] == 200

    requests = state.request_snapshot()
    assert len(requests) == 1
    assert requests[0]["path"] == "/v1/chat/completions"
    assert requests[0]["authorization"] == "Bearer sk-conversation-secret"
    assert requests[0]["body"]["model"] == "chat-model"
    assert requests[0]["body"]["stream"] is True
    assert requests[0]["body"]["stream_options"] == {"include_usage": True}


def test_rate_limit_retry_resets_content_with_new_generation(
    conversation_worker_client: httpx.Client,
    conversation_worker_database_url: str,
    chat_upstream: tuple[ChatUpstreamState, str],
) -> None:
    state, endpoint = chat_upstream
    state.reset("rate_then_success")
    submitted = enqueue_conversation_job(
        conversation_worker_client,
        conversation_worker_database_url,
        endpoint,
        prompt="限流后重试",
    )
    job_id = submitted["job"]["id"]

    assert run_process_once(conversation_worker_database_url)
    after_rate_limit = job_snapshot(conversation_worker_database_url, job_id)
    assert after_rate_limit["job"]["status"] == "queued"
    assert after_rate_limit["job"]["attempt_count"] == 1
    assert after_rate_limit["generation"]["stream_generation"] == 1
    assert after_rate_limit["message"]["content"] == ""
    assert [attempt["status"] for attempt in after_rate_limit["attempts"]] == [
        "retryable_failed"
    ]
    assert after_rate_limit["attempts"][0]["http_status"] == 429
    assert after_rate_limit["reservation"]["status"] == "reserved"
    assert after_rate_limit["ledger"] == []

    make_job_due(conversation_worker_database_url, job_id)
    assert run_process_once(conversation_worker_database_url)
    snapshot = job_snapshot(conversation_worker_database_url, job_id)
    assert_success(
        snapshot,
        expected_content="前半段，后半段",
        expected_generation=2,
        expected_attempts=2,
    )
    assert [attempt["status"] for attempt in snapshot["attempts"]] == [
        "retryable_failed",
        "succeeded",
    ]
    assert [cost["usage_source"] for cost in snapshot["costs"]] == [
        "estimated",
        "provider",
    ]
    assert [cost["attempt_status"] for cost in snapshot["costs"]] == [
        "retryable_failed",
        "succeeded",
    ]
    resets = [
        event for event in snapshot["events"] if event["kind"] == "content_reset"
    ]
    assert len(resets) == 1
    assert resets[0]["generation"] == 2
    generation_two_deltas = [
        event
        for event in snapshot["events"]
        if event["kind"] == "content_delta" and event["generation"] == 2
    ]
    assert resets[0]["sequence"] < generation_two_deltas[0]["sequence"]
    assert len(state.request_snapshot()) == 2


def test_success_over_reservation_is_absorbed_by_platform(
    conversation_worker_client: httpx.Client,
    conversation_worker_database_url: str,
    chat_upstream: tuple[ChatUpstreamState, str],
) -> None:
    state, endpoint = chat_upstream
    state.reset("overage")
    submitted = enqueue_conversation_job(
        conversation_worker_client,
        conversation_worker_database_url,
        endpoint,
        prompt="验证超预留",
    )
    job_id = submitted["job"]["id"]

    assert run_process_once(conversation_worker_database_url)
    snapshot = job_snapshot(conversation_worker_database_url, job_id)
    reservation = snapshot["reservation"]
    assert reservation["status"] == "settled"
    assert reservation["actual_cost_nanos"] > reservation["reserved_nanos"]
    assert reservation["charged_nanos"] == reservation["reserved_nanos"]
    assert reservation["platform_overage_nanos"] > 0
    assert snapshot["ledger"][0]["delta_nanos"] == -reservation["reserved_nanos"]


def test_queued_deadline_uses_conversation_terminal_projection(
    conversation_worker_client: httpx.Client,
    conversation_worker_database_url: str,
    chat_upstream: tuple[ChatUpstreamState, str],
) -> None:
    state, endpoint = chat_upstream
    state.reset("split_sse")
    submitted = enqueue_conversation_job(
        conversation_worker_client,
        conversation_worker_database_url,
        endpoint,
        prompt="排队阶段已超时",
    )
    job_id = submitted["job"]["id"]
    with psycopg.connect(
        psycopg_url(conversation_worker_database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            "UPDATE ai_jobs SET deadline_at=now() - interval '1 second' "
            "WHERE id=%s",
            (job_id,),
        )

    assert run_process_once(conversation_worker_database_url)
    snapshot = job_snapshot(conversation_worker_database_url, job_id)
    assert_event_consistency(snapshot)
    assert snapshot["job"]["status"] == "timed_out"
    assert snapshot["job"]["error_code"] == "ai_job_deadline_exceeded"
    assert snapshot["message"]["status"] == "timed_out"
    assert snapshot["message"]["error_code"] == "ai_job_deadline_exceeded"
    assert snapshot["events"][-1]["payload"]["status"] == "timed_out"
    assert state.request_snapshot() == []
    assert snapshot["costs"] == []
    assert snapshot["reservation"]["status"] == "released"
    assert snapshot["reservation"]["release_reason"] == "timed_out"
    assert snapshot["ledger"] == []
    with psycopg.connect(
        psycopg_url(conversation_worker_database_url)
    ) as connection:
        assert connection.execute(
            "SELECT count(*) FROM ai_body_logs WHERE job_id=%s",
            (job_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM ai_quality_samples WHERE source_job_id=%s",
            (job_id,),
        ).fetchone()[0] == 0


def test_idle_stream_renews_lease_and_blocks_second_worker(
    conversation_worker_client: httpx.Client,
    conversation_worker_database_url: str,
    chat_upstream: tuple[ChatUpstreamState, str],
) -> None:
    state, endpoint = chat_upstream
    state.reset("idle")
    submitted = enqueue_conversation_job(
        conversation_worker_client,
        conversation_worker_database_url,
        endpoint,
        prompt="请在空闲后继续回复",
    )
    job_id = submitted["job"]["id"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_worker = pool.submit(
            run_process_once,
            conversation_worker_database_url,
            lease_seconds=2,
        )
        try:
            assert state.request_received.wait(timeout=5)
            initial = job_snapshot(conversation_worker_database_url, job_id)
            initial_lease = initial["job"]["lease_until"]
            assert initial["job"]["status"] == "running"
            time.sleep(2.6)
            assert not run_process_once(
                conversation_worker_database_url,
                lease_seconds=2,
            )
            renewed = job_snapshot(conversation_worker_database_url, job_id)
            assert renewed["job"]["status"] == "running"
            assert renewed["job"]["attempt_count"] == 1
            assert renewed["job"]["lease_until"] > initial_lease
        finally:
            state.release.set()
        assert first_worker.result(timeout=10)

    snapshot = job_snapshot(conversation_worker_database_url, job_id)
    assert_success(
        snapshot,
        expected_content="空闲续租后完成",
        expected_generation=1,
        expected_attempts=1,
    )
    assert len(state.request_snapshot()) == 1


def test_cancel_wins_against_late_delta_and_completion(
    conversation_worker_client: httpx.Client,
    conversation_worker_database_url: str,
    chat_upstream: tuple[ChatUpstreamState, str],
) -> None:
    state, endpoint = chat_upstream
    state.reset("cancel")
    submitted = enqueue_conversation_job(
        conversation_worker_client,
        conversation_worker_database_url,
        endpoint,
        prompt="生成后立即取消",
    )
    job_id = submitted["job"]["id"]

    with ThreadPoolExecutor(max_workers=1) as pool:
        worker = pool.submit(
            run_process_once,
            conversation_worker_database_url,
        )
        try:
            assert state.first_delta_sent.wait(timeout=5)
            wait_for_message_content(
                conversation_worker_database_url,
                job_id,
                PARTIAL_REPLY,
            )
            cancelled = conversation_worker_client.post(
                f"/api/v1/ai/jobs/{job_id}/cancel",
                json={"operation_id": str(uuid4())},
            )
            assert cancelled.status_code == 200, cancelled.text
            assert cancelled.json()["job"]["status"] == "cancelled"
        finally:
            state.release.set()
        assert worker.result(timeout=10)

    snapshot = job_snapshot(conversation_worker_database_url, job_id)
    assert_event_consistency(snapshot)
    assert snapshot["job"]["status"] == "cancelled"
    assert snapshot["job"]["output"] is None
    assert snapshot["job"]["lease_id"] is None
    assert snapshot["generation"]["stream_generation"] == 1
    assert snapshot["message"]["status"] == "cancelled"
    assert snapshot["message"]["content"] == PARTIAL_REPLY
    assert snapshot["message"]["error_code"] is None
    assert snapshot["message"]["completed_at"] is not None
    assert [attempt["status"] for attempt in snapshot["attempts"]] == [
        "cancelled"
    ]
    assert len(snapshot["costs"]) == 1
    assert snapshot["costs"][0]["attempt_status"] == "cancelled"
    assert snapshot["costs"][0]["usage_source"] == "estimated"
    assert snapshot["costs"][0]["output_tokens"] > 0
    assert snapshot["reservation"]["status"] == "released"
    assert snapshot["reservation"]["release_reason"] == "cancelled"
    assert snapshot["ledger"] == []
    assert snapshot["events"][-1]["kind"] == "job_state"
    assert snapshot["events"][-1]["generation"] == 1
    assert snapshot["events"][-1]["payload"]["status"] == "cancelled"
    persisted_deltas = "".join(
        event["payload"]["text"]
        for event in snapshot["events"]
        if event["kind"] == "content_delta"
    )
    assert persisted_deltas == PARTIAL_REPLY
    assert LATE_REPLY not in persisted_deltas
    with psycopg.connect(
        psycopg_url(conversation_worker_database_url)
    ) as connection:
        body_log = connection.execute(
            """
            SELECT status, response_body
            FROM ai_body_logs
            WHERE job_id=%s
            """,
            (job_id,),
        ).fetchone()
        sample_count = connection.execute(
            "SELECT count(*) FROM ai_quality_samples WHERE source_job_id=%s",
            (job_id,),
        ).fetchone()[0]
    assert body_log == ("cancelled", PARTIAL_REPLY)
    assert sample_count == 0


@pytest.mark.parametrize(
    ("mode", "error_token", "http_status"),
    [
        ("authentication", "authentication", 401),
        ("invalid", "invalid", 200),
        ("invalid_with_usage", "invalid", 200),
        ("empty", "empty", 200),
    ],
)
def test_non_retryable_responses_finish_message_attempt_job_and_event(
    conversation_worker_client: httpx.Client,
    conversation_worker_database_url: str,
    chat_upstream: tuple[ChatUpstreamState, str],
    mode: str,
    error_token: str,
    http_status: int,
) -> None:
    state, endpoint = chat_upstream
    state.reset(mode)
    submitted = enqueue_conversation_job(
        conversation_worker_client,
        conversation_worker_database_url,
        endpoint,
        prompt=f"触发 {mode} 终态",
    )
    job_id = submitted["job"]["id"]

    assert run_process_once(conversation_worker_database_url)
    snapshot = job_snapshot(conversation_worker_database_url, job_id)
    assert_event_consistency(snapshot)
    job = snapshot["job"]
    message = snapshot["message"]
    attempts = snapshot["attempts"]
    events = snapshot["events"]

    assert job["status"] == "failed"
    assert job["output"] is None
    assert job["attempt_count"] == 1
    assert job["lease_id"] is None and job["lease_until"] is None
    assert job["finished_at"] is not None
    assert error_token in job["error_code"]
    assert snapshot["generation"]["stream_generation"] == 1
    assert message["status"] == "failed"
    assert message["content"] == ""
    assert message["error_code"] == job["error_code"]
    assert message["error_message"] == job["error_message"]
    assert message["completed_at"] is not None
    assert len(attempts) == 1
    assert attempts[0]["status"] == "failed"
    assert attempts[0]["error_code"] == job["error_code"]
    assert attempts[0]["http_status"] == http_status
    assert attempts[0]["provider_request_id"] == "chat-request-1"
    assert snapshot["costs"][0]["usage_source"] == (
        "provider" if mode in {"invalid_with_usage", "empty"} else "estimated"
    )
    assert not [event for event in events if event["kind"] == "content_delta"]
    assert events[-1]["kind"] == "job_state"
    assert events[-1]["generation"] == 1
    assert events[-1]["payload"]["status"] == "failed"
    assert events[-1]["payload"]["error_code"] == job["error_code"]
    assert events[-1]["payload"]["message_id"] == str(message["id"])
    assert len(state.request_snapshot()) == 1
    assert snapshot["reservation"]["status"] == "released"
    assert snapshot["reservation"]["release_reason"] == "failed"
    assert snapshot["ledger"] == []


def test_committed_sse_events_resume_and_expired_events_fall_back_to_snapshot(
    conversation_worker_client: httpx.Client,
    conversation_worker_live_server: LiveServer,
    conversation_worker_database_url: str,
    chat_upstream: tuple[ChatUpstreamState, str],
) -> None:
    state, endpoint = chat_upstream
    state.reset("split_sse")
    submitted = enqueue_conversation_job(
        conversation_worker_client,
        conversation_worker_database_url,
        endpoint,
        prompt="验证断线续读",
    )
    job_id = submitted["job"]["id"]
    assert run_process_once(conversation_worker_database_url)

    response = conversation_worker_client.get(
        f"/api/v1/ai/jobs/{job_id}/events"
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(response.text)
    assert [event["sequence"] for event in events] == list(
        range(1, len(events) + 1)
    )
    assert events[-1]["payload"]["status"] == "succeeded"

    resumed = conversation_worker_client.get(
        f"/api/v1/ai/jobs/{job_id}/events",
        headers={"Last-Event-ID": str(events[-2]["sequence"])},
    )
    resumed_events = parse_sse(resumed.text)
    assert [event["sequence"] for event in resumed_events] == [
        events[-1]["sequence"]
    ]
    assert (
        conversation_worker_client.get(
            f"/api/v1/ai/jobs/{job_id}/events",
            headers={"Last-Event-ID": "invalid"},
        ).json()["error"]["code"]
        == "invalid_event_cursor"
    )

    with httpx.Client(
        base_url=conversation_worker_live_server.origin,
        timeout=10,
        trust_env=False,
    ) as other:
        create_account(other, conversation_worker_database_url)
        assert other.get(
            f"/api/v1/ai/jobs/{job_id}/events"
        ).status_code == 404

    with psycopg.connect(
        psycopg_url(conversation_worker_database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            """
            UPDATE ai_job_events
            SET created_at=now() - interval '8 days',
                expires_at=now() - interval '1 second'
            WHERE job_id=%s
            """,
            (job_id,),
        )

    async def cleanup() -> int:
        database = Database(
            Settings(
                database_url=conversation_worker_database_url,
                environment="test",
            )
        )
        try:
            return await cleanup_expired_job_events(database)
        finally:
            await database.close()

    assert asyncio.run(cleanup()) == len(events)
    assert conversation_worker_client.get(
        f"/api/v1/ai/jobs/{job_id}/events"
    ).text == ""
    message = conversation_worker_client.get(
        f"/api/v1/ai/conversations/"
        f"{submitted['conversation']['id']}/messages"
    ).json()["items"][-1]
    assert message["status"] == "complete"
    assert message["content"] == "前半段，后半段"
