import subprocess
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import psycopg
import pytest
from psycopg.types.json import Jsonb

from tests.conftest import isolated_database, migrate, psycopg_url
from tests.test_calendar_series import create_account

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def reset_ai_schema_test_data(database_url):
    def cleanup() -> None:
        with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
            connection.execute(
                """
                UPDATE ai_config_capabilities
                SET current_version_id=NULL, next_version=1, updated_at=now()
                """
            )
            connection.execute("DELETE FROM ai_jobs")
            connection.execute("DELETE FROM temporary_media")
            connection.execute("DELETE FROM ai_conversations")
            connection.execute(
                """
                DELETE FROM user_operations
                WHERE scope LIKE 'ai_conversation.%'
                   OR scope LIKE 'ai_message.%'
                   OR scope LIKE 'ai_job.cancel:%'
                """
            )
            connection.execute("DELETE FROM ai_config_versions")

    cleanup()
    yield
    cleanup()


@contextmanager
def browser(live_server):
    with httpx.Client(base_url=live_server.origin, timeout=10, trust_env=False) as client:
        yield client


def insert_config(connection, capability: str) -> str:
    version = connection.execute(
        """
        UPDATE ai_config_capabilities
        SET next_version=next_version + 1, updated_at=now()
        WHERE capability=%s
        RETURNING next_version - 1
        """,
        (capability,),
    ).fetchone()[0]
    config_id = uuid4()
    connection.execute(
        """
        INSERT INTO ai_config_versions (
            id, capability, version, endpoint_url, model, api_key_ciphertext,
            timeout_seconds, max_output_tokens, status
        ) VALUES (%s, %s, %s, 'https://ai.example.com/v1', 'chat-model',
                  %s, 60, 4096, 'draft')
        """,
        (config_id, capability, version, b"encrypted-key"),
    )
    return str(config_id)


def insert_messages(connection, owner_id: str, conversation_id: str, start: int):
    current = datetime.now(UTC)
    request_id = uuid4()
    response_id = uuid4()
    connection.execute(
        """
        INSERT INTO ai_conversation_messages (
            id, owner_id, conversation_id, sequence, role, status,
            content, completed_at
        ) VALUES
            (%s, %s, %s, %s, 'user', 'complete', '安排今天', %s),
            (%s, %s, %s, %s, 'assistant', 'queued', '', NULL)
        """,
        (
            request_id,
            owner_id,
            conversation_id,
            start,
            current,
            response_id,
            owner_id,
            conversation_id,
            start + 1,
        ),
    )
    return str(request_id), str(response_id)


def insert_job(
    connection,
    owner_id: str,
    config_id: str,
    conversation_id: str,
) -> str:
    current = datetime.now(UTC)
    job_id = uuid4()
    connection.execute(
        """
        INSERT INTO ai_jobs (
            id, owner_id, capability, config_version_id, input_media_id,
            conversation_id, status, input_summary, available_at, deadline_at
        ) VALUES (
            %s, %s, 'conversation', %s, NULL, %s, 'queued', %s, %s, %s
        )
        """,
        (
            job_id,
            owner_id,
            config_id,
            conversation_id,
            Jsonb({"kind": "conversation_reply"}),
            current,
            current + timedelta(minutes=3),
        ),
    )
    return str(job_id)


def insert_screenshot_job(
    connection,
    owner_id: str,
    config_id: str,
) -> str:
    media_id = uuid4()
    job_id = uuid4()
    connection.execute(
        """
        INSERT INTO temporary_media (
            id, owner_id, purpose, original_filename, content_type,
            content_sha256, byte_size, width, height, encrypted_content, expires_at
        ) VALUES (
            %s, %s, 'calendar_screenshot', 'schedule.png', 'image/png',
            %s, 16, 2, 2, %s, now() + interval '24 hours'
        )
        """,
        (media_id, owner_id, "a" * 64, b"encrypted-image"),
    )
    connection.execute(
        """
        INSERT INTO ai_jobs (
            id, owner_id, capability, config_version_id, input_media_id,
            conversation_id, status, input_summary, available_at, deadline_at
        ) VALUES (
            %s, %s, 'calendar_screenshot', %s, %s, NULL, 'queued',
            '{}'::jsonb, now(), now() + interval '3 minutes'
        )
        """,
        (job_id, owner_id, config_id, media_id),
    )
    return str(job_id)


def test_conversation_schema_enforces_owner_media_and_active_generation(
    client,
    live_server,
    database_url,
):
    _, owner_id = create_account(client, database_url)
    with browser(live_server) as other:
        _, other_owner_id = create_account(other, database_url)

    conversation_id = uuid4()
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        connection.execute(
            """
            INSERT INTO ai_conversations (id, owner_id, title)
            VALUES (%s, %s, '新对话')
            """,
            (conversation_id, owner_id),
        )
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            connection.execute(
                """
                INSERT INTO ai_conversation_messages (
                    id, owner_id, conversation_id, sequence, role, status,
                    content, completed_at
                ) VALUES (%s, %s, %s, 1, 'user', 'complete', '越权消息', now())
                """,
                (uuid4(), other_owner_id, conversation_id),
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                """
                INSERT INTO ai_conversation_messages (
                    id, owner_id, conversation_id, sequence, role, status,
                    content, completed_at
                ) VALUES (%s, %s, %s, 1, 'assistant', 'complete', '', now())
                """,
                (uuid4(), owner_id, conversation_id),
            )

        conversation_config = insert_config(connection, "conversation")
        screenshot_config = insert_config(connection, "calendar_screenshot")
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                """
                INSERT INTO ai_jobs (
                    id, owner_id, capability, config_version_id, input_media_id,
                    status, input_summary, available_at, deadline_at
                ) VALUES (
                    %s, %s, 'calendar_screenshot', %s, NULL, 'queued',
                    '{}'::jsonb, now(), now() + interval '3 minutes'
                )
                """,
                (uuid4(), owner_id, screenshot_config),
            )

        with pytest.raises(psycopg.errors.CheckViolation):
            with connection.transaction():
                insert_job(
                    connection,
                    owner_id,
                    conversation_config,
                    str(conversation_id),
                )

        with connection.transaction():
            request_id, response_id = insert_messages(
                connection,
                owner_id,
                str(conversation_id),
                1,
            )
            first_job = insert_job(
                connection,
                owner_id,
                conversation_config,
                str(conversation_id),
            )
            connection.execute(
                """
                INSERT INTO ai_chat_generations (
                    job_id, owner_id, conversation_id, request_message_id,
                    response_message_id, history_through_sequence
                ) VALUES (%s, %s, %s, %s, %s, 3)
                """,
                (
                    first_job,
                    owner_id,
                    conversation_id,
                    request_id,
                    response_id,
                ),
            )

        with pytest.raises(psycopg.errors.UniqueViolation):
            insert_job(
                connection,
                owner_id,
                conversation_config,
                str(conversation_id),
            )

        with connection.transaction():
            connection.execute(
                """
                UPDATE ai_jobs
                SET status='failed', error_code='test_failure',
                    error_message='测试失败', finished_at=now(), updated_at=now()
                WHERE id=%s
                """,
                (first_job,),
            )
            connection.execute(
                """
                UPDATE ai_conversation_messages
                SET status='failed', error_code='test_failure',
                    error_message='测试失败', completed_at=now(), updated_at=now()
                WHERE id=%s
                """,
                (response_id,),
            )

        with connection.transaction():
            request_id, response_id = insert_messages(
                connection,
                owner_id,
                str(conversation_id),
                3,
            )
            second_job = insert_job(
                connection,
                owner_id,
                conversation_config,
                str(conversation_id),
            )
            connection.execute(
                """
                INSERT INTO ai_chat_generations (
                    job_id, owner_id, conversation_id, request_message_id,
                    response_message_id, history_through_sequence
                ) VALUES (%s, %s, %s, %s, %s, 3)
                """,
                (
                    second_job,
                    owner_id,
                    conversation_id,
                    request_id,
                    response_id,
                ),
            )
        assert connection.execute(
            """
            SELECT count(*) FROM ai_chat_generations
            WHERE conversation_id=%s
            """,
            (conversation_id,),
        ).fetchone()[0] == 2

        with connection.transaction():
            request_id, response_id = insert_messages(
                connection,
                owner_id,
                str(conversation_id),
                5,
            )
            screenshot_job = insert_screenshot_job(
                connection,
                owner_id,
                screenshot_config,
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            with connection.transaction():
                connection.execute(
                    """
                    INSERT INTO ai_chat_generations (
                        job_id, owner_id, conversation_id, request_message_id,
                        response_message_id, history_through_sequence
                    ) VALUES (%s, %s, %s, %s, %s, 5)
                    """,
                    (
                        screenshot_job,
                        owner_id,
                        conversation_id,
                        request_id,
                        response_id,
                    ),
                )


def test_ai_conversation_migration_round_trip_and_guard(admin_url):
    with isolated_database(admin_url) as database_url:
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT to_regclass('ai_conversations')"
            ).fetchone()[0] == "ai_conversations"
            assert connection.execute(
                """
                SELECT is_nullable FROM information_schema.columns
                WHERE table_name='ai_jobs' AND column_name='input_media_id'
                """
            ).fetchone()[0] == "YES"
            assert connection.execute(
                """
                SELECT capability FROM ai_config_capabilities
                WHERE capability='conversation'
                """
            ).fetchone() == ("conversation",)
            trigger_names = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT tgname FROM pg_trigger
                    WHERE tgname IN (
                        'trg_ai_jobs_conversation_mapping',
                        'trg_ai_chat_generations_job_mapping',
                        'trg_ai_conversation_messages_immutable'
                    )
                    """
                )
            }
            assert trigger_names == {
                "trg_ai_jobs_conversation_mapping",
                "trg_ai_chat_generations_job_mapping",
                "trg_ai_conversation_messages_immutable",
            }

        migrate(database_url, "downgrade", "0016_task_lifecycle")
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT to_regclass('ai_conversations')"
            ).fetchone()[0] is None
            assert connection.execute(
                """
                SELECT is_nullable FROM information_schema.columns
                WHERE table_name='ai_jobs' AND column_name='input_media_id'
                """
            ).fetchone()[0] == "NO"
        migrate(database_url, "upgrade", "head")

        owner_id = uuid4()
        with psycopg.connect(
            psycopg_url(database_url),
            autocommit=True,
        ) as connection:
            connection.execute(
                """
                INSERT INTO users (
                    id, email, password_hash, email_verified_at
                ) VALUES (%s, %s, 'test-hash', now())
                """,
                (owner_id, f"{owner_id}@example.com"),
            )
            connection.execute(
                """
                INSERT INTO ai_conversations (id, owner_id, title)
                VALUES (%s, %s, '保留的对话')
                """,
                (uuid4(), owner_id),
            )
        with pytest.raises(subprocess.CalledProcessError) as error:
            migrate(database_url, "downgrade", "0016_task_lifecycle")
        assert "cannot downgrade 0017_ai_conversations" in error.value.stderr
