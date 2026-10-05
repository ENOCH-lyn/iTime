import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest
from cryptography.fernet import Fernet

from itime.config import Settings
from recovery.finalize import (
    FinalizationSummary,
    RecoveryFinalizationError,
    finalize_recovered_database,
)
from tests.conftest import isolated_database, psycopg_url
from tests.test_account_deletion_api import (
    seed_config,
    seed_export_jobs,
    seed_focus_mail,
    seed_media,
    seed_policy,
    seed_running_conversation_job,
    seed_subscription,
    seed_user,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def recovery_settings(admin_url: str):
    with isolated_database(admin_url) as database_url:
        yield Settings(
            environment="test",
            database_url=database_url,
            encryption_key=Fernet.generate_key().decode(),
            db_pool_size=2,
            db_max_overflow=0,
        )


def _database_url(settings: Settings) -> str:
    return settings.database_url.get_secret_value()


def _seed_settled_job(
    connection,
    *,
    owner_id: UUID,
    policy_id: UUID,
    at: datetime,
    ledger_delta: int = -15,
) -> dict[str, UUID]:
    config_id = seed_config(connection, "calendar_screenshot")
    media_id = seed_media(connection, owner_id)
    job_id = uuid4()
    attempt_id = uuid4()
    lease_id = uuid4()
    cost_id = uuid4()
    daily_id = uuid4()
    reservation_id = uuid4()
    ledger_id = uuid4()
    connection.execute(
        """
        INSERT INTO ai_jobs (
            id, owner_id, capability, config_version_id, input_media_id,
            quota_mode, status, input_summary, output, attempt_count,
            available_at, deadline_at, version, finished_at
        ) VALUES (
            %s, %s, 'calendar_screenshot', %s, %s,
            'user', 'succeeded', '{"filename":"control.png"}',
            '{"resource_type":"calendar_import_batch"}', 1,
            %s, %s, 2, %s
        )
        """,
        (
            job_id,
            owner_id,
            config_id,
            media_id,
            at - timedelta(minutes=2),
            at + timedelta(minutes=3),
            at - timedelta(minutes=1),
        ),
    )
    connection.execute(
        """
        INSERT INTO ai_job_attempts (
            id, job_id, owner_id, attempt_number, lease_id, status,
            provider_started_at, started_at, finished_at
        ) VALUES (
            %s, %s, %s, 1, %s, 'succeeded', %s, %s, %s
        )
        """,
        (
            attempt_id,
            job_id,
            owner_id,
            lease_id,
            at - timedelta(minutes=2),
            at - timedelta(minutes=2),
            at - timedelta(minutes=1),
        ),
    )
    connection.execute(
        """
        INSERT INTO ai_provider_costs (
            id, job_id, owner_id, attempt_number, attempt_status,
            provider_started_at, usage_source, input_tokens,
            cached_input_tokens, output_tokens, total_tokens,
            usage_details, billing_basis, price_currency,
            input_price_nanos_per_million_tokens,
            cached_input_price_nanos_per_million_tokens,
            output_price_nanos_per_million_tokens,
            input_cost_nanos, cached_input_cost_nanos,
            output_cost_nanos, total_cost_nanos
        ) VALUES (
            %s, %s, %s, 1, 'succeeded',
            %s, 'provider', 10, 0, 5, 15,
            '{}', 'token', 'CNY',
            1000000, 1000000, 1000000,
            10, 0, 5, 15
        )
        """,
        (cost_id, job_id, owner_id, at - timedelta(minutes=2)),
    )
    connection.execute(
        """
        INSERT INTO ai_daily_allowances (
            id, owner_id, activity_date, window_start_at, window_end_at,
            time_zone, activity_day_start_minute, policy_version_id,
            currency, base_amount_nanos
        ) VALUES (
            %s, %s, %s, %s, %s,
            'UTC', 240, %s, 'CNY', 1000000000
        )
        """,
        (
            daily_id,
            owner_id,
            at.date(),
            at - timedelta(days=1),
            at + timedelta(days=1),
            policy_id,
        ),
    )
    connection.execute(
        """
        INSERT INTO ai_quota_reservations (
            id, job_id, owner_id, daily_allowance_id, status,
            reserved_nanos, estimator_version, actual_cost_nanos,
            charged_nanos, platform_overage_nanos, usage_source,
            provider_cost_id, accepted_at, terminal_at
        ) VALUES (
            %s, %s, %s, %s, 'settled',
            100, 'reservation-utf8-vision-v1', 15,
            15, 0, 'provider',
            %s, %s, %s
        )
        """,
        (
            reservation_id,
            job_id,
            owner_id,
            daily_id,
            cost_id,
            at - timedelta(minutes=3),
            at - timedelta(minutes=1),
        ),
    )
    connection.execute(
        """
        INSERT INTO ai_quota_ledger_entries (
            id, owner_id, daily_allowance_id, reservation_id,
            job_id, provider_cost_id, kind, delta_nanos,
            currency, usage_source, created_at
        ) VALUES (
            %s, %s, %s, %s,
            %s, %s, 'usage_charge', %s,
            'CNY', 'provider', %s
        )
        """,
        (
            ledger_id,
            owner_id,
            daily_id,
            reservation_id,
            job_id,
            cost_id,
            ledger_delta,
            at - timedelta(minutes=1),
        ),
    )
    return {
        "job": job_id,
        "cost": cost_id,
        "reservation": reservation_id,
        "ledger": ledger_id,
    }


def _seed_public_link(connection, owner_id: UUID, at: datetime) -> UUID:
    task_id = uuid4()
    link_id = uuid4()
    connection.execute(
        """
        INSERT INTO tasks (
            id, owner_id, title, description, priority, status
        ) VALUES (%s, %s, '恢复链接任务', '', 0, 'open')
        """,
        (task_id, owner_id),
    )
    connection.execute(
        """
        INSERT INTO task_links (
            id, task_id, owner_id, title, url, position,
            fetch_status, fetch_revision, attempt_count, available_at,
            lease_id, lease_until, last_attempt_at
        ) VALUES (
            %s, %s, %s, '资料', 'https://example.com/resource', 0,
            'running', 1, 1, %s, %s, %s, %s
        )
        """,
        (
            link_id,
            task_id,
            owner_id,
            at - timedelta(minutes=1),
            uuid4(),
            at + timedelta(minutes=5),
            at - timedelta(minutes=1),
        ),
    )
    return link_id


def _seed_retention_rows(connection, owner_id: UUID, at: datetime) -> dict[str, UUID]:
    ids = {
        "media": uuid4(),
        "task": uuid4(),
        "difficulty": uuid4(),
        "memory": uuid4(),
        "series": uuid4(),
    }
    connection.execute(
        """
        INSERT INTO temporary_media (
            id, owner_id, purpose, original_filename, content_type,
            content_sha256, byte_size, width, height,
            encrypted_content, expires_at
        ) VALUES (
            %s, %s, 'calendar_screenshot', 'expired.png', 'image/png',
            %s, 16, 2, 2, %s, %s
        )
        """,
        (ids["media"], owner_id, "e" * 64, b"expired", at - timedelta(seconds=1)),
    )
    connection.execute(
        """
        INSERT INTO tasks (
            id, owner_id, title, description, priority, status,
            deleted_at, purge_after
        ) VALUES (
            %s, %s, '过期任务', '应清理', 1, 'open', %s, %s
        )
        """,
        (
            ids["task"],
            owner_id,
            at - timedelta(days=31),
            at - timedelta(days=1),
        ),
    )
    connection.execute(
        """
        INSERT INTO difficulty_records (
            id, owner_id, content, category, emotion,
            occurred_at, time_zone, deleted_at, purge_after
        ) VALUES (
            %s, %s, '过期困难', 'scope', 'blocked',
            %s, 'UTC', %s, %s
        )
        """,
        (
            ids["difficulty"],
            owner_id,
            at - timedelta(days=31),
            at - timedelta(days=31),
            at - timedelta(days=1),
        ),
    )
    connection.execute(
        """
        INSERT INTO memories (
            id, owner_id, kind, title, content, source_type,
            deleted_at, purge_after
        ) VALUES (
            %s, %s, 'context', '过期记忆', '应清理', 'manual',
            %s, %s
        )
        """,
        (
            ids["memory"],
            owner_id,
            at - timedelta(days=31),
            at - timedelta(days=1),
        ),
    )
    connection.execute(
        """
        INSERT INTO calendar_series (
            id, owner_id, kind, name, description, location,
            source_type, time_zone, start_date, starts_at, ends_at,
            recurrence_frequency, recurrence_interval,
            recurrence_weekdays, course_week_numbers,
            deleted_at, purge_after
        ) VALUES (
            %s, %s, 'fixed', '过期日历', '应清理', '',
            'manual', 'UTC', %s, TIME '09:00', TIME '10:00',
            'none', 1, '[]', '[]', %s, %s
        )
        """,
        (
            ids["series"],
            owner_id,
            at.date(),
            at - timedelta(days=31),
            at - timedelta(days=1),
        ),
    )
    connection.execute(
        """
        INSERT INTO sync_states (owner_id)
        VALUES (%s)
        ON CONFLICT (owner_id) DO NOTHING
        """,
        (owner_id,),
    )
    connection.execute(
        """
        INSERT INTO change_events (
            owner_id, entity_type, entity_id, action,
            entity_version, changed_at
        ) VALUES (
            %s, 'task', %s, 'created', 1, %s
        )
        """,
        (owner_id, ids["task"], at - timedelta(days=31)),
    )
    connection.execute(
        """
        INSERT INTO auth_rate_buckets (key, hits, expires_at)
        VALUES (%s, 3, %s)
        """,
        ("f" * 64, at + timedelta(hours=1)),
    )
    return ids


def _seed_runtime_state(connection, at: datetime) -> dict[str, object]:
    owner_id = seed_user(connection)
    admin_id = seed_user(connection)
    deletion_owner_id = seed_user(connection, status="pending_deletion")
    connection.execute(
        "UPDATE users SET is_admin=true WHERE id=%s",
        (admin_id,),
    )
    device_session_id = uuid4()
    admin_session_id = uuid4()
    connection.execute(
        """
        INSERT INTO device_sessions (
            id, user_id, token_hash, auth_epoch, device_name,
            created_at, last_seen_at, expires_at
        ) VALUES (
            %s, %s, %s, 0, 'restore device', %s, %s, %s
        )
        """,
        (
            device_session_id,
            owner_id,
            "1" * 64,
            at,
            at,
            at + timedelta(days=1),
        ),
    )
    connection.execute(
        """
        INSERT INTO admin_sessions (
            id, user_id, token_hash, auth_epoch, auth_method,
            device_name, created_at, last_seen_at, expires_at
        ) VALUES (
            %s, %s, %s, 0, 'totp',
            'restore admin', %s, %s, %s
        )
        """,
        (
            admin_session_id,
            admin_id,
            "2" * 64,
            at,
            at,
            at + timedelta(hours=1),
        ),
    )
    policy_id = seed_policy(connection)
    conversation_config = seed_config(connection, "conversation")
    ai_job_id, _, _ = seed_running_conversation_job(
        connection,
        owner_id,
        conversation_config,
        policy_id,
        at,
    )
    connection.execute(
        """
        UPDATE ai_job_attempts
        SET started_at=%s
        WHERE job_id=%s
        """,
        (at - timedelta(days=8), ai_job_id),
    )
    connection.execute(
        """
        UPDATE ai_body_logs
        SET started_at=%s, expires_at=%s
        WHERE job_id=%s
        """,
        (
            at - timedelta(days=8),
            at - timedelta(days=1),
            ai_job_id,
        ),
    )
    connection.execute(
        """
        INSERT INTO ai_job_events (
            id, job_id, owner_id, sequence, generation,
            kind, payload, expires_at, created_at
        ) VALUES (
            %s, %s, %s, 1, 1,
            'content_delta', '{"text":"expired"}', %s, %s
        )
        """,
        (
            uuid4(),
            ai_job_id,
            owner_id,
            at - timedelta(seconds=1),
            at - timedelta(days=8),
        ),
    )
    connection.execute(
        "UPDATE ai_jobs SET last_event_sequence=1 WHERE id=%s",
        (ai_job_id,),
    )
    connection.execute(
        """
        INSERT INTO ai_quality_samples (
            id, source_job_id, source_body_log_id, owner_id,
            capability, request_body, response_body, status,
            sampled_at, expires_at
        ) VALUES (
            %s, %s, %s, %s,
            'conversation', '{}', 'expired', 'pending',
            %s, %s
        )
        """,
        (
            uuid4(),
            uuid4(),
            uuid4(),
            owner_id,
            at - timedelta(days=31),
            at - timedelta(days=1),
        ),
    )
    running_export_id, _, expired_export_id = seed_export_jobs(
        connection,
        owner_id,
        at,
    )
    connection.execute(
        """
        UPDATE data_export_jobs
        SET finished_at=%s, expires_at=%s
        WHERE id=%s
        """,
        (
            at - timedelta(days=2),
            at - timedelta(days=1),
            expired_export_id,
        ),
    )
    source_id, _, refresh_id = seed_subscription(
        connection,
        owner_id,
        at,
        running=True,
    )
    focus_id = seed_focus_mail(connection, owner_id, at)
    link_id = _seed_public_link(connection, owner_id, at)
    deletion_id = uuid4()
    connection.execute(
        """
        INSERT INTO account_deletions (
            id, user_id, subject_digest, operation_id, status,
            requested_at, purge_after, available_at,
            lease_id, lease_until, attempt_count, started_at, request_id
        ) VALUES (
            %s, %s, %s, %s, 'processing',
            %s, %s, %s,
            %s, %s, 1, %s, %s
        )
        """,
        (
            deletion_id,
            deletion_owner_id,
            "3" * 64,
            uuid4(),
            at - timedelta(days=8),
            at - timedelta(days=1),
            at - timedelta(days=1),
            uuid4(),
            at + timedelta(minutes=5),
            at - timedelta(minutes=1),
            str(uuid4()),
        ),
    )
    retention_ids = _seed_retention_rows(connection, owner_id, at)
    control_owner_id = seed_user(connection)
    control = _seed_settled_job(
        connection,
        owner_id=control_owner_id,
        policy_id=policy_id,
        at=at,
    )
    return {
        "owner": owner_id,
        "admin": admin_id,
        "device_session": device_session_id,
        "admin_session": admin_session_id,
        "ai_job": ai_job_id,
        "running_export": running_export_id,
        "expired_export": expired_export_id,
        "subscription": source_id,
        "refresh": refresh_id,
        "focus": focus_id,
        "link": link_id,
        "deletion": deletion_id,
        "retention": retention_ids,
        "control": control,
    }


def _control_snapshot(connection, control: dict[str, UUID]):
    return connection.execute(
        """
        SELECT job.status, job.finished_at,
               reservation.status, reservation.actual_cost_nanos,
               reservation.charged_nanos, reservation.platform_overage_nanos,
               cost.total_cost_nanos, ledger.delta_nanos
        FROM ai_jobs AS job
        JOIN ai_quota_reservations AS reservation
          ON reservation.job_id=job.id
        JOIN ai_provider_costs AS cost
          ON cost.id=reservation.provider_cost_id
        JOIN ai_quota_ledger_entries AS ledger
          ON ledger.reservation_id=reservation.id
        WHERE job.id=%s
        """,
        (control["job"],),
    ).fetchone()


def test_finalize_recovered_database_converges_atomically_and_is_idempotent(
    recovery_settings: Settings,
):
    at = datetime.now(UTC)
    database_url = _database_url(recovery_settings)
    with psycopg.connect(psycopg_url(database_url)) as connection:
        ids = _seed_runtime_state(connection, at)
        before_control = _control_snapshot(connection, ids["control"])
        connection.commit()

    first = asyncio.run(finalize_recovered_database(recovery_settings, at=at))
    second = asyncio.run(finalize_recovered_database(recovery_settings, at=at))

    assert first == FinalizationSummary(
        sessions_revoked=2,
        tokens_expired=1,
        jobs_recovered={
            "account_deletions": 1,
            "ai_jobs": 1,
            "calendar_subscriptions": 1,
            "data_exports": 1,
            "mail_outbox": 1,
            "public_links": 1,
        },
        focus_abandoned=1,
        retention_cleaned={
            "ai_body_logs": 1,
            "ai_job_events": 1,
            "ai_quality_samples": 1,
            "auth_rate_buckets": 1,
            "calendar_series": 1,
            "calendar_sources": 0,
            "data_export_artifacts": 1,
            "data_export_metadata": 0,
            "difficulties": 1,
            "memories": 1,
            "sync_events": 1,
            "tasks": 1,
            "temporary_media": 1,
            "voice_transcriptions": 0,
        },
        inconsistencies=(),
    )
    assert second == FinalizationSummary(
        sessions_revoked=0,
        tokens_expired=0,
        jobs_recovered={
            "account_deletions": 0,
            "ai_jobs": 0,
            "calendar_subscriptions": 0,
            "data_exports": 0,
            "mail_outbox": 0,
            "public_links": 0,
        },
        focus_abandoned=0,
        retention_cleaned={
            "ai_body_logs": 0,
            "ai_job_events": 0,
            "ai_quality_samples": 0,
            "auth_rate_buckets": 0,
            "calendar_series": 0,
            "calendar_sources": 0,
            "data_export_artifacts": 0,
            "data_export_metadata": 0,
            "difficulties": 0,
            "memories": 0,
            "sync_events": 0,
            "tasks": 0,
            "temporary_media": 0,
            "voice_transcriptions": 0,
        },
        inconsistencies=(),
    )

    with psycopg.connect(psycopg_url(database_url)) as connection:
        assert connection.execute(
            """
            SELECT revoked_at IS NOT NULL
            FROM device_sessions WHERE id=%s
            """,
            (ids["device_session"],),
        ).fetchone() == (True,)
        assert connection.execute(
            """
            SELECT revoked_at IS NOT NULL
            FROM admin_sessions WHERE id=%s
            """,
            (ids["admin_session"],),
        ).fetchone() == (True,)
        assert connection.execute(
            "SELECT auth_epoch FROM users WHERE id=%s",
            (ids["owner"],),
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT auth_epoch FROM users WHERE id=%s",
            (ids["admin"],),
        ).fetchone() == (1,)
        assert connection.execute(
            """
            SELECT job.status, job.lease_id, attempt.status,
                   reservation.status, reservation.release_reason
            FROM ai_jobs AS job
            JOIN ai_job_attempts AS attempt ON attempt.job_id=job.id
            JOIN ai_quota_reservations AS reservation
              ON reservation.job_id=job.id
            WHERE job.id=%s
            """,
            (ids["ai_job"],),
        ).fetchone() == (
            "cancelled",
            None,
            "cancelled",
            "released",
            "cancelled",
        )
        assert connection.execute(
            """
            SELECT status, lease_id FROM data_export_jobs WHERE id=%s
            """,
            (ids["running_export"],),
        ).fetchone() == ("queued", None)
        assert connection.execute(
            """
            SELECT status, artifact_ciphertext
            FROM data_export_jobs WHERE id=%s
            """,
            (ids["expired_export"],),
        ).fetchone() == ("expired", None)
        assert connection.execute(
            """
            SELECT refresh_status, lease_id
            FROM calendar_subscriptions WHERE source_id=%s
            """,
            (ids["subscription"],),
        ).fetchone() == ("queued", None)
        assert connection.execute(
            """
            SELECT status, error_code
            FROM calendar_subscription_refreshes WHERE id=%s
            """,
            (ids["refresh"],),
        ).fetchone() == ("stale", "calendar_subscription_recovery_interrupted")
        assert connection.execute(
            """
            SELECT fetch_status, lease_id
            FROM task_links WHERE id=%s
            """,
            (ids["link"],),
        ).fetchone() == ("queued", None)
        assert connection.execute(
            """
            SELECT status, lease_id
            FROM account_deletions WHERE id=%s
            """,
            (ids["deletion"],),
        ).fetchone() == ("pending", None)
        assert connection.execute(
            """
            SELECT status, details->>'reason'
            FROM focus_sessions AS focus
            JOIN focus_events AS event
              ON event.focus_session_id=focus.id
             AND event.kind='abandon'
            WHERE focus.id=%s
            """,
            (ids["focus"],),
        ).fetchone() == ("abandoned", "recovery_finalization")
        assert _control_snapshot(connection, ids["control"]) == before_control
        assert connection.execute(
            "SELECT title, purged_at IS NOT NULL FROM tasks WHERE id=%s",
            (ids["retention"]["task"],),
        ).fetchone() == ("已彻底删除的任务", True)
        assert connection.execute(
            """
            SELECT content, purged_at IS NOT NULL
            FROM difficulty_records WHERE id=%s
            """,
            (ids["retention"]["difficulty"],),
        ).fetchone() == (None, True)
        assert connection.execute(
            "SELECT content, purged_at IS NOT NULL FROM memories WHERE id=%s",
            (ids["retention"]["memory"],),
        ).fetchone() == (None, True)
        assert connection.execute(
            """
            SELECT name, purged_at IS NOT NULL
            FROM calendar_series WHERE id=%s
            """,
            (ids["retention"]["series"],),
        ).fetchone() == ("已彻底删除的安排", True)


def test_finalizer_rejects_inconsistent_terminal_facts_and_rolls_back(
    recovery_settings: Settings,
):
    at = datetime.now(UTC)
    database_url = _database_url(recovery_settings)
    with psycopg.connect(psycopg_url(database_url)) as connection:
        policy_id = seed_policy(connection)
        owner_id = seed_user(connection)
        session_id = uuid4()
        connection.execute(
            """
            INSERT INTO device_sessions (
                id, user_id, token_hash, auth_epoch, device_name,
                created_at, last_seen_at, expires_at
            ) VALUES (%s, %s, %s, 0, 'rollback device', %s, %s, %s)
            """,
            (
                session_id,
                owner_id,
                "4" * 64,
                at,
                at,
                at + timedelta(days=1),
            ),
        )
        mismatched = _seed_settled_job(
            connection,
            owner_id=seed_user(connection),
            policy_id=policy_id,
            at=at,
            ledger_delta=-14,
        )
        missing_cost = _seed_settled_job(
            connection,
            owner_id=seed_user(connection),
            policy_id=policy_id,
            at=at,
        )
        lease_job = _seed_settled_job(
            connection,
            owner_id=seed_user(connection),
            policy_id=policy_id,
            at=at,
        )
        completed_owner = seed_user(connection)
        connection.commit()
        connection.execute(
            """
            ALTER TABLE ai_quota_reservations
            DROP CONSTRAINT ck_ai_quota_reservations_state
            """
        )
        connection.execute(
            """
            ALTER TABLE ai_jobs
            DROP CONSTRAINT ck_ai_jobs_lease_state
            """
        )
        connection.execute(
            """
            ALTER TABLE account_deletions
            DROP CONSTRAINT ck_account_deletions_state
            """
        )
        connection.execute(
            """
            UPDATE ai_quota_reservations
            SET provider_cost_id=NULL
            WHERE id=%s
            """,
            (missing_cost["reservation"],),
        )
        connection.execute(
            """
            UPDATE ai_jobs
            SET lease_id=%s, lease_until=%s
            WHERE id=%s
            """,
            (uuid4(), at + timedelta(minutes=5), lease_job["job"]),
        )
        connection.execute(
            """
            INSERT INTO account_deletions (
                id, user_id, subject_digest, operation_id, status,
                requested_at, purge_after, available_at,
                completed_at, request_id
            ) VALUES (
                %s, %s, %s, %s, 'completed',
                %s, %s, %s, %s, %s
            )
            """,
            (
                uuid4(),
                completed_owner,
                "5" * 64,
                uuid4(),
                at - timedelta(days=8),
                at - timedelta(days=1),
                at - timedelta(days=1),
                at,
                str(uuid4()),
            ),
        )
        connection.commit()

    with pytest.raises(RecoveryFinalizationError) as caught:
        asyncio.run(finalize_recovered_database(recovery_settings, at=at))

    assert set(caught.value.inconsistencies) >= {
        "account_deletion_completed_user_present",
        "ai_job_lease_state",
        "quota_settlement_cost",
        "quota_settlement_ledger",
    }
    with psycopg.connect(psycopg_url(database_url)) as connection:
        assert connection.execute(
            """
            SELECT device_sessions.revoked_at, users.auth_epoch
            FROM device_sessions
            JOIN users ON users.id=device_sessions.user_id
            WHERE device_sessions.id=%s
            """,
            (session_id,),
        ).fetchone() == (None, 0)
        assert connection.execute(
            """
            SELECT delta_nanos FROM ai_quota_ledger_entries WHERE id=%s
            """,
            (mismatched["ledger"],),
        ).fetchone() == (-14,)


def test_finalizer_rolls_back_prior_mutations_when_retention_fails(
    recovery_settings: Settings,
):
    at = datetime.now(UTC)
    database_url = _database_url(recovery_settings)
    owner_id = uuid4()
    session_id = uuid4()
    event_id = uuid4()
    with psycopg.connect(psycopg_url(database_url)) as connection:
        connection.execute(
            """
            INSERT INTO users (id, email, password_hash, status)
            VALUES (%s, %s, 'test-password-hash', 'active')
            """,
            (owner_id, f"{owner_id}@example.com"),
        )
        connection.execute(
            """
            INSERT INTO device_sessions (
                id, user_id, token_hash, auth_epoch, device_name,
                created_at, last_seen_at, expires_at
            ) VALUES (%s, %s, %s, 0, 'rollback device', %s, %s, %s)
            """,
            (
                session_id,
                owner_id,
                "6" * 64,
                at,
                at,
                at + timedelta(days=1),
            ),
        )
        connection.execute(
            """
            INSERT INTO change_events (
                owner_id, entity_type, entity_id, action,
                entity_version, changed_at
            ) VALUES (%s, 'task', %s, 'created', 1, %s)
            RETURNING sequence
            """,
            (owner_id, event_id, at - timedelta(days=31)),
        )
        connection.execute(
            "DELETE FROM sync_states WHERE owner_id=%s",
            (owner_id,),
        )
        connection.commit()

    with pytest.raises(RecoveryFinalizationError) as caught:
        asyncio.run(finalize_recovered_database(recovery_settings, at=at))
    assert caught.value.inconsistencies == ("sync_state_unavailable",)

    with psycopg.connect(psycopg_url(database_url)) as connection:
        assert connection.execute(
            """
            SELECT device_sessions.revoked_at, users.auth_epoch
            FROM device_sessions
            JOIN users ON users.id=device_sessions.user_id
            WHERE device_sessions.id=%s
            """,
            (session_id,),
        ).fetchone() == (None, 0)
        assert connection.execute(
            """
            SELECT count(*) FROM change_events
            WHERE owner_id=%s AND entity_id=%s
            """,
            (owner_id, event_id),
        ).fetchone() == (1,)


def test_finalizer_requires_timezone_aware_recovery_time(
    recovery_settings: Settings,
):
    with pytest.raises(
        RecoveryFinalizationError,
        match="恢复收尾时间必须包含时区",
    ):
        asyncio.run(
            finalize_recovered_database(
                recovery_settings,
                at=datetime(2026, 10, 5, 12, 0),
            )
        )
