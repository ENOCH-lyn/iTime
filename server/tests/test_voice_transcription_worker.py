import asyncio
import hashlib
import json
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import httpx
import psycopg
import pytest

from itime.ai import transcription_service
from itime.ai.worker import process_one
from itime.config import Settings
from itime.database import Database
from tests import test_voice_transcription_api as api_support
from tests.conftest import psycopg_url
from tests.test_audio_validation import wav_bytes
from tests.test_calendar_series import create_account, login_account

transcription_database_url = api_support.transcription_database_url
transcription_live_server = api_support.transcription_live_server
transcription_client = api_support.transcription_client
reset_transcriptions = api_support.reset_transcriptions

pytestmark = pytest.mark.integration


@dataclass
class TranscriptionUpstreamState:
    modes: list[str] = field(default_factory=lambda: ["success"])
    requests: list[dict] = field(default_factory=list)
    received: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def reset(self, *modes: str) -> None:
        with self.lock:
            self.modes = list(modes) or ["success"]
            self.requests.clear()
        self.received.clear()
        self.release.clear()

    def next_mode(self) -> str:
        with self.lock:
            return self.modes.pop(0) if self.modes else "success"


def parse_multipart(content_type: str, body: bytes) -> dict:
    message = BytesParser(policy=policy.default).parsebytes(
        b"Content-Type: "
        + content_type.encode()
        + b"\r\nMIME-Version: 1.0\r\n\r\n"
        + body
    )
    parts: dict[str, object] = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not isinstance(name, str):
            continue
        payload = part.get_payload(decode=True) or b""
        filename = part.get_filename()
        parts[name] = (
            {
                "filename": filename,
                "content_type": part.get_content_type(),
                "contents": payload,
            }
            if filename is not None
            else payload.decode()
        )
    return parts


def transcription_handler(state: TranscriptionUpstreamState):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            content_type = self.headers.get("Content-Type", "")
            parts = parse_multipart(content_type, body)
            mode = state.next_mode()
            with state.lock:
                state.requests.append(
                    {
                        "path": self.path,
                        "authorization": self.headers.get("Authorization"),
                        "parts": parts,
                    }
                )
            state.received.set()
            if mode == "blocked":
                state.release.wait(timeout=10)
            elif mode == "slow":
                time.sleep(2)

            if mode == "rate_limited":
                status = 429
                response = {"error": "private-rate-limit-body"}
            elif mode == "unavailable":
                status = 503
                response = {"error": "private-upstream-body"}
            elif mode == "unauthorized":
                status = 401
                response = {"error": "private-auth-body"}
            elif mode == "empty":
                status = 200
                response = {"text": "   "}
            elif mode == "control":
                status = 200
                response = {"text": "任务\x01内容"}
            elif mode == "too_long":
                status = 200
                response = {"text": "字" * 4001}
            elif mode == "oversized":
                status = 200
                response = {"text": "字" * (300 * 1024)}
            elif mode == "malformed":
                status = 200
                encoded = b"{invalid-json"
                response = None
            else:
                status = 200
                response = {
                    "text": "  周五前完成论文提纲  ",
                    "language": " zh ",
                    "duration": 999.5,
                    "usage": {
                        "input_tokens": 7,
                        "output_tokens": 9,
                        "total_tokens": 16,
                        "private_field": "must-not-persist",
                    },
                }
            if response is not None:
                encoded = json.dumps(response, ensure_ascii=False).encode()
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("x-request-id", f"transcription-{mode}")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
            except OSError:
                pass

        def log_message(self, format: str, *args) -> None:
            pass

    return Handler


@pytest.fixture(scope="session")
def transcription_upstream() -> Iterator[
    tuple[TranscriptionUpstreamState, str]
]:
    state = TranscriptionUpstreamState()
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        transcription_handler(state),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state, f"http://127.0.0.1:{server.server_port}"
    finally:
        state.release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture(autouse=True)
def reset_transcription_upstream(transcription_upstream):
    transcription_upstream[0].reset()


def run_worker_once(database_url: str) -> bool:
    async def execute() -> bool:
        settings = Settings(database_url=database_url, environment="test")
        database = Database(settings)
        try:
            return await process_one(database, settings)
        finally:
            await database.close()

    return asyncio.run(execute())


def make_due(database_url: str, job_id: str) -> None:
    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            """
            UPDATE ai_jobs
            SET available_at=now() - interval '1 second'
            WHERE id=%s
            """,
            (job_id,),
        )


def resolve(
    client,
    item: dict,
    *,
    action: str,
    operation_id=None,
    expected_version: int | None = None,
    transcript_sha256: str | None = None,
):
    return client.post(
        f"/api/v1/voice-transcriptions/{item['id']}/resolve",
        json={
            "operation_id": str(operation_id or uuid4()),
            "expected_version": expected_version or item["version"],
            "action": action,
            "transcript_sha256": (
                transcript_sha256
                if action == "accept"
                else None
            ),
        },
    )


@pytest.mark.parametrize("primary_mode", ["rate_limited", "unavailable"])
def test_worker_posts_multipart_falls_back_and_settles_successful_route(
    transcription_client,
    transcription_database_url,
    transcription_upstream,
    primary_mode,
):
    create_account(
        transcription_client,
        transcription_database_url,
    )
    state, origin = transcription_upstream
    state.reset(primary_mode, "success")
    primary_id, fallback_id = api_support.publish_transcription_route(
        transcription_database_url,
        primary_price=60_000_000,
        fallback_price=120_000_000,
        primary_endpoint=f"{origin}/primary/v1",
        fallback_endpoint=f"{origin}/fallback/v1",
    )
    contents = wav_bytes(duration_ms=1500)
    accepted = api_support.upload(transcription_client, contents)
    assert accepted.status_code == 202, accepted.text
    job_id = accepted.json()["job"]["id"]
    transcription_id = accepted.json()["transcription"]["id"]

    assert run_worker_once(transcription_database_url)
    retrying = transcription_client.get(
        f"/api/v1/voice-transcriptions/{transcription_id}"
    ).json()
    assert retrying["job"]["status"] == "queued"
    assert retrying["job"]["attempt_count"] == 1
    assert retrying["transcription"]["transcript"] is None

    make_due(transcription_database_url, job_id)
    assert run_worker_once(transcription_database_url)
    completed = transcription_client.get(
        f"/api/v1/voice-transcriptions/{transcription_id}"
    )
    assert completed.status_code == 200
    body = completed.json()
    assert body["job"]["status"] == "succeeded"
    assert body["job"]["output"] == {
        "resource_type": "voice_transcription",
        "transcription_id": transcription_id,
    }
    assert body["transcription"]["transcript"] == "周五前完成论文提纲"
    assert body["transcription"]["transcript_sha256"] == hashlib.sha256(
        "周五前完成论文提纲".encode()
    ).hexdigest()
    assert body["transcription"]["language"] == "zh"
    assert body["transcription"]["ready_at"] is not None

    assert len(state.requests) == 2
    assert [request["path"] for request in state.requests] == [
        "/primary/v1/audio/transcriptions",
        "/fallback/v1/audio/transcriptions",
    ]
    assert [request["authorization"] for request in state.requests] == [
        "Bearer transcription-primary-key",
        "Bearer transcription-fallback-key",
    ]
    for index, request in enumerate(state.requests):
        assert request["parts"]["model"] == (
            "transcribe-primary" if index == 0 else "transcribe-fallback"
        )
        assert request["parts"]["response_format"] == "json"
        assert request["parts"]["file"] == {
            "filename": "voice.wav",
            "content_type": "audio/wav",
            "contents": contents,
        }

    with psycopg.connect(
        psycopg_url(transcription_database_url)
    ) as connection:
        costs = connection.execute(
            """
            SELECT attempt_number, attempt_status, usage_source,
                   billing_basis, audio_duration_ms,
                   audio_price_nanos_per_minute, audio_cost_nanos,
                   total_cost_nanos, usage_details
            FROM ai_provider_costs
            WHERE job_id=%s
            ORDER BY attempt_number
            """,
            (job_id,),
        ).fetchall()
        assert costs[0][:8] == (
            1,
            "retryable_failed",
            "measured",
            "audio_minute",
            1500,
            60_000_000,
            1_500_000,
            1_500_000,
        )
        assert costs[0][8] == {}
        assert costs[1][:8] == (
            2,
            "succeeded",
            "measured",
            "audio_minute",
            1500,
            120_000_000,
            3_000_000,
            3_000_000,
        )
        assert costs[1][8] == {
            "provider_duration_seconds": 999.5,
            "input_tokens": 7,
            "output_tokens": 9,
            "total_tokens": 16,
        }
        reservation = connection.execute(
            """
            SELECT status, reserved_nanos, actual_cost_nanos,
                   charged_nanos, platform_overage_nanos,
                   provider_cost_id
            FROM ai_quota_reservations
            WHERE job_id=%s
            """,
            (job_id,),
        ).fetchone()
        assert reservation[:5] == (
            "settled",
            3_000_000,
            3_000_000,
            3_000_000,
            0,
        )
        assert reservation[5] is not None
        ledger = connection.execute(
            """
            SELECT count(*), sum(delta_nanos)
            FROM ai_quota_ledger_entries
            WHERE job_id=%s AND kind='usage_charge'
            """,
            (job_id,),
        ).fetchone()
        assert ledger == (1, -3_000_000)
        assert connection.execute(
            """
            SELECT encrypted_content IS NOT NULL, deletion_reason
            FROM temporary_media
            WHERE id=(SELECT input_media_id FROM ai_jobs WHERE id=%s)
            """,
            (job_id,),
        ).fetchone() == (True, None)
        log = connection.execute(
            """
            SELECT request_body, response_body, status
            FROM ai_body_logs
            WHERE job_id=%s AND attempt_number=2
            """,
            (job_id,),
        ).fetchone()
        assert log[0]["file"] == {
            "filename": "voice.wav",
            "content_type": "audio/wav",
            "byte_size": len(contents),
            "duration_ms": 1500,
            "content_sha256": hashlib.sha256(contents).hexdigest(),
            "content": "omitted_by_retention_policy",
        }
        assert log[1:] == ("周五前完成论文提纲", "succeeded")
        serialized_request = json.dumps(log[0], ensure_ascii=False)
        assert "transcription-primary-key" not in serialized_request
        assert "transcription-fallback-key" not in serialized_request
        assert "base64" not in serialized_request
        sample = connection.execute(
            """
            SELECT request_body, response_body, status
            FROM ai_quality_samples
            WHERE source_job_id=%s
            """,
            (job_id,),
        ).fetchone()
        assert sample == (log[0], "周五前完成论文提纲", "pending")
        route_ids = connection.execute(
            """
            SELECT config_version_id
            FROM ai_job_config_routes
            WHERE job_id=%s
            ORDER BY attempt_number
            """,
            (job_id,),
        ).fetchall()
        assert [str(row[0]) for row in route_ids] == [
            primary_id,
            fallback_id,
            fallback_id,
        ]


def test_accept_is_replayable_deletes_audio_and_keeps_text_until_expiry(
    transcription_client,
    transcription_database_url,
    transcription_upstream,
):
    create_account(
        transcription_client,
        transcription_database_url,
    )
    state, origin = transcription_upstream
    state.reset("success")
    api_support.publish_transcription_route(
        transcription_database_url,
        fallback_price=None,
        primary_endpoint=f"{origin}/v1",
    )
    accepted = api_support.upload(transcription_client, wav_bytes()).json()
    assert run_worker_once(transcription_database_url)
    ready = transcription_client.get(
        "/api/v1/voice-transcriptions/"
        + accepted["transcription"]["id"]
    ).json()["transcription"]
    operation_id = uuid4()

    stale = resolve(
        transcription_client,
        ready,
        action="accept",
        expected_version=ready["version"] - 1,
        transcript_sha256=ready["transcript_sha256"],
    )
    assert stale.status_code == 409
    assert (
        stale.json()["error"]["code"]
        == "voice_transcription_version_conflict"
    )
    changed = resolve(
        transcription_client,
        ready,
        action="accept",
        transcript_sha256="0" * 64,
    )
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "voice_transcription_changed"

    response = resolve(
        transcription_client,
        ready,
        action="accept",
        operation_id=operation_id,
        transcript_sha256=ready["transcript_sha256"],
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["operation_id"] == str(operation_id)
    assert body["transcription"]["resolution"] == "accepted"
    assert body["transcription"]["transcript"] == "周五前完成论文提纲"
    replay = resolve(
        transcription_client,
        ready,
        action="accept",
        operation_id=operation_id,
        transcript_sha256=ready["transcript_sha256"],
    )
    assert replay.json() == body
    conflicting = resolve(
        transcription_client,
        ready,
        action="discard",
    )
    assert conflicting.status_code == 200
    assert conflicting.json()["transcription"]["resolution"] == "accepted"
    with psycopg.connect(
        psycopg_url(transcription_database_url)
    ) as connection:
        assert connection.execute(
            """
            SELECT encrypted_content, deletion_reason
            FROM temporary_media
            WHERE id=(SELECT media_id FROM voice_transcriptions WHERE id=%s)
            """,
            (ready["id"],),
        ).fetchone() == (None, "accepted")

    with psycopg.connect(
        psycopg_url(transcription_database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            """
            UPDATE voice_transcriptions
            SET created_at=now() - interval '2 days',
                expires_at=now() - interval '1 day'
            WHERE id=%s
            """,
            (ready["id"],),
        )
    assert run_transcription_cleanup(transcription_database_url) == 1
    expired = transcription_client.get(
        f"/api/v1/voice-transcriptions/{ready['id']}"
    ).json()["transcription"]
    assert expired["resolution"] == "accepted"
    assert expired["transcript"] is None
    assert expired["transcript_sha256"] is None
    assert expired["text_purged_at"] is not None
    with psycopg.connect(
        psycopg_url(transcription_database_url)
    ) as connection:
        stored_operation = connection.execute(
            """
            SELECT response, response_purged_at
            FROM user_operations
            WHERE operation_id=%s
            """,
            (operation_id,),
        ).fetchone()
        assert stored_operation[0] is None
        assert stored_operation[1] is not None
    purged_replay = resolve(
        transcription_client,
        ready,
        action="accept",
        operation_id=operation_id,
        transcript_sha256=ready["transcript_sha256"],
    )
    assert purged_replay.status_code == 409
    assert purged_replay.json()["error"]["code"] == "operation_result_expired"


def test_discard_cancels_active_job_and_is_owner_scoped(
    transcription_client,
    transcription_database_url,
    transcription_upstream,
):
    _, owner_id = create_account(
        transcription_client,
        transcription_database_url,
    )
    _state, origin = transcription_upstream
    api_support.publish_transcription_route(
        transcription_database_url,
        fallback_price=None,
        primary_endpoint=f"{origin}/v1",
    )
    accepted = api_support.upload(transcription_client, wav_bytes()).json()
    item = accepted["transcription"]

    response = resolve(
        transcription_client,
        item,
        action="discard",
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["transcription"]["resolution"] == "discarded"
    assert body["transcription"]["transcript"] is None
    assert body["transcription"]["text_purged_at"] is not None
    assert body["job"]["status"] == "cancelled"
    with psycopg.connect(
        psycopg_url(transcription_database_url)
    ) as connection:
        assert connection.execute(
            """
            SELECT encrypted_content, deletion_reason
            FROM temporary_media
            WHERE id=(SELECT media_id FROM voice_transcriptions WHERE id=%s)
            """,
            (item["id"],),
        ).fetchone() == (None, "discarded")
        assert connection.execute(
            """
            SELECT status, release_reason
            FROM ai_quota_reservations WHERE job_id=%s
            """,
            (accepted["job"]["id"],),
        ).fetchone() == ("released", "cancelled")
        assert connection.execute(
            "SELECT count(*) FROM ai_quota_ledger_entries WHERE owner_id=%s",
            (owner_id,),
        ).fetchone()[0] == 0
    assert not run_worker_once(transcription_database_url)


def test_concurrent_accept_and_discard_leave_one_resolution(
    transcription_database_url,
    transcription_live_server,
    transcription_upstream,
):
    with httpx.Client(
        base_url=transcription_live_server.origin,
        timeout=15,
        trust_env=False,
    ) as setup:
        email, _owner_id = create_account(
            setup,
            transcription_database_url,
        )
        _state, origin = transcription_upstream
        api_support.publish_transcription_route(
            transcription_database_url,
            fallback_price=None,
            primary_endpoint=f"{origin}/v1",
        )
        accepted = api_support.upload(setup, wav_bytes()).json()
        assert run_worker_once(transcription_database_url)
        ready = setup.get(
            "/api/v1/voice-transcriptions/"
            + accepted["transcription"]["id"]
        ).json()["transcription"]

    with ExitStack() as stack:
        clients = [
            stack.enter_context(
                httpx.Client(
                    base_url=transcription_live_server.origin,
                    timeout=15,
                    trust_env=False,
                )
            )
            for _ in range(2)
        ]
        for client in clients:
            login_account(client, email)
        with ThreadPoolExecutor(max_workers=2) as pool:
            accept_future = pool.submit(
                resolve,
                clients[0],
                ready,
                action="accept",
                transcript_sha256=ready["transcript_sha256"],
            )
            discard_future = pool.submit(
                resolve,
                clients[1],
                ready,
                action="discard",
            )
            responses = [accept_future.result(), discard_future.result()]

    assert [response.status_code for response in responses] == [200, 200]
    resolutions = {
        response.json()["transcription"]["resolution"]
        for response in responses
    }
    assert len(resolutions) == 1
    assert resolutions <= {"accepted", "discarded"}


def run_transcription_cleanup(database_url: str) -> int:
    async def execute() -> int:
        database = Database(
            Settings(database_url=database_url, environment="test")
        )
        try:
            return await transcription_service.cleanup_expired_transcriptions(
                database
            )
        finally:
            await database.close()

    return asyncio.run(execute())


def test_two_cleanup_workers_expire_each_transcription_once(
    transcription_client,
    transcription_database_url,
    transcription_upstream,
):
    create_account(
        transcription_client,
        transcription_database_url,
    )
    state, origin = transcription_upstream
    state.reset("success", "success")
    api_support.publish_transcription_route(
        transcription_database_url,
        fallback_price=None,
        primary_endpoint=f"{origin}/v1",
    )
    first = api_support.upload(transcription_client, wav_bytes()).json()
    assert run_worker_once(transcription_database_url)
    second = api_support.upload(transcription_client, wav_bytes()).json()
    assert run_worker_once(transcription_database_url)
    first_ready = transcription_client.get(
        "/api/v1/voice-transcriptions/"
        + first["transcription"]["id"]
    ).json()["transcription"]
    accepted = resolve(
        transcription_client,
        first_ready,
        action="accept",
        transcript_sha256=first_ready["transcript_sha256"],
    )
    assert accepted.status_code == 200
    queued = api_support.upload(transcription_client, wav_bytes()).json()
    item_ids = [
        first["transcription"]["id"],
        second["transcription"]["id"],
        queued["transcription"]["id"],
    ]
    with psycopg.connect(
        psycopg_url(transcription_database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            """
            UPDATE voice_transcriptions
            SET created_at=now() - interval '2 days',
                expires_at=now() - interval '1 day'
            WHERE id = ANY(%s::uuid[])
            """,
            (item_ids,),
        )
        connection.execute(
            """
            UPDATE temporary_media
            SET expires_at=now() - interval '1 day'
            WHERE id IN (
                SELECT media_id FROM voice_transcriptions
                WHERE id = ANY(%s::uuid[])
            )
            """,
            (item_ids,),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        counts = list(
            pool.map(
                run_transcription_cleanup,
                [transcription_database_url] * 2,
            )
        )
    assert sum(counts) == 3
    with psycopg.connect(
        psycopg_url(transcription_database_url)
    ) as connection:
        rows = connection.execute(
            """
            SELECT transcription.id, transcription.resolution,
                   transcription.transcript, transcription.text_purged_at,
                   job.status, media.encrypted_content, media.deletion_reason
            FROM voice_transcriptions AS transcription
            JOIN ai_jobs AS job ON job.id=transcription.job_id
            JOIN temporary_media AS media ON media.id=transcription.media_id
            WHERE transcription.id = ANY(%s::uuid[])
            ORDER BY transcription.id
            """,
            (item_ids,),
        ).fetchall()
        by_id = {str(row[0]): row[1:] for row in rows}
        assert by_id[first["transcription"]["id"]][0] == "accepted"
        assert by_id[first["transcription"]["id"]][1] is None
        assert by_id[second["transcription"]["id"]][0] == "expired"
        assert by_id[second["transcription"]["id"]][1] is None
        assert by_id[queued["transcription"]["id"]][0] == "expired"
        assert by_id[queued["transcription"]["id"]][3] == "timed_out"
        assert all(row[2] is not None for row in by_id.values())
        assert by_id[first["transcription"]["id"]][4:] == (
            None,
            "accepted",
        )
        assert by_id[second["transcription"]["id"]][4:] == (
            None,
            "expired",
        )
        assert by_id[queued["transcription"]["id"]][4:] == (
            None,
            "expired",
        )


@pytest.mark.parametrize(
    ("mode", "error_code"),
    [
        ("unauthorized", "ai_authentication_failed"),
        ("empty", "transcription_output_invalid"),
        ("control", "transcription_output_invalid"),
        ("too_long", "transcription_output_invalid"),
        ("oversized", "transcription_output_too_large"),
        ("malformed", "transcription_output_invalid"),
    ],
)
def test_terminal_provider_failure_releases_quota_and_deletes_audio(
    transcription_client,
    transcription_database_url,
    transcription_upstream,
    mode,
    error_code,
):
    create_account(
        transcription_client,
        transcription_database_url,
    )
    state, origin = transcription_upstream
    state.reset(mode)
    api_support.publish_transcription_route(
        transcription_database_url,
        fallback_price=None,
        primary_endpoint=f"{origin}/v1",
    )
    accepted = api_support.upload(
        transcription_client,
        wav_bytes(duration_ms=500),
    ).json()

    assert run_worker_once(transcription_database_url)
    result = transcription_client.get(
        f"/api/v1/voice-transcriptions/{accepted['transcription']['id']}"
    ).json()
    assert result["job"]["status"] == "failed"
    assert result["job"]["error"]["code"] == error_code
    assert result["transcription"]["transcript"] is None
    with psycopg.connect(
        psycopg_url(transcription_database_url)
    ) as connection:
        media = connection.execute(
            """
            SELECT encrypted_content, deletion_reason
            FROM temporary_media
            WHERE id=(SELECT input_media_id FROM ai_jobs WHERE id=%s)
            """,
            (accepted["job"]["id"],),
        ).fetchone()
        assert media == (None, "expired")
        assert connection.execute(
            """
            SELECT status, release_reason
            FROM ai_quota_reservations WHERE job_id=%s
            """,
            (accepted["job"]["id"],),
        ).fetchone() == ("released", "failed")
        cost = connection.execute(
            """
            SELECT usage_source, billing_basis, audio_duration_ms,
                   audio_cost_nanos, total_cost_nanos
            FROM ai_provider_costs WHERE job_id=%s
            """,
            (accepted["job"]["id"],),
        ).fetchone()
        assert cost == (
            "measured",
            "audio_minute",
            500,
            500_000,
            500_000,
        )


def test_corrupt_media_fails_before_request_with_zero_cost(
    transcription_client,
    transcription_database_url,
    transcription_upstream,
):
    create_account(
        transcription_client,
        transcription_database_url,
    )
    state, origin = transcription_upstream
    api_support.publish_transcription_route(
        transcription_database_url,
        fallback_price=None,
        primary_endpoint=f"{origin}/v1",
    )
    accepted = api_support.upload(transcription_client, wav_bytes()).json()
    with psycopg.connect(
        psycopg_url(transcription_database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            """
            UPDATE temporary_media
            SET content_sha256=%s
            WHERE id=(SELECT input_media_id FROM ai_jobs WHERE id=%s)
            """,
            ("f" * 64, accepted["job"]["id"]),
        )

    assert run_worker_once(transcription_database_url)
    result = transcription_client.get(
        f"/api/v1/voice-transcriptions/{accepted['transcription']['id']}"
    ).json()
    assert result["job"]["status"] == "failed"
    assert result["job"]["error"]["code"] == "temporary_media_corrupted"
    assert state.requests == []
    with psycopg.connect(
        psycopg_url(transcription_database_url)
    ) as connection:
        cost = connection.execute(
            """
            SELECT usage_source, provider_started_at, audio_cost_nanos,
                   total_cost_nanos
            FROM ai_provider_costs WHERE job_id=%s
            """,
            (accepted["job"]["id"],),
        ).fetchone()
        assert cost == ("not_sent", None, 0, 0)


def test_expired_queued_job_releases_reservation_and_audio_without_request(
    transcription_client,
    transcription_database_url,
    transcription_upstream,
):
    create_account(
        transcription_client,
        transcription_database_url,
    )
    state, origin = transcription_upstream
    api_support.publish_transcription_route(
        transcription_database_url,
        fallback_price=None,
        primary_endpoint=f"{origin}/v1",
    )
    accepted = api_support.upload(transcription_client, wav_bytes()).json()
    with psycopg.connect(
        psycopg_url(transcription_database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            """
            UPDATE ai_jobs
            SET deadline_at=now() - interval '1 second',
                available_at=now() - interval '1 second'
            WHERE id=%s
            """,
            (accepted["job"]["id"],),
        )

    assert run_worker_once(transcription_database_url)
    result = transcription_client.get(
        f"/api/v1/voice-transcriptions/{accepted['transcription']['id']}"
    ).json()
    assert result["job"]["status"] == "timed_out"
    assert result["job"]["error"]["code"] == "ai_job_deadline_exceeded"
    assert state.requests == []
    with psycopg.connect(
        psycopg_url(transcription_database_url)
    ) as connection:
        assert connection.execute(
            """
            SELECT encrypted_content, deletion_reason
            FROM temporary_media
            WHERE id=(SELECT input_media_id FROM ai_jobs WHERE id=%s)
            """,
            (accepted["job"]["id"],),
        ).fetchone() == (None, "expired")
        assert connection.execute(
            """
            SELECT status, release_reason
            FROM ai_quota_reservations WHERE job_id=%s
            """,
            (accepted["job"]["id"],),
        ).fetchone() == ("released", "timed_out")
        assert connection.execute(
            "SELECT count(*) FROM ai_provider_costs WHERE job_id=%s",
            (accepted["job"]["id"],),
        ).fetchone()[0] == 0


def test_timeout_retries_fallback_within_original_deadline(
    transcription_client,
    transcription_database_url,
    transcription_upstream,
):
    create_account(
        transcription_client,
        transcription_database_url,
    )
    state, origin = transcription_upstream
    state.reset("slow", "success")
    api_support.publish_transcription_route(
        transcription_database_url,
        primary_endpoint=f"{origin}/slow/v1",
        fallback_endpoint=f"{origin}/fallback/v1",
        primary_timeout_seconds=1,
    )
    accepted = api_support.upload(transcription_client, wav_bytes()).json()

    assert run_worker_once(transcription_database_url)
    make_due(transcription_database_url, accepted["job"]["id"])
    assert run_worker_once(transcription_database_url)
    result = transcription_client.get(
        f"/api/v1/voice-transcriptions/{accepted['transcription']['id']}"
    ).json()
    assert result["job"]["status"] == "succeeded"
    with psycopg.connect(
        psycopg_url(transcription_database_url)
    ) as connection:
        attempts = connection.execute(
            """
            SELECT status, error_code
            FROM ai_job_attempts
            WHERE job_id=%s ORDER BY attempt_number
            """,
            (accepted["job"]["id"],),
        ).fetchall()
        assert attempts == [
            ("retryable_failed", "ai_upstream_timeout"),
            ("succeeded", None),
        ]


def test_cancelled_request_cannot_publish_late_transcript(
    transcription_client,
    transcription_database_url,
    transcription_upstream,
):
    create_account(
        transcription_client,
        transcription_database_url,
    )
    state, origin = transcription_upstream
    state.reset("blocked")
    api_support.publish_transcription_route(
        transcription_database_url,
        fallback_price=None,
        primary_endpoint=f"{origin}/v1",
    )
    accepted = api_support.upload(transcription_client, wav_bytes()).json()
    job_id = accepted["job"]["id"]

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            run_worker_once,
            transcription_database_url,
        )
        assert state.received.wait(timeout=5)
        cancelled = transcription_client.post(
            f"/api/v1/ai/jobs/{job_id}/cancel",
            json={"operation_id": str(uuid4())},
        )
        assert cancelled.status_code == 200, cancelled.text
        state.release.set()
        assert future.result(timeout=10)

    result = transcription_client.get(
        "/api/v1/voice-transcriptions/"
        + accepted["transcription"]["id"]
    ).json()
    assert result["job"]["status"] == "cancelled"
    assert result["job"]["output"] is None
    assert result["transcription"]["transcript"] is None
    assert result["transcription"]["resolution"] == "discarded"
    assert result["transcription"]["text_purged_at"] is not None
    with psycopg.connect(
        psycopg_url(transcription_database_url)
    ) as connection:
        assert connection.execute(
            """
            SELECT attempt_status, usage_source, billing_basis
            FROM ai_provider_costs WHERE job_id=%s
            """,
            (job_id,),
        ).fetchone() == ("cancelled", "measured", "audio_minute")
        assert connection.execute(
            """
            SELECT status, release_reason
            FROM ai_quota_reservations WHERE job_id=%s
            """,
            (job_id,),
        ).fetchone() == ("released", "cancelled")
        assert connection.execute(
            """
            SELECT encrypted_content, deletion_reason
            FROM temporary_media
            WHERE id=(SELECT input_media_id FROM ai_jobs WHERE id=%s)
            """,
            (job_id,),
        ).fetchone() == (None, "cancelled")


def test_quality_opt_out_keeps_technical_log_without_sample(
    transcription_client,
    transcription_database_url,
    transcription_upstream,
):
    _, owner_id = create_account(
        transcription_client,
        transcription_database_url,
    )
    state, origin = transcription_upstream
    state.reset("success")
    api_support.publish_transcription_route(
        transcription_database_url,
        fallback_price=None,
        primary_endpoint=f"{origin}/v1",
    )
    with psycopg.connect(
        psycopg_url(transcription_database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            """
            UPDATE user_privacy_settings
            SET quality_improvement_enabled=false, updated_at=now()
            WHERE owner_id=%s
            """,
            (owner_id,),
        )
    accepted = api_support.upload(transcription_client, wav_bytes()).json()

    assert run_worker_once(transcription_database_url)
    completed = transcription_client.get(
        "/api/v1/voice-transcriptions/"
        + accepted["transcription"]["id"]
    ).json()
    assert completed["job"]["status"] == "succeeded"
    with psycopg.connect(
        psycopg_url(transcription_database_url)
    ) as connection:
        assert connection.execute(
            "SELECT count(*) FROM ai_body_logs WHERE job_id=%s",
            (accepted["job"]["id"],),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM ai_quality_samples WHERE source_job_id=%s",
            (accepted["job"]["id"],),
        ).fetchone()[0] == 0
