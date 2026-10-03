from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.errors import CheckViolation, ForeignKeyViolation, RaiseException, UniqueViolation

from itime.database import Base
from itime.execution.models import (
    DifficultyRecord,
    ExecutionRevision,
    FocusEvent,
    FocusSession,
)
from tests.conftest import isolated_database, migrate, psycopg_url

pytestmark = pytest.mark.integration

EXECUTION_TABLES = {
    "focus_sessions",
    "focus_events",
    "execution_revisions",
    "difficulty_records",
}


def seed_user(connection) -> UUID:
    owner_id = uuid4()
    connection.execute(
        """
        INSERT INTO users (id, email, password_hash, email_verified_at)
        VALUES (%s, %s, 'test-password-hash', now())
        """,
        (owner_id, f"{owner_id}@example.com"),
    )
    return owner_id


def seed_task(connection, owner_id: UUID, title: str = "专注任务") -> UUID:
    task_id = uuid4()
    connection.execute(
        "INSERT INTO tasks (id, owner_id, title) VALUES (%s, %s, %s)",
        (task_id, owner_id, title),
    )
    return task_id


def insert_focus(
    connection,
    owner_id: UUID,
    *,
    task_id: UUID | None = None,
    status: str = "running",
    focus_id: UUID | None = None,
) -> UUID:
    focus_id = focus_id or uuid4()
    terminal = status in {"finished", "abandoned"}
    connection.execute(
        """
        INSERT INTO focus_sessions (
            id, owner_id, source, task_id, title_snapshot,
            task_title_snapshot, step_snapshots, mode, target_seconds,
            status, started_at, ended_at, active_started_at,
            accumulated_seconds, effective_started_at,
            effective_ended_at, effective_duration_seconds,
            time_zone, version
        ) VALUES (
            %s, %s, 'timer', %s, '专注任务',
            %s, '[]', 'stopwatch', NULL,
            %s, TIMESTAMPTZ '2026-10-03 01:00:00+00',
            CASE WHEN %s THEN TIMESTAMPTZ '2026-10-03 02:00:00+00' ELSE NULL END,
            CASE WHEN %s = 'running'
                THEN TIMESTAMPTZ '2026-10-03 01:00:00+00' ELSE NULL END,
            CASE WHEN %s THEN 3600 ELSE 0 END,
            CASE WHEN %s THEN TIMESTAMPTZ '2026-10-03 01:00:00+00' ELSE NULL END,
            CASE WHEN %s THEN TIMESTAMPTZ '2026-10-03 02:00:00+00' ELSE NULL END,
            CASE WHEN %s THEN 3600 ELSE NULL END,
            'Asia/Shanghai', 1
        )
        """,
        (
            focus_id,
            owner_id,
            task_id,
            "专注任务" if task_id else None,
            status,
            terminal,
            status,
            terminal,
            terminal,
            terminal,
            terminal,
        ),
    )
    return focus_id


def test_execution_migration_round_trip_and_orm_consistency(admin_url):
    with isolated_database(admin_url) as database_url:
        with psycopg.connect(psycopg_url(database_url)) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema='public'
                    """
                )
            }
            assert tables >= EXECUTION_TABLES
            assert {
                FocusSession.__table__.name,
                FocusEvent.__table__.name,
                ExecutionRevision.__table__.name,
                DifficultyRecord.__table__.name,
            } == EXECUTION_TABLES
            assert Base.metadata.tables.keys() >= EXECUTION_TABLES

        migrate(database_url, "downgrade", "0024_planning_lifecycle")
        with psycopg.connect(psycopg_url(database_url)) as connection:
            remaining = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema='public'
                    """
                )
            }
            assert not EXECUTION_TABLES & remaining

        migrate(database_url, "upgrade", "head")
        migrate(database_url, "check")


def test_only_one_open_focus_per_owner(admin_url):
    with isolated_database(admin_url) as database_url:
        with psycopg.connect(psycopg_url(database_url)) as connection:
            owner_id = seed_user(connection)
            other_owner_id = seed_user(connection)
            insert_focus(connection, owner_id)
            connection.commit()

            with pytest.raises(UniqueViolation):
                with connection.transaction():
                    insert_focus(connection, owner_id, status="paused")

            insert_focus(connection, owner_id, status="finished")
            insert_focus(connection, other_owner_id, status="paused")
            connection.commit()


def test_execution_owner_and_state_constraints(admin_url):
    with isolated_database(admin_url) as database_url:
        with psycopg.connect(psycopg_url(database_url)) as connection:
            owner_id = seed_user(connection)
            other_owner_id = seed_user(connection)
            task_id = seed_task(connection, owner_id)
            connection.commit()

            with pytest.raises(ForeignKeyViolation):
                with connection.transaction():
                    insert_focus(connection, other_owner_id, task_id=task_id)

            with pytest.raises(CheckViolation):
                with connection.transaction():
                    connection.execute(
                        """
                        INSERT INTO focus_sessions (
                            id, owner_id, source, title_snapshot, step_snapshots,
                            mode, status, started_at, ended_at, active_started_at,
                            accumulated_seconds, effective_started_at,
                            effective_ended_at, effective_duration_seconds,
                            time_zone, version
                        ) VALUES (
                            %s, %s, 'timer', '非法终态', '[]',
                            'stopwatch', 'finished',
                            TIMESTAMPTZ '2026-10-03 01:00:00+00',
                            TIMESTAMPTZ '2026-10-03 02:00:00+00',
                            TIMESTAMPTZ '2026-10-03 01:00:00+00',
                            3600,
                            TIMESTAMPTZ '2026-10-03 01:00:00+00',
                            TIMESTAMPTZ '2026-10-03 02:00:00+00',
                            3600, 'Asia/Shanghai', 1
                        )
                        """,
                        (uuid4(), owner_id),
                    )

            with pytest.raises(CheckViolation):
                with connection.transaction():
                    connection.execute(
                        """
                        INSERT INTO focus_sessions (
                            id, owner_id, source, title_snapshot, step_snapshots,
                            mode, target_seconds, status, started_at,
                            active_started_at, accumulated_seconds,
                            time_zone, version
                        ) VALUES (
                            %s, %s, 'timer', '非法倒计时', '[]',
                            'stopwatch', 1500, 'running',
                            TIMESTAMPTZ '2026-10-03 01:00:00+00',
                            TIMESTAMPTZ '2026-10-03 01:00:00+00',
                            0, 'Asia/Shanghai', 1
                        )
                        """,
                        (uuid4(), owner_id),
                    )


def test_focus_events_are_immutable(admin_url):
    with isolated_database(admin_url) as database_url:
        with psycopg.connect(psycopg_url(database_url)) as connection:
            owner_id = seed_user(connection)
            focus_id = insert_focus(connection, owner_id)
            event_id = uuid4()
            connection.execute(
                """
                INSERT INTO focus_events (
                    id, owner_id, focus_session_id, sequence, kind,
                    occurred_at, source, details
                ) VALUES (
                    %s, %s, %s, 1, 'start',
                    TIMESTAMPTZ '2026-10-03 01:00:00+00',
                    'online', '{}'
                )
                """,
                (event_id, owner_id, focus_id),
            )
            connection.commit()

            for statement in (
                "UPDATE focus_events SET kind='pause' WHERE id=%s",
                "DELETE FROM focus_events WHERE id=%s",
            ):
                with pytest.raises(RaiseException):
                    with connection.transaction():
                        connection.execute(statement, (event_id,))


def test_execution_user_cascade_delete(admin_url):
    with isolated_database(admin_url) as database_url:
        with psycopg.connect(psycopg_url(database_url)) as connection:
            owner_id = seed_user(connection)
            task_id = seed_task(connection, owner_id)
            focus_id = insert_focus(
                connection,
                owner_id,
                task_id=task_id,
                status="finished",
            )
            connection.execute(
                """
                INSERT INTO focus_events (
                    id, owner_id, focus_session_id, sequence, kind,
                    occurred_at, source, details
                ) VALUES (
                    %s, %s, %s, 1, 'finish',
                    TIMESTAMPTZ '2026-10-03 02:00:00+00',
                    'online', '{}'
                )
                """,
                (uuid4(), owner_id, focus_id),
            )
            connection.execute(
                """
                INSERT INTO execution_revisions (
                    id, owner_id, focus_session_id, session_version,
                    before_data, after_data, reason
                ) VALUES (%s, %s, %s, 2, '{}', '{}', '修正测试')
                """,
                (uuid4(), owner_id, focus_id),
            )
            connection.execute(
                """
                INSERT INTO difficulty_records (
                    id, owner_id, task_id, focus_session_id,
                    task_title_snapshot, content, occurred_at,
                    time_zone, version
                ) VALUES (
                    %s, %s, %s, %s, '专注任务', '这里有点难',
                    TIMESTAMPTZ '2026-10-03 01:30:00+00',
                    'Asia/Shanghai', 1
                )
                """,
                (uuid4(), owner_id, task_id, focus_id),
            )
            connection.commit()

            connection.execute("DELETE FROM users WHERE id=%s", (owner_id,))
            connection.commit()
            for table in EXECUTION_TABLES:
                assert connection.execute(
                    f"SELECT count(*) FROM {table}"  # noqa: S608
                ).fetchone()[0] == 0
