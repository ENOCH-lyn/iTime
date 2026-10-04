import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest
from sqlalchemy import text

from itime.calendar.subscription_service import (
    encrypt_subscription_url,
    subscription_url_hmac,
)
from itime.config import Settings
from itime.data_export.snapshot import (
    collect_export_snapshot,
    read_consistent_snapshot,
)
from itime.database import Database
from tests.conftest import psycopg_url
from tests.test_tasks import PASSWORD, browser, create_account, task_payload

pytestmark = pytest.mark.integration

FORBIDDEN_KEYS = {
    "password_hash",
    "token_hash",
    "csrf_token",
    "api_key_ciphertext",
    "encrypted_content",
    "encrypted_url",
    "encrypted_final_url",
    "url_hmac",
    "final_url_hmac",
    "lease_id",
    "lease_until",
    "request_body",
    "response_body",
    "provider_request_id",
    "request_hash",
}


def recursive_keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {
            key
            for item in value.values()
            for key in recursive_keys(item)
        }
    if isinstance(value, list):
        return {key for item in value for key in recursive_keys(item)}
    return set()


def seed_export_facts(
    database_url: str,
    settings: Settings,
    owner_id: UUID,
) -> dict[str, str]:
    marker = f"owner-{uuid4().hex}"
    source_id = uuid4()
    subscription_event_id = uuid4()
    subscription_exception_id = uuid4()
    conversation_id = uuid4()
    focus_id = uuid4()
    memory_id = uuid4()
    subscription_url = f"https://calendar.example/{uuid4().hex}?token=secret"
    current = datetime.now(UTC)
    with psycopg.connect(psycopg_url(database_url)) as connection:
        connection.execute(
            """
            INSERT INTO calendar_sources (id, owner_id, kind, name, version)
            VALUES (%s, %s, 'ics_subscription', %s, 1)
            """,
            (source_id, owner_id, marker),
        )
        connection.execute(
            """
            INSERT INTO calendar_subscriptions (
                source_id, owner_id, encrypted_url, url_hmac,
                display_url, display_host, default_time_zone,
                lifecycle_status, refresh_status, refresh_interval_minutes,
                next_refresh_at, fetch_revision, generation,
                attempt_count, consecutive_failures, version
            ) VALUES (
                %s, %s, %s, %s,
                'https://calendar.example/…', 'calendar.example',
                'Asia/Shanghai', 'active', 'idle', 360,
                %s, 1, 0, 0, 0, 1
            )
            """,
            (
                source_id,
                owner_id,
                encrypt_subscription_url(settings, subscription_url),
                subscription_url_hmac(settings, subscription_url),
                current + timedelta(hours=6),
            ),
        )
        connection.execute(
            """
            INSERT INTO calendar_subscription_events (
                id, source_id, owner_id, uid, recurrence_id,
                baseline, content_sha256, first_seen_generation,
                last_seen_generation
            ) VALUES (
                %s, %s, %s, 'export-event', '',
                %s::jsonb, %s, 1, 1
            )
            """,
            (
                subscription_event_id,
                source_id,
                owner_id,
                json.dumps(
                    {
                        "name": marker,
                        "description": "",
                        "location": "",
                        "busy": True,
                        "status": "confirmed",
                        "start": {
                            "kind": "date-time",
                            "semantic": "utc",
                            "local": "2026-10-05T09:00:00",
                            "time_zone": "UTC",
                        },
                        "duration": {"kind": "seconds", "value": 3600},
                        "rrule": "FREQ=DAILY;COUNT=2",
                        "rdates": [],
                        "exdates": [],
                    }
                ),
                "c" * 64,
            ),
        )
        connection.execute(
            """
            INSERT INTO calendar_subscription_exceptions (
                id, owner_id, master_event_id, source_occurrence_key,
                original_start_at, original_end_at, original_all_day,
                action, override_name, version
            ) VALUES (
                %s, %s, %s, 'INSTANT:2026-10-06T09:00:00Z',
                '2026-10-06T09:00:00Z', '2026-10-06T10:00:00Z',
                false, 'overridden', '本人单次名称', 1
            )
            """,
            (subscription_exception_id, owner_id, subscription_event_id),
        )
        connection.execute(
            """
            INSERT INTO ai_conversations (
                id, owner_id, title, title_source, version
            ) VALUES (%s, %s, %s, 'user', 1)
            """,
            (conversation_id, owner_id, marker),
        )
        connection.execute(
            """
            INSERT INTO ai_conversation_messages (
                id, owner_id, conversation_id, sequence, role, status,
                content, completed_at
            ) VALUES (%s, %s, %s, 1, 'user', 'complete', %s, %s)
            """,
            (uuid4(), owner_id, conversation_id, marker, current),
        )
        connection.execute(
            """
            INSERT INTO focus_sessions (
                id, owner_id, source, title_snapshot, step_snapshots,
                mode, status, started_at, ended_at, accumulated_seconds,
                effective_started_at, effective_ended_at,
                effective_duration_seconds, time_zone, version
            ) VALUES (
                %s, %s, 'manual', %s, '[]',
                'stopwatch', 'finished', %s, %s, 1200,
                %s, %s, 1200, 'Asia/Shanghai', 1
            )
            """,
            (
                focus_id,
                owner_id,
                marker,
                current - timedelta(minutes=20),
                current,
                current - timedelta(minutes=20),
                current,
            ),
        )
        connection.execute(
            """
            INSERT INTO memories (
                id, owner_id, kind, title, content, source_type, version
            ) VALUES (%s, %s, 'context', %s, %s, 'manual', 1)
            """,
            (memory_id, owner_id, marker, f"{marker}-memory"),
        )
        connection.commit()
    return {
        "marker": marker,
        "source_id": str(source_id),
        "subscription_url": subscription_url,
    }


def seed_export_transcriptions(
    database_url: str,
    owner_id: UUID,
) -> tuple[str, str]:
    config_id = uuid4()
    current_id = uuid4()
    expired_id = uuid4()
    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,
    ) as connection:
        version = connection.execute(
            """
            UPDATE ai_config_capabilities
            SET next_version=next_version + 1, updated_at=now()
            WHERE capability='transcription'
            RETURNING next_version - 1
            """
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO ai_config_versions (
                id, capability, version, endpoint_url, model,
                api_key_ciphertext, timeout_seconds, max_output_tokens,
                pricing_basis, price_currency,
                audio_price_nanos_per_minute, status
            ) VALUES (
                %s, 'transcription', %s, 'https://ai.example.com/v1',
                'export-transcription', %s, 60, NULL,
                'audio_minute', 'CNY', 6000000, 'draft'
            )
            """,
            (config_id, version, b"encrypted-key"),
        )
        for item_id, expired, transcript in (
            (current_id, False, "仍在保留期内的转写"),
            (expired_id, True, "已经过期且不能导出的转写"),
        ):
            media_id = uuid4()
            job_id = uuid4()
            connection.execute(
                """
                INSERT INTO temporary_media (
                    id, owner_id, purpose, original_filename, content_type,
                    content_sha256, byte_size, width, height,
                    duration_ms, sample_rate_hz, channel_count,
                    sample_width_bits, encrypted_content, expires_at,
                    created_at
                ) VALUES (
                    %s, %s, 'voice_transcription', 'export.wav', 'audio/wav',
                    %s, 32044, NULL, NULL, 1000, 16000, 1, 16,
                    %s,
                    CASE WHEN %s THEN now() - interval '1 hour'
                         ELSE now() + interval '1 hour' END,
                    CASE WHEN %s THEN now() - interval '2 hours'
                         ELSE now() END
                )
                """,
                (
                    media_id,
                    owner_id,
                    hashlib.sha256(str(media_id).encode()).hexdigest(),
                    b"private-encrypted-audio",
                    expired,
                    expired,
                ),
            )
            connection.execute(
                """
                INSERT INTO ai_jobs (
                    id, owner_id, capability, config_version_id,
                    input_media_id, status, input_summary, output,
                    available_at, deadline_at, finished_at, created_at,
                    updated_at
                ) VALUES (
                    %s, %s, 'transcription', %s, %s, 'succeeded',
                    '{"purpose":"task_title","duration_ms":1000}',
                    %s::jsonb, now(), now() + interval '3 minutes', now(),
                    CASE WHEN %s THEN now() - interval '2 hours'
                         ELSE now() END,
                    now()
                )
                """,
                (
                    job_id,
                    owner_id,
                    config_id,
                    media_id,
                    json.dumps(
                        {
                            "resource_type": "voice_transcription",
                            "transcription_id": str(item_id),
                        }
                    ),
                    expired,
                ),
            )
            connection.execute(
                """
                INSERT INTO voice_transcriptions (
                    id, owner_id, job_id, media_id, purpose,
                    transcript, transcript_sha256, language, ready_at,
                    expires_at, created_at, updated_at
                ) VALUES (
                    %s, %s, %s, %s, 'task_title',
                    %s, %s, 'zh', now(),
                    CASE WHEN %s THEN now() - interval '1 hour'
                         ELSE now() + interval '1 hour' END,
                    CASE WHEN %s THEN now() - interval '2 hours'
                         ELSE now() END,
                    now()
                )
                """,
                (
                    item_id,
                    owner_id,
                    job_id,
                    media_id,
                    transcript,
                    hashlib.sha256(transcript.encode()).hexdigest(),
                    expired,
                    expired,
                ),
            )
    return str(current_id), str(expired_id)


def read_snapshot(settings: Settings, owner_id: UUID) -> dict:
    database = Database(settings)

    async def execute() -> dict:
        try:
            return await read_consistent_snapshot(database, owner_id, settings)
        finally:
            await database.close()

    return asyncio.run(execute())


def test_snapshot_covers_user_data_and_excludes_secrets(
    client,
    database_url,
    settings,
    live_server,
):
    _, owner_id_value = create_account(client, database_url)
    owner_id = UUID(owner_id_value)
    created_task = client.post(
        "/api/v1/tasks",
        json=task_payload(title="导出任务"),
    )
    assert created_task.status_code == 201, created_task.text
    created_link = created_task.json()["task"]["links"][0]
    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            """
            UPDATE task_links
            SET fetch_status='ready',
                attempt_count=1,
                last_attempt_at=now(),
                last_success_at=now(),
                final_host='export.example',
                media_type='text/html',
                page_title='导出页面',
                summary='导出摘要',
                extracted_text='导出网页正文',
                content_sha256=%s,
                updated_at=now()
            WHERE id=%s
            """,
            ("a" * 64, created_link["id"]),
        )
    facts = seed_export_facts(database_url, settings, owner_id)

    with browser(live_server) as other:
        _, other_id = create_account(other, database_url)
        hidden_marker = f"other-{uuid4().hex}"
        assert other.post(
            "/api/v1/tasks",
            json=task_payload(title=hidden_marker),
        ).status_code == 201

    payload = read_snapshot(settings, owner_id)
    assert set(payload) == {
        "schema_version",
        "snapshot_at",
        "account",
        "settings",
        "tasks",
        "calendar",
        "conversations",
        "planning",
        "execution",
        "reviews",
        "quota",
        "media",
    }
    assert payload["schema_version"] == 1
    assert payload["account"]["profile"]["id"] == owner_id_value
    assert payload["tasks"]["tasks"][0]["title"] == "导出任务"
    assert payload["tasks"]["links"][0]["summary"] == "导出摘要"
    assert payload["tasks"]["links"][0]["extracted_text"] == "导出网页正文"
    assert payload["tasks"]["links"][0]["content_sha256"] == "a" * 64
    assert payload["calendar"]["subscriptions"][0]["url"] == facts["subscription_url"]
    assert payload["calendar"]["subscription_exceptions"] == [
        {
            "id": payload["calendar"]["subscription_exceptions"][0]["id"],
            "master_event_id": payload["calendar"]["subscription_events"][0]["id"],
            "source_occurrence_key": "INSTANT:2026-10-06T09:00:00Z",
            "original_start_at": "2026-10-06T09:00:00Z",
            "original_end_at": "2026-10-06T10:00:00Z",
            "original_all_day": False,
            "action": "overridden",
            "moved_all_day": None,
            "moved_start_date": None,
            "moved_end_date": None,
            "moved_starts_at": None,
            "moved_ends_at": None,
            "moved_time_zone": None,
            "moved_ambiguous_offset_minutes": None,
            "moved_start_at": None,
            "moved_end_at": None,
            "override_name": "本人单次名称",
            "override_description": None,
            "override_location": None,
            "override_busy": None,
            "version": 1,
            "created_at": payload["calendar"]["subscription_exceptions"][0][
                "created_at"
            ],
            "updated_at": payload["calendar"]["subscription_exceptions"][0][
                "updated_at"
            ],
        }
    ]
    assert payload["conversations"]["messages"][0]["content"] == facts["marker"]
    assert payload["execution"]["focus_sessions"][0]["title_snapshot"] == facts["marker"]
    assert payload["reviews"]["memories"][0]["content"] == f"{facts['marker']}-memory"
    assert payload["quota"]["ledger"] == []
    assert payload["media"]["temporary_media"] == []
    rendered = json.dumps(payload, ensure_ascii=False)
    assert hidden_marker not in rendered
    assert other_id not in rendered
    assert not recursive_keys(payload) & FORBIDDEN_KEYS


def test_snapshot_includes_only_public_cancelled_deletion_history(
    client,
    database_url,
    settings,
):
    email, owner_id_value = create_account(client, database_url)
    requested = client.post(
        "/api/v1/account-deletion",
        json={
            "operation_id": str(uuid4()),
            "email_confirmation": email,
            "current_password": PASSWORD,
        },
    )
    assert requested.status_code == 202, requested.text
    pending = requested.json()["deletion"]
    cancelled = client.post(
        "/api/v1/account-deletion/cancel",
        json={
            "email": email,
            "password": PASSWORD,
            "device_name": "导出注销历史",
        },
    )
    assert cancelled.status_code == 200, cancelled.text

    payload = read_snapshot(
        settings,
        UUID(owner_id_value),
    )
    history = payload["account"]["account_deletions"]
    assert len(history) == 1
    assert set(history[0]) == {
        "id",
        "status",
        "requested_at",
        "purge_after",
        "cancelled_at",
        "created_at",
        "updated_at",
    }
    assert history[0]["id"] == pending["id"]
    assert history[0]["status"] == "cancelled"
    assert history[0]["cancelled_at"] is not None
    rendered = json.dumps(history, ensure_ascii=False)
    for forbidden in (
        "subject_digest",
        "operation_id",
        "request_id",
        "lease_id",
        "lease_until",
        "last_error_code",
        "last_error_message",
    ):
        assert forbidden not in rendered


def test_snapshot_exports_only_unexpired_transcription_text_and_no_audio(
    client,
    database_url,
    settings,
):
    _, owner_id_value = create_account(client, database_url)
    owner_id = UUID(owner_id_value)
    current_id, expired_id = seed_export_transcriptions(
        database_url,
        owner_id,
    )

    payload = read_snapshot(settings, owner_id)

    transcriptions = payload["media"]["voice_transcriptions"]
    assert [item["id"] for item in transcriptions] == [current_id]
    assert transcriptions[0]["transcript"] == "仍在保留期内的转写"
    assert transcriptions[0]["purpose"] == "task_title"
    assert [
        item["capability"]
        for item in payload["media"]["transcription_jobs"]
    ] == ["transcription", "transcription"]
    media = payload["media"]["temporary_media"]
    assert {item["duration_ms"] for item in media} == {1000}
    rendered = json.dumps(payload, ensure_ascii=False)
    assert "private-encrypted-audio" not in rendered
    assert "已经过期且不能导出的转写" not in rendered
    assert expired_id not in {
        item["id"] for item in transcriptions
    }
    assert not recursive_keys(payload) & FORBIDDEN_KEYS


def test_snapshot_exports_calendar_trash_and_skips_purged_url_decryption(
    client,
    database_url,
    settings,
):
    _, owner_id_value = create_account(client, database_url)
    owner_id = UUID(owner_id_value)
    facts = seed_export_facts(database_url, settings, owner_id)
    series_id = uuid4()
    current = datetime.now(UTC)
    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            """
            INSERT INTO calendar_series (
                id, owner_id, kind, name, description, location, busy,
                source_type, time_zone, start_date, starts_at, ends_at,
                end_day_offset, recurrence_frequency, recurrence_interval,
                recurrence_weekdays, course_week_numbers,
                deleted_at, purge_after
            ) VALUES (
                %s, %s, 'fixed', '回收中的安排', '完整说明', '完整地点', true,
                'manual', 'Asia/Shanghai', DATE '2026-10-05',
                TIME '09:00', TIME '10:00', 0, 'none', 1,
                '[]'::jsonb, '[]'::jsonb, %s, %s
            )
            """,
            (
                series_id,
                owner_id,
                current,
                current + timedelta(days=30),
            ),
        )
        connection.execute(
            """
            UPDATE calendar_sources
            SET deleted_at=%s, purge_after=%s
            WHERE id=%s
            """,
            (
                current,
                current + timedelta(days=30),
                UUID(facts["source_id"]),
            ),
        )

    recoverable = read_snapshot(settings, owner_id)
    series = next(
        item
        for item in recoverable["calendar"]["series"]
        if item["id"] == str(series_id)
    )
    assert series["lifecycle"] == "trashed"
    assert series["name"] == "回收中的安排"
    source = next(
        item
        for item in recoverable["calendar"]["sources"]
        if item["id"] == facts["source_id"]
    )
    assert source["lifecycle"] == "trashed"
    subscription = next(
        item
        for item in recoverable["calendar"]["subscriptions"]
        if item["source_id"] == facts["source_id"]
    )
    assert subscription["lifecycle"] == "trashed"
    assert subscription["url"] == facts["subscription_url"]

    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            """
            UPDATE calendar_sources
            SET name='已彻底删除的订阅', purged_at=now()
            WHERE id=%s
            """,
            (UUID(facts["source_id"]),),
        )
        connection.execute(
            """
            UPDATE calendar_subscriptions
            SET encrypted_url=''::bytea,
                url_hmac=%s,
                display_url='已清理',
                display_host='deleted.invalid',
                lifecycle_status='paused',
                refresh_status='idle'
            WHERE source_id=%s
            """,
            ("0" * 64, UUID(facts["source_id"])),
        )

    purged = read_snapshot(settings, owner_id)
    purged_subscription = next(
        item
        for item in purged["calendar"]["subscriptions"]
        if item["source_id"] == facts["source_id"]
    )
    assert purged_subscription["lifecycle"] == "purged"
    assert purged_subscription["url"] is None


def test_repeatable_read_snapshot_excludes_later_commit(
    database_url,
    settings,
):
    with psycopg.connect(psycopg_url(database_url)) as connection:
        owner_id = UUID(str(uuid4()))
        connection.execute(
            """
            INSERT INTO users (id, email, password_hash, email_verified_at)
            VALUES (%s, %s, 'test-password-hash', now())
            """,
            (owner_id, f"{owner_id}@example.com"),
        )
        connection.commit()

    late_marker = f"late-{uuid4().hex}"
    database = Database(settings)

    async def execute() -> tuple[dict, dict]:
        try:
            async with database.engine.connect() as raw:
                connection = await raw.execution_options(
                    isolation_level="REPEATABLE READ"
                )
                async with connection.begin():
                    await connection.execute(text("SET TRANSACTION READ ONLY"))
                    snapshot_at = await connection.scalar(
                        text("SELECT transaction_timestamp()")
                    )
                    with psycopg.connect(psycopg_url(database_url)) as writer:
                        writer.execute(
                            """
                            INSERT INTO tasks (
                                id, owner_id, title, description,
                                priority, status, version
                            ) VALUES (%s, %s, %s, '', 0, 'open', 1)
                            """,
                            (uuid4(), owner_id, late_marker),
                        )
                        writer.commit()
                    first = await collect_export_snapshot(
                        connection,
                        owner_id,
                        settings,
                        snapshot_at=snapshot_at,
                    )
            second = await read_consistent_snapshot(database, owner_id, settings)
            return first, second
        finally:
            await database.close()

    before, after = asyncio.run(execute())
    assert late_marker not in json.dumps(before, ensure_ascii=False)
    assert late_marker in json.dumps(after, ensure_ascii=False)
