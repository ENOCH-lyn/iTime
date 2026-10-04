import subprocess
from uuid import uuid4

import psycopg
import pytest
from psycopg.errors import CheckViolation, ForeignKeyViolation, UniqueViolation

from itime.ai.models import AIConfigFallback, AIJobConfigRoute
from tests.conftest import isolated_database, migrate, psycopg_url
from tests.test_planning_models import seed_user

pytestmark = pytest.mark.integration

FALLBACK_COLUMNS = {
    "capability",
    "position",
    "config_version_id",
    "created_at",
    "updated_at",
}
ROUTE_COLUMNS = {
    "job_id",
    "attempt_number",
    "owner_id",
    "capability",
    "config_version_id",
    "created_at",
}


def seed_config(
    connection,
    capability: str,
    version: int,
    *,
    status: str = "tested",
):
    config_id = uuid4()
    tested = status in {"tested", "published", "retired"}
    published = status in {"published", "retired"}
    connection.execute(
        """
        INSERT INTO ai_config_versions (
            id, capability, version, endpoint_url, model,
            api_key_ciphertext, timeout_seconds, max_output_tokens,
            price_currency, input_price_nanos_per_million_tokens,
            cached_input_price_nanos_per_million_tokens,
            output_price_nanos_per_million_tokens, status,
            last_test_id, last_test_started_at, last_test_completed_at,
            published_at, retired_at
        ) VALUES (
            %s, %s, %s, 'https://ai.example.com/v1', %s,
            %s, 60, 1024, 'CNY', 1000000000, 500000000,
            2000000000, %s,
            CASE WHEN %s THEN %s ELSE NULL END,
            CASE WHEN %s THEN now() ELSE NULL END,
            CASE WHEN %s THEN now() ELSE NULL END,
            CASE WHEN %s THEN now() ELSE NULL END,
            CASE WHEN %s THEN now() ELSE NULL END
        )
        """,
        (
            config_id,
            capability,
            version,
            f"{capability}-model-{version}",
            b"encrypted-key",
            status,
            tested,
            uuid4(),
            tested,
            tested,
            published,
            status == "retired",
        ),
    )
    return config_id


def seed_job(connection, owner_id, config_id, *, max_attempts=3):
    media_id = uuid4()
    job_id = uuid4()
    connection.execute(
        """
        INSERT INTO temporary_media (
            id, owner_id, purpose, original_filename, content_type,
            content_sha256, byte_size, width, height,
            encrypted_content, expires_at
        ) VALUES (
            %s, %s, 'calendar_screenshot', 'route.png', 'image/png',
            %s, 16, 2, 2, %s, now() + interval '1 day'
        )
        """,
        (media_id, owner_id, "a" * 64, b"encrypted-image"),
    )
    connection.execute(
        """
        INSERT INTO ai_jobs (
            id, owner_id, capability, config_version_id, input_media_id,
            status, input_summary, attempt_count, max_attempts,
            available_at, deadline_at
        ) VALUES (
            %s, %s, 'calendar_screenshot', %s, %s,
            'queued', '{"filename":"route.png"}', 0, %s,
            now(), now() + interval '3 minutes'
        )
        """,
        (job_id, owner_id, config_id, media_id, max_attempts),
    )
    return job_id


def test_fallback_and_job_route_constraints_and_guards(admin_url):
    with isolated_database(admin_url) as database_url:
        with psycopg.connect(
            psycopg_url(database_url),
            autocommit=True,
        ) as connection:
            owner_id = seed_user(connection)
            primary_id = seed_config(
                connection,
                "calendar_screenshot",
                1,
                status="published",
            )
            backup_id = seed_config(connection, "calendar_screenshot", 2)
            other_capability_id = seed_config(connection, "conversation", 1)
            connection.execute(
                """
                UPDATE ai_config_capabilities
                SET current_version_id=%s, next_version=3
                WHERE capability='calendar_screenshot'
                """,
                (primary_id,),
            )
            job_id = seed_job(connection, owner_id, primary_id)

            fallback_columns = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema='public'
                      AND table_name='ai_config_fallbacks'
                    """
                )
            }
            route_columns = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema='public'
                      AND table_name='ai_job_config_routes'
                    """
                )
            }
            assert fallback_columns == FALLBACK_COLUMNS
            assert route_columns == ROUTE_COLUMNS
            assert set(AIConfigFallback.__table__.columns.keys()) == (
                FALLBACK_COLUMNS
            )
            assert set(AIJobConfigRoute.__table__.columns.keys()) == (
                ROUTE_COLUMNS
            )
            assert connection.execute(
                """
                SELECT routing_version FROM ai_config_capabilities
                WHERE capability='calendar_screenshot'
                """
            ).fetchone() == (1,)
            assert connection.execute(
                """
                SELECT attempt_number, config_version_id
                FROM ai_job_config_routes
                WHERE job_id=%s
                ORDER BY attempt_number
                """,
                (job_id,),
            ).fetchall() == [
                (1, primary_id),
                (2, primary_id),
                (3, primary_id),
            ]

            connection.execute(
                """
                INSERT INTO ai_config_fallbacks (
                    capability, position, config_version_id
                ) VALUES ('calendar_screenshot', 1, %s)
                """,
                (backup_id,),
            )
            connection.execute(
                """
                UPDATE ai_job_config_routes
                SET config_version_id=%s
                WHERE job_id=%s AND attempt_number IN (2, 3)
                """,
                (backup_id, job_id),
            )
            assert connection.execute(
                """
                SELECT config_version_id FROM ai_job_config_routes
                WHERE job_id=%s ORDER BY attempt_number
                """,
                (job_id,),
            ).fetchall() == [
                (primary_id,),
                (backup_id,),
                (backup_id,),
            ]

            with pytest.raises(CheckViolation):
                connection.execute(
                    """
                    INSERT INTO ai_config_fallbacks (
                        capability, position, config_version_id
                    ) VALUES ('calendar_screenshot', 3, %s)
                    """,
                    (backup_id,),
                )
            with pytest.raises(UniqueViolation):
                connection.execute(
                    """
                    INSERT INTO ai_config_fallbacks (
                        capability, position, config_version_id
                    ) VALUES ('calendar_screenshot', 2, %s)
                    """,
                    (backup_id,),
                )
            with pytest.raises(ForeignKeyViolation):
                connection.execute(
                    """
                    INSERT INTO ai_config_fallbacks (
                        capability, position, config_version_id
                    ) VALUES ('calendar_screenshot', 2, %s)
                    """,
                    (other_capability_id,),
                )

            connection.execute(
                """
                UPDATE ai_jobs
                SET status='running', attempt_count=1,
                    lease_id=%s, lease_until=now() + interval '1 minute'
                WHERE id=%s
                """,
                (uuid4(), job_id),
            )
            with pytest.raises(CheckViolation):
                connection.execute(
                    """
                    UPDATE ai_job_config_routes
                    SET config_version_id=%s
                    WHERE job_id=%s AND attempt_number=2
                    """,
                    (primary_id, job_id),
                )
            with pytest.raises(CheckViolation):
                connection.execute(
                    """
                    DELETE FROM ai_job_config_routes
                    WHERE job_id=%s AND attempt_number=2
                    """,
                    (job_id,),
                )

            connection.execute("DELETE FROM users WHERE id=%s", (owner_id,))
            assert connection.execute(
                "SELECT count(*) FROM ai_job_config_routes WHERE owner_id=%s",
                (owner_id,),
            ).fetchone() == (0,)


def test_fallback_route_migration_backfills_and_blocks_loss(admin_url):
    with isolated_database(admin_url) as database_url:
        migrate(database_url, "downgrade", "0032_subscription_exceptions")
        with psycopg.connect(
            psycopg_url(database_url),
            autocommit=True,
        ) as connection:
            owner_id = seed_user(connection)
            primary_id = seed_config(
                connection,
                "calendar_screenshot",
                1,
                status="published",
            )
            backup_id = seed_config(connection, "calendar_screenshot", 2)
            connection.execute(
                """
                UPDATE ai_config_capabilities
                SET current_version_id=%s, next_version=3
                WHERE capability='calendar_screenshot'
                """,
                (primary_id,),
            )
            job_id = seed_job(connection, owner_id, primary_id)

        migrate(database_url, "upgrade", "head")
        with psycopg.connect(
            psycopg_url(database_url),
            autocommit=True,
        ) as connection:
            assert connection.execute(
                """
                SELECT attempt_number, config_version_id
                FROM ai_job_config_routes
                WHERE job_id=%s ORDER BY attempt_number
                """,
                (job_id,),
            ).fetchall() == [
                (1, primary_id),
                (2, primary_id),
                (3, primary_id),
            ]
            connection.execute(
                """
                UPDATE ai_job_config_routes
                SET config_version_id=%s
                WHERE job_id=%s AND attempt_number=2
                """,
                (backup_id, job_id),
            )

        with pytest.raises(subprocess.CalledProcessError) as error:
            migrate(
                database_url,
                "downgrade",
                "0032_subscription_exceptions",
            )
        assert "cannot downgrade 0033_ai_fallback_routes" in error.value.stderr

        with psycopg.connect(
            psycopg_url(database_url),
            autocommit=True,
        ) as connection:
            connection.execute(
                """
                UPDATE ai_job_config_routes
                SET config_version_id=%s
                WHERE job_id=%s
                """,
                (primary_id, job_id),
            )
        migrate(database_url, "downgrade", "0032_subscription_exceptions")
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT to_regclass('ai_job_config_routes')"
            ).fetchone()[0] is None
            assert connection.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_name='ai_config_capabilities'
                  AND column_name='routing_version'
                """
            ).fetchone() is None
        migrate(database_url, "upgrade", "head")
        migrate(database_url, "check")
