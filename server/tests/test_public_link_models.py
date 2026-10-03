from uuid import uuid4

import psycopg
import pytest
from psycopg.errors import CheckViolation

from itime.ai.conversation_models import ChatGeneration
from itime.tasks.models import TaskLink
from tests.conftest import isolated_database, migrate, psycopg_url

pytestmark = pytest.mark.integration

TASK_LINK_COLUMNS = {
    "id",
    "task_id",
    "owner_id",
    "title",
    "url",
    "position",
    "fetch_status",
    "fetch_revision",
    "attempt_count",
    "available_at",
    "lease_id",
    "lease_until",
    "last_attempt_at",
    "last_success_at",
    "final_host",
    "media_type",
    "page_title",
    "summary",
    "extracted_text",
    "content_sha256",
    "last_error_code",
    "last_error_message",
    "last_http_status",
    "updated_at",
    "created_at",
}


def seed_link(connection) -> tuple:
    owner_id = uuid4()
    task_id = uuid4()
    link_id = uuid4()
    connection.execute(
        """
        INSERT INTO users (id, email, password_hash, email_verified_at)
        VALUES (%s, %s, 'test-password-hash', now())
        """,
        (owner_id, f"{owner_id}@example.com"),
    )
    connection.execute(
        "INSERT INTO tasks (id, owner_id, title) VALUES (%s, %s, '公开资料')",
        (task_id, owner_id),
    )
    connection.execute(
        """
        INSERT INTO task_links (id, task_id, owner_id, title, url, position)
        VALUES (%s, %s, %s, '课程要求', 'https://example.com/article', 0)
        """,
        (link_id, task_id, owner_id),
    )
    connection.commit()
    return owner_id, task_id, link_id


def assert_check_violation(connection, statement: str, values: tuple) -> None:
    with pytest.raises(CheckViolation):
        with connection.transaction():
            connection.execute(statement, values)


def test_public_link_migration_constraints_triggers_and_orm(admin_url):
    with isolated_database(admin_url) as database_url:
        with psycopg.connect(psycopg_url(database_url)) as connection:
            owner_id, _, link_id = seed_link(connection)
            row = connection.execute(
                """
                SELECT fetch_status, fetch_revision, attempt_count,
                       available_at IS NOT NULL, lease_id, lease_until
                FROM task_links
                WHERE id=%s
                """,
                (link_id,),
            ).fetchone()
            assert row == ("queued", 1, 0, True, None, None)

            database_columns = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema='public' AND table_name='task_links'
                    """
                )
            }
            assert database_columns == TASK_LINK_COLUMNS
            assert set(TaskLink.__table__.columns.keys()) == TASK_LINK_COLUMNS
            assert "public_source_context" in ChatGeneration.__table__.columns

            trigger = connection.execute(
                """
                SELECT count(*)
                FROM pg_trigger
                WHERE tgname='trg_planning_revision_task_links'
                  AND NOT tgisinternal
                """
            ).fetchone()[0]
            assert trigger == 1
            revision_before = connection.execute(
                """
                SELECT revision
                FROM planning_input_revisions
                WHERE owner_id=%s
                """,
                (owner_id,),
            ).fetchone()[0]
            connection.execute(
                """
                UPDATE task_links
                SET fetch_status='running',
                    attempt_count=1,
                    lease_id=%s,
                    lease_until=now() + interval '30 seconds',
                    last_attempt_at=now(),
                    updated_at=now()
                WHERE id=%s
                """,
                (uuid4(), link_id),
            )
            connection.commit()
            assert connection.execute(
                """
                SELECT revision
                FROM planning_input_revisions
                WHERE owner_id=%s
                """,
                (owner_id,),
            ).fetchone()[0] == revision_before + 1

            assert_check_violation(
                connection,
                """
                UPDATE task_links
                SET fetch_status='running', lease_id=NULL, lease_until=NULL
                WHERE id=%s
                """,
                (link_id,),
            )
            assert_check_violation(
                connection,
                """
                UPDATE task_links
                SET fetch_status='ready', lease_id=NULL, lease_until=NULL
                WHERE id=%s
                """,
                (link_id,),
            )
            assert_check_violation(
                connection,
                "UPDATE task_links SET attempt_count=-1 WHERE id=%s",
                (link_id,),
            )
            assert_check_violation(
                connection,
                """
                UPDATE task_links
                SET content_sha256='not-a-hash'
                WHERE id=%s
                """,
                (link_id,),
            )
            assert_check_violation(
                connection,
                """
                UPDATE task_links
                SET fetch_status='failed',
                    lease_id=NULL,
                    lease_until=NULL,
                    last_error_code=NULL,
                    last_error_message=NULL
                WHERE id=%s
                """,
                (link_id,),
            )

            connection.execute(
                """
                INSERT INTO change_events (
                    owner_id, entity_type, entity_id, action, entity_version
                ) VALUES (%s, 'task_link', %s, 'updated', 1)
                """,
                (owner_id, link_id),
            )
            connection.commit()


def test_public_link_migration_downgrade_and_upgrade(admin_url):
    with isolated_database(admin_url) as database_url:
        with psycopg.connect(psycopg_url(database_url)) as connection:
            seed_link(connection)

        migrate(database_url, "downgrade", "0029_account_deletions")
        with psycopg.connect(psycopg_url(database_url)) as connection:
            task_link_columns = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema='public' AND table_name='task_links'
                    """
                )
            }
            generation_columns = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema='public'
                      AND table_name='ai_chat_generations'
                    """
                )
            }
            assert "fetch_status" not in task_link_columns
            assert "public_source_context" not in generation_columns

        migrate(database_url, "upgrade", "head")
        migrate(database_url, "check")
