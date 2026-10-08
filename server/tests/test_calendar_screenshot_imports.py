import asyncio
import hashlib
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
from io import BytesIO
from uuid import uuid4

import httpx
import psycopg
import pytest
from cryptography.fernet import Fernet
from PIL import Image, ImageDraw

from itime.ai.worker import process_one
from itime.config import Settings
from itime.database import Database
from tests.conftest import SERVER, LiveServer, isolated_database, psycopg_url
from tests.quota_helpers import ensure_quota_policy
from tests.test_calendar_imports import confirm_payload
from tests.test_calendar_series import create_account

pytestmark = pytest.mark.integration


def recognized_draft(name: str = "高等数学") -> dict:
    return {
        "kind": "fixed",
        "term_id": None,
        "name": name,
        "description": "",
        "location": "教学楼 A101",
        "busy": True,
        "time_zone": "Asia/Shanghai",
        "start_date": "2026-10-05",
        "starts_at": "09:00:00",
        "ends_at": "10:30:00",
        "end_day_offset": 0,
        "start_period_number": None,
        "end_period_number": None,
        "ambiguous_utc_offset_minutes": None,
        "recurrence": {
            "frequency": "none",
            "interval": 1,
            "weekdays": [],
            "month": None,
            "day_of_month": None,
            "until": None,
            "count": None,
            "week_numbers": [],
        },
    }


@dataclass
class VisionState:
    mode: str = "success"
    requests: list[dict] = field(default_factory=list)
    received: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def reset(self, mode: str = "success") -> None:
        with self.lock:
            self.mode = mode
            self.requests.clear()
        self.received.clear()
        self.release.clear()


def vision_handler(state: VisionState):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length))
            with state.lock:
                mode = state.mode
                state.requests.append(
                    {
                        "path": self.path,
                        "authorization": self.headers.get("Authorization"),
                        "body": body,
                    }
                )
            state.received.set()
            if mode == "blocked":
                state.release.wait(timeout=10)

            if mode == "unavailable":
                status = 503
                response = {"error": "private-upstream-body"}
            elif mode == "invalid":
                status = 200
                response = {
                    "choices": [{"message": {"content": "not json"}}],
                    "usage": {
                        "prompt_tokens": 400,
                        "completion_tokens": 20,
                        "total_tokens": 420,
                    },
                }
            else:
                items = [
                    {
                        "raw_text": "周一 09:00 高等数学",
                        "confidence": 0.97,
                        "warnings": [],
                        "draft": recognized_draft(),
                    },
                    {
                        "raw_text": "无法判断日期",
                        "confidence": 0.31,
                        "warnings": ["日期模糊，请人工校对"],
                        "draft": {"kind": "fixed", "name": "待校对课程"},
                    },
                ]
                status = 200
                response = {
                    "choices": [
                        {"message": {"content": json.dumps({"items": items})}}
                    ],
                    "usage": {
                        "prompt_tokens": 400,
                        "completion_tokens": 120,
                        "total_tokens": 520,
                    },
                }
            encoded = json.dumps(response).encode()
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("x-request-id", "vision-request-1")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
            except OSError:
                pass

        def log_message(self, format: str, *args) -> None:
            pass

    return Handler


@pytest.fixture(scope="session")
def screenshot_database_url(admin_url: str) -> Iterator[str]:
    with isolated_database(admin_url) as database_url:
        yield database_url


@pytest.fixture(scope="session")
def screenshot_live_server(
    screenshot_database_url: str,
    tmp_path_factory,
) -> Iterator[LiveServer]:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    log_path = tmp_path_factory.mktemp("screenshot-api") / "server.log"
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
                "ITIME_DATABASE_URL": screenshot_database_url,
                "ITIME_MAX_REQUEST_BYTES": str(6 * 1024 * 1024),
            },
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 15
            with httpx.Client(base_url=origin, timeout=1, trust_env=False) as probe:
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        pytest.fail(f"截图测试 API 启动失败，请检查 {log_path}")
                    try:
                        if probe.get("/api/ready").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.05)
                else:
                    pytest.fail(f"截图测试 API 启动超时，请检查 {log_path}")
            yield LiveServer(origin, log_path)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@pytest.fixture
def screenshot_client(screenshot_live_server: LiveServer):
    with httpx.Client(
        base_url=screenshot_live_server.origin,
        timeout=15,
        trust_env=False,
    ) as client:
        yield client


@pytest.fixture(scope="session")
def vision_upstream():
    state = VisionState()
    server = ThreadingHTTPServer(("127.0.0.1", 0), vision_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state, f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        state.release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture(autouse=True)
def reset_vision(vision_upstream, screenshot_database_url):
    with psycopg.connect(
        psycopg_url(screenshot_database_url),
        autocommit=True,
    ) as connection:
        connection.execute("DELETE FROM calendar_import_identities")
        connection.execute("DELETE FROM calendar_import_candidates")
        connection.execute("DELETE FROM calendar_import_batches")
        connection.execute(
            "DELETE FROM calendar_series WHERE source_type='screenshot'"
        )
        connection.execute(
            "DELETE FROM calendar_sources WHERE kind IN ('screenshot', 'image')"
        )
        connection.execute("DELETE FROM ai_quota_ledger_entries")
        connection.execute("DELETE FROM ai_quota_reservations")
        connection.execute("DELETE FROM ai_job_attempts")
        connection.execute("DELETE FROM ai_jobs")
        connection.execute("DELETE FROM temporary_media")
        connection.execute(
            "DELETE FROM user_operations"
            " WHERE scope='calendar_screenshot.upload'"
            " OR scope LIKE 'ai_job.cancel:%'"
        )
        connection.execute(
            "UPDATE ai_config_capabilities"
            " SET current_version_id=NULL, next_version=1, updated_at=now()"
        )
        connection.execute("DELETE FROM ai_config_versions")
    vision_upstream[0].reset()


def png_bytes(label: str = "高等数学 周一 09:00") -> bytes:
    image = Image.new("RGB", (640, 360), color=(250, 252, 249))
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 40, 600, 320), outline=(25, 48, 60), width=3)
    draw.text((70, 100), label, fill=(20, 30, 35))
    output = BytesIO()
    image.save(output, format="PNG")
    image.close()
    return output.getvalue()


def publish_config(database_url: str, settings: Settings, endpoint: str) -> str:
    cipher = Fernet(
        (settings.ai_encryption_key or settings.encryption_key).get_secret_value()
    )
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        current_id = connection.execute(
            """
            SELECT current_version_id
            FROM ai_config_capabilities
            WHERE capability='calendar_screenshot'
            """
        ).fetchone()[0]
        if current_id is not None:
            connection.execute(
                """
                UPDATE ai_config_versions
                SET status='retired', retired_at=now(), updated_at=now()
                WHERE id=%s AND status='published'
                """,
                (current_id,),
            )
        version = connection.execute(
            """
            UPDATE ai_config_capabilities
            SET current_version_id=NULL, next_version=next_version + 1, updated_at=now()
            WHERE capability='calendar_screenshot'
            RETURNING next_version - 1
            """
        ).fetchone()[0]
        config_id = uuid4()
        test_id = uuid4()
        connection.execute(
            """
            INSERT INTO ai_config_versions (
                id, capability, version, endpoint_url, model, api_key_ciphertext,
                timeout_seconds, max_output_tokens, status, last_test_id,
                price_currency, input_price_nanos_per_million_tokens,
                cached_input_price_nanos_per_million_tokens,
                output_price_nanos_per_million_tokens,
                last_test_started_at, last_test_completed_at, published_at
            ) VALUES (
                %s, 'calendar_screenshot', %s, %s, 'vision-model',
                %s, 5, 4096, 'published', %s,
                'CNY', 1000000000, 500000000, 2000000000,
                now(), now(), now()
            )
            """,
            (
                config_id,
                version,
                endpoint,
                cipher.encrypt(b"sk-screenshot-secret"),
                test_id,
            ),
        )
        connection.execute(
            """
            UPDATE ai_config_capabilities
            SET current_version_id=%s, updated_at=now()
            WHERE capability='calendar_screenshot'
            """,
            (config_id,),
        )
    ensure_quota_policy(database_url)
    return str(config_id)


def upload_screenshot(
    client: httpx.Client,
    contents: bytes,
    *,
    operation_id=None,
    filename: str = "schedule.png",
) -> httpx.Response:
    return client.post(
        "/api/v1/calendar/imports/screenshots",
        params={"filename": filename},
        headers={
            "X-Operation-ID": str(operation_id or uuid4()),
            "Content-Type": "image/png",
        },
        content=contents,
    )


def run_worker_once(database_url: str) -> bool:
    async def execute() -> bool:
        settings = Settings(database_url=database_url, environment="test")
        database = Database(settings)
        try:
            return await process_one(database, settings)
        finally:
            await database.close()

    return asyncio.run(execute())


def test_real_screenshot_job_creates_candidates_and_confirm_deletes_media(
    screenshot_client,
    screenshot_database_url,
    vision_upstream,
):
    _, owner_id = create_account(screenshot_client, screenshot_database_url)
    state, endpoint = vision_upstream
    settings = Settings(database_url=screenshot_database_url, environment="test")
    publish_config(screenshot_database_url, settings, endpoint)
    contents = png_bytes()
    operation_id = uuid4()

    uploaded = upload_screenshot(
        screenshot_client,
        contents,
        operation_id=operation_id,
    )
    assert uploaded.status_code == 202, uploaded.text
    job = uploaded.json()["job"]
    assert job["status"] == "queued"
    assert job["input_summary"]["width"] == 640
    assert (
        upload_screenshot(
            screenshot_client,
            contents,
            operation_id=operation_id,
        ).json()
        == uploaded.json()
    )
    with psycopg.connect(psycopg_url(screenshot_database_url)) as connection:
        stored = connection.execute(
            """
            SELECT encrypted_content, content_sha256
            FROM temporary_media WHERE id=(
                SELECT input_media_id FROM ai_jobs WHERE id=%s
            )
            """,
            (job["id"],),
        ).fetchone()
        assert stored[0] != contents
        assert stored[1] == hashlib.sha256(contents).hexdigest()

    assert run_worker_once(screenshot_database_url)
    completed = screenshot_client.get(f"/api/v1/ai/jobs/{job['id']}")
    assert completed.status_code == 200
    assert completed.json()["status"] == "succeeded"
    with psycopg.connect(psycopg_url(screenshot_database_url)) as connection:
        body_log = connection.execute(
            """
            SELECT id, request_body, response_body, status
            FROM ai_body_logs
            WHERE job_id=%s
            """,
            (job["id"],),
        ).fetchone()
        sample = connection.execute(
            """
            SELECT source_body_log_id, request_body, response_body, status
            FROM ai_quality_samples
            WHERE source_job_id=%s
            """,
            (job["id"],),
        ).fetchone()
        provider_cost = connection.execute(
            """
            SELECT attempt_status, usage_source, input_tokens, output_tokens,
                   total_tokens, price_currency, total_cost_nanos
            FROM ai_provider_costs
            WHERE job_id=%s
            """,
            (job["id"],),
        ).fetchone()
        reservation = connection.execute(
            """
            SELECT status, reserved_nanos, actual_cost_nanos,
                   charged_nanos, platform_overage_nanos, usage_source
            FROM ai_quota_reservations
            WHERE job_id=%s
            """,
            (job["id"],),
        ).fetchone()
        ledger = connection.execute(
            """
            SELECT kind, delta_nanos, currency, usage_source
            FROM ai_quota_ledger_entries
            WHERE job_id=%s
            """,
            (job["id"],),
        ).fetchone()
    assert body_log is not None
    assert body_log[3] == "succeeded"
    serialized_log_request = json.dumps(body_log[1], ensure_ascii=False)
    assert "image_metadata" in serialized_log_request
    assert "omitted_by_retention_policy" in serialized_log_request
    assert "base64" not in serialized_log_request
    assert "sk-screenshot-secret" not in serialized_log_request
    assert json.loads(body_log[2])["items"][0]["draft"]["name"] == "高等数学"
    assert sample == (body_log[0], body_log[1], body_log[2], "pending")
    assert provider_cost == (
        "succeeded",
        "provider",
        400,
        120,
        520,
        "CNY",
        640_000,
    )
    assert reservation[0] == "settled"
    assert reservation[2] == 640_000
    assert reservation[3] == min(reservation[1], 640_000)
    assert reservation[4] == 640_000 - reservation[3]
    assert reservation[5] == "provider"
    assert ledger == ("usage_charge", -reservation[3], "CNY", "provider")
    batch_id = completed.json()["output"]["batch_id"]
    detail = screenshot_client.get(
        f"/api/v1/calendar/imports/{batch_id}"
    ).json()
    assert detail["batch"]["format"] == "image"
    assert detail["batch"]["source"]["kind"] == "image"
    assert detail["batch"]["counts"]["valid"] == 1
    assert detail["batch"]["counts"]["invalid"] == 1
    valid = next(item for item in detail["candidates"] if item["state"] == "valid")
    assert valid["raw_data"]["confidence"] == 0.97
    invalid = next(item for item in detail["candidates"] if item["state"] == "invalid")
    assert invalid["warnings"][0]["code"] == "ai_recognition_warning"

    confirmed = screenshot_client.post(
        f"/api/v1/calendar/imports/{batch_id}/confirm",
        json=confirm_payload(detail["batch"], [valid]),
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["outcomes"][0]["series"]["source_type"] == "screenshot"
    occurrences = screenshot_client.get(
        "/api/v1/calendar/occurrences",
        params={"start": "2026-10-05", "end": "2026-10-06"},
    ).json()["items"]
    assert [item["name"] for item in occurrences] == ["高等数学"]
    with psycopg.connect(psycopg_url(screenshot_database_url)) as connection:
        media = connection.execute(
            """
            SELECT encrypted_content, deletion_reason
            FROM temporary_media WHERE id=(
                SELECT input_media_id FROM ai_jobs WHERE id=%s
            )
            """,
            (job["id"],),
        ).fetchone()
        assert media == (None, "confirmed")

    assert len(state.requests) == 1
    request = state.requests[0]
    assert request["path"] == "/v1/chat/completions"
    assert request["authorization"] == "Bearer sk-screenshot-secret"
    image_url = request["body"]["messages"][1]["content"][1]["image_url"]["url"]
    assert image_url.startswith("data:image/png;base64,")


def test_screenshot_requires_published_config_and_ai_access(
    screenshot_client,
    screenshot_database_url,
):
    _, owner_id = create_account(screenshot_client, screenshot_database_url)
    with psycopg.connect(psycopg_url(screenshot_database_url), autocommit=True) as connection:
        current_id = connection.execute(
            """
            SELECT current_version_id
            FROM ai_config_capabilities
            WHERE capability='calendar_screenshot'
            """
        ).fetchone()[0]
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
            UPDATE ai_config_capabilities
            SET current_version_id=NULL, updated_at=now()
            WHERE capability='calendar_screenshot'
            """
        )
    unavailable = upload_screenshot(screenshot_client, png_bytes())
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "ai_capability_unavailable"
    with psycopg.connect(psycopg_url(screenshot_database_url)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM temporary_media WHERE owner_id=%s",
            (owner_id,),
        ).fetchone()[0] == 0

    with psycopg.connect(psycopg_url(screenshot_database_url), autocommit=True) as connection:
        connection.execute(
            "UPDATE users SET ai_access='frozen' WHERE id=%s",
            (owner_id,),
        )
    denied = upload_screenshot(screenshot_client, png_bytes())
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "ai_access_frozen"


def test_retry_then_invalid_output_preserves_media(
    screenshot_client,
    screenshot_database_url,
    vision_upstream,
):
    create_account(screenshot_client, screenshot_database_url)
    state, endpoint = vision_upstream
    settings = Settings(database_url=screenshot_database_url, environment="test")
    publish_config(screenshot_database_url, settings, endpoint)
    state.mode = "unavailable"
    job = upload_screenshot(screenshot_client, png_bytes()).json()["job"]

    assert run_worker_once(screenshot_database_url)
    queued = screenshot_client.get(f"/api/v1/ai/jobs/{job['id']}").json()
    assert queued["status"] == "queued"
    assert queued["attempt_count"] == 1
    with psycopg.connect(
        psycopg_url(screenshot_database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            "UPDATE ai_jobs SET available_at=now() - interval '1 second' WHERE id=%s",
            (job["id"],),
        )
    state.mode = "invalid"
    assert run_worker_once(screenshot_database_url)
    failed = screenshot_client.get(f"/api/v1/ai/jobs/{job['id']}").json()
    assert failed["status"] == "failed"
    assert failed["attempt_count"] == 2
    assert failed["error"]["code"] == "screenshot_output_invalid"
    with psycopg.connect(psycopg_url(screenshot_database_url)) as connection:
        assert connection.execute(
            """
            SELECT encrypted_content IS NOT NULL
            FROM temporary_media WHERE id=(
                SELECT input_media_id FROM ai_jobs WHERE id=%s
            )
            """,
            (job["id"],),
        ).fetchone()[0]
        assert connection.execute(
            "SELECT count(*) FROM calendar_import_batches WHERE origin_job_id=%s",
            (job["id"],),
        ).fetchone()[0] == 0
        costs = connection.execute(
            """
            SELECT attempt_status, usage_source, input_tokens, output_tokens
            FROM ai_provider_costs
            WHERE job_id=%s
            ORDER BY attempt_number
            """,
            (job["id"],),
        ).fetchall()
        assert costs[0][0:2] == ("retryable_failed", "estimated")
        assert costs[1] == ("failed", "provider", 400, 20)
        reservation = connection.execute(
            """
            SELECT status, release_reason
            FROM ai_quota_reservations
            WHERE job_id=%s
            """,
            (job["id"],),
        ).fetchone()
        assert reservation == ("released", "failed")
        assert connection.execute(
            "SELECT count(*) FROM ai_quota_ledger_entries WHERE job_id=%s",
            (job["id"],),
        ).fetchone()[0] == 0


def test_cancel_wins_against_late_provider_result(
    screenshot_client,
    screenshot_database_url,
    vision_upstream,
):
    create_account(screenshot_client, screenshot_database_url)
    state, endpoint = vision_upstream
    settings = Settings(database_url=screenshot_database_url, environment="test")
    publish_config(screenshot_database_url, settings, endpoint)
    state.mode = "blocked"
    job = upload_screenshot(screenshot_client, png_bytes()).json()["job"]

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run_worker_once, screenshot_database_url)
        assert state.received.wait(timeout=5)
        cancelled = screenshot_client.post(
            f"/api/v1/ai/jobs/{job['id']}/cancel",
            json={"operation_id": str(uuid4())},
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["job"]["status"] == "cancelled"
        state.release.set()
        assert future.result(timeout=10)

    final = screenshot_client.get(f"/api/v1/ai/jobs/{job['id']}").json()
    assert final["status"] == "cancelled"
    assert final["output"] is None
    with psycopg.connect(psycopg_url(screenshot_database_url)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM calendar_import_batches WHERE origin_job_id=%s",
            (job["id"],),
        ).fetchone()[0] == 0
        cost = connection.execute(
            """
            SELECT attempt_status, usage_source, output_tokens
            FROM ai_provider_costs
            WHERE job_id=%s
            """,
            (job["id"],),
        ).fetchone()
        assert cost is not None
        assert cost[0:2] == ("cancelled", "estimated")
        reservation = connection.execute(
            """
            SELECT status, release_reason
            FROM ai_quota_reservations
            WHERE job_id=%s
            """,
            (job["id"],),
        ).fetchone()
        assert reservation == ("released", "cancelled")


def test_screenshot_migration_round_trip(admin_url):
    with isolated_database(admin_url) as database_url:
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_name='calendar_import_batches'
                  AND column_name='origin_job_id'
                """
            ).fetchone() == ("origin_job_id",)
        migrate_result = subprocess.run(
            [sys.executable, "-m", "alembic", "downgrade", "0013_ai_jobs_media"],
            cwd=SERVER,
            env={
                **os.environ,
                "ITIME_ENVIRONMENT": "test",
                "ITIME_DATABASE_URL": database_url,
            },
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        assert migrate_result.returncode == 0
