import subprocess
from uuid import uuid4

import psycopg
import pytest
from psycopg.errors import CheckViolation

from tests.conftest import isolated_database, migrate, psycopg_url

pytestmark = pytest.mark.integration


def seed_cost_graph(connection) -> tuple[str, str]:
    owner_id = uuid4()
    config_id = uuid4()
    media_id = uuid4()
    job_id = uuid4()
    lease_id = uuid4()
    connection.execute(
        """
        INSERT INTO users (id, email, password_hash, email_verified_at)
        VALUES (%s, %s, 'test-password-hash', now())
        """,
        (owner_id, f"{owner_id}@example.com"),
    )
    connection.execute(
        """
        INSERT INTO ai_config_versions (
            id, capability, version, endpoint_url, model, api_key_ciphertext,
            timeout_seconds, max_output_tokens, status, price_currency,
            input_price_nanos_per_million_tokens,
            cached_input_price_nanos_per_million_tokens,
            output_price_nanos_per_million_tokens
        ) VALUES (
            %s, 'calendar_screenshot', 1, 'https://ai.example.com/v1',
            'vision-model', %s, 60, 1024, 'draft', 'CNY',
            1000000000, 500000000, 2000000000
        )
        """,
        (config_id, b"encrypted-key"),
    )
    connection.execute(
        """
        INSERT INTO temporary_media (
            id, owner_id, purpose, original_filename, content_type,
            content_sha256, byte_size, width, height, encrypted_content, expires_at
        ) VALUES (
            %s, %s, 'calendar_screenshot', 'schedule.png', 'image/png',
            %s, 16, 2, 2, %s, now() + interval '1 day'
        )
        """,
        (media_id, owner_id, "a" * 64, b"encrypted-image"),
    )
    connection.execute(
        """
        INSERT INTO ai_jobs (
            id, owner_id, capability, config_version_id, input_media_id,
            status, input_summary, error_code, error_message, attempt_count,
            max_attempts, available_at, deadline_at, finished_at
        ) VALUES (
            %s, %s, 'calendar_screenshot', %s, %s, 'failed',
            '{"filename":"schedule.png"}', 'ai_output_invalid',
            '模型输出无法校验。', 1, 3, now(), now() + interval '3 minutes', now()
        )
        """,
        (job_id, owner_id, config_id, media_id),
    )
    connection.execute(
        """
        INSERT INTO ai_job_attempts (
            id, job_id, owner_id, attempt_number, lease_id, status,
            provider_started_at, started_at, finished_at,
            error_code, error_stage
        ) VALUES (
            %s, %s, %s, 1, %s, 'failed', now(), now(), now(),
            'ai_output_invalid', 'validation'
        )
        """,
        (uuid4(), job_id, owner_id, lease_id),
    )
    return str(owner_id), str(job_id)


def test_provider_cost_schema_constraints_and_immutability(admin_url):
    with isolated_database(admin_url) as database_url:
        with psycopg.connect(
            psycopg_url(database_url),
            autocommit=True,
        ) as connection:
            owner_id, job_id = seed_cost_graph(connection)
            cost_id = uuid4()
            connection.execute(
                """
                INSERT INTO ai_provider_costs (
                    id, job_id, owner_id, attempt_number, attempt_status,
                    provider_started_at, usage_source, input_tokens,
                    cached_input_tokens, output_tokens, total_tokens,
                    usage_details, price_currency,
                    input_price_nanos_per_million_tokens,
                    cached_input_price_nanos_per_million_tokens,
                    output_price_nanos_per_million_tokens,
                    input_cost_nanos, cached_input_cost_nanos,
                    output_cost_nanos, total_cost_nanos
                ) VALUES (
                    %s, %s, %s, 1, 'failed', now(), 'provider',
                    1000, 100, 500, 1500, '{"reported":true}', 'CNY',
                    1000000000, 500000000, 2000000000,
                    900000, 50000, 1000000, 1950000
                )
                """,
                (cost_id, job_id, owner_id),
            )
            stored = connection.execute(
                """
                SELECT usage_source, input_tokens, cached_input_tokens,
                       output_tokens, total_cost_nanos
                FROM ai_provider_costs
                WHERE id=%s
                """,
                (cost_id,),
            ).fetchone()
            assert stored == ("provider", 1000, 100, 500, 1_950_000)

            with pytest.raises(CheckViolation):
                connection.execute(
                    "UPDATE ai_provider_costs SET total_tokens=1501 WHERE id=%s",
                    (cost_id,),
                )

            with pytest.raises(CheckViolation):
                connection.execute(
                    """
                    INSERT INTO ai_config_versions (
                        id, capability, version, endpoint_url, model,
                        api_key_ciphertext, timeout_seconds, max_output_tokens,
                        status, price_currency
                    ) VALUES (
                        %s, 'calendar_screenshot', 2,
                        'https://ai.example.com/v1', 'vision-model',
                        %s, 60, 1024, 'draft', 'CNY'
                    )
                    """,
                    (uuid4(), b"encrypted-key"),
                )


def test_provider_cost_migration_round_trip_and_data_guard(admin_url):
    with isolated_database(admin_url) as database_url:
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT to_regclass('ai_provider_costs')"
            ).fetchone()[0] == "ai_provider_costs"
            columns = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name='ai_config_versions'
                    """
                )
            }
            assert "price_currency" in columns

        migrate(database_url, "downgrade", "0018_ai_governance")
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT to_regclass('ai_provider_costs')"
            ).fetchone()[0] is None
        migrate(database_url, "upgrade", "head")

        with psycopg.connect(
            psycopg_url(database_url),
            autocommit=True,
        ) as connection:
            seed_cost_graph(connection)
        with pytest.raises(subprocess.CalledProcessError) as error:
            migrate(database_url, "downgrade", "0018_ai_governance")
        assert "cannot downgrade 0019_ai_provider_costs" in error.value.stderr
