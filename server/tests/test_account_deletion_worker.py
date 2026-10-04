import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql

import itime.account_deletion.service as deletion_service
from itime.account_deletion.service import (
    ClaimedDeletion,
    claim_next_deletion,
    purge_claimed_account,
    record_deletion_failure,
)
from itime.account_deletion.worker import process_one
from itime.config import Settings
from itime.database import Database
from tests.conftest import isolated_database, psycopg_url
from tests.test_tasks import PASSWORD, create_account

pytestmark = pytest.mark.integration


def seed_user(
    connection,
    *,
    email: str | None = None,
    status: str = "active",
) -> tuple[UUID, str]:
    owner_id = uuid4()
    email = email or f"{owner_id}@example.com"
    connection.execute(
        """
        INSERT INTO users (
            id, email, password_hash, email_verified_at, status
        ) VALUES (%s, %s, 'private-password-hash', now(), %s)
        """,
        (owner_id, email, status),
    )
    return owner_id, email


def seed_deletion(
    connection,
    owner_id: UUID,
    *,
    status: str = "pending",
    due: bool = True,
    lease_id: UUID | None = None,
    lease_until: datetime | None = None,
) -> UUID:
    deletion_id = uuid4()
    current = datetime.now(UTC)
    requested_at = current - timedelta(days=8) if due else current
    purge_after = (
        current - timedelta(days=1)
        if due
        else current + timedelta(days=7)
    )
    processing = status == "processing"
    connection.execute(
        """
        INSERT INTO account_deletions (
            id, user_id, subject_digest, operation_id, status,
            requested_at, purge_after, available_at,
            lease_id, lease_until, attempt_count, started_at,
            cancelled_at, request_id
        ) VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s
        )
        """,
        (
            deletion_id,
            owner_id,
            owner_id.hex * 2,
            uuid4(),
            status,
            requested_at,
            purge_after,
            purge_after,
            lease_id,
            lease_until,
            1 if processing else 0,
            requested_at if processing else None,
            current - timedelta(days=2) if status == "cancelled" else None,
            str(uuid4()),
        ),
    )
    return deletion_id


def seed_personal_data(
    connection,
    owner_id: UUID,
    email: str,
    control_owner_id: UUID,
) -> dict[str, UUID]:
    current = datetime.now(UTC)
    project_id = uuid4()
    task_id = uuid4()
    source_id = uuid4()
    subscription_event_id = uuid4()
    subscription_exception_id = uuid4()
    conversation_id = uuid4()
    focus_id = uuid4()
    memory_id = uuid4()
    export_id = uuid4()
    invitation_id = uuid4()
    target_event_id = uuid4()
    config_id = uuid4()
    plan_id = uuid4()
    plan_version_id = uuid4()
    source_conflict_id = uuid4()
    week_start = current.date() - timedelta(days=current.weekday())
    config_version = connection.execute(
        """
        UPDATE ai_config_capabilities
        SET next_version=next_version + 1, updated_at=now()
        WHERE capability='calendar_screenshot'
        RETURNING next_version - 1
        """
    ).fetchone()[0]
    connection.execute(
        """
        INSERT INTO ai_config_versions (
            id, capability, version, endpoint_url, model,
            api_key_ciphertext, timeout_seconds, max_output_tokens,
            status, created_by_user_id
        ) VALUES (
            %s, 'calendar_screenshot', %s,
            'https://ai.example.com/v1', 'retained-model',
            %s, 60, 1024, 'draft', %s
        )
        """,
        (config_id, config_version, b"global-encrypted-key", owner_id),
    )
    connection.execute(
        """
        UPDATE registration_policies
        SET updated_by_user_id=%s, updated_at=now()
        WHERE id=1
        """,
        (owner_id,),
    )
    connection.execute(
        """
        INSERT INTO user_time_settings (owner_id, time_zone)
        VALUES (%s, 'UTC')
        """,
        (owner_id,),
    )
    connection.execute(
        """
        INSERT INTO user_privacy_settings (owner_id)
        VALUES (%s)
        """,
        (owner_id,),
    )
    connection.execute(
        """
        INSERT INTO memory_settings (owner_id)
        VALUES (%s)
        """,
        (owner_id,),
    )
    connection.execute(
        """
        INSERT INTO projects (id, owner_id, kind, name, description)
        VALUES (%s, %s, 'project', '待删除项目', 'private project')
        """,
        (project_id, owner_id),
    )
    connection.execute(
        """
        INSERT INTO tasks (
            id, owner_id, project_id, title, description
        ) VALUES (%s, %s, %s, '待删除任务', 'private task')
        """,
        (task_id, owner_id, project_id),
    )
    connection.execute(
        """
        INSERT INTO calendar_sources (
            id, owner_id, kind, name, deleted_at, purge_after
        )
        VALUES (
            %s, %s, 'ics_subscription', '待删除日历',
            %s, %s
        )
        """,
        (source_id, owner_id, current, current + timedelta(days=30)),
    )
    connection.execute(
        """
        INSERT INTO calendar_subscriptions (
            source_id, owner_id, encrypted_url, url_hmac,
            display_url, display_host, default_time_zone,
            lifecycle_status, refresh_status, refresh_interval_minutes,
            next_refresh_at, fetch_revision, generation,
            attempt_count, consecutive_failures
        ) VALUES (
            %s, %s, %s, %s,
            'https://private.example/…', 'private.example', 'UTC',
            'active', 'idle', 60, %s, 1, 0, 0, 0
        )
        """,
        (
            source_id,
            owner_id,
            b"private-encrypted-url",
            "a" * 64,
            current + timedelta(hours=1),
        ),
    )
    connection.execute(
        """
        INSERT INTO calendar_subscription_events (
            id, source_id, owner_id, uid, recurrence_id,
            baseline, content_sha256, first_seen_generation,
            last_seen_generation
        ) VALUES (
            %s, %s, %s, 'deletion-event', '',
            '{}'::jsonb, %s, 1, 1
        )
        """,
        (subscription_event_id, source_id, owner_id, "d" * 64),
    )
    connection.execute(
        """
        INSERT INTO calendar_subscription_exceptions (
            id, owner_id, master_event_id, source_occurrence_key,
            original_start_at, original_end_at, original_all_day,
            action, version
        ) VALUES (
            %s, %s, %s, 'INSTANT:2026-10-06T09:00:00Z',
            '2026-10-06T09:00:00Z', '2026-10-06T10:00:00Z',
            false, 'cancelled', 1
        )
        """,
        (
            subscription_exception_id,
            owner_id,
            subscription_event_id,
        ),
    )
    connection.execute(
        """
        INSERT INTO plans (
            id, owner_id, week_start_date, time_zone,
            current_version_id, version
        ) VALUES (%s, %s, %s, 'UTC', %s, 1)
        """,
        (plan_id, owner_id, week_start, plan_version_id),
    )
    connection.execute(
        """
        INSERT INTO plan_versions (
            id, plan_id, owner_id, version, source, scope,
            target_date, density, buffer_minutes,
            availability_start, availability_end, summary,
            assumptions, input_revision
        ) VALUES (
            %s, %s, %s, 1, 'manual', 'week',
            %s, 'balanced', 0, '08:00', '22:00',
            '待删除计划', '[]'::jsonb, 0
        )
        """,
        (plan_version_id, plan_id, owner_id, week_start),
    )
    connection.execute(
        """
        INSERT INTO plan_source_conflicts (
            id, owner_id, plan_id, plan_version_id, source_id,
            source_generation, status, changes, affected_block_ids,
            details_limited, version, detected_at
        ) VALUES (
            %s, %s, %s, %s, %s,
            1, 'open', '[{"kind":"removed"}]'::jsonb, '[]'::jsonb,
            false, 1, %s
        )
        """,
        (
            source_conflict_id,
            owner_id,
            plan_id,
            plan_version_id,
            source_id,
            current,
        ),
    )
    connection.execute(
        """
        INSERT INTO ai_conversations (
            id, owner_id, title, title_source
        ) VALUES (%s, %s, '待删除对话正文', 'user')
        """,
        (conversation_id, owner_id),
    )
    connection.execute(
        """
        INSERT INTO ai_conversation_messages (
            id, owner_id, conversation_id, sequence,
            role, status, content, completed_at
        ) VALUES (
            %s, %s, %s, 1,
            'user', 'complete', 'private conversation body', %s
        )
        """,
        (uuid4(), owner_id, conversation_id, current),
    )
    connection.execute(
        """
        INSERT INTO focus_sessions (
            id, owner_id, source, title_snapshot, step_snapshots,
            mode, status, started_at, ended_at, accumulated_seconds,
            effective_started_at, effective_ended_at,
            effective_duration_seconds, time_zone
        ) VALUES (
            %s, %s, 'manual', '待删除专注', '[]',
            'stopwatch', 'finished', %s, %s, 600,
            %s, %s, 600, 'UTC'
        )
        """,
        (
            focus_id,
            owner_id,
            current - timedelta(minutes=10),
            current,
            current - timedelta(minutes=10),
            current,
        ),
    )
    connection.execute(
        """
        INSERT INTO memories (
            id, owner_id, kind, title, content, source_type
        ) VALUES (
            %s, %s, 'context', '待删除记忆',
            'private memory body', 'manual'
        )
        """,
        (memory_id, owner_id),
    )
    connection.execute(
        """
        INSERT INTO data_export_jobs (
            id, owner_id, status, schema_version, attempt_count,
            max_attempts, available_at, error_code, error_message,
            finished_at
        ) VALUES (
            %s, %s, 'failed', 1, 1, 3, %s,
            'data_export_account_deletion', '已停止', %s
        )
        """,
        (export_id, owner_id, current, current),
    )
    token_id = uuid4()
    connection.execute(
        """
        INSERT INTO email_tokens (
            id, user_id, purpose, token_hash, created_at, expires_at, used_at
        ) VALUES (
            %s, %s, 'reset', %s, %s, %s, %s
        )
        """,
        (
            token_id,
            owner_id,
            "b" * 64,
            current,
            current + timedelta(hours=1),
            current,
        ),
    )
    connection.execute(
        """
        INSERT INTO mail_outbox (
            id, token_id, encrypted_message, available_at,
            finished_at, error_code
        ) VALUES (
            %s, %s, NULL, %s, %s, 'account_deletion'
        )
        """,
        (uuid4(), token_id, current, current),
    )
    connection.execute(
        """
        INSERT INTO registration_invitations (
            id, email, token_hash, expires_at, consumed_at,
            consumed_by_user_id
        ) VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            invitation_id,
            email,
            "c" * 64,
            current + timedelta(days=1),
            current,
            owner_id,
        ),
    )
    connection.execute(
        """
        INSERT INTO security_events (
            id, user_id, action, request_id, target_id, details
        ) VALUES
            (%s, %s, 'private_event', %s, %s, '{"private":"body"}'),
            (%s, %s, 'admin_target_event', %s, %s, '{"email":"private"}')
        """,
        (
            uuid4(),
            owner_id,
            str(uuid4()),
            str(owner_id),
            target_event_id,
            control_owner_id,
            str(uuid4()),
            str(owner_id),
        ),
    )
    return {
        "project": project_id,
        "task": task_id,
        "source": source_id,
        "conversation": conversation_id,
        "focus": focus_id,
        "memory": memory_id,
        "export": export_id,
        "invitation": invitation_id,
        "target_event": target_event_id,
        "config": config_id,
    }


def owner_tables_with_rows(connection, owner_id: UUID) -> set[str]:
    table_names = [
        row[0]
        for row in connection.execute(
            """
            SELECT table_name
            FROM information_schema.columns
            WHERE table_schema='public' AND column_name='owner_id'
            ORDER BY table_name
            """
        )
    ]
    return {
        table_name
        for table_name in table_names
        if connection.execute(
            sql.SQL("SELECT count(*) FROM {} WHERE owner_id=%s").format(
                sql.Identifier(table_name)
            ),
            (owner_id,),
        ).fetchone()[0]
        > 0
    }


def test_two_workers_purge_due_account_and_leave_minimal_receipts(admin_url):
    with isolated_database(admin_url) as database_url:
        with psycopg.connect(psycopg_url(database_url)) as connection:
            owner_id, email = seed_user(
                connection,
                email=f"delete-{uuid4().hex}@example.com",
                status="pending_deletion",
            )
            control_owner_id, _ = seed_user(connection)
            deletion_id = seed_deletion(connection, owner_id)
            cancelled_id = seed_deletion(
                connection,
                owner_id,
                status="cancelled",
                due=True,
            )
            seeded = seed_personal_data(
                connection,
                owner_id,
                email,
                control_owner_id,
            )
            control_project_id = uuid4()
            connection.execute(
                """
                INSERT INTO projects (
                    id, owner_id, kind, name, description
                ) VALUES (%s, %s, 'project', '保留项目', '')
                """,
                (control_project_id, control_owner_id),
            )
            populated_tables = owner_tables_with_rows(connection, owner_id)
            assert populated_tables >= {
                "ai_conversations",
                "calendar_sources",
                "calendar_subscription_exceptions",
                "data_export_jobs",
                "focus_sessions",
                "memories",
                "plan_source_conflicts",
                "plans",
                "projects",
                "sync_states",
                "tasks",
            }
            global_counts = connection.execute(
                """
                SELECT
                    (SELECT count(*) FROM registration_policies),
                    (SELECT count(*) FROM ai_config_capabilities),
                    (SELECT count(*) FROM ai_quota_policy_state)
                """
            ).fetchone()
            receipt_before = connection.execute(
                """
                SELECT subject_digest, operation_id, requested_at,
                       purge_after, request_id
                FROM account_deletions
                WHERE id=%s
                """,
                (deletion_id,),
            ).fetchone()
            connection.commit()

        settings = Settings(database_url=database_url, environment="test")
        databases = [Database(settings), Database(settings)]

        async def run_workers():
            try:
                return await asyncio.gather(
                    process_one(databases[0]),
                    process_one(databases[1]),
                )
            finally:
                await asyncio.gather(*(item.close() for item in databases))

        assert sorted(asyncio.run(run_workers())) == [False, True]

        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT count(*) FROM users WHERE id=%s",
                (owner_id,),
            ).fetchone() == (0,)
            assert connection.execute(
                "SELECT count(*) FROM users WHERE id=%s",
                (control_owner_id,),
            ).fetchone() == (1,)
            for table_name in populated_tables:
                assert connection.execute(
                    sql.SQL(
                        "SELECT count(*) FROM {} WHERE owner_id=%s"
                    ).format(sql.Identifier(table_name)),
                    (owner_id,),
                ).fetchone() == (0,), table_name

            receipt = connection.execute(
                """
                SELECT user_id, status, subject_digest, operation_id,
                       requested_at, purge_after, completed_at,
                       lease_id, lease_until, last_error_code, request_id
                FROM account_deletions
                WHERE id=%s
                """,
                (deletion_id,),
            ).fetchone()
            assert receipt[:6] == (
                None,
                "completed",
                receipt_before[0],
                receipt_before[1],
                receipt_before[2],
                receipt_before[3],
            )
            assert receipt[6] is not None
            assert receipt[7:10] == (None, None, None)
            assert receipt[10] == receipt_before[4]
            assert connection.execute(
                """
                SELECT user_id, status
                FROM account_deletions
                WHERE id=%s
                """,
                (cancelled_id,),
            ).fetchone() == (None, "cancelled")
            assert connection.execute(
                "SELECT count(*) FROM registration_invitations WHERE id=%s",
                (seeded["invitation"],),
            ).fetchone() == (0,)
            assert connection.execute(
                """
                SELECT user_id, target_id, details
                FROM security_events
                WHERE id=%s
                """,
                (seeded["target_event"],),
            ).fetchone() == (control_owner_id, None, {})
            assert connection.execute(
                "SELECT count(*) FROM projects WHERE id=%s",
                (control_project_id,),
            ).fetchone() == (1,)
            assert connection.execute(
                """
                SELECT created_by_user_id
                FROM ai_config_versions
                WHERE id=%s
                """,
                (seeded["config"],),
            ).fetchone() == (None,)
            assert connection.execute(
                """
                SELECT updated_by_user_id
                FROM registration_policies
                WHERE id=1
                """
            ).fetchone() == (None,)
            assert connection.execute(
                """
                SELECT
                    (SELECT count(*) FROM registration_policies),
                    (SELECT count(*) FROM ai_config_capabilities),
                    (SELECT count(*) FROM ai_quota_policy_state)
                """
            ).fetchone() == global_counts

            replacement_id, _ = seed_user(connection, email=email)
            connection.commit()
            assert replacement_id != owner_id


def test_purge_failure_rolls_back_and_records_bounded_retry(
    admin_url,
    monkeypatch,
):
    with isolated_database(admin_url) as database_url:
        with psycopg.connect(psycopg_url(database_url)) as connection:
            owner_id, email = seed_user(
                connection,
                status="pending_deletion",
            )
            control_owner_id, _ = seed_user(connection)
            deletion_id = seed_deletion(connection, owner_id)
            seeded = seed_personal_data(
                connection,
                owner_id,
                email,
                control_owner_id,
            )
            connection.commit()

        async def fail_after_lock(*_args, **_kwargs):
            raise RuntimeError("private failure details")

        monkeypatch.setattr(
            deletion_service,
            "scrub_account_audit",
            fail_after_lock,
        )
        settings = Settings(database_url=database_url, environment="test")
        database = Database(settings)

        async def fail_once():
            try:
                with pytest.raises(RuntimeError, match="private failure"):
                    await process_one(database)
            finally:
                await database.close()

        before = datetime.now(UTC)
        asyncio.run(fail_once())
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT status FROM users WHERE id=%s",
                (owner_id,),
            ).fetchone() == ("pending_deletion",)
            assert connection.execute(
                "SELECT count(*) FROM tasks WHERE id=%s",
                (seeded["task"],),
            ).fetchone() == (1,)
            deletion = connection.execute(
                """
                SELECT status, attempt_count, lease_id, lease_until,
                       available_at, last_error_code, last_error_message
                FROM account_deletions
                WHERE id=%s
                """,
                (deletion_id,),
            ).fetchone()
            assert deletion[:4] == ("pending", 1, None, None)
            assert deletion[4] > before
            assert deletion[5:] == (
                "account_deletion_failed",
                "账号数据清理失败，系统会自动重试。",
            )


def test_claim_reclaims_stale_lease_and_rejects_wrong_claim(admin_url):
    with isolated_database(admin_url) as database_url:
        with psycopg.connect(psycopg_url(database_url)) as connection:
            future_owner, _ = seed_user(
                connection,
                status="pending_deletion",
            )
            future_id = seed_deletion(
                connection,
                future_owner,
                due=False,
            )
            stale_owner, _ = seed_user(
                connection,
                status="pending_deletion",
            )
            old_lease = uuid4()
            stale_id = seed_deletion(
                connection,
                stale_owner,
                status="processing",
                lease_id=old_lease,
                lease_until=datetime.now(UTC) - timedelta(minutes=1),
            )
            cancelled_owner, _ = seed_user(connection)
            cancelled_id = seed_deletion(
                connection,
                cancelled_owner,
                status="cancelled",
            )
            connection.commit()

        settings = Settings(database_url=database_url, environment="test")
        database = Database(settings)

        async def exercise():
            claim = await claim_next_deletion(database, lease_seconds=60)
            assert claim is not None
            assert claim.id == stale_id
            assert claim.user_id == stale_owner
            assert claim.lease_id != old_lease
            assert claim.attempt_number == 2
            wrong = ClaimedDeletion(
                id=claim.id,
                user_id=claim.user_id,
                lease_id=uuid4(),
                attempt_number=claim.attempt_number,
            )
            assert not await purge_claimed_account(database, wrong)
            assert not await record_deletion_failure(
                database,
                wrong,
            )
            assert await purge_claimed_account(database, claim)
            assert await claim_next_deletion(database) is None
            await database.close()

        asyncio.run(exercise())
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT count(*) FROM users WHERE id=%s",
                (stale_owner,),
            ).fetchone() == (0,)
            assert connection.execute(
                "SELECT status FROM account_deletions WHERE id=%s",
                (stale_id,),
            ).fetchone() == ("completed",)
            assert connection.execute(
                "SELECT status FROM users WHERE id=%s",
                (future_owner,),
            ).fetchone() == ("pending_deletion",)
            assert connection.execute(
                "SELECT status FROM account_deletions WHERE id=%s",
                (future_id,),
            ).fetchone() == ("pending",)
            assert connection.execute(
                "SELECT status FROM account_deletions WHERE id=%s",
                (cancelled_id,),
            ).fetchone() == ("cancelled",)


def test_purged_credentials_fail_and_email_registers_as_new_account(
    client,
    database_url,
):
    email, old_owner_text = create_account(client, database_url)
    old_owner_id = UUID(old_owner_text)
    requested = client.post(
        "/api/v1/account-deletion",
        json={
            "operation_id": str(uuid4()),
            "email_confirmation": email,
            "current_password": PASSWORD,
        },
    )
    assert requested.status_code == 202, requested.text
    deletion_id = UUID(requested.json()["deletion"]["id"])
    lease_id = uuid4()
    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            """
            UPDATE account_deletions
            SET status='processing',
                requested_at=now()-interval '8 days',
                purge_after=now()-interval '1 day',
                available_at=now()-interval '1 day',
                started_at=now(),
                lease_id=%s,
                lease_until=now()+interval '2 minutes',
                attempt_count=1,
                updated_at=now()
            WHERE id=%s
            """,
            (lease_id, deletion_id),
        )

    settings = Settings(database_url=database_url, environment="test")
    database = Database(settings)

    async def purge():
        try:
            assert await purge_claimed_account(
                database,
                ClaimedDeletion(
                    id=deletion_id,
                    user_id=old_owner_id,
                    lease_id=lease_id,
                    attempt_number=1,
                ),
            )
        finally:
            await database.close()

    asyncio.run(purge())

    login = client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": PASSWORD,
            "device_name": "已删除账号",
        },
    )
    assert login.status_code == 401
    assert login.json()["error"]["code"] == "invalid_credentials"

    registered = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD},
    )
    assert registered.status_code == 202, registered.text
    with psycopg.connect(psycopg_url(database_url)) as connection:
        new_owner_id = connection.execute(
            "SELECT id FROM users WHERE email=%s",
            (email,),
        ).fetchone()[0]
        assert new_owner_id != old_owner_id
        assert connection.execute(
            """
            SELECT user_id, status
            FROM account_deletions
            WHERE id=%s
            """,
            (deletion_id,),
        ).fetchone() == (None, "completed")
