import subprocess
from typing import get_args
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.errors import CheckViolation, ForeignKeyViolation

from tests.conftest import isolated_database, migrate, psycopg_url

pytestmark = pytest.mark.integration


def seed_user(connection, label: str) -> UUID:
    owner_id = uuid4()
    connection.execute(
        """
        INSERT INTO users (id, email, password_hash, email_verified_at)
        VALUES (%s, %s, 'test-password-hash', now())
        """,
        (owner_id, f"voice-model-{label}-{owner_id}@example.com"),
    )
    return owner_id


def seed_transcription_config(connection, *, status: str = "draft") -> UUID:
    version = connection.execute(
        """
        UPDATE ai_config_capabilities
        SET next_version=next_version + 1, updated_at=now()
        WHERE capability='transcription'
        RETURNING next_version - 1
        """
    ).fetchone()[0]
    config_id = uuid4()
    tested = status in {"tested", "published", "retired"}
    published = status in {"published", "retired"}
    connection.execute(
        """
        INSERT INTO ai_config_versions (
            id, capability, version, endpoint_url, model,
            api_key_ciphertext, timeout_seconds, max_output_tokens,
            pricing_basis, price_currency, audio_price_nanos_per_minute,
            status, last_test_id, last_test_started_at,
            last_test_completed_at, published_at, retired_at
        ) VALUES (
            %s, 'transcription', %s, 'https://ai.example.com/v1',
            'transcribe-model', %s, 60, NULL,
            'audio_minute', 'CNY', 6000000, %s,
            CASE WHEN %s THEN %s ELSE NULL END,
            CASE WHEN %s THEN now() ELSE NULL END,
            CASE WHEN %s THEN now() ELSE NULL END,
            CASE WHEN %s THEN now() ELSE NULL END,
            CASE WHEN %s THEN now() ELSE NULL END
        )
        """,
        (
            config_id,
            version,
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


def seed_voice_media(connection, owner_id: UUID) -> UUID:
    media_id = uuid4()
    connection.execute(
        """
        INSERT INTO temporary_media (
            id, owner_id, purpose, original_filename, content_type,
            content_sha256, byte_size, width, height,
            duration_ms, sample_rate_hz, channel_count, sample_width_bits,
            encrypted_content, expires_at
        ) VALUES (
            %s, %s, 'voice_transcription', 'voice.wav', 'audio/wav',
            %s, 32044, NULL, NULL, 1000, 16000, 1, 16,
            %s, now() + interval '1 day'
        )
        """,
        (media_id, owner_id, "a" * 64, b"encrypted-audio"),
    )
    return media_id


def seed_voice_job(
    connection,
    owner_id: UUID,
    config_id: UUID,
    media_id: UUID,
) -> UUID:
    job_id = uuid4()
    connection.execute(
        """
        INSERT INTO ai_jobs (
            id, owner_id, capability, config_version_id, input_media_id,
            status, input_summary, available_at, deadline_at
        ) VALUES (
            %s, %s, 'transcription', %s, %s, 'queued',
            '{"purpose":"task_title","duration_ms":1000}',
            now(), now() + interval '3 minutes'
        )
        """,
        (job_id, owner_id, config_id, media_id),
    )
    return job_id


def test_voice_transcription_migration_models_and_constraints(admin_url):
    with isolated_database(admin_url) as database_url:
        with psycopg.connect(
            psycopg_url(database_url),
            autocommit=True,
        ) as connection:
            assert connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone() == ("0035_voice_transcriptions",)
            assert connection.execute(
                """
                SELECT capability, next_version, current_version_id
                FROM ai_config_capabilities
                WHERE capability='transcription'
                """
            ).fetchone() == ("transcription", 1, None)
            assert connection.execute(
                "SELECT to_regclass('voice_transcriptions')"
            ).fetchone() == ("voice_transcriptions",)

            owner_id = seed_user(connection, "owner")
            other_id = seed_user(connection, "other")
            config_id = seed_transcription_config(connection)
            with pytest.raises(CheckViolation):
                connection.execute(
                    """
                    UPDATE ai_config_versions
                    SET audio_price_nanos_per_minute=7000000
                    WHERE id=%s
                    """,
                    (config_id,),
                )
            media_id = seed_voice_media(connection, owner_id)
            job_id = seed_voice_job(connection, owner_id, config_id, media_id)
            transcription_id = uuid4()
            connection.execute(
                """
                INSERT INTO voice_transcriptions (
                    id, owner_id, job_id, media_id, capability,
                    media_purpose, purpose, expires_at
                ) VALUES (
                    %s, %s, %s, %s, 'transcription',
                    'voice_transcription', 'task_title',
                    now() + interval '1 day'
                )
                """,
                (transcription_id, owner_id, job_id, media_id),
            )

            with pytest.raises(CheckViolation):
                connection.execute(
                    """
                    INSERT INTO temporary_media (
                        id, owner_id, purpose, original_filename, content_type,
                        content_sha256, byte_size, width, height,
                        duration_ms, sample_rate_hz, channel_count,
                        sample_width_bits, encrypted_content, expires_at
                    ) VALUES (
                        %s, %s, 'voice_transcription', 'invalid.wav',
                        'audio/wav', %s, 32044, 10, NULL,
                        1000, 16000, 1, 16, %s, now() + interval '1 day'
                    )
                    """,
                    (uuid4(), owner_id, "b" * 64, b"encrypted-audio"),
                )

            with pytest.raises(CheckViolation):
                connection.execute(
                    """
                    INSERT INTO ai_config_versions (
                        id, capability, version, endpoint_url, model,
                        api_key_ciphertext, timeout_seconds, max_output_tokens,
                        pricing_basis, price_currency,
                        input_price_nanos_per_million_tokens,
                        cached_input_price_nanos_per_million_tokens,
                        output_price_nanos_per_million_tokens,
                        audio_price_nanos_per_minute, status
                    ) VALUES (
                        %s, 'transcription', 999,
                        'https://ai.example.com/v1', 'invalid-price',
                        %s, 60, NULL, 'token', 'CNY',
                        1, 1, 1, NULL, 'draft'
                    )
                    """,
                    (uuid4(), b"encrypted-key"),
                )

            second_media_id = seed_voice_media(connection, owner_id)
            second_job_id = seed_voice_job(
                connection,
                owner_id,
                config_id,
                second_media_id,
            )
            with pytest.raises(ForeignKeyViolation):
                connection.execute(
                    """
                    INSERT INTO voice_transcriptions (
                        id, owner_id, job_id, media_id, capability,
                        media_purpose, purpose, expires_at
                    ) VALUES (
                        %s, %s, %s, %s, 'transcription',
                        'voice_transcription', 'difficulty_content',
                        now() + interval '1 day'
                    )
                    """,
                    (uuid4(), other_id, second_job_id, second_media_id),
                )

            connection.execute(
                """
                UPDATE voice_transcriptions
                SET transcript='完成论文', transcript_sha256=%s, ready_at=now()
                WHERE id=%s
                """,
                ("c" * 64, transcription_id),
            )
            with pytest.raises(CheckViolation):
                connection.execute(
                    """
                    UPDATE voice_transcriptions
                    SET resolution='discarded', resolved_at=now()
                    WHERE id=%s
                    """,
                    (transcription_id,),
                )

            lease_id = uuid4()
            connection.execute(
                """
                UPDATE ai_jobs
                SET status='running', attempt_count=1,
                    lease_id=%s, lease_until=now() + interval '1 minute'
                WHERE id=%s
                """,
                (lease_id, job_id),
            )
            connection.execute(
                """
                INSERT INTO ai_job_attempts (
                    id, job_id, owner_id, attempt_number, lease_id,
                    status, provider_started_at, started_at
                ) VALUES (
                    %s, %s, %s, 1, %s, 'running', now(), now()
                )
                """,
                (uuid4(), job_id, owner_id, lease_id),
            )
            connection.execute(
                """
                INSERT INTO ai_provider_costs (
                    id, job_id, owner_id, attempt_number, attempt_status,
                    provider_started_at, usage_source, input_tokens,
                    cached_input_tokens, output_tokens, total_tokens,
                    usage_details, billing_basis, audio_duration_ms,
                    price_currency, audio_price_nanos_per_minute,
                    audio_cost_nanos, total_cost_nanos
                ) VALUES (
                    %s, %s, %s, 1, 'succeeded', now(), 'measured',
                    0, 0, 0, 0, '{"duration_ms":1000}',
                    'audio_minute', 1000, 'CNY', 6000000, 100000, 100000
                )
                """,
                (uuid4(), job_id, owner_id),
            )
            invalid_media_id = seed_voice_media(connection, owner_id)
            invalid_job_id = seed_voice_job(
                connection,
                owner_id,
                config_id,
                invalid_media_id,
            )
            invalid_lease_id = uuid4()
            connection.execute(
                """
                UPDATE ai_jobs
                SET status='running', attempt_count=1,
                    lease_id=%s, lease_until=now() + interval '1 minute'
                WHERE id=%s
                """,
                (invalid_lease_id, invalid_job_id),
            )
            connection.execute(
                """
                INSERT INTO ai_job_attempts (
                    id, job_id, owner_id, attempt_number, lease_id,
                    status, provider_started_at, started_at
                ) VALUES (
                    %s, %s, %s, 1, %s, 'running', now(), now()
                )
                """,
                (uuid4(), invalid_job_id, owner_id, invalid_lease_id),
            )
            with pytest.raises(CheckViolation):
                connection.execute(
                    """
                    INSERT INTO ai_provider_costs (
                        id, job_id, owner_id, attempt_number, attempt_status,
                        provider_started_at, usage_source, input_tokens,
                        cached_input_tokens, output_tokens, total_tokens,
                        usage_details, billing_basis, audio_duration_ms,
                        price_currency, audio_price_nanos_per_minute,
                        audio_cost_nanos, total_cost_nanos
                    ) VALUES (
                        %s, %s, %s, 1, 'failed', now(), 'measured',
                        0, 0, 0, 0, '{}',
                        'audio_minute', 1000, 'CNY', 6000000, 100000, 99999
                    )
                    """,
                    (uuid4(), invalid_job_id, owner_id),
                )

        from itime.ai.models import (
            AIConfigVersion,
            AIProviderCost,
            TemporaryMedia,
            VoiceTranscription,
        )
        from itime.ai.schemas import Capability
        from itime.database import Base

        assert "transcription" in get_args(Capability)
        assert AIConfigVersion.__table__.c.max_output_tokens.nullable
        assert TemporaryMedia.__table__.c.width.nullable
        assert TemporaryMedia.__table__.c.duration_ms.nullable
        assert AIProviderCost.__table__.c.audio_duration_ms.nullable
        assert VoiceTranscription.__tablename__ == "voice_transcriptions"
        assert "voice_transcriptions" in Base.metadata.tables
        migrate(database_url, "check")

        with psycopg.connect(
            psycopg_url(database_url),
            autocommit=True,
        ) as connection:
            connection.execute(
                "DELETE FROM users WHERE id IN (%s, %s)",
                (owner_id, other_id),
            )
            connection.execute(
                "DELETE FROM ai_config_versions WHERE capability='transcription'"
            )
        migrate(database_url, "downgrade", "0034_calendar_lifecycle")
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT to_regclass('voice_transcriptions')"
            ).fetchone() == (None,)
            assert connection.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name='temporary_media'
                  AND column_name='duration_ms'
                """
            ).fetchone() is None
            assert connection.execute(
                """
                SELECT capability
                FROM ai_config_capabilities
                WHERE capability='transcription'
                """
            ).fetchone() is None
        migrate(database_url, "upgrade", "head")
        migrate(database_url, "check")


def test_voice_transcription_downgrade_blocks_live_data(admin_url):
    with isolated_database(admin_url) as database_url:
        with psycopg.connect(
            psycopg_url(database_url),
            autocommit=True,
        ) as connection:
            owner_id = seed_user(connection, "downgrade")
            config_id = seed_transcription_config(connection)
            media_id = seed_voice_media(connection, owner_id)
            job_id = seed_voice_job(connection, owner_id, config_id, media_id)
            connection.execute(
                """
                INSERT INTO voice_transcriptions (
                    id, owner_id, job_id, media_id, capability,
                    media_purpose, purpose, expires_at
                ) VALUES (
                    %s, %s, %s, %s, 'transcription',
                    'voice_transcription', 'task_title',
                    now() + interval '1 day'
                )
                """,
                (uuid4(), owner_id, job_id, media_id),
            )

        with pytest.raises(subprocess.CalledProcessError) as error:
            migrate(database_url, "downgrade", "0034_calendar_lifecycle")
        assert (
            "voice transcription data must be cleared before downgrade"
            in error.value.stderr
        )
