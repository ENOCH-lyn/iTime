import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from cryptography.fernet import Fernet

from itime.auth.security import Security
from itime.config import Settings
from recovery.ledger import (
    LedgerError,
    build_ledger_snapshot,
    read_encrypted_snapshot,
    write_encrypted_snapshot,
)
from recovery.replay import RecoveryReplayError, ReplaySummary, replay_ledger
from tests.conftest import isolated_database, psycopg_url

pytestmark = pytest.mark.integration

OBJECT_KINDS = {
    "task": "tasks",
    "difficulty": "difficulty_records",
    "memory": "memories",
    "calendar-series": "calendar_series",
    "calendar-source": "calendar_sources",
}


@pytest.fixture(scope="module")
def recovery_databases(admin_url: str):
    with isolated_database(admin_url) as source_url:
        with isolated_database(admin_url) as restore_url:
            yield source_url, restore_url


def insert_baseline(
    database_url: str,
    *,
    ids: dict[str, object],
    at: datetime,
) -> None:
    owner_id = ids["owner"]
    completed_id = ids["completed"]
    pending_id = ids["pending"]
    control_id = ids["control"]
    object_ids = ids["objects"]
    with psycopg.connect(psycopg_url(database_url)) as connection:
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO users (id, email, password_hash, status)
                VALUES (%s, %s, 'test-password-hash', 'active')
                """,
                [
                    (owner_id, f"owner-{owner_id}@example.com"),
                    (completed_id, f"completed-{completed_id}@example.com"),
                    (pending_id, f"pending-{pending_id}@example.com"),
                    (control_id, f"control-{control_id}@example.com"),
                ],
            )
        task_rows = [
            (
                object_ids["task"][state],
                owner_id,
                f"shared task title {state}",
                f"private task body {state}",
            )
            for state in ("trashed", "purged")
        ]
        task_rows.extend(
            [
                (
                    ids["completed_task"],
                    completed_id,
                    "completed account private task",
                    "must be removed",
                ),
                (
                    ids["control_task"],
                    control_id,
                    "shared task title purged",
                    "control must remain",
                ),
            ]
        )
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO tasks (
                    id, owner_id, title, description, priority, status, version
                ) VALUES (%s, %s, %s, %s, 1, 'open', 1)
                """,
                task_rows,
            )
        connection.execute(
            """
            INSERT INTO task_steps (id, task_id, owner_id, title, position)
            VALUES (%s, %s, %s, 'private purged step', 0)
            """,
            (
                ids["purged_step"],
                object_ids["task"]["purged"],
                owner_id,
            ),
        )

        for state in ("trashed", "purged"):
            connection.execute(
                """
                INSERT INTO difficulty_records (
                    id, owner_id, content, category, emotion,
                    occurred_at, time_zone, version
                ) VALUES (%s, %s, %s, 'scope', 'blocked', %s, 'UTC', 1)
                """,
                (
                    object_ids["difficulty"][state],
                    owner_id,
                    f"private difficulty {state}",
                    at,
                ),
            )
            connection.execute(
                """
                INSERT INTO memories (
                    id, owner_id, kind, title, content, source_type, version
                ) VALUES (%s, %s, 'context', %s, %s, 'manual', 1)
                """,
                (
                    object_ids["memory"][state],
                    owner_id,
                    f"private memory {state}",
                    f"private memory body {state}",
                ),
            )
            source_id = object_ids["calendar-source"][state]
            connection.execute(
                """
                INSERT INTO calendar_sources (
                    id, owner_id, kind, name, version
                ) VALUES (%s, %s, 'ics_subscription', %s, 1)
                """,
                (source_id, owner_id, f"private source {state}"),
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
                    %s, 'private.example', 'UTC',
                    'active', 'idle', 360,
                    %s, 1, 0, 0, 0, 1
                )
                """,
                (
                    source_id,
                    owner_id,
                    f"encrypted-{state}".encode(),
                    ("d" if state == "trashed" else "e") * 64,
                    f"https://private.example/{state}?token=secret",
                    at,
                ),
            )
            connection.execute(
                """
                INSERT INTO calendar_series (
                    id, owner_id, kind, name, description, location,
                    source_type, time_zone, start_date, starts_at, ends_at,
                    recurrence_frequency, recurrence_interval,
                    recurrence_weekdays, course_week_numbers, version
                ) VALUES (
                    %s, %s, 'fixed', %s, %s, 'private location',
                    'manual', 'UTC', DATE '2026-10-05', TIME '09:00', TIME '10:00',
                    'none', 1, '[]'::jsonb, '[]'::jsonb, 1
                )
                """,
                (
                    object_ids["calendar-series"][state],
                    owner_id,
                    f"private series {state}",
                    f"private series body {state}",
                ),
            )

        connection.execute(
            """
            INSERT INTO device_sessions (
                id, user_id, token_hash, auth_epoch, device_name,
                created_at, last_seen_at, expires_at
            ) VALUES (%s, %s, %s, 0, 'recovery test', %s, %s, %s)
            """,
            (
                ids["pending_session"],
                pending_id,
                "9" * 64,
                at,
                at,
                at + timedelta(days=1),
            ),
        )
        connection.commit()


def apply_source_deletions(
    database_url: str,
    *,
    ids: dict[str, object],
    at: datetime,
    security: Security,
) -> None:
    deleted_at = at - timedelta(days=31)
    trashed_purge_after = at + timedelta(days=30)
    purged_purge_after = at - timedelta(days=1)
    object_ids = ids["objects"]
    with psycopg.connect(psycopg_url(database_url)) as connection:
        completed_id = ids["completed"]
        pending_id = ids["pending"]
        completed_digest = security.keyed_digest(
            f"account-deletion:{completed_id}"
        )
        pending_digest = security.keyed_digest(f"account-deletion:{pending_id}")
        connection.execute(
            "UPDATE users SET status='pending_deletion' WHERE id=%s",
            (pending_id,),
        )
        connection.execute("DELETE FROM users WHERE id=%s", (completed_id,))
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO account_deletions (
                    id, user_id, subject_digest, operation_id, status,
                    requested_at, purge_after, available_at,
                    completed_at, request_id
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s
                )
                """,
                [
                    (
                        uuid4(),
                        pending_id,
                        pending_digest,
                        uuid4(),
                        "pending",
                        at,
                        at + timedelta(days=7),
                        at + timedelta(days=7),
                        None,
                        str(uuid4()),
                    ),
                    (
                        uuid4(),
                        None,
                        completed_digest,
                        uuid4(),
                        "completed",
                        deleted_at,
                        purged_purge_after,
                        purged_purge_after,
                        at,
                        str(uuid4()),
                    ),
                ],
            )

        for kind, table_name in OBJECT_KINDS.items():
            for state, version in (("trashed", 2), ("purged", 3)):
                purged_at = at if state == "purged" else None
                purge_after = (
                    purged_purge_after
                    if state == "purged"
                    else trashed_purge_after
                )
                assignments = """
                    deleted_at=%s, purge_after=%s, purged_at=%s, version=%s
                """
                if kind == "difficulty" and state == "purged":
                    assignments += """,
                        content=NULL, category=NULL, emotion=NULL,
                        task_id=NULL, focus_session_id=NULL,
                        occurrence_key=NULL, task_title_snapshot=NULL
                    """
                if kind == "memory" and state == "purged":
                    assignments += """,
                        title=NULL, content=NULL, source_proposal_id=NULL
                    """
                connection.execute(
                    f"UPDATE {table_name} SET {assignments} WHERE id=%s",  # noqa: S608
                    (
                        deleted_at,
                        purge_after,
                        purged_at,
                        version,
                        object_ids[kind][state],
                    ),
                )
        connection.commit()


@pytest.fixture(scope="module")
def replay_fixture(recovery_databases):
    source_url, restore_url = recovery_databases
    generated_at = datetime.now(UTC)
    key = Fernet.generate_key().decode()
    source_settings = Settings(
        environment="test",
        database_url=source_url,
        encryption_key=key,
        db_pool_size=2,
        db_max_overflow=0,
    )
    restore_settings = Settings(
        environment="test",
        database_url=restore_url,
        encryption_key=key,
        db_pool_size=2,
        db_max_overflow=0,
    )
    ids: dict[str, object] = {
        "owner": uuid4(),
        "completed": uuid4(),
        "pending": uuid4(),
        "control": uuid4(),
        "completed_task": uuid4(),
        "control_task": uuid4(),
        "pending_session": uuid4(),
        "purged_step": uuid4(),
        "objects": {
            kind: {"trashed": uuid4(), "purged": uuid4()}
            for kind in OBJECT_KINDS
        },
    }
    insert_baseline(source_url, ids=ids, at=generated_at)
    insert_baseline(restore_url, ids=ids, at=generated_at)
    apply_source_deletions(
        source_url,
        ids=ids,
        at=generated_at,
        security=Security(source_settings),
    )
    ledger = asyncio.run(
        build_ledger_snapshot(
            source_settings,
            generated_at=generated_at,
        )
    )
    return restore_settings, ids, ledger, generated_at


def test_replay_applies_account_and_object_deletions_idempotently(replay_fixture):
    settings, ids, ledger, generated_at = replay_fixture
    replay_at = generated_at + timedelta(seconds=1)
    first = asyncio.run(replay_ledger(settings, ledger, at=replay_at))
    second = asyncio.run(replay_ledger(settings, ledger, at=replay_at))

    assert first == ReplaySummary(
        applied=12,
        skipped=0,
        accounts_pending=1,
        accounts_purged=1,
        objects_trashed=5,
        objects_purged=5,
    )
    assert second == ReplaySummary(
        applied=0,
        skipped=12,
        accounts_pending=0,
        accounts_purged=0,
        objects_trashed=0,
        objects_purged=0,
    )

    object_ids = ids["objects"]
    with psycopg.connect(psycopg_url(settings.database_url.get_secret_value())) as connection:
        assert connection.execute(
            "SELECT count(*) FROM users WHERE id=%s",
            (ids["completed"],),
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM tasks WHERE id=%s",
            (ids["completed_task"],),
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT status, auth_epoch FROM users WHERE id=%s",
            (ids["pending"],),
        ).fetchone() == ("pending_deletion", 1)
        assert connection.execute(
            "SELECT revoked_at IS NOT NULL FROM device_sessions WHERE id=%s",
            (ids["pending_session"],),
        ).fetchone() == (True,)
        assert connection.execute(
            """
            SELECT status, count(*)
            FROM account_deletions
            GROUP BY status
            ORDER BY status
            """
        ).fetchall() == [("completed", 1), ("pending", 1)]

        for kind, table_name in OBJECT_KINDS.items():
            trashed = connection.execute(
                f"""
                SELECT deleted_at IS NOT NULL, purge_after, purged_at, version
                FROM {table_name}
                WHERE id=%s
                """,  # noqa: S608
                (object_ids[kind]["trashed"],),
            ).fetchone()
            assert trashed[0] is True
            assert trashed[1] == generated_at + timedelta(days=30)
            assert trashed[2] is None
            assert trashed[3] == 2

            purged = connection.execute(
                f"""
                SELECT deleted_at IS NOT NULL, purged_at IS NOT NULL, version
                FROM {table_name}
                WHERE id=%s
                """,  # noqa: S608
                (object_ids[kind]["purged"],),
            ).fetchone()
            assert purged[0:2] == (True, True)
            assert purged[2] >= 3

        assert connection.execute(
            "SELECT title, description FROM tasks WHERE id=%s",
            (object_ids["task"]["purged"],),
        ).fetchone() == ("已彻底删除的任务", "")
        assert connection.execute(
            "SELECT count(*) FROM task_steps WHERE id=%s",
            (ids["purged_step"],),
        ).fetchone() == (0,)
        assert connection.execute(
            """
            SELECT content, category, emotion
            FROM difficulty_records
            WHERE id=%s
            """,
            (object_ids["difficulty"]["purged"],),
        ).fetchone() == (None, None, None)
        assert connection.execute(
            "SELECT title, content FROM memories WHERE id=%s",
            (object_ids["memory"]["purged"],),
        ).fetchone() == (None, None)
        assert connection.execute(
            "SELECT name, description, location FROM calendar_series WHERE id=%s",
            (object_ids["calendar-series"]["purged"],),
        ).fetchone() == ("已彻底删除的安排", "", "")
        assert connection.execute(
            """
            SELECT s.name, c.encrypted_url, c.display_url, c.display_host
            FROM calendar_sources AS s
            JOIN calendar_subscriptions AS c ON c.source_id=s.id
            WHERE s.id=%s
            """,
            (object_ids["calendar-source"]["purged"],),
        ).fetchone() == (
            "已彻底删除的订阅",
            b"",
            "已清理",
            "deleted.invalid",
        )
        assert connection.execute(
            "SELECT title, description FROM tasks WHERE id=%s",
            (ids["control_task"],),
        ).fetchone() == ("shared task title purged", "control must remain")


def test_replay_rejects_conflicts_wrong_cluster_unknown_kind_and_stale_ledger(
    replay_fixture,
):
    settings, ids, ledger, generated_at = replay_fixture
    task_entry = next(
        entry
        for entry in ledger.entries
        if entry.kind == "task"
        and entry.subject == str(ids["objects"]["task"]["trashed"])
    )

    wrong_owner = replace(task_entry, owner_id=str(uuid4()))
    with pytest.raises(RecoveryReplayError, match="对象所有者不匹配"):
        asyncio.run(
            replay_ledger(
                settings,
                replace(ledger, entries=(wrong_owner,)),
                at=generated_at + timedelta(minutes=1),
            )
        )

    unknown = replace(task_entry, kind="unknown")
    with pytest.raises(RecoveryReplayError, match="台账内容无效"):
        asyncio.run(
            replay_ledger(
                settings,
                replace(ledger, entries=(unknown,)),
                at=generated_at + timedelta(minutes=1),
            )
        )

    with pytest.raises(RecoveryReplayError, match="数据库系统标识不匹配"):
        asyncio.run(
            replay_ledger(
                settings,
                replace(
                    ledger,
                    source_system_identifier="0",
                    entries=(),
                ),
                at=generated_at + timedelta(minutes=1),
            )
        )

    with pytest.raises(RecoveryReplayError, match="删除台账已超过 1 小时"):
        asyncio.run(
            replay_ledger(
                settings,
                replace(
                    ledger,
                    generated_at=(
                        generated_at - timedelta(hours=2)
                    ).isoformat(timespec="microseconds").replace("+00:00", "Z"),
                    entries=(),
                ),
                at=generated_at,
            )
        )

    task_id = ids["objects"]["task"]["trashed"]
    with psycopg.connect(psycopg_url(settings.database_url.get_secret_value())) as connection:
        connection.execute(
            """
            UPDATE tasks
            SET deleted_at=NULL, purge_after=NULL, purged_at=NULL, version=999
            WHERE id=%s
            """,
            (task_id,),
        )
        connection.commit()
    result = asyncio.run(
        replay_ledger(
            settings,
            replace(ledger, entries=(task_entry,)),
            at=generated_at + timedelta(minutes=1),
        )
    )
    assert result == ReplaySummary(
        applied=0,
        skipped=1,
        accounts_pending=0,
        accounts_purged=0,
        objects_trashed=0,
        objects_purged=0,
    )
    with psycopg.connect(psycopg_url(settings.database_url.get_secret_value())) as connection:
        assert connection.execute(
            """
            SELECT deleted_at, purge_after, purged_at, version
            FROM tasks WHERE id=%s
            """,
            (task_id,),
        ).fetchone() == (None, None, None, 999)


def test_replay_input_must_pass_encrypted_snapshot_authentication(
    replay_fixture,
    tmp_path: Path,
):
    _, _, ledger, _ = replay_fixture
    key = Fernet.generate_key()
    path = write_encrypted_snapshot(ledger, tmp_path / "ledger.bin", key)

    with pytest.raises(LedgerError, match="无法读取删除台账"):
        read_encrypted_snapshot(
            path,
            Fernet.generate_key(),
            expected_previous_digest=None,
        )

    valid = read_encrypted_snapshot(
        path,
        key,
        expected_previous_digest=None,
    )
    assert valid == ledger
