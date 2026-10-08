from uuid import UUID, uuid4

import psycopg
import pytest
from cryptography.fernet import Fernet
from psycopg.errors import CheckViolation, ForeignKeyViolation, UniqueViolation
from pydantic import ValidationError

from itime.config import Settings
from itime.data_export.models import DataExportJob
from itime.database import Base
from tests.conftest import isolated_database, migrate, psycopg_url

pytestmark = pytest.mark.integration


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


def insert_queued_job(connection, owner_id: UUID) -> UUID:
    job_id = uuid4()
    connection.execute(
        """
        INSERT INTO data_export_jobs (
            id, owner_id, status, schema_version, attempt_count,
            max_attempts, available_at
        ) VALUES (%s, %s, 'queued', 1, 0, 3, now())
        """,
        (job_id, owner_id),
    )
    return job_id


def test_data_export_migration_round_trip_and_orm_consistency(admin_url):
    with isolated_database(admin_url) as database_url:
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert (
                connection.execute(
                    "SELECT version_num FROM alembic_version"
                ).fetchone()[0]
                    == "0039_calendar_ai_import_sources"
            )
            assert connection.execute(
                "SELECT to_regclass('data_export_jobs')"
            ).fetchone() == ("data_export_jobs",)
            assert DataExportJob.__table__.name == "data_export_jobs"
            assert "data_export_jobs" in Base.metadata.tables

        migrate(database_url, "downgrade", "0026_review_memory")
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT to_regclass('data_export_jobs')"
            ).fetchone() == (None,)

        migrate(database_url, "upgrade", "head")
        migrate(database_url, "check")


def test_data_export_owner_active_unique_state_and_cascade(database_url):
    with psycopg.connect(psycopg_url(database_url)) as connection:
        owner_id = seed_user(connection)
        insert_queued_job(connection, owner_id)
        connection.commit()

        with pytest.raises(UniqueViolation):
            with connection.transaction():
                insert_queued_job(connection, owner_id)

        with pytest.raises(CheckViolation):
            with connection.transaction():
                connection.execute(
                    """
                    INSERT INTO data_export_jobs (
                        id, owner_id, status, schema_version, attempt_count,
                        max_attempts, available_at, snapshot_at, finished_at
                    ) VALUES (
                        %s, %s, 'succeeded', 1, 1, 3, now(), now(), now()
                    )
                    """,
                    (uuid4(), owner_id),
                )

        with pytest.raises(ForeignKeyViolation):
            with connection.transaction():
                insert_queued_job(connection, uuid4())

        connection.execute("DELETE FROM users WHERE id=%s", (owner_id,))
        connection.commit()
        assert connection.execute(
            "SELECT count(*) FROM data_export_jobs WHERE owner_id=%s",
            (owner_id,),
        ).fetchone() == (0,)


def test_export_key_must_be_distinct_in_production(database_url):
    keys = [Fernet.generate_key().decode() for _ in range(4)]
    common = {
        "database_url": database_url,
        "environment": "production",
        "public_origin": "https://itime.example",
        "encryption_key": keys[0],
        "ai_encryption_key": keys[1],
        "calendar_subscription_encryption_key": keys[2],
    }

    with pytest.raises(ValidationError):
        Settings(**common, export_encryption_key=keys[0])
    with pytest.raises(ValidationError):
        Settings(**common, export_encryption_key=keys[1])
    with pytest.raises(ValidationError):
        Settings(**common, export_encryption_key=keys[2])

    settings = Settings(**common, export_encryption_key=keys[3])
    assert settings.export_encryption_key is not None
    assert settings.export_encryption_key.get_secret_value() == keys[3]
