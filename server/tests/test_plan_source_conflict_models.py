from uuid import uuid4

import psycopg
import pytest
from psycopg.errors import CheckViolation, ForeignKeyViolation, UniqueViolation

from itime.planning.models import PlanSourceConflict
from tests.conftest import isolated_database, migrate, psycopg_url
from tests.test_planning_models import seed_plan_version, seed_user

pytestmark = pytest.mark.integration

CONFLICT_COLUMNS = {
    "id",
    "owner_id",
    "plan_id",
    "plan_version_id",
    "source_id",
    "source_generation",
    "status",
    "resolution",
    "changes",
    "affected_block_ids",
    "details_limited",
    "version",
    "detected_at",
    "resolved_at",
}


def seed_source(connection, owner_id):
    source_id = uuid4()
    connection.execute(
        """
        INSERT INTO calendar_sources (id, owner_id, kind, name)
        VALUES (%s, %s, 'ics_subscription', '学校课表')
        """,
        (source_id, owner_id),
    )
    return source_id


def seed_conflict(connection):
    owner_id = seed_user(connection)
    plan_id, plan_version_id = seed_plan_version(connection, owner_id)
    source_id = seed_source(connection, owner_id)
    conflict_id = uuid4()
    connection.execute(
        """
        INSERT INTO plan_source_conflicts (
            id, owner_id, plan_id, plan_version_id, source_id,
            source_generation, status, resolution, changes,
            affected_block_ids, details_limited, version,
            detected_at, resolved_at
        ) VALUES (
            %s, %s, %s, %s, %s,
            2, 'open', NULL,
            '[{"kind":"moved","occurrence_key":"subscription:test"}]'::jsonb,
            '[]'::jsonb, false, 1, now(), NULL
        )
        """,
        (conflict_id, owner_id, plan_id, plan_version_id, source_id),
    )
    connection.commit()
    return owner_id, plan_id, plan_version_id, source_id, conflict_id


def assert_check_violation(connection, statement: str, values: tuple) -> None:
    with pytest.raises(CheckViolation):
        with connection.transaction():
            connection.execute(statement, values)


def test_plan_source_conflict_constraints_ownership_and_cascade(admin_url):
    with isolated_database(admin_url) as database_url:
        with psycopg.connect(psycopg_url(database_url)) as connection:
            owner_id, plan_id, plan_version_id, source_id, conflict_id = (
                seed_conflict(connection)
            )
            columns = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema='public'
                      AND table_name='plan_source_conflicts'
                    """
                )
            }
            assert columns == CONFLICT_COLUMNS
            assert set(PlanSourceConflict.__table__.columns.keys()) == CONFLICT_COLUMNS

            assert_check_violation(
                connection,
                """
                UPDATE plan_source_conflicts
                SET status='resolved', resolution=NULL, resolved_at=NULL
                WHERE id=%s
                """,
                (conflict_id,),
            )
            assert_check_violation(
                connection,
                """
                UPDATE plan_source_conflicts
                SET status='open', resolution='kept', resolved_at=now()
                WHERE id=%s
                """,
                (conflict_id,),
            )
            assert_check_violation(
                connection,
                """
                UPDATE plan_source_conflicts
                SET changes='{}'::jsonb
                WHERE id=%s
                """,
                (conflict_id,),
            )
            assert_check_violation(
                connection,
                """
                UPDATE plan_source_conflicts
                SET changes='[]'::jsonb, details_limited=false
                WHERE id=%s
                """,
                (conflict_id,),
            )
            assert_check_violation(
                connection,
                """
                UPDATE plan_source_conflicts
                SET affected_block_ids='{}'::jsonb
                WHERE id=%s
                """,
                (conflict_id,),
            )
            assert_check_violation(
                connection,
                """
                UPDATE plan_source_conflicts
                SET source_generation=0
                WHERE id=%s
                """,
                (conflict_id,),
            )
            assert_check_violation(
                connection,
                """
                UPDATE plan_source_conflicts
                SET version=0
                WHERE id=%s
                """,
                (conflict_id,),
            )

            with pytest.raises(UniqueViolation):
                with connection.transaction():
                    connection.execute(
                        """
                        INSERT INTO plan_source_conflicts (
                            id, owner_id, plan_id, plan_version_id, source_id,
                            source_generation, status, changes,
                            affected_block_ids, details_limited, version,
                            detected_at
                        ) VALUES (
                            %s, %s, %s, %s, %s, 2, 'open',
                            '[{"kind":"added"}]'::jsonb, '[]'::jsonb,
                            false, 1, now()
                        )
                        """,
                        (
                            uuid4(),
                            owner_id,
                            plan_id,
                            plan_version_id,
                            source_id,
                        ),
                    )

            other_owner_id = seed_user(connection)
            other_source_id = seed_source(connection, other_owner_id)
            connection.commit()
            with pytest.raises(ForeignKeyViolation):
                with connection.transaction():
                    connection.execute(
                        """
                        INSERT INTO plan_source_conflicts (
                            id, owner_id, plan_id, plan_version_id, source_id,
                            source_generation, status, changes,
                            affected_block_ids, details_limited, version,
                            detected_at
                        ) VALUES (
                            %s, %s, %s, %s, %s, 3, 'open',
                            '[{"kind":"added"}]'::jsonb, '[]'::jsonb,
                            false, 1, now()
                        )
                        """,
                        (
                            uuid4(),
                            owner_id,
                            plan_id,
                            plan_version_id,
                            other_source_id,
                        ),
                    )

            connection.execute(
                "DELETE FROM users WHERE id=%s",
                (owner_id,),
            )
            connection.commit()
            assert connection.execute(
                "SELECT count(*) FROM plan_source_conflicts WHERE owner_id=%s",
                (owner_id,),
            ).fetchone()[0] == 0


def test_plan_source_conflict_migration_downgrade_and_upgrade(admin_url):
    with isolated_database(admin_url) as database_url:
        migrate(database_url, "downgrade", "0030_public_task_links")
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT to_regclass('plan_source_conflicts')"
            ).fetchone()[0] is None

        migrate(database_url, "upgrade", "head")
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()[0] == "0039_calendar_ai_import_sources"
        migrate(database_url, "check")
