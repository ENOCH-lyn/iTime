import subprocess
from uuid import uuid4

import psycopg
import pytest
from psycopg.errors import CheckViolation, ForeignKeyViolation, UniqueViolation

from itime.calendar.models import CalendarSubscriptionException
from tests.conftest import isolated_database, migrate, psycopg_url
from tests.test_planning_models import seed_user

pytestmark = pytest.mark.integration

EXCEPTION_COLUMNS = {
    "id",
    "owner_id",
    "master_event_id",
    "source_occurrence_key",
    "original_start_at",
    "original_end_at",
    "original_all_day",
    "action",
    "moved_all_day",
    "moved_start_date",
    "moved_end_date",
    "moved_starts_at",
    "moved_ends_at",
    "moved_time_zone",
    "moved_ambiguous_offset_minutes",
    "moved_start_at",
    "moved_end_at",
    "override_name",
    "override_description",
    "override_location",
    "override_busy",
    "version",
    "created_at",
    "updated_at",
}


def seed_subscription_event(connection, owner_id):
    source_id = uuid4()
    event_id = uuid4()
    connection.execute(
        """
        INSERT INTO calendar_sources (id, owner_id, kind, name)
        VALUES (%s, %s, 'ics_subscription', '模型测试订阅')
        """,
        (source_id, owner_id),
    )
    connection.execute(
        """
        INSERT INTO calendar_subscriptions (
            source_id, owner_id, encrypted_url, url_hmac,
            display_url, display_host, default_time_zone,
            next_refresh_at
        ) VALUES (
            %s, %s, %s, %s,
            'https://calendar.example/...', 'calendar.example', 'UTC',
            now()
        )
        """,
        (source_id, owner_id, b"encrypted", "a" * 64),
    )
    connection.execute(
        """
        INSERT INTO calendar_subscription_events (
            id, source_id, owner_id, uid, recurrence_id,
            baseline, content_sha256, first_seen_generation,
            last_seen_generation
        ) VALUES (
            %s, %s, %s, 'model-event', '',
            '{}'::jsonb, %s, 1, 1
        )
        """,
        (event_id, source_id, owner_id, "b" * 64),
    )
    return source_id, event_id


def insert_cancelled_exception(
    connection,
    owner_id,
    event_id,
    *,
    exception_id=None,
    source_occurrence_key="INSTANT:2026-10-06T09:00:00Z",
):
    exception_id = exception_id or uuid4()
    connection.execute(
        """
        INSERT INTO calendar_subscription_exceptions (
            id, owner_id, master_event_id, source_occurrence_key,
            original_start_at, original_end_at, original_all_day,
            action, version
        ) VALUES (
            %s, %s, %s, %s,
            '2026-10-06T09:00:00Z', '2026-10-06T10:00:00Z',
            false, 'cancelled', 1
        )
        """,
        (exception_id, owner_id, event_id, source_occurrence_key),
    )
    return exception_id


def assert_check_violation(connection, statement: str, values: tuple) -> None:
    with pytest.raises(CheckViolation):
        with connection.transaction():
            connection.execute(statement, values)


def test_subscription_exception_constraints_ownership_and_cascade(admin_url):
    with isolated_database(admin_url) as database_url:
        with psycopg.connect(psycopg_url(database_url)) as connection:
            owner_id = seed_user(connection)
            _source_id, event_id = seed_subscription_event(connection, owner_id)
            exception_id = insert_cancelled_exception(
                connection,
                owner_id,
                event_id,
            )
            connection.commit()

            columns = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema='public'
                      AND table_name='calendar_subscription_exceptions'
                    """
                )
            }
            assert columns == EXCEPTION_COLUMNS
            assert set(
                CalendarSubscriptionException.__table__.columns.keys()
            ) == EXCEPTION_COLUMNS

            assert_check_violation(
                connection,
                """
                UPDATE calendar_subscription_exceptions
                SET action='unknown'
                WHERE id=%s
                """,
                (exception_id,),
            )
            assert_check_violation(
                connection,
                """
                UPDATE calendar_subscription_exceptions
                SET original_end_at=original_start_at
                WHERE id=%s
                """,
                (exception_id,),
            )
            assert_check_violation(
                connection,
                """
                UPDATE calendar_subscription_exceptions
                SET source_occurrence_key=''
                WHERE id=%s
                """,
                (exception_id,),
            )
            assert_check_violation(
                connection,
                """
                UPDATE calendar_subscription_exceptions
                SET version=0
                WHERE id=%s
                """,
                (exception_id,),
            )
            assert_check_violation(
                connection,
                """
                UPDATE calendar_subscription_exceptions
                SET action='overridden'
                WHERE id=%s
                """,
                (exception_id,),
            )
            assert_check_violation(
                connection,
                """
                UPDATE calendar_subscription_exceptions
                SET action='moved',
                    moved_all_day=true,
                    moved_start_date='2026-10-07',
                    moved_end_date='2026-10-08',
                    moved_time_zone='UTC'
                WHERE id=%s
                """,
                (exception_id,),
            )

            with pytest.raises(UniqueViolation):
                with connection.transaction():
                    insert_cancelled_exception(
                        connection,
                        owner_id,
                        event_id,
                    )

            other_owner_id = seed_user(connection)
            _other_source_id, other_event_id = seed_subscription_event(
                connection,
                other_owner_id,
            )
            connection.commit()
            with pytest.raises(ForeignKeyViolation):
                with connection.transaction():
                    insert_cancelled_exception(
                        connection,
                        owner_id,
                        other_event_id,
                        source_occurrence_key="INSTANT:2026-10-07T09:00:00Z",
                    )

            connection.execute(
                "DELETE FROM calendar_subscription_events WHERE id=%s",
                (event_id,),
            )
            connection.commit()
            assert connection.execute(
                """
                SELECT count(*) FROM calendar_subscription_exceptions
                WHERE owner_id=%s
                """,
                (owner_id,),
            ).fetchone()[0] == 0


def test_subscription_exception_move_shapes_and_planning_revision(admin_url):
    with isolated_database(admin_url) as database_url:
        with psycopg.connect(psycopg_url(database_url)) as connection:
            owner_id = seed_user(connection)
            _source_id, event_id = seed_subscription_event(connection, owner_id)
            revision_before = connection.execute(
                """
                SELECT revision FROM planning_input_revisions
                WHERE owner_id=%s
                """,
                (owner_id,),
            ).fetchone()[0]
            timed_id = uuid4()
            connection.execute(
                """
                INSERT INTO calendar_subscription_exceptions (
                    id, owner_id, master_event_id, source_occurrence_key,
                    original_start_at, original_end_at, original_all_day,
                    action, moved_all_day, moved_start_date, moved_end_date,
                    moved_starts_at, moved_ends_at, moved_time_zone,
                    moved_ambiguous_offset_minutes, moved_start_at,
                    moved_end_at, version
                ) VALUES (
                    %s, %s, %s, 'INSTANT:2026-11-01T05:30:00Z',
                    '2026-11-01T05:30:00Z', '2026-11-01T06:00:00Z', false,
                    'moved', false, '2026-11-01', '2026-11-01',
                    '01:30:00', '01:45:00', 'America/New_York',
                    -300, '2026-11-01T06:30:00Z', '2026-11-01T06:45:00Z', 1
                )
                """,
                (timed_id, owner_id, event_id),
            )
            revision_after_insert = connection.execute(
                """
                SELECT revision FROM planning_input_revisions
                WHERE owner_id=%s
                """,
                (owner_id,),
            ).fetchone()[0]
            assert revision_after_insert == revision_before + 1

            connection.execute(
                """
                UPDATE calendar_subscription_exceptions
                SET version=2, updated_at=now()
                WHERE id=%s
                """,
                (timed_id,),
            )
            revision_after_update = connection.execute(
                """
                SELECT revision FROM planning_input_revisions
                WHERE owner_id=%s
                """,
                (owner_id,),
            ).fetchone()[0]
            assert revision_after_update == revision_after_insert + 1

            all_day_id = uuid4()
            connection.execute(
                """
                INSERT INTO calendar_subscription_exceptions (
                    id, owner_id, master_event_id, source_occurrence_key,
                    original_start_at, original_end_at, original_all_day,
                    action, moved_all_day, moved_start_date, moved_end_date,
                    moved_time_zone, moved_start_at, moved_end_at, version
                ) VALUES (
                    %s, %s, %s, 'DATE:2026-10-08',
                    '2026-10-08T00:00:00Z', '2026-10-09T00:00:00Z', true,
                    'moved', true, '2026-10-10', '2026-10-12',
                    'UTC', '2026-10-10T00:00:00Z', '2026-10-12T00:00:00Z', 1
                )
                """,
                (all_day_id, owner_id, event_id),
            )
            connection.execute(
                """
                DELETE FROM calendar_subscription_exceptions
                WHERE id=%s
                """,
                (all_day_id,),
            )
            revision_after_delete = connection.execute(
                """
                SELECT revision FROM planning_input_revisions
                WHERE owner_id=%s
                """,
                (owner_id,),
            ).fetchone()[0]
            assert revision_after_delete == revision_after_update + 2

            assert_check_violation(
                connection,
                """
                UPDATE calendar_subscription_exceptions
                SET moved_all_day=true,
                    moved_starts_at='09:00:00',
                    moved_ends_at='10:00:00',
                    moved_ambiguous_offset_minutes=0
                WHERE id=%s
                """,
                (timed_id,),
            )
            assert_check_violation(
                connection,
                """
                UPDATE calendar_subscription_exceptions
                SET moved_end_date=moved_start_date + 367
                WHERE id=%s
                """,
                (timed_id,),
            )
            connection.rollback()


def test_subscription_exception_migration_blocks_loss_and_round_trips(admin_url):
    with isolated_database(admin_url) as database_url:
        with psycopg.connect(psycopg_url(database_url)) as connection:
            owner_id = seed_user(connection)
            _source_id, event_id = seed_subscription_event(connection, owner_id)
            insert_cancelled_exception(connection, owner_id, event_id)
            connection.commit()

        with pytest.raises(subprocess.CalledProcessError) as error:
            migrate(
                database_url,
                "downgrade",
                "0031_plan_source_conflicts",
            )
        assert "cannot downgrade 0032_calendar_subscription_exceptions" in (
            error.value.stderr
        )

        with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
            connection.execute("DELETE FROM calendar_subscription_exceptions")
        migrate(database_url, "downgrade", "0031_plan_source_conflicts")
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT to_regclass('calendar_subscription_exceptions')"
            ).fetchone()[0] is None
        migrate(database_url, "upgrade", "head")
        migrate(database_url, "check")
