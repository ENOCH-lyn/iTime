import asyncio
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import httpx
import psycopg
import pyotp
import pytest
from cryptography.fernet import Fernet
from psycopg.errors import CheckViolation

from itime.admin.operations import create_admin
from itime.auth.security import Security
from itime.config import Settings
from itime.database import Database
from tests.conftest import isolated_database, migrate, psycopg_url

pytestmark = pytest.mark.integration
PASSWORD = "Valid-admin-password-123"
ADMIN_AUTH = "/api/v1/admin/auth"
AI_CONFIGS = "/api/v1/admin/ai-configs"
UPSTREAM_BODY_SECRET = "upstream-private-response-body"


@dataclass
class UpstreamState:
    mode: str = "success"
    requests: list[dict] = field(default_factory=list)
    idle_transactions: list[int] = field(default_factory=list)
    received: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)


def upstream_handler(state: UpstreamState, database_url: str):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            with psycopg.connect(psycopg_url(database_url)) as connection:
                idle = connection.execute(
                    "SELECT count(*) FROM pg_stat_activity"
                    " WHERE datname=current_database()"
                    " AND pid <> pg_backend_pid()"
                    " AND state='idle in transaction'"
                ).fetchone()[0]
            with state.lock:
                mode = state.mode
                state.requests.append(
                    {
                        "path": self.path,
                        "authorization": self.headers.get("Authorization"),
                        "body": json.loads(body),
                    }
                )
                state.idle_transactions.append(idle)
            state.received.set()
            if mode == "blocked":
                state.release.wait(timeout=5)
            if mode == "timeout":
                time.sleep(1.2)

            status, response = {
                "success": (
                    200,
                    {"choices": [{"message": {"content": '{"marker":"09:00"}'}}]},
                ),
                "blocked": (
                    200,
                    {"choices": [{"message": {"content": '{"marker":"09:00"}'}}]},
                ),
                "authentication": (401, {"error": UPSTREAM_BODY_SECRET}),
                "rate_limit": (429, {"error": UPSTREAM_BODY_SECRET}),
                "server_error": (503, {"error": UPSTREAM_BODY_SECRET}),
                "invalid": (200, {"private": UPSTREAM_BODY_SECRET}),
                "timeout": (
                    200,
                    {"choices": [{"message": {"content": "too late"}}]},
                ),
            }[mode]
            encoded = json.dumps(response).encode()
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
            except OSError:
                pass

        def log_message(self, format: str, *args) -> None:
            pass

    return Handler


@pytest.fixture
def upstream(database_url):
    state = UpstreamState()
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        upstream_handler(state, database_url),
    )
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
def reset_ai_configs(database_url):
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        connection.execute(
            "UPDATE ai_config_capabilities"
            " SET current_version_id=NULL, next_version=1, updated_at=now()"
        )
        connection.execute("DELETE FROM ai_job_attempts")
        connection.execute("DELETE FROM ai_jobs")
        connection.execute("DELETE FROM temporary_media")
        connection.execute("DELETE FROM ai_config_versions")
    yield
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        connection.execute(
            "UPDATE ai_config_capabilities"
            " SET current_version_id=NULL, next_version=1, updated_at=now()"
        )
        connection.execute("DELETE FROM ai_job_attempts")
        connection.execute("DELETE FROM ai_jobs")
        connection.execute("DELETE FROM temporary_media")
        connection.execute("DELETE FROM ai_config_versions")


async def create_admin_account(database_url: str, email: str) -> str:
    settings = Settings(database_url=database_url, environment="test")
    database = Database(settings)
    try:
        return await create_admin(
            database,
            Security(settings),
            settings,
            email,
            PASSWORD,
        )
    finally:
        await database.close()


def setup_admin(client: httpx.Client, database_url: str) -> dict:
    email = f"ai-manager-{uuid4().hex}@example.com"
    link = asyncio.run(create_admin_account(database_url, email))
    token = re.search(r"#token=([A-Za-z0-9_-]{43})$", link).group(1)
    started = client.post(
        f"{ADMIN_AUTH}/setup",
        json={"token": token, "password": PASSWORD},
    )
    assert started.status_code == 200, started.text
    confirmed = client.post(
        f"{ADMIN_AUTH}/setup/confirm",
        json={
            "token": token,
            "password": PASSWORD,
            "code": pyotp.TOTP(started.json()["secret"]).now(),
            "device_name": "AI 配置测试",
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    client.headers["x-csrf-token"] = confirmed.json()["csrf_token"]
    return confirmed.json()


def create_config(
    client: httpx.Client,
    endpoint: str,
    *,
    key: str = "sk-ai-config-private-key",
    model: str = "vision-model-1",
    timeout: int = 2,
) -> httpx.Response:
    return client.post(
        AI_CONFIGS,
        json={
            "capability": "calendar_screenshot",
            "endpoint_url": endpoint,
            "model": model,
            "api_key": key,
            "timeout_seconds": timeout,
            "max_output_tokens": 321,
        },
    )


def test_create_list_detail_constraints_encryption_and_audit(
    client,
    database_url,
    settings,
    upstream,
    live_server,
):
    admin = setup_admin(client, database_url)
    state, endpoint = upstream
    key = "sk-never-return-this-key"

    csrf = client.headers.pop("x-csrf-token")
    denied = create_config(client, endpoint)
    assert denied.status_code == 403
    client.headers["x-csrf-token"] = csrf

    created = create_config(client, endpoint, key=key)
    assert created.status_code == 201, created.text
    item = created.json()
    assert item["version"] == 1
    assert item["status"] == "draft"
    assert item["key_configured"] is True
    assert key not in created.text
    assert "api_key" not in item
    assert state.requests == []

    second = create_config(client, endpoint, model="vision-model-2")
    assert second.status_code == 201
    assert second.json()["version"] == 2
    listing = client.get(AI_CONFIGS)
    detail = client.get(f"{AI_CONFIGS}/{item['id']}")
    assert listing.status_code == detail.status_code == 200
    assert [entry["version"] for entry in listing.json()["items"]] == [2, 1]
    assert key not in listing.text + detail.text
    assert client.get(f"{AI_CONFIGS}/{uuid4()}").status_code == 404

    with httpx.Client(base_url=live_server.origin, trust_env=False) as outsider:
        assert outsider.get(AI_CONFIGS).status_code == 401

    for invalid_endpoint in (
        "http://user:pass@127.0.0.1:8000/v1",
        endpoint + "?tenant=private",
        endpoint + "#fragment",
        "http://example.com/v1",
    ):
        rejected = create_config(client, invalid_endpoint)
        assert rejected.status_code == 422
        assert key not in rejected.text
    assert create_config(client, endpoint, model="bad model").status_code == 422

    with psycopg.connect(psycopg_url(database_url)) as connection:
        ciphertext = connection.execute(
            "SELECT api_key_ciphertext FROM ai_config_versions WHERE id=%s",
            (item["id"],),
        ).fetchone()[0]
        assert key.encode() not in bytes(ciphertext)
        ai_key = settings.ai_encryption_key or settings.encryption_key
        assert Fernet(ai_key.get_secret_value()).decrypt(bytes(ciphertext)).decode() == key
        with pytest.raises(CheckViolation):
            connection.execute(
                "UPDATE ai_config_versions SET model='mutated-model' WHERE id=%s",
                (item["id"],),
            )
        connection.rollback()
        actions = {
            row[0]
            for row in connection.execute(
                "SELECT action FROM security_events"
                " WHERE user_id=%s AND target_id=%s",
                (admin["admin"]["id"], item["id"]),
            )
        }
        assert "ai_config_version_created" in actions
    logs = live_server.log_path.read_text()
    assert key not in logs


def test_real_synthetic_http_test_and_atomic_concurrent_publish(
    client,
    database_url,
    upstream,
    live_server,
):
    admin = setup_admin(client, database_url)
    state, endpoint = upstream
    configs = [
        create_config(client, endpoint, key=f"sk-publish-{index}").json()
        for index in range(3)
    ]
    for config in configs:
        tested = client.post(f"{AI_CONFIGS}/{config['id']}/test")
        assert tested.status_code == 200, tested.text
        assert tested.json()["status"] == "tested"
    assert len(state.requests) == 3
    assert state.idle_transactions == [0, 0, 0]
    request = state.requests[0]
    assert request["path"] == "/v1/chat/completions"
    assert request["authorization"] == "Bearer sk-publish-0"
    assert request["body"]["model"] == "vision-model-1"
    assert request["body"]["max_tokens"] == 321
    assert request["body"]["response_format"] == {"type": "json_object"}
    image_url = request["body"]["messages"][0]["content"][1]["image_url"]["url"]
    assert image_url.startswith("data:image/png;base64,iVBOR")

    first = client.post(f"{AI_CONFIGS}/{configs[0]['id']}/publish")
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "published"

    cookies = dict(client.cookies)
    headers = {"x-csrf-token": client.headers["x-csrf-token"]}

    def publish(config_id: str) -> httpx.Response:
        return httpx.post(
            live_server.origin + f"{AI_CONFIGS}/{config_id}/publish",
            cookies=cookies,
            headers=headers,
            timeout=10,
            trust_env=False,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(publish, [configs[1]["id"], configs[2]["id"]]))
    assert [response.status_code for response in responses] == [200, 200]
    rolled_back = client.post(f"{AI_CONFIGS}/{configs[0]['id']}/publish")
    assert rolled_back.status_code == 200, rolled_back.text
    assert rolled_back.json()["status"] == "published"

    with psycopg.connect(psycopg_url(database_url)) as connection:
        rows = connection.execute(
            "SELECT id, status FROM ai_config_versions ORDER BY version"
        ).fetchall()
        assert [row[1] for row in rows].count("published") == 1
        assert [row[1] for row in rows].count("retired") == 2
        pointer = connection.execute(
            "SELECT current_version_id FROM ai_config_capabilities"
            " WHERE capability='calendar_screenshot'"
        ).fetchone()[0]
        assert str(pointer) == configs[0]["id"]
        actions = connection.execute(
            "SELECT action FROM security_events WHERE user_id=%s"
            " AND action LIKE 'ai_config_%%'",
            (admin["admin"]["id"],),
        ).fetchall()
        assert sum(row[0] == "ai_config_test_succeeded" for row in actions) == 3
        assert sum(row[0] == "ai_config_version_published" for row in actions) == 3
        assert sum(row[0] == "ai_config_version_rolled_back" for row in actions) == 1


@pytest.mark.parametrize(
    ("mode", "expected_status", "expected_code", "expected_stage"),
    [
        ("authentication", 422, "ai_config_test_authentication_failed", "authentication"),
        ("rate_limit", 502, "ai_config_test_rate_limited", "upstream"),
        ("server_error", 502, "ai_config_test_upstream_unavailable", "upstream"),
        ("invalid", 502, "ai_config_test_invalid_response", "response"),
        ("timeout", 504, "ai_config_test_timeout", "network"),
    ],
)
def test_provider_failures_are_stable_saved_and_redacted(
    mode,
    expected_status,
    expected_code,
    expected_stage,
    client,
    database_url,
    upstream,
    live_server,
):
    setup_admin(client, database_url)
    state, endpoint = upstream
    state.mode = mode
    key = f"sk-failure-{mode}-private"
    config = create_config(client, endpoint, key=key, timeout=1).json()
    response = client.post(f"{AI_CONFIGS}/{config['id']}/test")
    assert response.status_code == expected_status
    assert response.json()["error"]["code"] == expected_code
    assert response.json()["error"]["saved"] == "saved"
    assert response.json()["error"]["details"]["stage"] == expected_stage
    assert key not in response.text
    assert UPSTREAM_BODY_SECRET not in response.text

    current = client.get(f"{AI_CONFIGS}/{config['id']}").json()
    assert current["status"] == "draft"
    assert current["test_in_progress"] is False
    assert current["last_test_error_code"] == expected_code
    assert current["last_test_error_stage"] == expected_stage
    assert current["last_test_completed_at"] is not None
    logs = live_server.log_path.read_text()
    assert key not in logs
    assert UPSTREAM_BODY_SECRET not in logs


def test_test_result_reauth_rejects_revoked_admin_without_holding_transaction(
    client,
    database_url,
    upstream,
    live_server,
):
    admin = setup_admin(client, database_url)
    state, endpoint = upstream
    state.mode = "blocked"
    config = create_config(client, endpoint).json()
    cookies = dict(client.cookies)
    headers = {"x-csrf-token": client.headers["x-csrf-token"]}

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            httpx.post,
            live_server.origin + f"{AI_CONFIGS}/{config['id']}/test",
            cookies=cookies,
            headers=headers,
            timeout=10,
            trust_env=False,
        )
        assert state.received.wait(timeout=5)
        assert state.idle_transactions == [0]
        with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
            connection.execute(
                "UPDATE admin_sessions SET revoked_at=now() WHERE id=%s",
                (admin["session_id"],),
            )
        state.release.set()
        response = future.result(timeout=10)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "admin_unauthenticated"
    with psycopg.connect(psycopg_url(database_url)) as connection:
        stored = connection.execute(
            "SELECT status, last_test_completed_at, last_test_error_code"
            " FROM ai_config_versions WHERE id=%s",
            (config["id"],),
        ).fetchone()
    assert stored == ("draft", None, None)


def test_ai_config_migration_round_trip(admin_url):
    with isolated_database(admin_url) as database_url:
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT to_regclass('ai_config_versions')"
            ).fetchone()[0] == "ai_config_versions"
        migrate(database_url, "downgrade", "0011_calendar_file_imports")
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT to_regclass('ai_config_versions')"
            ).fetchone()[0] is None
        migrate(database_url, "upgrade", "head")
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT capability, next_version, current_version_id"
                " FROM ai_config_capabilities"
            ).fetchone() == ("calendar_screenshot", 1, None)
