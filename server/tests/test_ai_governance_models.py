import subprocess
from uuid import uuid4

import psycopg
import pytest

from tests.conftest import isolated_database, migrate, psycopg_url

pytestmark = pytest.mark.integration


def test_ai_governance_migration_round_trip_backfill_and_guard(admin_url):
    with isolated_database(admin_url) as database_url:
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT to_regclass('user_privacy_settings')"
            ).fetchone()[0] == "user_privacy_settings"
            assert connection.execute(
                "SELECT to_regclass('ai_body_logs')"
            ).fetchone()[0] == "ai_body_logs"
            assert connection.execute(
                "SELECT to_regclass('ai_quality_samples')"
            ).fetchone()[0] == "ai_quality_samples"
            assert connection.execute(
                """
                SELECT data_type
                FROM information_schema.columns
                WHERE table_name='security_events' AND column_name='details'
                """
            ).fetchone() == ("jsonb",)

        migrate(database_url, "downgrade", "0017_ai_conversations")
        owner_id = uuid4()
        with psycopg.connect(
            psycopg_url(database_url),
            autocommit=True,
        ) as connection:
            assert connection.execute(
                "SELECT to_regclass('user_privacy_settings')"
            ).fetchone()[0] is None
            connection.execute(
                """
                INSERT INTO users (
                    id, email, password_hash, email_verified_at
                ) VALUES (%s, %s, 'test-password-hash', now())
                """,
                (owner_id, f"{owner_id}@example.com"),
            )

        migrate(database_url, "upgrade", "head")
        with psycopg.connect(
            psycopg_url(database_url),
            autocommit=True,
        ) as connection:
            assert connection.execute(
                """
                SELECT quality_improvement_enabled, version
                FROM user_privacy_settings
                WHERE owner_id=%s
                """,
                (owner_id,),
            ).fetchone() == (True, 1)

        migrate(database_url, "downgrade", "0017_ai_conversations")
        migrate(database_url, "upgrade", "head")
        with psycopg.connect(
            psycopg_url(database_url),
            autocommit=True,
        ) as connection:
            connection.execute(
                """
                UPDATE user_privacy_settings
                SET quality_improvement_enabled=false, version=2
                WHERE owner_id=%s
                """,
                (owner_id,),
            )
        with pytest.raises(subprocess.CalledProcessError) as error:
            migrate(database_url, "downgrade", "0017_ai_conversations")
        assert "cannot downgrade 0018_ai_governance" in error.value.stderr
