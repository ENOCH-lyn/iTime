import subprocess
from uuid import uuid4

import psycopg
import pytest
from psycopg.errors import CheckViolation, RaiseException, UniqueViolation

from tests.conftest import isolated_database, migrate, psycopg_url

pytestmark = pytest.mark.integration


def seed_quota_graph(connection) -> tuple[str, str, str, str]:
    owner_id = uuid4()
    policy_id = uuid4()
    config_id = uuid4()
    media_id = uuid4()
    job_id = uuid4()
    daily_id = uuid4()
    reservation_id = uuid4()
    connection.execute(
        """
        INSERT INTO users (id, email, password_hash, email_verified_at)
        VALUES (%s, %s, 'test-password-hash', now())
        """,
        (owner_id, f"{owner_id}@example.com"),
    )
    connection.execute(
        """
        INSERT INTO user_time_settings (owner_id, time_zone)
        VALUES (%s, 'Asia/Shanghai')
        """,
        (owner_id,),
    )
    connection.execute(
        """
        INSERT INTO ai_quota_policy_versions (
            id, version, currency, daily_allowance_nanos,
            reservation_estimator_version, status, published_at
        ) VALUES (
            %s, 1, 'CNY', 1000000000,
            'reservation-utf8-vision-v1', 'published', now()
        )
        """,
        (policy_id,),
    )
    connection.execute(
        """
        UPDATE ai_quota_policy_state
        SET current_version_id=%s, next_version=2, updated_at=now()
        WHERE id=1
        """,
        (policy_id,),
    )
    version = connection.execute(
        """
        UPDATE ai_config_capabilities
        SET next_version=next_version + 1
        WHERE capability='calendar_screenshot'
        RETURNING next_version - 1
        """
    ).fetchone()[0]
    connection.execute(
        """
        INSERT INTO ai_config_versions (
            id, capability, version, endpoint_url, model,
            api_key_ciphertext, timeout_seconds, max_output_tokens,
            price_currency, input_price_nanos_per_million_tokens,
            cached_input_price_nanos_per_million_tokens,
            output_price_nanos_per_million_tokens, status
        ) VALUES (
            %s, 'calendar_screenshot', %s, 'https://ai.example.com/v1',
            'quota-model', %s, 60, 1024, 'CNY',
            1000000000, 500000000, 2000000000, 'draft'
        )
        """,
        (config_id, version, b"encrypted-key"),
    )
    connection.execute(
        """
        INSERT INTO temporary_media (
            id, owner_id, purpose, original_filename, content_type,
            content_sha256, byte_size, width, height,
            encrypted_content, expires_at
        ) VALUES (
            %s, %s, 'calendar_screenshot', 'quota.png', 'image/png',
            %s, 16, 2, 2, %s, now() + interval '1 day'
        )
        """,
        (media_id, owner_id, "a" * 64, b"encrypted-image"),
    )
    connection.execute(
        """
        INSERT INTO ai_daily_allowances (
            id, owner_id, activity_date, window_start_at, window_end_at,
            time_zone, activity_day_start_minute, policy_version_id,
            currency, base_amount_nanos
        ) VALUES (
            %s, %s, DATE '2026-10-03',
            TIMESTAMPTZ '2026-10-02 20:00:00+00',
            TIMESTAMPTZ '2026-10-03 20:00:00+00',
            'Asia/Shanghai', 240, %s, 'CNY', 1000000000
        )
        """,
        (daily_id, owner_id, policy_id),
    )
    connection.execute(
        """
        INSERT INTO ai_jobs (
            id, owner_id, capability, config_version_id, input_media_id,
            quota_mode, status, input_summary, available_at, deadline_at
        ) VALUES (
            %s, %s, 'calendar_screenshot', %s, %s, 'user', 'queued',
            '{"filename":"quota.png"}', now(), now() + interval '3 minutes'
        )
        """,
        (job_id, owner_id, config_id, media_id),
    )
    connection.execute(
        """
        INSERT INTO ai_quota_reservations (
            id, job_id, owner_id, daily_allowance_id, status,
            reserved_nanos, estimator_version, accepted_at
        ) VALUES (
            %s, %s, %s, %s, 'reserved',
            500000000, 'reservation-utf8-vision-v1', now()
        )
        """,
        (reservation_id, job_id, owner_id, daily_id),
    )
    return str(owner_id), str(job_id), str(daily_id), str(reservation_id)


def test_quota_schema_constraints_and_immutable_ledger(admin_url):
    with isolated_database(admin_url) as database_url:
        with psycopg.connect(psycopg_url(database_url)) as connection:
            owner_id, job_id, daily_id, reservation_id = seed_quota_graph(connection)
            connection.commit()

            with pytest.raises(UniqueViolation):
                with connection.transaction():
                    connection.execute(
                        """
                        INSERT INTO ai_quota_reservations (
                            id, job_id, owner_id, daily_allowance_id, status,
                            reserved_nanos, estimator_version, accepted_at
                        ) VALUES (
                            %s, %s, %s, %s, 'reserved',
                            1, 'reservation-utf8-vision-v1', now()
                        )
                        """,
                        (uuid4(), job_id, owner_id, daily_id),
                    )

            adjustment_id = uuid4()
            ledger_id = uuid4()
            with connection.transaction():
                connection.execute(
                    """
                    INSERT INTO ai_quota_admin_adjustments (
                        id, operation_id, request_hash, target_owner_id,
                        daily_allowance_id, delta_nanos, reason, request_id
                    ) VALUES (
                        %s, %s, %s, %s, %s, 100000000,
                        '人工补偿', %s
                    )
                    """,
                    (
                        adjustment_id,
                        uuid4(),
                        "b" * 64,
                        owner_id,
                        daily_id,
                        str(uuid4()),
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO ai_quota_ledger_entries (
                        id, owner_id, daily_allowance_id, adjustment_id,
                        kind, delta_nanos, currency
                    ) VALUES (
                        %s, %s, %s, %s, 'admin_credit', 100000000, 'CNY'
                    )
                    """,
                    (ledger_id, owner_id, daily_id, adjustment_id),
                )

            with pytest.raises(CheckViolation):
                with connection.transaction():
                    connection.execute(
                        """
                        UPDATE ai_quota_ledger_entries
                        SET delta_nanos=200000000
                        WHERE id=%s
                        """,
                        (ledger_id,),
                    )

            with pytest.raises(CheckViolation):
                with connection.transaction():
                    connection.execute(
                        """
                        UPDATE ai_quota_reservations
                        SET status='settled'
                        WHERE id=%s
                        """,
                        (reservation_id,),
                    )

            orphan_media_id = uuid4()
            with pytest.raises(RaiseException):
                with connection.transaction():
                    connection.execute(
                        """
                        INSERT INTO temporary_media (
                            id, owner_id, purpose, original_filename,
                            content_type, content_sha256, byte_size,
                            width, height, encrypted_content, expires_at
                        ) VALUES (
                            %s, %s, 'calendar_screenshot', 'orphan.png',
                            'image/png', %s, 16, 2, 2, %s,
                            now() + interval '1 day'
                        )
                        """,
                        (
                            orphan_media_id,
                            owner_id,
                            "c" * 64,
                            b"encrypted-image",
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO ai_jobs (
                            id, owner_id, capability, config_version_id,
                            input_media_id, quota_mode, status, input_summary,
                            available_at, deadline_at
                        )
                        SELECT %s, %s, 'calendar_screenshot',
                               config_version_id, %s, 'user', 'queued',
                               '{"filename":"orphan.png"}',
                               now(), now() + interval '3 minutes'
                        FROM ai_jobs WHERE id=%s
                        """,
                        (uuid4(), owner_id, orphan_media_id, job_id),
                    )

            with connection.transaction():
                connection.execute("DELETE FROM users WHERE id=%s", (owner_id,))
            assert connection.execute(
                "SELECT count(*) FROM ai_daily_allowances WHERE owner_id=%s",
                (owner_id,),
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT count(*) FROM ai_quota_ledger_entries WHERE owner_id=%s",
                (owner_id,),
            ).fetchone()[0] == 0


def test_quota_migration_round_trip_and_data_guard(admin_url):
    with isolated_database(admin_url) as database_url:
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT to_regclass('ai_quota_policy_versions')"
            ).fetchone()[0] == "ai_quota_policy_versions"
            assert connection.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name='ai_jobs' AND column_name='quota_mode'
                """
            ).fetchone() == ("quota_mode",)

        migrate(database_url, "downgrade", "0020_ai_provider_cost_lifecycle")
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT to_regclass('ai_quota_policy_versions')"
            ).fetchone()[0] is None
        migrate(database_url, "upgrade", "head")

        with psycopg.connect(psycopg_url(database_url)) as connection:
            seed_quota_graph(connection)
            connection.commit()
        with pytest.raises(subprocess.CalledProcessError) as error:
            migrate(database_url, "downgrade", "0020_ai_provider_cost_lifecycle")
        assert "cannot downgrade 0021_ai_user_quota" in error.value.stderr
