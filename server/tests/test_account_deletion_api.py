import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import ANY
from uuid import UUID, uuid4

import psycopg
import pytest

from itime.ai.conversation_service import append_conversation_delta
from itime.ai.job_service import ClaimedJob, claim_next_job, complete_job
from itime.calendar.subscription_fetcher import CalendarFetchResult
from itime.calendar.subscription_service import (
    ClaimedSubscription,
    claim_next_subscription,
    complete_not_modified,
)
from itime.config import Settings
from itime.data_export.archive import ExportArchive
from itime.data_export.service import (
    ClaimedExport,
    claim_next_export,
    complete_export,
)
from itime.database import Database
from tests.conftest import isolated_database, psycopg_url

pytestmark = pytest.mark.integration


def seed_user(connection, *, status: str = "active") -> UUID:
    owner_id = uuid4()
    connection.execute(
        """
        INSERT INTO users (
            id, email, password_hash, email_verified_at, status
        ) VALUES (%s, %s, 'test-password-hash', now(), %s)
        """,
        (owner_id, f"{owner_id}@example.com", status),
    )
    return owner_id


def seed_config(connection, capability: str) -> UUID:
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
            id, capability, version, endpoint_url, model,
            api_key_ciphertext, timeout_seconds, max_output_tokens,
            status
        ) VALUES (
            %s, %s, %s, 'https://ai.example.com/v1', 'test-model',
            %s, 60, 1024, 'draft'
        )
        """,
        (config_id, capability, version, b"encrypted-key"),
    )
    return config_id


def seed_media(connection, owner_id: UUID) -> UUID:
    media_id = uuid4()
    connection.execute(
        """
        INSERT INTO temporary_media (
            id, owner_id, purpose, original_filename, content_type,
            content_sha256, byte_size, width, height,
            encrypted_content, expires_at
        ) VALUES (
            %s, %s, 'calendar_screenshot', 'schedule.png', 'image/png',
            %s, 16, 2, 2, %s, now() + interval '1 day'
        )
        """,
        (media_id, owner_id, "a" * 64, b"encrypted-image"),
    )
    return media_id


def seed_screenshot_job(
    connection,
    owner_id: UUID,
    config_id: UUID,
    *,
    status: str = "queued",
) -> tuple[UUID, UUID | None]:
    media_id = seed_media(connection, owner_id)
    job_id = uuid4()
    lease_id = uuid4() if status == "running" else None
    connection.execute(
        """
        INSERT INTO ai_jobs (
            id, owner_id, capability, config_version_id, input_media_id,
            status, input_summary, attempt_count, available_at, deadline_at,
            lease_id, lease_until
        ) VALUES (
            %s, %s, 'calendar_screenshot', %s, %s,
            %s, '{"filename":"schedule.png"}',
            CASE WHEN %s = 'running' THEN 1 ELSE 0 END,
            now(), now() + interval '10 minutes',
            %s,
            CASE WHEN %s = 'running' THEN now() + interval '5 minutes' ELSE NULL END
        )
        """,
        (
            job_id,
            owner_id,
            config_id,
            media_id,
            status,
            status,
            lease_id,
            status,
        ),
    )
    return job_id, lease_id


def seed_running_conversation_job(
    connection,
    owner_id: UUID,
    config_id: UUID,
    policy_id: UUID,
    current: datetime,
) -> tuple[UUID, UUID, UUID]:
    conversation_id = uuid4()
    request_message_id = uuid4()
    response_message_id = uuid4()
    job_id = uuid4()
    lease_id = uuid4()
    daily_id = uuid4()
    connection.execute(
        """
        INSERT INTO ai_conversations (
            id, owner_id, title, title_source
        ) VALUES (%s, %s, '注销测试对话', 'user')
        """,
        (conversation_id, owner_id),
    )
    connection.execute(
        """
        INSERT INTO ai_conversation_messages (
            id, owner_id, conversation_id, sequence, role, status,
            content, completed_at
        ) VALUES
            (%s, %s, %s, 1, 'user', 'complete', '待处理问题', %s),
            (%s, %s, %s, 2, 'assistant', 'running', '部分回复', NULL)
        """,
        (
            request_message_id,
            owner_id,
            conversation_id,
            current,
            response_message_id,
            owner_id,
            conversation_id,
        ),
    )
    connection.execute(
        """
        INSERT INTO ai_jobs (
            id, owner_id, capability, config_version_id, input_media_id,
            conversation_id, quota_mode, status, input_summary,
            attempt_count, available_at, deadline_at, lease_id, lease_until
        ) VALUES (
            %s, %s, 'conversation', %s, NULL, %s, 'user', 'running',
            '{"kind":"conversation_reply"}', 1,
            %s, %s, %s, %s
        )
        """,
        (
            job_id,
            owner_id,
            config_id,
            conversation_id,
            current,
            current + timedelta(minutes=10),
            lease_id,
            current + timedelta(minutes=5),
        ),
    )
    connection.execute(
        """
        INSERT INTO ai_chat_generations (
            job_id, owner_id, conversation_id,
            request_message_id, response_message_id,
            history_through_sequence, context_message_ids, stream_generation
        ) VALUES (%s, %s, %s, %s, %s, 1, '[]', 1)
        """,
        (
            job_id,
            owner_id,
            conversation_id,
            request_message_id,
            response_message_id,
        ),
    )
    connection.execute(
        """
        INSERT INTO ai_job_attempts (
            id, job_id, owner_id, attempt_number, lease_id,
            status, started_at
        ) VALUES (%s, %s, %s, 1, %s, 'running', %s)
        """,
        (uuid4(), job_id, owner_id, lease_id, current),
    )
    connection.execute(
        """
        INSERT INTO ai_body_logs (
            id, job_id, owner_id, attempt_number, capability,
            request_body, response_body, status, started_at, expires_at
        ) VALUES (
            %s, %s, %s, 1, 'conversation',
            '{"messages":[]}', '部分回复', 'running',
            %s, %s
        )
        """,
        (uuid4(), job_id, owner_id, current, current + timedelta(days=7)),
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
            current.date(),
            current - timedelta(days=1),
            current + timedelta(days=1),
            policy_id,
        ),
    )
    connection.execute(
        """
        INSERT INTO ai_quota_reservations (
            id, job_id, owner_id, daily_allowance_id, status,
            reserved_nanos, estimator_version, accepted_at
        ) VALUES (
            %s, %s, %s, %s, 'reserved',
            500000000, 'reservation-utf8-vision-v1', %s
        )
        """,
        (uuid4(), job_id, owner_id, daily_id, current),
    )
    return job_id, lease_id, response_message_id


def seed_export_jobs(
    connection,
    owner_id: UUID,
    current: datetime,
) -> tuple[UUID, UUID, UUID]:
    running_id = uuid4()
    running_lease = uuid4()
    succeeded_id = uuid4()
    connection.execute(
        """
        INSERT INTO data_export_jobs (
            id, owner_id, status, schema_version, attempt_count,
            max_attempts, available_at, lease_id, lease_until
        ) VALUES (
            %s, %s, 'running', 1, 1, 3, %s, %s, %s
        )
        """,
        (
            running_id,
            owner_id,
            current,
            running_lease,
            current + timedelta(minutes=5),
        ),
    )
    connection.execute(
        """
        INSERT INTO data_export_jobs (
            id, owner_id, status, schema_version, attempt_count,
            max_attempts, available_at, snapshot_at, artifact_ciphertext,
            artifact_sha256, artifact_byte_size, filename, expires_at,
            finished_at
        ) VALUES (
            %s, %s, 'succeeded', 1, 1, 3, %s, %s, %s,
            %s, 8, 'itime-export.zip', %s, %s
        )
        """,
        (
            succeeded_id,
            owner_id,
            current,
            current,
            b"artifact",
            "b" * 64,
            current + timedelta(days=1),
            current,
        ),
    )
    return running_id, running_lease, succeeded_id


def seed_subscription(
    connection,
    owner_id: UUID,
    current: datetime,
    *,
    running: bool,
) -> tuple[UUID, UUID | None, UUID | None]:
    source_id = uuid4()
    lease_id = uuid4() if running else None
    refresh_id = uuid4() if running else None
    connection.execute(
        """
        INSERT INTO calendar_sources (id, owner_id, kind, name)
        VALUES (%s, %s, 'ics_subscription', '测试订阅')
        """,
        (source_id, owner_id),
    )
    connection.execute(
        """
        INSERT INTO calendar_subscriptions (
            source_id, owner_id, encrypted_url, url_hmac,
            display_url, display_host, default_time_zone,
            lifecycle_status, refresh_status, refresh_interval_minutes,
            next_refresh_at, fetch_revision, generation, attempt_count,
            lease_id, lease_until, consecutive_failures
        ) VALUES (
            %s, %s, %s, %s,
            'https://calendar.example/…', 'calendar.example', 'UTC',
            'active', %s, 60, %s, 1, 0,
            CASE WHEN %s THEN 1 ELSE 0 END,
            %s, CASE WHEN %s THEN %s ELSE NULL END, 0
        )
        """,
        (
            source_id,
            owner_id,
            b"encrypted-url",
            "c" * 64,
            "running" if running else "idle",
            current - timedelta(minutes=1),
            running,
            lease_id,
            running,
            current + timedelta(minutes=5),
        ),
    )
    if running:
        connection.execute(
            """
            INSERT INTO calendar_subscription_refreshes (
                id, source_id, owner_id, lease_id, fetch_revision,
                attempt_number, trigger, status, started_at
            ) VALUES (
                %s, %s, %s, %s, 1, 1, 'automatic', 'running', %s
            )
            """,
            (refresh_id, source_id, owner_id, lease_id, current),
        )
    return source_id, lease_id, refresh_id


def seed_focus_mail(connection, owner_id: UUID, current: datetime) -> UUID:
    focus_id = uuid4()
    connection.execute(
        """
        INSERT INTO focus_sessions (
            id, owner_id, source, title_snapshot, step_snapshots,
            mode, status, started_at, active_started_at,
            accumulated_seconds, time_zone
        ) VALUES (
            %s, %s, 'timer', '注销前专注', '[]',
            'stopwatch', 'running', %s, %s, 30, 'UTC'
        )
        """,
        (
            focus_id,
            owner_id,
            current - timedelta(minutes=2),
            current - timedelta(minutes=1),
        ),
    )
    connection.execute(
        """
        INSERT INTO focus_events (
            id, owner_id, focus_session_id, sequence, kind,
            occurred_at, source, details
        ) VALUES (%s, %s, %s, 1, 'start', %s, 'online', '{}')
        """,
        (uuid4(), owner_id, focus_id, current - timedelta(minutes=2)),
    )
    token_id = uuid4()
    connection.execute(
        """
        INSERT INTO email_tokens (
            id, user_id, purpose, token_hash, created_at, expires_at
        ) VALUES (%s, %s, 'reset', %s, %s, %s)
        """,
        (
            token_id,
            owner_id,
            "d" * 64,
            current,
            current + timedelta(hours=1),
        ),
    )
    connection.execute(
        """
        INSERT INTO mail_outbox (
            id, token_id, encrypted_message, available_at
        ) VALUES (%s, %s, %s, %s)
        """,
        (uuid4(), token_id, b"encrypted-mail", current),
    )
    return focus_id


def seed_policy(connection) -> UUID:
    policy_id = uuid4()
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
    return policy_id


def test_quiesce_account_stops_transient_work_and_rejects_late_results(
    admin_url,
):
    from itime.account_deletion.quiesce import quiesce_account

    with isolated_database(admin_url) as database_url:
        current = datetime.now(UTC)
        with psycopg.connect(psycopg_url(database_url)) as connection:
            owner_id = seed_user(connection)
            policy_id = seed_policy(connection)
            conversation_config = seed_config(connection, "conversation")
            screenshot_config = seed_config(connection, "calendar_screenshot")
            (
                conversation_job_id,
                conversation_lease_id,
                response_message_id,
            ) = seed_running_conversation_job(
                connection,
                owner_id,
                conversation_config,
                policy_id,
                current,
            )
            screenshot_job_id, _ = seed_screenshot_job(
                connection,
                owner_id,
                screenshot_config,
            )
            running_export_id, export_lease_id, succeeded_export_id = (
                seed_export_jobs(connection, owner_id, current)
            )
            source_id, subscription_lease_id, refresh_id = seed_subscription(
                connection,
                owner_id,
                current,
                running=True,
            )
            focus_id = seed_focus_mail(connection, owner_id, current)
            connection.commit()

        settings = Settings(database_url=database_url, environment="test")
        database = Database(settings)

        async def exercise():
            async with database.transaction() as session:
                summary = await quiesce_account(
                    session,
                    owner_id,
                    requested_at=current,
                )
            assert summary.ai_jobs == 2
            assert summary.exports == 2
            assert summary.subscription_refreshes == 1
            assert summary.focus_sessions == 1
            assert summary.emails == 1
            assert summary.temporary_media == 1

            conversation_claim = ClaimedJob(
                id=conversation_job_id,
                owner_id=owner_id,
                capability="conversation",
                config_version_id=conversation_config,
                input_media_id=None,
                conversation_id=None,
                planning_snapshot_id=None,
                review_session_id=None,
                review_snapshot_id=None,
                lease_id=conversation_lease_id,
                attempt_number=1,
                deadline_at=current + timedelta(minutes=10),
            )
            assert not await append_conversation_delta(
                database,
                conversation_claim,
                generation_number=1,
                text_delta="不应保存",
                lease_seconds=60,
            )
            assert not await complete_job(
                database,
                conversation_claim,
                {"text": "不应保存"},
            )

            export_claim = ClaimedExport(
                id=running_export_id,
                owner_id=owner_id,
                lease_id=export_lease_id,
                attempt_number=1,
            )
            archive = ExportArchive(
                content=b"late",
                sha256="e" * 64,
                byte_size=4,
                filename="late.zip",
                manifest={},
            )
            assert not await complete_export(
                database,
                settings,
                export_claim,
                snapshot_at=current,
                archive=archive,
                ciphertext=b"late-ciphertext",
            )

            subscription_claim = ClaimedSubscription(
                source_id=source_id,
                owner_id=owner_id,
                encrypted_url=b"encrypted-url",
                encrypted_final_url=None,
                etag=None,
                last_modified=None,
                default_time_zone="UTC",
                fetch_revision=1,
                attempt_number=1,
                lease_id=subscription_lease_id,
                refresh_id=refresh_id,
            )
            assert not await complete_not_modified(
                database,
                subscription_claim,
                CalendarFetchResult(
                    status=304,
                    final_url="https://calendar.example/feed.ics",
                    etag=None,
                    last_modified=None,
                    content_sha256=None,
                    content=None,
                    redirect_count=0,
                ),
            )
            await database.close()

        asyncio.run(exercise())

        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                """
                SELECT id, status, lease_id, finished_at
                FROM ai_jobs
                WHERE id IN (%s, %s)
                ORDER BY id
                """,
                (conversation_job_id, screenshot_job_id),
            ).fetchall() == sorted(
                [
                    (conversation_job_id, "cancelled", None, ANY),
                    (screenshot_job_id, "cancelled", None, ANY),
                ],
                key=lambda row: row[0],
            )
            assert connection.execute(
                """
                SELECT status, finished_at
                FROM ai_job_attempts
                WHERE job_id=%s
                """,
                (conversation_job_id,),
            ).fetchone() == ("cancelled", ANY)
            assert connection.execute(
                "SELECT status FROM ai_body_logs WHERE job_id=%s",
                (conversation_job_id,),
            ).fetchone() == ("cancelled",)
            assert connection.execute(
                """
                SELECT status, release_reason
                FROM ai_quota_reservations
                WHERE job_id=%s
                """,
                (conversation_job_id,),
            ).fetchone() == ("released", "cancelled")
            assert connection.execute(
                """
                SELECT status, content
                FROM ai_conversation_messages
                WHERE id=%s
                """,
                (response_message_id,),
            ).fetchone() == ("cancelled", "部分回复")
            assert connection.execute(
                """
                SELECT status, lease_id, error_code, artifact_ciphertext
                FROM data_export_jobs
                WHERE id=%s
                """,
                (running_export_id,),
            ).fetchone() == (
                "failed",
                None,
                "data_export_account_deletion",
                None,
            )
            assert connection.execute(
                """
                SELECT status, artifact_ciphertext
                FROM data_export_jobs
                WHERE id=%s
                """,
                (succeeded_export_id,),
            ).fetchone() == ("expired", None)
            assert connection.execute(
                """
                SELECT refresh_status, lease_id
                FROM calendar_subscriptions
                WHERE source_id=%s
                """,
                (source_id,),
            ).fetchone() == ("idle", None)
            assert connection.execute(
                """
                SELECT status, error_code, finished_at
                FROM calendar_subscription_refreshes
                WHERE id=%s
                """,
                (refresh_id,),
            ).fetchone() == (
                "stale",
                "calendar_subscription_account_deletion",
                ANY,
            )
            assert connection.execute(
                """
                SELECT status, ended_at, effective_duration_seconds
                FROM focus_sessions
                WHERE id=%s
                """,
                (focus_id,),
            ).fetchone() == ("abandoned", current, 90)
            assert connection.execute(
                """
                SELECT kind, details->>'reason'
                FROM focus_events
                WHERE focus_session_id=%s
                ORDER BY sequence
                """,
                (focus_id,),
            ).fetchall() == [
                ("start", None),
                ("abandon", "account_deletion"),
            ]
            assert connection.execute(
                """
                SELECT count(*)
                FROM email_tokens
                WHERE user_id=%s AND used_at IS NOT NULL
                """,
                (owner_id,),
            ).fetchone() == (1,)
            assert connection.execute(
                """
                SELECT encrypted_message, finished_at, error_code
                FROM mail_outbox
                WHERE token_id IN (
                    SELECT id FROM email_tokens WHERE user_id=%s
                )
                """,
                (owner_id,),
            ).fetchone() == (None, ANY, "account_deletion")
            assert connection.execute(
                """
                SELECT encrypted_content, deletion_reason
                FROM temporary_media
                WHERE owner_id=%s
                """,
                (owner_id,),
            ).fetchone() == (None, "account_deletion")
            assert connection.execute(
                """
                SELECT count(*) FROM ai_quota_ledger_entries
                WHERE owner_id=%s AND kind='usage_charge'
                """,
                (owner_id,),
            ).fetchone() == (0,)
            assert connection.execute(
                """
                SELECT count(*) FROM calendar_subscription_events
                WHERE owner_id=%s
                """,
                (owner_id,),
            ).fetchone() == (0,)


def test_workers_only_claim_active_accounts(admin_url):
    with isolated_database(admin_url) as database_url:
        current = datetime.now(UTC)
        with psycopg.connect(psycopg_url(database_url)) as connection:
            blocked_owner = seed_user(connection, status="pending_deletion")
            active_owner = seed_user(connection)
            screenshot_config = seed_config(connection, "calendar_screenshot")
            blocked_job, _ = seed_screenshot_job(
                connection,
                blocked_owner,
                screenshot_config,
            )
            active_job, _ = seed_screenshot_job(
                connection,
                active_owner,
                screenshot_config,
            )
            blocked_export = uuid4()
            active_export = uuid4()
            connection.execute(
                """
                INSERT INTO data_export_jobs (
                    id, owner_id, status, schema_version, attempt_count,
                    max_attempts, available_at
                ) VALUES
                    (%s, %s, 'queued', 1, 0, 3, %s),
                    (%s, %s, 'queued', 1, 0, 3, %s)
                """,
                (
                    blocked_export,
                    blocked_owner,
                    current,
                    active_export,
                    active_owner,
                    current,
                ),
            )
            blocked_source, _, _ = seed_subscription(
                connection,
                blocked_owner,
                current,
                running=False,
            )
            active_source, _, _ = seed_subscription(
                connection,
                active_owner,
                current,
                running=False,
            )
            connection.commit()

        settings = Settings(database_url=database_url, environment="test")
        database = Database(settings)

        async def exercise():
            ai_claim = await claim_next_job(database)
            export_claim = await claim_next_export(database, lease_seconds=60)
            subscription_claim = await claim_next_subscription(
                database,
                lease_seconds=60,
            )
            assert ai_claim is not None
            assert ai_claim.id == active_job
            assert export_claim is not None
            assert export_claim.id == active_export
            assert subscription_claim is not None
            assert subscription_claim.source_id == active_source
            assert await claim_next_job(database) is None
            assert await claim_next_export(database, lease_seconds=60) is None
            assert (
                await claim_next_subscription(database, lease_seconds=60)
                is None
            )
            await database.close()

        asyncio.run(exercise())

        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT status FROM ai_jobs WHERE id=%s",
                (blocked_job,),
            ).fetchone() == ("queued",)
            assert connection.execute(
                "SELECT status FROM data_export_jobs WHERE id=%s",
                (blocked_export,),
            ).fetchone() == ("queued",)
            assert connection.execute(
                """
                SELECT refresh_status
                FROM calendar_subscriptions
                WHERE source_id=%s
                """,
                (blocked_source,),
            ).fetchone() == ("idle",)
            assert connection.execute(
                """
                SELECT count(*)
                FROM calendar_subscription_refreshes
                WHERE owner_id=%s
                """,
                (blocked_owner,),
            ).fetchone() == (0,)
