import subprocess
from uuid import uuid4

import psycopg
import pytest
from psycopg.errors import CheckViolation
from pydantic import ValidationError

from itime.config import Settings
from itime.database import Base
from itime.sync.models import SyncState
from itime.tasks.models import ChangeEvent
from tests.conftest import isolated_database, migrate, psycopg_url

pytestmark = pytest.mark.integration


def seed_user(connection) -> str:
    owner_id = str(uuid4())
    connection.execute(
        """
        INSERT INTO users (id, email, password_hash, email_verified_at)
        VALUES (%s, %s, 'test-password-hash', now())
        """,
        (owner_id, f"{owner_id}@example.com"),
    )
    return owner_id


def test_sync_migration_backfill_trigger_constraints_and_downgrade(admin_url):
    with isolated_database(admin_url) as database_url:
        migrate(database_url, "downgrade", "0027_data_exports")
        with psycopg.connect(psycopg_url(database_url)) as connection:
            existing_owner = seed_user(connection)
            connection.commit()

        migrate(database_url, "upgrade", "head")
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone() == ("0028_sync_feed",)
            assert connection.execute(
                "SELECT discarded_through_sequence FROM sync_states WHERE owner_id=%s",
                (existing_owner,),
            ).fetchone() == (0,)

            new_owner = seed_user(connection)
            assert connection.execute(
                "SELECT discarded_through_sequence FROM sync_states WHERE owner_id=%s",
                (new_owner,),
            ).fetchone() == (0,)

            conversation_id = uuid4()
            connection.execute(
                """
                INSERT INTO change_events (
                    owner_id, entity_type, entity_id, action, entity_version
                ) VALUES (%s, 'conversation', %s, 'updated', 1)
                """,
                (existing_owner, conversation_id),
            )
            with pytest.raises(CheckViolation):
                with connection.transaction():
                    connection.execute(
                        """
                        INSERT INTO change_events (
                            owner_id, entity_type, entity_id, action, entity_version
                        ) VALUES (%s, 'unknown', %s, 'updated', 1)
                        """,
                        (existing_owner, uuid4()),
                    )
            connection.commit()

        with pytest.raises(subprocess.CalledProcessError):
            migrate(database_url, "downgrade", "0027_data_exports")

        with psycopg.connect(psycopg_url(database_url)) as connection:
            connection.execute(
                "DELETE FROM change_events WHERE entity_type='conversation'"
            )
            connection.execute("DELETE FROM users WHERE id=%s", (existing_owner,))
            assert connection.execute(
                "SELECT count(*) FROM sync_states WHERE owner_id=%s",
                (existing_owner,),
            ).fetchone() == (0,)
            connection.commit()

        migrate(database_url, "downgrade", "0027_data_exports")
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT to_regclass('sync_states')"
            ).fetchone() == (None,)
        migrate(database_url, "upgrade", "head")
        migrate(database_url, "check")


def test_sync_model_metadata_and_retention_bounds(database_url):
    assert SyncState.__table__.name == "sync_states"
    assert ChangeEvent.__table__.name == "change_events"
    assert "sync_states" in Base.metadata.tables
    constraint_sql = " ".join(
        str(constraint.sqltext)
        for constraint in Base.metadata.tables["change_events"].constraints
        if getattr(constraint, "name", "") == "ck_change_events_entity_type"
    )
    for entity_type in (
        "review_session",
        "review_report",
        "review_annotation",
        "memory",
        "memory_settings",
        "conversation",
    ):
        assert entity_type in constraint_sql

    settings = Settings(
        environment="test",
        database_url=database_url,
        sync_event_retention_days=30,
    )
    assert settings.sync_event_retention_days == 30
    with pytest.raises(ValidationError):
        Settings(
            environment="test",
            database_url=database_url,
            sync_event_retention_days=6,
        )
    with pytest.raises(ValidationError):
        Settings(
            environment="test",
            database_url=database_url,
            sync_event_retention_days=91,
        )
