import asyncio
import json
import stat
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from cryptography.fernet import Fernet

from itime.config import Settings
from recovery.ledger import (
    LedgerError,
    build_ledger_snapshot,
    canonical_payload,
    ledger_digest,
    read_encrypted_snapshot,
    write_encrypted_snapshot,
)
from tests.conftest import isolated_database, psycopg_url

pytestmark = pytest.mark.integration

GENERATED_AT = datetime(2026, 10, 5, 6, 30, tzinfo=UTC)
KINDS = {
    "task",
    "difficulty",
    "memory",
    "calendar-series",
    "calendar-source",
}


@pytest.fixture(scope="module")
def recovery_database(admin_url: str):
    with isolated_database(admin_url) as database_url:
        yield database_url


def lifecycle_times(state: str) -> tuple[datetime | None, datetime | None, datetime | None]:
    if state == "active":
        return None, None, None
    deleted_at = GENERATED_AT - timedelta(days=31)
    purge_after = GENERATED_AT - timedelta(days=1)
    purged_at = GENERATED_AT if state == "purged" else None
    if state == "trashed":
        purge_after = GENERATED_AT + timedelta(days=30)
    return deleted_at, purge_after, purged_at


def seed_ledger_rows(database_url: str) -> dict[str, object]:
    owner_id = uuid4()
    pending_user_id = uuid4()
    cancelled_user_id = uuid4()
    private_email = f"ledger-{uuid4().hex}@private.example"
    private_body = "LEDGER_PRIVATE_BODY Cookie=session-secret api_key=provider-secret"
    private_url = "https://private.example/calendar?token=LEDGER_PRIVATE_TOKEN"
    password_hash = "LEDGER_PASSWORD_HASH_SENTINEL"
    pending_digest = "a" * 64
    completed_digest = "b" * 64
    cancelled_digest = "c" * 64
    object_ids: dict[str, dict[str, UUID]] = {kind: {} for kind in KINDS}

    with psycopg.connect(psycopg_url(database_url)) as connection:
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO users (id, email, password_hash, status)
                VALUES (%s, %s, %s, %s)
                """,
                [
                    (owner_id, private_email, password_hash, "active"),
                    (
                        pending_user_id,
                        f"pending-{uuid4().hex}@private.example",
                        password_hash,
                        "pending_deletion",
                    ),
                    (
                        cancelled_user_id,
                        f"cancelled-{uuid4().hex}@private.example",
                        password_hash,
                        "active",
                    ),
                ],
            )
        requested_at = GENERATED_AT - timedelta(days=8)
        purge_after = GENERATED_AT - timedelta(days=1)
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO account_deletions (
                    id, user_id, subject_digest, operation_id, status,
                    requested_at, purge_after, available_at,
                    cancelled_at, completed_at, request_id
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s
                )
                """,
                [
                    (
                        uuid4(),
                        pending_user_id,
                        pending_digest,
                        uuid4(),
                        "pending",
                        requested_at,
                        GENERATED_AT + timedelta(days=1),
                        GENERATED_AT + timedelta(days=1),
                        None,
                        None,
                        str(uuid4()),
                    ),
                    (
                        uuid4(),
                        None,
                        completed_digest,
                        uuid4(),
                        "completed",
                        requested_at,
                        purge_after,
                        purge_after,
                        None,
                        GENERATED_AT,
                        str(uuid4()),
                    ),
                    (
                        uuid4(),
                        cancelled_user_id,
                        cancelled_digest,
                        uuid4(),
                        "cancelled",
                        requested_at,
                        GENERATED_AT + timedelta(days=1),
                        GENERATED_AT + timedelta(days=1),
                        GENERATED_AT,
                        None,
                        str(uuid4()),
                    ),
                ],
            )

        for state in ("active", "trashed", "purged"):
            deleted_at, item_purge_after, purged_at = lifecycle_times(state)

            task_id = uuid4()
            object_ids["task"][state] = task_id
            connection.execute(
                """
                INSERT INTO tasks (
                    id, owner_id, title, description, priority, status,
                    deleted_at, purge_after, purged_at, version
                ) VALUES (%s, %s, %s, %s, 1, 'open', %s, %s, %s, %s)
                """,
                (
                    task_id,
                    owner_id,
                    f"{private_body}-{state}",
                    private_body,
                    deleted_at,
                    item_purge_after,
                    purged_at,
                    10 + len(object_ids["task"]),
                ),
            )

            difficulty_id = uuid4()
            object_ids["difficulty"][state] = difficulty_id
            connection.execute(
                """
                INSERT INTO difficulty_records (
                    id, owner_id, content, occurred_at, time_zone,
                    deleted_at, purge_after, purged_at, version
                ) VALUES (%s, %s, %s, %s, 'UTC', %s, %s, %s, %s)
                """,
                (
                    difficulty_id,
                    owner_id,
                    None if state == "purged" else f"{private_body}-{state}",
                    GENERATED_AT,
                    deleted_at,
                    item_purge_after,
                    purged_at,
                    20 + len(object_ids["difficulty"]),
                ),
            )

            memory_id = uuid4()
            object_ids["memory"][state] = memory_id
            connection.execute(
                """
                INSERT INTO memories (
                    id, owner_id, kind, title, content, source_type,
                    deleted_at, purge_after, purged_at, version
                ) VALUES (
                    %s, %s, 'context', %s, %s, 'manual',
                    %s, %s, %s, %s
                )
                """,
                (
                    memory_id,
                    owner_id,
                    None if state == "purged" else f"private-{state}",
                    None if state == "purged" else private_body,
                    deleted_at,
                    item_purge_after,
                    purged_at,
                    30 + len(object_ids["memory"]),
                ),
            )

            source_id = uuid4()
            object_ids["calendar-source"][state] = source_id
            connection.execute(
                """
                INSERT INTO calendar_sources (
                    id, owner_id, kind, name,
                    deleted_at, purge_after, purged_at, version
                ) VALUES (
                    %s, %s, 'ics_subscription', %s,
                    %s, %s, %s, %s
                )
                """,
                (
                    source_id,
                    owner_id,
                    f"{private_body}-{state}",
                    deleted_at,
                    item_purge_after,
                    purged_at,
                    40 + len(object_ids["calendar-source"]),
                ),
            )

            series_id = uuid4()
            object_ids["calendar-series"][state] = series_id
            connection.execute(
                """
                INSERT INTO calendar_series (
                    id, owner_id, kind, name, description, location,
                    source_type, time_zone, start_date, starts_at, ends_at,
                    recurrence_frequency, recurrence_interval,
                    recurrence_weekdays, course_week_numbers,
                    deleted_at, purge_after, purged_at, version
                ) VALUES (
                    %s, %s, 'fixed', %s, %s, '',
                    'manual', 'UTC', DATE '2026-10-05', TIME '09:00', TIME '10:00',
                    'none', 1, '[]'::jsonb, '[]'::jsonb,
                    %s, %s, %s, %s
                )
                """,
                (
                    series_id,
                    owner_id,
                    f"{private_body}-{state}",
                    private_body,
                    deleted_at,
                    item_purge_after,
                    purged_at,
                    50 + len(object_ids["calendar-series"]),
                ),
            )

        trashed_source_id = object_ids["calendar-source"]["trashed"]
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
                %s, 'private.example', 'UTC',
                'paused', 'idle', 360,
                %s, 1, 0, 0, 0, 1
            )
            """,
            (
                trashed_source_id,
                owner_id,
                b"encrypted-url-sentinel",
                "f" * 64,
                private_url,
                GENERATED_AT,
            ),
        )
        connection.commit()

    return {
        "owner_id": owner_id,
        "pending_digest": pending_digest,
        "completed_digest": completed_digest,
        "cancelled_digest": cancelled_digest,
        "object_ids": object_ids,
        "secrets": (private_email, private_body, private_url, password_hash),
    }


@pytest.fixture(scope="module")
def ledger_fixture(recovery_database: str):
    settings = Settings(
        environment="test",
        database_url=recovery_database,
        encryption_key=Fernet.generate_key().decode(),
        db_pool_size=2,
        db_max_overflow=0,
    )
    seeded = seed_ledger_rows(recovery_database)
    first = asyncio.run(build_ledger_snapshot(settings, generated_at=GENERATED_AT))
    second = asyncio.run(build_ledger_snapshot(settings, generated_at=GENERATED_AT))
    return settings, seeded, first, second


def test_snapshot_is_deterministic_sorted_and_contains_only_deletion_state(ledger_fixture):
    _, seeded, first, second = ledger_fixture
    assert first == second
    assert canonical_payload(first) == canonical_payload(second)
    assert ledger_digest(first) == ledger_digest(second)
    assert first.schema_version == 1
    assert first.generated_at == "2026-10-05T06:30:00.000000Z"
    assert first.source_system_identifier.isdigit()

    keys = [(entry.kind, entry.subject) for entry in first.entries]
    assert keys == sorted(keys)
    assert len(keys) == len(set(keys)) == 12
    assert ("account", seeded["pending_digest"]) in keys
    assert ("account", seeded["completed_digest"]) in keys
    assert ("account", seeded["cancelled_digest"]) not in keys

    entries = {(entry.kind, entry.subject): entry for entry in first.entries}
    object_ids = seeded["object_ids"]
    for kind in KINDS:
        assert (kind, str(object_ids[kind]["active"])) not in entries
        trashed = entries[(kind, str(object_ids[kind]["trashed"]))]
        purged = entries[(kind, str(object_ids[kind]["purged"]))]
        assert trashed.state == "trashed"
        assert trashed.purged_at is None
        assert purged.state == "purged"
        assert purged.purged_at == "2026-10-05T06:30:00.000000Z"
        assert trashed.owner_id == str(seeded["owner_id"])
        assert purged.owner_id == str(seeded["owner_id"])

    account_entries = [entry for entry in first.entries if entry.kind == "account"]
    assert {entry.state for entry in account_entries} == {"pending", "completed"}
    assert all(entry.owner_id is None for entry in account_entries)

    payload = canonical_payload(first)
    rendered = payload.decode()
    for secret in seeded["secrets"]:
        assert secret not in rendered
    assert set(json.loads(rendered)) == {
        "entries",
        "generated_at",
        "previous_digest",
        "schema_version",
        "source_system_identifier",
    }
    assert {
        key
        for entry in json.loads(rendered)["entries"]
        for key in entry
    } == {
        "deleted_at",
        "kind",
        "owner_id",
        "purge_after",
        "purged_at",
        "state",
        "subject",
        "version",
    }


def test_encrypted_snapshot_is_private_and_round_trips(
    ledger_fixture,
    tmp_path: Path,
):
    _, seeded, ledger, _ = ledger_fixture
    key = Fernet.generate_key()
    path = write_encrypted_snapshot(ledger, tmp_path / "ledger.bin", key)

    assert path == tmp_path / "ledger.bin"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    encrypted = path.read_bytes()
    assert canonical_payload(ledger) not in encrypted
    assert str(seeded["owner_id"]).encode() not in encrypted
    for secret in seeded["secrets"]:
        assert secret.encode() not in encrypted
    assert read_encrypted_snapshot(
        path,
        key,
        expected_previous_digest=None,
    ) == ledger

    digest = ledger_digest(ledger)
    chained = replace(
        ledger,
        generated_at="2026-10-05T06:45:00.000000Z",
        previous_digest=digest,
    )
    chained_path = write_encrypted_snapshot(chained, tmp_path / "chained.bin", key)
    assert read_encrypted_snapshot(
        chained_path,
        key,
        expected_previous_digest=digest,
    ) == chained


def test_encrypted_snapshot_rejects_wrong_key_tampering_and_broken_chain(
    ledger_fixture,
    tmp_path: Path,
):
    _, _, ledger, _ = ledger_fixture
    key = Fernet.generate_key()
    path = write_encrypted_snapshot(ledger, tmp_path / "ledger.bin", key)

    with pytest.raises(LedgerError, match="无法读取删除台账 ledger.bin"):
        read_encrypted_snapshot(
            path,
            Fernet.generate_key(),
            expected_previous_digest=None,
        )

    tampered = bytearray(path.read_bytes())
    tampered[len(tampered) // 2] ^= 1
    tampered_path = tmp_path / "tampered.bin"
    tampered_path.write_bytes(tampered)
    with pytest.raises(LedgerError, match="无法读取删除台账 tampered.bin"):
        read_encrypted_snapshot(
            tampered_path,
            key,
            expected_previous_digest=None,
        )

    with pytest.raises(LedgerError, match="删除台账链校验失败"):
        read_encrypted_snapshot(
            path,
            key,
            expected_previous_digest="0" * 64,
        )
