import asyncio
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
    return {"marker": marker, "subscription_url": subscription_url}


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
    assert payload["calendar"]["subscriptions"][0]["url"] == facts["subscription_url"]
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
