import hashlib
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from uuid import uuid4

import httpx
import psycopg
import pytest
from cryptography.fernet import Fernet

from itime.config import Settings
from tests.conftest import SERVER, LiveServer, isolated_database, psycopg_url
from tests.quota_helpers import ensure_quota_policy
from tests.test_audio_validation import wav_bytes
from tests.test_calendar_series import create_account, login_account

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def transcription_database_url(admin_url: str) -> Iterator[str]:
    with isolated_database(admin_url) as database_url:
        yield database_url


@pytest.fixture(scope="session")
def transcription_live_server(
    transcription_database_url: str,
    tmp_path_factory,
) -> Iterator[LiveServer]:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    log_path = tmp_path_factory.mktemp("transcription-api") / "server.log"
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
                "ITIME_DATABASE_URL": transcription_database_url,
                "ITIME_MAX_REQUEST_BYTES": str(3 * 1024 * 1024),
                "ITIME_AI_TRANSCRIPTION_QUEUE_LIMIT": "4",
            },
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 15
            with httpx.Client(base_url=origin, timeout=1, trust_env=False) as probe:
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        pytest.fail(f"转写测试 API 启动失败，请检查 {log_path}")
                    try:
                        if probe.get("/api/ready").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.05)
                else:
                    pytest.fail(f"转写测试 API 启动超时，请检查 {log_path}")
            yield LiveServer(origin, log_path)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@pytest.fixture
def transcription_client(transcription_live_server: LiveServer):
    with httpx.Client(
        base_url=transcription_live_server.origin,
        timeout=15,
        trust_env=False,
    ) as client:
        yield client


@pytest.fixture(autouse=True)
def reset_transcriptions(transcription_database_url: str):
    with psycopg.connect(
        psycopg_url(transcription_database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            "DELETE FROM user_operations "
            "WHERE scope='voice_transcription.upload'"
        )
        connection.execute("DELETE FROM ai_quota_ledger_entries")
        connection.execute("DELETE FROM ai_quota_reservations")
        connection.execute("DELETE FROM ai_body_logs")
        connection.execute("DELETE FROM ai_quality_samples")
        connection.execute("DELETE FROM ai_provider_costs")
        connection.execute("DELETE FROM ai_job_attempts")
        connection.execute("DELETE FROM voice_transcriptions")
        connection.execute("DELETE FROM ai_jobs")
        connection.execute("DELETE FROM temporary_media")
        connection.execute(
            "DELETE FROM ai_config_fallbacks WHERE capability='transcription'"
        )
        connection.execute(
            """
            UPDATE ai_config_capabilities
            SET current_version_id=NULL, next_version=1,
                routing_version=routing_version + 1, updated_at=now()
            WHERE capability='transcription'
            """
        )
        connection.execute(
            "DELETE FROM ai_config_versions WHERE capability='transcription'"
        )


def publish_transcription_route(
    database_url: str,
    *,
    primary_price: int = 60_000_000,
    fallback_price: int | None = 120_000_000,
) -> tuple[str, str | None]:
    settings = Settings(database_url=database_url, environment="test")
    cipher = Fernet(
        (settings.ai_encryption_key or settings.encryption_key).get_secret_value()
    )
    primary_id = uuid4()
    fallback_id = uuid4() if fallback_price is not None else None
    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            """
            INSERT INTO ai_config_versions (
                id, capability, version, endpoint_url, model,
                api_key_ciphertext, timeout_seconds, max_output_tokens,
                pricing_basis, price_currency, audio_price_nanos_per_minute,
                status, last_test_id, last_test_started_at,
                last_test_completed_at, published_at
            ) VALUES (
                %s, 'transcription', 1, 'https://transcribe.example.com/v1',
                'transcribe-primary', %s, 30, NULL,
                'audio_minute', 'CNY', %s,
                'published', %s, now(), now(), now()
            )
            """,
            (
                primary_id,
                cipher.encrypt(b"transcription-primary-key"),
                primary_price,
                uuid4(),
            ),
        )
        if fallback_id is not None:
            connection.execute(
                """
                INSERT INTO ai_config_versions (
                    id, capability, version, endpoint_url, model,
                    api_key_ciphertext, timeout_seconds, max_output_tokens,
                    pricing_basis, price_currency,
                    audio_price_nanos_per_minute, status, last_test_id,
                    last_test_started_at, last_test_completed_at
                ) VALUES (
                    %s, 'transcription', 2,
                    'https://transcribe-fallback.example.com/v1',
                    'transcribe-fallback', %s, 30, NULL,
                    'audio_minute', 'CNY', %s,
                    'tested', %s, now(), now()
                )
                """,
                (
                    fallback_id,
                    cipher.encrypt(b"transcription-fallback-key"),
                    fallback_price,
                    uuid4(),
                ),
            )
            connection.execute(
                """
                INSERT INTO ai_config_fallbacks (
                    capability, position, config_version_id
                ) VALUES ('transcription', 1, %s)
                """,
                (fallback_id,),
            )
        connection.execute(
            """
            UPDATE ai_config_capabilities
            SET current_version_id=%s, next_version=%s,
                routing_version=routing_version + 1, updated_at=now()
            WHERE capability='transcription'
            """,
            (primary_id, 3 if fallback_id is not None else 2),
        )
    ensure_quota_policy(database_url)
    return str(primary_id), str(fallback_id) if fallback_id is not None else None


def upload(
    client: httpx.Client,
    contents: bytes,
    *,
    operation_id=None,
    purpose: str = "task_title",
    filename: str = "voice.wav",
    content_type: str = "audio/wav",
) -> httpx.Response:
    return client.post(
        "/api/v1/voice-transcriptions",
        params={"purpose": purpose, "filename": filename},
        headers={
            "X-Operation-ID": str(operation_id or uuid4()),
            "Content-Type": content_type,
        },
        content=contents,
    )


def assert_no_admission_rows(database_url: str, owner_id: str) -> None:
    with psycopg.connect(psycopg_url(database_url)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM temporary_media WHERE owner_id=%s",
            (owner_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM ai_jobs WHERE owner_id=%s",
            (owner_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM voice_transcriptions WHERE owner_id=%s",
            (owner_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM ai_quota_reservations WHERE owner_id=%s",
            (owner_id,),
        ).fetchone()[0] == 0


def finish_job_without_charge(database_url: str, job_id: str) -> None:
    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            """
            UPDATE ai_jobs
            SET status='cancelled', finished_at=now(), updated_at=now()
            WHERE id=%s
            """,
            (job_id,),
        )
        connection.execute(
            """
            UPDATE ai_quota_reservations
            SET status='released', terminal_at=now(),
                release_reason='cancelled', updated_at=now()
            WHERE job_id=%s
            """,
            (job_id,),
        )


def seed_global_active_jobs(
    database_url: str,
    config_id: str,
    *,
    count: int,
) -> None:
    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,
    ) as connection:
        for index in range(count):
            owner_id = uuid4()
            media_id = uuid4()
            job_id = uuid4()
            transcription_id = uuid4()
            connection.execute(
                """
                INSERT INTO users (
                    id, email, password_hash, email_verified_at
                ) VALUES (%s, %s, 'seeded-hash', now())
                """,
                (owner_id, f"queue-{uuid4().hex}@example.com"),
            )
            connection.execute(
                """
                INSERT INTO temporary_media (
                    id, owner_id, purpose, original_filename, content_type,
                    content_sha256, byte_size, width, height, duration_ms,
                    sample_rate_hz, channel_count, sample_width_bits,
                    encrypted_content, expires_at
                ) VALUES (
                    %s, %s, 'voice_transcription', %s, 'audio/wav',
                    %s, 8044, NULL, NULL, 250, 16000, 1, 16,
                    %s, now() + interval '1 day'
                )
                """,
                (
                    media_id,
                    owner_id,
                    f"queued-{index}.wav",
                    hashlib.sha256(str(media_id).encode()).hexdigest(),
                    b"encrypted-audio",
                ),
            )
            connection.execute(
                """
                INSERT INTO ai_jobs (
                    id, owner_id, capability, config_version_id,
                    input_media_id, quota_mode, input_summary,
                    available_at, deadline_at
                ) VALUES (
                    %s, %s, 'transcription', %s, %s, 'platform',
                    '{"purpose": "task_title"}', now(), now() + interval '3 minutes'
                )
                """,
                (job_id, owner_id, config_id, media_id),
            )
            connection.execute(
                """
                INSERT INTO voice_transcriptions (
                    id, owner_id, job_id, media_id, purpose, expires_at
                ) VALUES (
                    %s, %s, %s, %s, 'task_title', now() + interval '1 day'
                )
                """,
                (transcription_id, owner_id, job_id, media_id),
            )


def test_upload_is_atomic_replayable_queryable_and_owner_scoped(
    transcription_client,
    transcription_database_url,
    transcription_live_server,
):
    _, owner_id = create_account(
        transcription_client,
        transcription_database_url,
    )
    primary_id, fallback_id = publish_transcription_route(
        transcription_database_url
    )
    contents = wav_bytes(duration_ms=1000)
    operation_id = uuid4()

    response = upload(
        transcription_client,
        contents,
        operation_id=operation_id,
    )
    assert response.status_code == 202, response.text
    body = response.json()
    item = body["transcription"]
    job = body["job"]
    assert body["operation_id"] == str(operation_id)
    assert item["purpose"] == "task_title"
    assert item["resolution"] == "pending"
    assert item["transcript"] is None
    assert item["job_id"] == job["id"]
    assert job["status"] == "queued"
    assert job["input_summary"] == {
        "purpose": "task_title",
        "filename": "voice.wav",
        "content_type": "audio/wav",
        "byte_size": len(contents),
        "duration_ms": 1000,
        "sample_rate_hz": 16000,
        "channel_count": 1,
        "sample_width_bits": 16,
        "content_sha256": hashlib.sha256(contents).hexdigest(),
    }

    replay = upload(
        transcription_client,
        contents,
        operation_id=operation_id,
    )
    assert replay.status_code == 202
    assert replay.json() == body
    changed = upload(
        transcription_client,
        wav_bytes(duration_ms=1100),
        operation_id=operation_id,
    )
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "operation_id_reused"

    with psycopg.connect(
        psycopg_url(transcription_database_url)
    ) as connection:
        stored = connection.execute(
            """
            SELECT media.encrypted_content, media.content_sha256,
                   media.duration_ms, media.sample_rate_hz,
                   media.channel_count, media.sample_width_bits,
                   reservation.reserved_nanos,
                   array_agg(route.config_version_id ORDER BY route.attempt_number)
            FROM voice_transcriptions AS transcription
            JOIN temporary_media AS media
              ON media.id=transcription.media_id
            JOIN ai_quota_reservations AS reservation
              ON reservation.job_id=transcription.job_id
            JOIN ai_job_config_routes AS route
              ON route.job_id=transcription.job_id
            WHERE transcription.id=%s
            GROUP BY media.encrypted_content, media.content_sha256,
                     media.duration_ms, media.sample_rate_hz,
                     media.channel_count, media.sample_width_bits,
                     reservation.reserved_nanos
            """,
            (item["id"],),
        ).fetchone()
        assert bytes(stored[0]) != contents
        assert stored[1:6] == (
            hashlib.sha256(contents).hexdigest(),
            1000,
            16000,
            1,
            16,
        )
        assert stored[6] == 2_000_000
        assert [str(value) for value in stored[7]] == [
            primary_id,
            fallback_id,
            fallback_id,
        ]
        assert connection.execute(
            "SELECT count(*) FROM temporary_media WHERE owner_id=%s",
            (owner_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM ai_jobs WHERE owner_id=%s",
            (owner_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM voice_transcriptions WHERE owner_id=%s",
            (owner_id,),
        ).fetchone()[0] == 1

    second = upload(
        transcription_client,
        wav_bytes(duration_ms=500),
        purpose="difficulty_content",
    )
    assert second.status_code == 202, second.text
    first_page = transcription_client.get(
        "/api/v1/voice-transcriptions",
        params={"limit": 1},
    )
    assert first_page.status_code == 200
    first_page_body = first_page.json()
    assert len(first_page_body["items"]) == 1
    assert first_page_body["next_cursor"] is not None
    second_page = transcription_client.get(
        "/api/v1/voice-transcriptions",
        params={
            "limit": 1,
            **first_page_body["next_cursor"],
        },
    )
    assert second_page.status_code == 200
    listed_ids = {
        first_page_body["items"][0]["transcription"]["id"],
        second_page.json()["items"][0]["transcription"]["id"],
    }
    assert listed_ids == {item["id"], second.json()["transcription"]["id"]}
    filtered = transcription_client.get(
        "/api/v1/voice-transcriptions",
        params={"purpose": "difficulty_content"},
    )
    assert [
        row["transcription"]["id"] for row in filtered.json()["items"]
    ] == [second.json()["transcription"]["id"]]
    detail = transcription_client.get(
        f"/api/v1/voice-transcriptions/{item['id']}"
    )
    assert detail.status_code == 200
    assert detail.json()["job"]["id"] == job["id"]

    with httpx.Client(
        base_url=transcription_live_server.origin,
        timeout=15,
        trust_env=False,
    ) as other:
        create_account(other, transcription_database_url)
        assert other.get(
            f"/api/v1/voice-transcriptions/{item['id']}"
        ).status_code == 404
        assert other.get("/api/v1/voice-transcriptions").json()["items"] == []


def test_upload_prerequisites_fail_without_persisting_audio(
    transcription_client,
    transcription_database_url,
    transcription_live_server,
):
    with httpx.Client(
        base_url=transcription_live_server.origin,
        timeout=15,
        trust_env=False,
    ) as anonymous:
        unauthenticated = upload(
            anonymous,
            b"not-a-wave",
        )
    assert unauthenticated.status_code == 401
    assert unauthenticated.json()["error"]["code"] == "unauthenticated"

    _, owner_id = create_account(
        transcription_client,
        transcription_database_url,
    )
    invalid = upload(transcription_client, b"not-a-wave")
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "invalid_audio"
    assert_no_admission_rows(transcription_database_url, owner_id)

    unavailable = upload(transcription_client, wav_bytes())
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "ai_capability_unavailable"
    assert_no_admission_rows(transcription_database_url, owner_id)

    publish_transcription_route(transcription_database_url)
    with psycopg.connect(
        psycopg_url(transcription_database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            "DELETE FROM user_time_settings WHERE owner_id=%s",
            (owner_id,),
        )
    missing_time_zone = upload(transcription_client, wav_bytes())
    assert missing_time_zone.status_code == 409
    assert missing_time_zone.json()["error"]["code"] == "time_settings_required"
    assert_no_admission_rows(transcription_database_url, owner_id)

    with psycopg.connect(
        psycopg_url(transcription_database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            """
            INSERT INTO user_time_settings (owner_id, time_zone)
            VALUES (%s, 'Asia/Shanghai')
            """,
            (owner_id,),
        )
    ensure_quota_policy(
        transcription_database_url,
        daily_allowance_nanos=1,
    )
    exhausted = upload(transcription_client, wav_bytes())
    assert exhausted.status_code == 429
    assert exhausted.json()["error"]["code"] == "ai_quota_exhausted"
    assert_no_admission_rows(transcription_database_url, owner_id)

    with psycopg.connect(
        psycopg_url(transcription_database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            "UPDATE users SET ai_access='frozen' WHERE id=%s",
            (owner_id,),
        )
    frozen = upload(transcription_client, wav_bytes())
    assert frozen.status_code == 403
    assert frozen.json()["error"]["code"] == "ai_access_frozen"
    assert_no_admission_rows(transcription_database_url, owner_id)


def test_same_operation_concurrently_creates_one_graph(
    transcription_database_url,
    transcription_live_server,
):
    with httpx.Client(
        base_url=transcription_live_server.origin,
        timeout=15,
        trust_env=False,
    ) as setup_client:
        email, owner_id = create_account(
            setup_client,
            transcription_database_url,
        )
    publish_transcription_route(transcription_database_url)
    operation_id = uuid4()
    contents = wav_bytes()

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
            responses = list(
                pool.map(
                    lambda client: upload(
                        client,
                        contents,
                        operation_id=operation_id,
                    ),
                    clients,
                )
            )

    assert [response.status_code for response in responses] == [202, 202]
    assert responses[0].json() == responses[1].json()
    with psycopg.connect(
        psycopg_url(transcription_database_url)
    ) as connection:
        assert connection.execute(
            "SELECT count(*) FROM temporary_media WHERE owner_id=%s",
            (owner_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM ai_jobs WHERE owner_id=%s",
            (owner_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM voice_transcriptions WHERE owner_id=%s",
            (owner_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM ai_quota_reservations WHERE owner_id=%s",
            (owner_id,),
        ).fetchone()[0] == 1


def test_account_active_and_rolling_admission_limits(
    transcription_client,
    transcription_database_url,
):
    _, owner_id = create_account(
        transcription_client,
        transcription_database_url,
    )
    publish_transcription_route(
        transcription_database_url,
        primary_price=1,
        fallback_price=None,
    )
    for _ in range(3):
        accepted = upload(transcription_client, wav_bytes(duration_ms=250))
        assert accepted.status_code == 202, accepted.text
    active_limited = upload(
        transcription_client,
        wav_bytes(duration_ms=250),
    )
    assert active_limited.status_code == 429
    assert active_limited.json()["error"]["code"] == "ai_job_limit_reached"
    with psycopg.connect(
        psycopg_url(transcription_database_url),
        autocommit=True,
    ) as connection:
        rows = connection.execute(
            "SELECT id FROM ai_jobs WHERE owner_id=%s",
            (owner_id,),
        ).fetchall()
    for row in rows:
        finish_job_without_charge(transcription_database_url, str(row[0]))

    for _ in range(17):
        accepted = upload(transcription_client, wav_bytes(duration_ms=250))
        assert accepted.status_code == 202, accepted.text
        finish_job_without_charge(
            transcription_database_url,
            accepted.json()["job"]["id"],
        )
    rate_limited = upload(
        transcription_client,
        wav_bytes(duration_ms=250),
    )
    assert rate_limited.status_code == 429
    assert (
        rate_limited.json()["error"]["code"]
        == "voice_transcription_rate_limit_reached"
    )
    with psycopg.connect(
        psycopg_url(transcription_database_url)
    ) as connection:
        assert connection.execute(
            """
            SELECT count(*) FROM voice_transcriptions
            WHERE owner_id=%s
              AND created_at >= now() - interval '10 minutes'
            """,
            (owner_id,),
        ).fetchone()[0] == 20


def test_deployment_queue_limit_rejects_before_media_is_saved(
    transcription_client,
    transcription_database_url,
):
    _, owner_id = create_account(
        transcription_client,
        transcription_database_url,
    )
    primary_id, _ = publish_transcription_route(
        transcription_database_url,
        fallback_price=None,
    )
    seed_global_active_jobs(
        transcription_database_url,
        primary_id,
        count=4,
    )

    limited = upload(transcription_client, wav_bytes())
    assert limited.status_code == 503
    assert limited.json()["error"]["code"] == "ai_transcription_queue_full"
    assert_no_admission_rows(transcription_database_url, owner_id)
