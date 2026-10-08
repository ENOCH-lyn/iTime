import subprocess
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.errors import CheckViolation, UniqueViolation

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


def insert_pending_deletion(
    connection,
    owner_id: UUID,
    *,
    subject_digest: str = "a" * 64,
) -> UUID:
    deletion_id = uuid4()
    connection.execute(
        """
        INSERT INTO account_deletions (
            id, user_id, subject_digest, operation_id, status,
            requested_at, purge_after, available_at, request_id
        ) VALUES (
            %s, %s, %s, %s, 'pending',
            now(), now() + interval '7 days', now(), %s
        )
        """,
        (deletion_id, owner_id, subject_digest, uuid4(), str(uuid4())),
    )
    return deletion_id


def test_account_deletion_migration_constraints_and_downgrade(admin_url):
    with isolated_database(admin_url) as database_url:
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone() == ("0039_calendar_ai_import_sources",)

            owner_id = seed_user(connection)
            deletion_id = insert_pending_deletion(connection, owner_id)
            connection.execute(
                "UPDATE users SET status='pending_deletion' WHERE id=%s",
                (owner_id,),
            )
            connection.execute(
                """
                INSERT INTO temporary_media (
                    id, owner_id, purpose, original_filename, content_type,
                    content_sha256, byte_size, width, height,
                    encrypted_content, expires_at, deleted_at, deletion_reason
                ) VALUES (
                    %s, %s, 'calendar_screenshot', 'calendar.png', 'image/png',
                    %s, 1, 1, 1, NULL, now(), now(), 'account_deletion'
                )
                """,
                (uuid4(), owner_id, "b" * 64),
            )
            connection.commit()

            assert connection.execute(
                "SELECT status FROM users WHERE id=%s",
                (owner_id,),
            ).fetchone() == ("pending_deletion",)

            with pytest.raises(CheckViolation):
                with connection.transaction():
                    connection.execute(
                        """
                        UPDATE account_deletions
                        SET purge_after=requested_at
                        WHERE id=%s
                        """,
                        (deletion_id,),
                    )

            for invalid_digest in ("a" * 63, "A" * 64, "g" * 64):
                with pytest.raises(CheckViolation):
                    with connection.transaction():
                        connection.execute(
                            """
                            UPDATE account_deletions
                            SET subject_digest=%s
                            WHERE id=%s
                            """,
                            (invalid_digest, deletion_id),
                        )

            with pytest.raises(UniqueViolation):
                with connection.transaction():
                    insert_pending_deletion(connection, owner_id)

            with pytest.raises(CheckViolation):
                with connection.transaction():
                    connection.execute(
                        """
                        UPDATE account_deletions
                        SET status='processing', started_at=now()
                        WHERE id=%s
                        """,
                        (deletion_id,),
                    )

            lease_id = uuid4()
            connection.execute(
                """
                UPDATE account_deletions
                SET status='processing', started_at=now(),
                    lease_id=%s, lease_until=now() + interval '1 minute'
                WHERE id=%s
                """,
                (lease_id, deletion_id),
            )
            connection.commit()

            with pytest.raises(CheckViolation):
                with connection.transaction():
                    connection.execute(
                        """
                        UPDATE account_deletions
                        SET status='completed', completed_at=now(),
                            lease_id=NULL, lease_until=NULL
                        WHERE id=%s
                        """,
                        (deletion_id,),
                    )

            connection.execute(
                """
                UPDATE account_deletions
                SET status='cancelled', cancelled_at=now(),
                    lease_id=NULL, lease_until=NULL
                WHERE id=%s
                """,
                (deletion_id,),
            )
            connection.execute("DELETE FROM users WHERE id=%s", (owner_id,))
            connection.commit()
            assert connection.execute(
                "SELECT user_id FROM account_deletions WHERE id=%s",
                (deletion_id,),
            ).fetchone() == (None,)

        with pytest.raises(subprocess.CalledProcessError):
            migrate(database_url, "downgrade", "0028_sync_feed")

        with psycopg.connect(psycopg_url(database_url)) as connection:
            connection.execute("DELETE FROM account_deletions")
            connection.commit()

        migrate(database_url, "downgrade", "0028_sync_feed")
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT to_regclass('account_deletions')"
            ).fetchone() == (None,)

            active_owner_id = seed_user(connection)
            with pytest.raises(CheckViolation):
                with connection.transaction():
                    connection.execute(
                        "UPDATE users SET status='pending_deletion' WHERE id=%s",
                        (active_owner_id,),
                    )

        migrate(database_url, "upgrade", "head")
        migrate(database_url, "check")


def test_account_deletion_completed_receipt_and_orm_metadata(database_url):
    from itime.account_deletion.models import AccountDeletion
    from itime.database import Base

    with psycopg.connect(psycopg_url(database_url)) as connection:
        deletion_id = uuid4()
        connection.execute(
            """
            INSERT INTO account_deletions (
                id, user_id, subject_digest, operation_id, status,
                requested_at, purge_after, available_at,
                completed_at, request_id
            ) VALUES (
                %s, NULL, %s, %s, 'completed',
                now() - interval '8 days', now() - interval '1 day',
                now() - interval '1 day', now(), %s
            )
            """,
            (deletion_id, "c" * 64, uuid4(), str(uuid4())),
        )
        connection.commit()

        with pytest.raises(CheckViolation):
            with connection.transaction():
                connection.execute(
                    """
                    UPDATE account_deletions
                    SET user_id=%s
                    WHERE id=%s
                    """,
                    (seed_user(connection), deletion_id),
                )

    assert AccountDeletion.__table__.name == "account_deletions"
    assert "account_deletions" in Base.metadata.tables
    assert AccountDeletion.__table__.c.user_id.nullable
    assert AccountDeletion.__table__.c.operation_id.unique
