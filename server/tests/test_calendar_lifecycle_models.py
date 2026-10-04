import subprocess
from datetime import timedelta
from uuid import uuid4

import psycopg
import pytest
from psycopg.errors import CheckViolation

from itime.calendar.models import CalendarSeries, CalendarSource
from tests.conftest import isolated_database, migrate, psycopg_url

pytestmark = pytest.mark.integration


LIFECYCLE_COLUMNS = {"deleted_at", "purge_after", "purged_at"}


def seed_user(connection) -> object:
    owner_id = uuid4()
    connection.execute(
        """
        INSERT INTO users (id, email, password_hash, email_verified_at)
        VALUES (%s, %s, 'test-password-hash', now())
        """,
        (owner_id, f"calendar-lifecycle-{owner_id}@example.com"),
    )
    return owner_id


def seed_source(connection, owner_id) -> object:
    source_id = uuid4()
    connection.execute(
        """
        INSERT INTO calendar_sources (id, owner_id, kind, name)
        VALUES (%s, %s, 'ics_subscription', '生命周期订阅')
        """,
        (source_id, owner_id),
    )
    return source_id


def seed_series(connection, owner_id) -> object:
    series_id = uuid4()
    connection.execute(
        """
        INSERT INTO calendar_series (
            id, owner_id, kind, name, time_zone, start_date,
            starts_at, ends_at, recurrence_frequency,
            recurrence_weekdays, course_week_numbers
        ) VALUES (
            %s, %s, 'fixed', '生命周期安排', 'Asia/Shanghai',
            DATE '2026-10-05', TIME '09:00', TIME '10:00',
            'none', '[]'::jsonb, '[]'::jsonb
        )
        """,
        (series_id, owner_id),
    )
    return series_id


def revision(connection, owner_id) -> int:
    return connection.execute(
        """
        SELECT revision
        FROM planning_input_revisions
        WHERE owner_id=%s
        """,
        (owner_id,),
    ).fetchone()[0]


def test_calendar_lifecycle_orm_and_database_constraints(admin_url):
    assert LIFECYCLE_COLUMNS <= set(CalendarSeries.__table__.columns.keys())
    assert LIFECYCLE_COLUMNS <= set(CalendarSource.__table__.columns.keys())

    with isolated_database(admin_url) as database_url:
        with psycopg.connect(
            psycopg_url(database_url),
            autocommit=True,
        ) as connection:
            owner_id = seed_user(connection)
            source_id = seed_source(connection, owner_id)
            series_id = seed_series(connection, owner_id)

            connection.execute(
                """
                UPDATE calendar_series
                SET deleted_at=now(), purge_after=now() + interval '30 days'
                WHERE id=%s
                """,
                (series_id,),
            )
            deleted, retention, purged = connection.execute(
                """
                SELECT deleted_at IS NOT NULL, purge_after - deleted_at, purged_at
                FROM calendar_series
                WHERE id=%s
                """,
                (series_id,),
            ).fetchone()
            assert deleted is True
            assert retention == timedelta(days=30)
            assert purged is None

            with pytest.raises(CheckViolation):
                with connection.transaction():
                    connection.execute(
                        """
                        UPDATE calendar_sources
                        SET deleted_at=now()
                        WHERE id=%s
                        """,
                        (source_id,),
                    )
            with pytest.raises(CheckViolation):
                with connection.transaction():
                    connection.execute(
                        """
                        UPDATE calendar_sources
                        SET deleted_at=now(),
                            purge_after=now() - interval '1 second'
                        WHERE id=%s
                        """,
                        (source_id,),
                    )
            with pytest.raises(CheckViolation):
                with connection.transaction():
                    connection.execute(
                        """
                        UPDATE calendar_sources
                        SET purged_at=now()
                        WHERE id=%s
                        """,
                        (source_id,),
                    )

            indexes = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT indexname
                    FROM pg_indexes
                    WHERE schemaname='public'
                      AND tablename IN ('calendar_series', 'calendar_sources')
                    """
                )
            }
            assert {
                "ix_calendar_series_owner_lifecycle_updated",
                "ix_calendar_series_purge_due",
                "ix_calendar_sources_owner_lifecycle_updated",
                "ix_calendar_sources_purge_due",
            } <= indexes


def test_source_lifecycle_revision_trigger_is_column_scoped(admin_url):
    with isolated_database(admin_url) as database_url:
        with psycopg.connect(
            psycopg_url(database_url),
            autocommit=True,
        ) as connection:
            owner_id = seed_user(connection)
            source_id = seed_source(connection, owner_id)
            seed_series(connection, owner_id)
            before = revision(connection, owner_id)

            connection.execute(
                "UPDATE calendar_sources SET name='普通重命名' WHERE id=%s",
                (source_id,),
            )
            assert revision(connection, owner_id) == before

            connection.execute(
                """
                UPDATE calendar_sources
                SET deleted_at=now(), purge_after=now() + interval '30 days'
                WHERE id=%s
                """,
                (source_id,),
            )
            assert revision(connection, owner_id) == before + 1

            connection.execute(
                """
                UPDATE calendar_sources
                SET deleted_at=NULL, purge_after=NULL
                WHERE id=%s
                """,
                (source_id,),
            )
            assert revision(connection, owner_id) == before + 2

            trigger = connection.execute(
                """
                SELECT trigger.tgname
                FROM pg_trigger AS trigger
                JOIN pg_class AS target ON target.oid=trigger.tgrelid
                WHERE NOT trigger.tgisinternal
                  AND target.relname='calendar_sources'
                  AND trigger.tgname='trg_planning_revision_calendar_sources_lifecycle'
                """
            ).fetchone()
            assert trigger == ("trg_planning_revision_calendar_sources_lifecycle",)


def test_calendar_lifecycle_migration_requires_empty_state_for_downgrade(admin_url):
    with isolated_database(admin_url) as database_url:
        with psycopg.connect(
            psycopg_url(database_url),
            autocommit=True,
        ) as connection:
            owner_id = seed_user(connection)
            source_id = seed_source(connection, owner_id)
            connection.execute(
                """
                UPDATE calendar_sources
                SET deleted_at=now(), purge_after=now() + interval '30 days'
                WHERE id=%s
                """,
                (source_id,),
            )

        with pytest.raises(subprocess.CalledProcessError):
            migrate(database_url, "downgrade", "0033_ai_fallback_routes")

        with psycopg.connect(
            psycopg_url(database_url),
            autocommit=True,
        ) as connection:
            connection.execute(
                """
                UPDATE calendar_sources
                SET deleted_at=NULL, purge_after=NULL, purged_at=NULL
                WHERE id=%s
                """,
                (source_id,),
            )

        migrate(database_url, "downgrade", "0033_ai_fallback_routes")
        with psycopg.connect(psycopg_url(database_url)) as connection:
            columns = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema='public'
                      AND table_name='calendar_sources'
                    """
                )
            }
            assert LIFECYCLE_COLUMNS.isdisjoint(columns)

        migrate(database_url, "upgrade", "head")
        migrate(database_url, "check")
