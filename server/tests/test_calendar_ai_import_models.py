from uuid import uuid4

import psycopg
import pytest

from itime.calendar.models import CalendarImportBatch
from itime.database import Base
from tests.conftest import isolated_database, migrate, psycopg_url

pytestmark = pytest.mark.integration


def _seed_user(connection) -> str:
    owner_id = str(uuid4())
    connection.execute(
        """
        INSERT INTO users (id, email, password_hash, email_verified_at)
        VALUES (%s, %s, 'test-password-hash', now())
        """,
        (owner_id, f"{owner_id}@example.com"),
    )
    return owner_id


def test_ai_import_model_has_canonical_sources_and_recovery_fields():
    table = CalendarImportBatch.__table__

    assert {
        "source_type",
        "source_display",
        "input_summary",
        "input_body_ciphertext",
        "input_body_expires_at",
        "conversation_id",
        "message_id",
        "error_code",
        "error_message",
    } <= set(table.c.keys())
    constraint_sql = " ".join(
        str(constraint.sqltext)
        for constraint in table.constraints
        if hasattr(constraint, "sqltext")
    )
    for source_type in (
        "xlsx_file",
        "ics_file",
        "image",
        "public_url",
        "text",
    ):
        assert source_type in constraint_sql
    for state in (
        "queued",
        "processing",
        "needs_review",
        "confirmed",
        "cancelled",
        "failed",
        "expired",
    ):
        assert state in constraint_sql
    assert {
        constraint.name
        for constraint in table.constraints
        if constraint.name is not None
    } >= {
        "fk_calendar_import_batches_conversation_owner",
        "fk_calendar_import_batches_message_owner_conversation",
    }
    assert "calendar_import_batches" in Base.metadata.tables


def test_ai_import_migration_backfills_old_batches_and_supports_queued_text(
    admin_url,
):
    with isolated_database(admin_url) as database_url:
        migrate(database_url, "downgrade", "0038_agent_task_plan")
        with psycopg.connect(psycopg_url(database_url)) as connection:
            owner_id = _seed_user(connection)
            source_id = str(uuid4())
            batch_id = str(uuid4())
            connection.execute(
                """
                INSERT INTO calendar_sources (id, owner_id, kind, name)
                VALUES (%s, %s, 'screenshot', 'legacy.png')
                """,
                (source_id, owner_id),
            )
            connection.execute(
                """
                INSERT INTO calendar_import_batches (
                    id, owner_id, source_id, format, filename, content_sha256,
                    state, candidate_count, expires_at
                ) VALUES (
                    %s, %s, %s, 'screenshot', 'legacy.png', %s,
                    'ready', 1, now() + interval '7 days'
                )
                """,
                (batch_id, owner_id, source_id, "a" * 64),
            )
            connection.commit()

        migrate(database_url, "upgrade", "head")
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone() == ("0041_ai_import_source_check",)
            assert connection.execute(
                """
                SELECT source_type, state, input_summary
                FROM calendar_import_batches
                WHERE id=%s
                """,
                (batch_id,),
            ).fetchone() == ("image", "needs_review", {})

            text_source_id = str(uuid4())
            text_batch_id = str(uuid4())
            connection.execute(
                """
                INSERT INTO calendar_sources (id, owner_id, kind, name)
                VALUES (%s, %s, 'text', '文字描述')
                """,
                (text_source_id, owner_id),
            )
            connection.execute(
                """
                INSERT INTO calendar_import_batches (
                    id, owner_id, source_id, format, source_type, filename,
                    content_sha256, state, candidate_count, input_summary,
                    input_body_ciphertext, input_body_expires_at, expires_at
                ) VALUES (
                    %s, %s, %s, 'text', 'text', '文字描述',
                    %s, 'queued', 0, '{"character_count": 18}',
                    %s, now() + interval '7 days', now() + interval '7 days'
                )
                """,
                (
                    text_batch_id,
                    owner_id,
                    text_source_id,
                    "b" * 64,
                    b"encrypted",
                ),
            )
            connection.commit()
            assert connection.execute(
                """
                SELECT source_type, state, candidate_count
                FROM calendar_import_batches
                WHERE id=%s
                """,
                (text_batch_id,),
            ).fetchone() == ("text", "queued", 0)

            connection.execute(
                "DELETE FROM calendar_import_batches WHERE id=%s",
                (text_batch_id,),
            )
            connection.execute(
                "DELETE FROM calendar_sources WHERE id=%s",
                (text_source_id,),
            )
            connection.commit()

        migrate(database_url, "downgrade", "0038_agent_task_plan")
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                """
                SELECT format, state
                FROM calendar_import_batches
                WHERE id=%s
                """,
                (batch_id,),
            ).fetchone() == ("screenshot", "ready")
            assert connection.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name='calendar_import_batches'
                  AND column_name='source_type'
                """
            ).fetchone() is None
        migrate(database_url, "upgrade", "head")
        migrate(database_url, "check")
