from datetime import date
from typing import get_args
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.errors import (
    CheckViolation,
    ForeignKeyViolation,
    UniqueViolation,
)

from tests.conftest import isolated_database, migrate, psycopg_url

pytestmark = pytest.mark.integration

PLANNING_TABLES = {
    "planning_input_revisions",
    "planning_input_snapshots",
    "ai_proposals",
    "ai_proposal_changes",
    "plans",
    "plan_versions",
    "scheduled_blocks",
    "plan_source_conflicts",
    "plan_unscheduled_items",
}

REVISION_TRIGGER_TABLES = {
    "user_time_settings",
    "tasks",
    "task_steps",
    "task_schedules",
    "task_occurrence_states",
    "task_links",
    "calendar_series",
    "calendar_exceptions",
    "calendar_additions",
    "calendar_subscription_events",
    "calendar_subscription_exceptions",
    "calendar_subscription_overrides",
    "plans",
}


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


def seed_planning_config(connection) -> UUID:
    version = connection.execute(
        """
        UPDATE ai_config_capabilities
        SET next_version=next_version + 1, updated_at=now()
        WHERE capability='planning'
        RETURNING next_version - 1
        """
    ).fetchone()[0]
    config_id = uuid4()
    connection.execute(
        """
        INSERT INTO ai_config_versions (
            id, capability, version, endpoint_url, model,
            api_key_ciphertext, timeout_seconds, max_output_tokens,
            status
        ) VALUES (
            %s, 'planning', %s, 'https://ai.example.com/v1',
            'planning-model', %s, 60, 4096, 'draft'
        )
        """,
        (config_id, version, b"encrypted-key"),
    )
    return config_id


def seed_snapshot(
    connection,
    owner_id: UUID,
    *,
    target_date: date = date(2026, 10, 5),
) -> UUID:
    snapshot_id = uuid4()
    connection.execute(
        """
        INSERT INTO planning_input_snapshots (
            id, owner_id, revision, scope, target_date,
            week_start_date, week_end_date, time_zone, density,
            buffer_minutes, availability_start, availability_end,
            user_instructions, selected_task_ids, task_candidates,
            fixed_occurrences, preserved_dates, content_sha256
        ) VALUES (
            %s, %s, 0, 'week', %s,
            DATE '2026-10-05', DATE '2026-10-11',
            'Asia/Shanghai', 'balanced', 15, TIME '08:00', TIME '22:00',
            '', '[]', '[]', '[]', '[]', %s
        )
        """,
        (snapshot_id, owner_id, target_date, snapshot_id.hex * 2),
    )
    return snapshot_id


def seed_planning_job(
    connection,
    owner_id: UUID,
    config_id: UUID,
    snapshot_id: UUID,
    *,
    conversation_id: UUID | None = None,
) -> UUID:
    job_id = uuid4()
    connection.execute(
        """
        INSERT INTO ai_jobs (
            id, owner_id, capability, config_version_id,
            planning_snapshot_id, conversation_id, quota_mode,
            status, input_summary, available_at, deadline_at
        ) VALUES (
            %s, %s, 'planning', %s, %s, %s, 'platform',
            'queued', '{"scope":"week"}', now(), now() + interval '3 minutes'
        )
        """,
        (job_id, owner_id, config_id, snapshot_id, conversation_id),
    )
    return job_id


def seed_proposal(
    connection,
    owner_id: UUID,
    job_id: UUID,
    snapshot_id: UUID,
) -> UUID:
    proposal_id = uuid4()
    connection.execute(
        """
        INSERT INTO ai_proposals (
            id, owner_id, kind, source_job_id, input_snapshot_id,
            schema_version, status, title, summary, assumptions,
            warnings, payload, expires_at
        ) VALUES (
            %s, %s, 'plan', %s, %s, 1, 'pending',
            '本周计划', '按现有任务生成', '[]', '[]', '{}',
            now() + interval '7 days'
        )
        """,
        (proposal_id, owner_id, job_id, snapshot_id),
    )
    return proposal_id


def seed_task(connection, owner_id: UUID, *, title: str = "测试任务") -> UUID:
    task_id = uuid4()
    connection.execute(
        """
        INSERT INTO tasks (id, owner_id, title)
        VALUES (%s, %s, %s)
        """,
        (task_id, owner_id, title),
    )
    return task_id


def seed_plan_version(
    connection,
    owner_id: UUID,
    *,
    version: int = 1,
    plan_id: UUID | None = None,
) -> tuple[UUID, UUID]:
    plan_id = plan_id or uuid4()
    plan_version_id = uuid4()
    if version == 1:
        connection.execute(
            """
            INSERT INTO plans (
                id, owner_id, week_start_date, time_zone,
                current_version_id, version
            ) VALUES (
                %s, %s, DATE '2026-10-05', 'Asia/Shanghai', %s, 1
            )
            """,
            (plan_id, owner_id, plan_version_id),
        )
    connection.execute(
        """
        INSERT INTO plan_versions (
            id, plan_id, owner_id, version, source, scope,
            target_date, density, buffer_minutes,
            availability_start, availability_end,
            summary, assumptions, input_revision
        ) VALUES (
            %s, %s, %s, %s, 'manual', 'week',
            DATE '2026-10-05', 'balanced', 15,
            TIME '08:00', TIME '22:00',
            '手工计划', '[]', 0
        )
        """,
        (plan_version_id, plan_id, owner_id, version),
    )
    return plan_id, plan_version_id


def test_planning_migration_round_trip_and_orm_consistency(admin_url):
    with isolated_database(admin_url) as database_url:
        with psycopg.connect(psycopg_url(database_url)) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema='public'
                    """
                )
            }
            assert tables >= PLANNING_TABLES
            assert connection.execute(
                """
                SELECT capability, next_version, current_version_id
                FROM ai_config_capabilities
                WHERE capability='planning'
                """
            ).fetchone() == ("planning", 1, None)
            assert (
                connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
                == "0032_subscription_exceptions"
            )

        from itime.ai.models import AIJob
        from itime.ai.schemas import Capability
        from itime.database import Base
        from itime.planning import models as planning_models

        assert set(get_args(Capability)) >= {
            "calendar_screenshot",
            "conversation",
            "planning",
        }
        assert AIJob.__table__.c.planning_snapshot_id.nullable
        assert {
            planning_models.PlanningInputRevision.__tablename__,
            planning_models.PlanningInputSnapshot.__tablename__,
            planning_models.Proposal.__tablename__,
            planning_models.ProposalChange.__tablename__,
            planning_models.Plan.__tablename__,
            planning_models.PlanVersion.__tablename__,
            planning_models.ScheduledBlock.__tablename__,
            planning_models.PlanSourceConflict.__tablename__,
            planning_models.PlanUnscheduledItem.__tablename__,
        } == PLANNING_TABLES
        assert Base.metadata.tables.keys() >= PLANNING_TABLES
        migrate(database_url, "check")

        migrate(database_url, "downgrade", "0021_ai_user_quota")
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert all(
                connection.execute(
                    "SELECT to_regclass(%s)",
                    (table_name,),
                ).fetchone()[0]
                is None
                for table_name in PLANNING_TABLES
            )
            assert (
                connection.execute(
                    """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name='ai_jobs'
                  AND column_name='planning_snapshot_id'
                """
                ).fetchone()
                is None
            )
            assert (
                connection.execute(
                    """
                SELECT capability
                FROM ai_config_capabilities
                WHERE capability='planning'
                """
                ).fetchone()
                is None
            )
            with pytest.raises(CheckViolation):
                connection.execute(
                    """
                    INSERT INTO ai_config_capabilities (capability)
                    VALUES ('planning')
                    """
                )

        migrate(database_url, "upgrade", "head")
        migrate(database_url, "check")


def test_planning_downgrade_removes_planning_capability_data(admin_url):
    with isolated_database(admin_url) as database_url:
        with psycopg.connect(psycopg_url(database_url)) as connection:
            owner_id = seed_user(connection)
            config_id = seed_planning_config(connection)
            snapshot_id = seed_snapshot(connection, owner_id)
            job_id = seed_planning_job(
                connection,
                owner_id,
                config_id,
                snapshot_id,
            )
            seed_proposal(connection, owner_id, job_id, snapshot_id)
            connection.commit()

        migrate(database_url, "downgrade", "0021_ai_user_quota")
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert (
                connection.execute(
                    "SELECT count(*) FROM ai_jobs WHERE capability='planning'"
                ).fetchone()[0]
                == 0
            )
            assert (
                connection.execute(
                    "SELECT count(*) FROM ai_config_versions WHERE capability='planning'"
                ).fetchone()[0]
                == 0
            )
            assert (
                connection.execute(
                    """
                SELECT count(*) FROM ai_config_capabilities
                WHERE capability='planning'
                """
                ).fetchone()[0]
                == 0
            )

        migrate(database_url, "upgrade", "head")
        migrate(database_url, "check")


def test_planning_revision_triggers_cover_sources_and_plan_switches(admin_url):
    with isolated_database(admin_url) as database_url:
        with psycopg.connect(psycopg_url(database_url)) as connection:
            triggered_tables = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT DISTINCT target.relname
                    FROM pg_trigger AS trigger
                    JOIN pg_class AS target ON target.oid=trigger.tgrelid
                    WHERE NOT trigger.tgisinternal
                      AND trigger.tgname LIKE 'trg_planning_revision_%'
                    """
                )
            }
            assert triggered_tables == REVISION_TRIGGER_TABLES

            owner_id = seed_user(connection)
            connection.execute(
                """
                INSERT INTO user_time_settings (owner_id, time_zone)
                VALUES (%s, 'Asia/Shanghai')
                """,
                (owner_id,),
            )
            assert (
                connection.execute(
                    """
                SELECT revision
                FROM planning_input_revisions
                WHERE owner_id=%s
                """,
                    (owner_id,),
                ).fetchone()[0]
                == 1
            )

            task_id = seed_task(connection, owner_id)
            connection.execute(
                "UPDATE tasks SET title='更新后的任务' WHERE id=%s",
                (task_id,),
            )
            assert (
                connection.execute(
                    """
                SELECT revision
                FROM planning_input_revisions
                WHERE owner_id=%s
                """,
                    (owner_id,),
                ).fetchone()[0]
                == 3
            )

            plan_id, _ = seed_plan_version(connection, owner_id)
            _, second_version_id = seed_plan_version(
                connection,
                owner_id,
                version=2,
                plan_id=plan_id,
            )
            connection.execute(
                """
                UPDATE plans
                SET current_version_id=%s, version=2, updated_at=now()
                WHERE id=%s
                """,
                (second_version_id, plan_id),
            )
            assert (
                connection.execute(
                    """
                SELECT revision
                FROM planning_input_revisions
                WHERE owner_id=%s
                """,
                    (owner_id,),
                ).fetchone()[0]
                == 5
            )


def test_planning_revision_trigger_does_not_block_user_cascade_delete(admin_url):
    with isolated_database(admin_url) as database_url:
        with psycopg.connect(psycopg_url(database_url)) as connection:
            owner_id = seed_user(connection)
            task_id = seed_task(connection, owner_id)
            _, version_id = seed_plan_version(connection, owner_id)
            connection.execute(
                """
                INSERT INTO plan_unscheduled_items (
                    id, plan_version_id, owner_id, position, task_id,
                    task_title_snapshot, remaining_minutes, reason, explanation
                ) VALUES (
                    %s, %s, %s, 0, %s,
                    '删除账号时保留到级联', 60, 'no_capacity', '测试级联删除'
                )
                """,
                (uuid4(), version_id, owner_id, task_id),
            )
            connection.commit()

            with connection.transaction():
                connection.execute("DELETE FROM users WHERE id=%s", (owner_id,))

            assert (
                connection.execute(
                    """
                    SELECT count(*)
                    FROM planning_input_revisions
                    WHERE owner_id=%s
                    """,
                    (owner_id,),
                ).fetchone()[0]
                == 0
            )


def test_planning_ownership_uniqueness_and_conversation_mapping(admin_url):
    with isolated_database(admin_url) as database_url:
        with psycopg.connect(psycopg_url(database_url)) as connection:
            owner_id = seed_user(connection)
            other_owner_id = seed_user(connection)
            config_id = seed_planning_config(connection)
            snapshot_id = seed_snapshot(connection, owner_id)

            with pytest.raises(ForeignKeyViolation):
                with connection.transaction():
                    seed_planning_job(
                        connection,
                        other_owner_id,
                        config_id,
                        snapshot_id,
                    )

            job_id = seed_planning_job(
                connection,
                owner_id,
                config_id,
                snapshot_id,
            )
            connection.commit()

            with pytest.raises(UniqueViolation):
                with connection.transaction():
                    seed_planning_job(
                        connection,
                        owner_id,
                        config_id,
                        snapshot_id,
                    )

            seed_proposal(connection, owner_id, job_id, snapshot_id)
            connection.commit()
            with pytest.raises(UniqueViolation):
                with connection.transaction():
                    seed_proposal(connection, owner_id, job_id, snapshot_id)

            cross_owner_snapshot_id = seed_snapshot(connection, owner_id)
            cross_owner_job_id = seed_planning_job(
                connection,
                owner_id,
                config_id,
                cross_owner_snapshot_id,
            )
            connection.commit()
            with pytest.raises(ForeignKeyViolation):
                with connection.transaction():
                    seed_proposal(
                        connection,
                        other_owner_id,
                        cross_owner_job_id,
                        cross_owner_snapshot_id,
                    )

            plan_id, plan_version_id = seed_plan_version(connection, owner_id)
            connection.commit()
            with pytest.raises(UniqueViolation):
                with connection.transaction():
                    connection.execute(
                        """
                        INSERT INTO plans (
                            id, owner_id, week_start_date, time_zone,
                            current_version_id, version
                        ) VALUES (
                            %s, %s, DATE '2026-10-05',
                            'Asia/Shanghai', %s, 1
                        )
                        """,
                        (uuid4(), owner_id, uuid4()),
                    )

            other_task_id = seed_task(connection, other_owner_id)
            connection.commit()
            with pytest.raises(ForeignKeyViolation):
                with connection.transaction():
                    connection.execute(
                        """
                        INSERT INTO scheduled_blocks (
                            id, plan_version_id, owner_id, position,
                            task_id, task_title_snapshot, local_date,
                            local_starts_at, local_ends_at, time_zone,
                            starts_at, ends_at, estimate_source,
                            estimated_minutes
                        ) VALUES (
                            %s, %s, %s, 0, %s, '越权任务',
                            DATE '2026-10-05', TIME '09:00', TIME '10:00',
                            'Asia/Shanghai',
                            TIMESTAMPTZ '2026-10-05 01:00:00+00',
                            TIMESTAMPTZ '2026-10-05 02:00:00+00',
                            'user', 60
                        )
                        """,
                        (
                            uuid4(),
                            plan_version_id,
                            owner_id,
                            other_task_id,
                        ),
                    )

            conversation_id = uuid4()
            connection.execute(
                """
                INSERT INTO ai_conversations (id, owner_id, title)
                VALUES (%s, %s, '规划对话')
                """,
                (conversation_id, owner_id),
            )
            orphan_snapshot_id = seed_snapshot(connection, owner_id)
            connection.commit()
            with pytest.raises(CheckViolation):
                with connection.transaction():
                    seed_planning_job(
                        connection,
                        owner_id,
                        config_id,
                        orphan_snapshot_id,
                        conversation_id=conversation_id,
                    )

            request_message_id = uuid4()
            response_message_id = uuid4()
            mapped_snapshot_id = seed_snapshot(connection, owner_id)
            with connection.transaction():
                connection.execute(
                    """
                    INSERT INTO ai_conversation_messages (
                        id, owner_id, conversation_id, sequence,
                        role, status, content, completed_at
                    ) VALUES (
                        %s, %s, %s, 1, 'user', 'complete',
                        '生成本周计划', now()
                    ), (
                        %s, %s, %s, 2, 'assistant', 'queued', '', NULL
                    )
                    """,
                    (
                        request_message_id,
                        owner_id,
                        conversation_id,
                        response_message_id,
                        owner_id,
                        conversation_id,
                    ),
                )
                mapped_job_id = seed_planning_job(
                    connection,
                    owner_id,
                    config_id,
                    mapped_snapshot_id,
                    conversation_id=conversation_id,
                )
                connection.execute(
                    """
                    INSERT INTO ai_chat_generations (
                        job_id, owner_id, conversation_id,
                        request_message_id, response_message_id,
                        history_through_sequence, context_message_ids
                    ) VALUES (%s, %s, %s, %s, %s, 1, '[]')
                    """,
                    (
                        mapped_job_id,
                        owner_id,
                        conversation_id,
                        request_message_id,
                        response_message_id,
                    ),
                )

            assert connection.execute(
                """
                SELECT capability, planning_snapshot_id, conversation_id
                FROM ai_jobs
                WHERE id=%s
                """,
                (mapped_job_id,),
            ).fetchone() == (
                "planning",
                mapped_snapshot_id,
                conversation_id,
            )
            assert plan_id is not None


def test_plan_history_rows_are_immutable(admin_url):
    with isolated_database(admin_url) as database_url:
        with psycopg.connect(psycopg_url(database_url)) as connection:
            owner_id = seed_user(connection)
            task_id = seed_task(connection, owner_id)
            _, plan_version_id = seed_plan_version(connection, owner_id)
            block_id = uuid4()
            unscheduled_id = uuid4()
            connection.execute(
                """
                INSERT INTO scheduled_blocks (
                    id, plan_version_id, owner_id, position,
                    task_id, task_title_snapshot, local_date,
                    local_starts_at, local_ends_at, time_zone,
                    starts_at, ends_at, estimate_source,
                    estimated_minutes
                ) VALUES (
                    %s, %s, %s, 0, %s, '测试任务',
                    DATE '2026-10-05', TIME '09:00', TIME '10:00',
                    'Asia/Shanghai',
                    TIMESTAMPTZ '2026-10-05 01:00:00+00',
                    TIMESTAMPTZ '2026-10-05 02:00:00+00',
                    'user', 60
                )
                """,
                (block_id, plan_version_id, owner_id, task_id),
            )
            connection.execute(
                """
                INSERT INTO plan_unscheduled_items (
                    id, plan_version_id, owner_id, position,
                    task_id, task_title_snapshot, remaining_minutes,
                    reason, explanation
                ) VALUES (
                    %s, %s, %s, 0, %s, '测试任务', 30,
                    'no_capacity', '当天容量不足'
                )
                """,
                (unscheduled_id, plan_version_id, owner_id, task_id),
            )
            connection.commit()

            with pytest.raises(CheckViolation):
                with connection.transaction():
                    connection.execute(
                        """
                        INSERT INTO scheduled_blocks (
                            id, plan_version_id, owner_id, position,
                            task_id, task_title_snapshot,
                            step_ids, step_titles_snapshot,
                            local_date, local_starts_at, local_ends_at,
                            time_zone, starts_at, ends_at,
                            estimate_source, estimated_minutes
                        ) VALUES (
                            %s, %s, %s, 1, %s, '步骤数组不一致',
                            '["00000000-0000-0000-0000-000000000001"]',
                            '[]',
                            DATE '2026-10-05', TIME '10:15', TIME '11:15',
                            'Asia/Shanghai',
                            TIMESTAMPTZ '2026-10-05 02:15:00+00',
                            TIMESTAMPTZ '2026-10-05 03:15:00+00',
                            'user', 60
                        )
                        """,
                        (uuid4(), plan_version_id, owner_id, task_id),
                    )

            mutations = (
                (
                    "UPDATE plan_versions SET summary='被改写' WHERE id=%s",
                    plan_version_id,
                ),
                (
                    "UPDATE scheduled_blocks SET position=1 WHERE id=%s",
                    block_id,
                ),
                (
                    """
                    UPDATE plan_unscheduled_items
                    SET reason='outside_scope'
                    WHERE id=%s
                    """,
                    unscheduled_id,
                ),
            )
            for statement, row_id in mutations:
                with pytest.raises(CheckViolation):
                    with connection.transaction():
                        connection.execute(statement, (row_id,))

            assert (
                connection.execute(
                    "SELECT summary FROM plan_versions WHERE id=%s",
                    (plan_version_id,),
                ).fetchone()[0]
                == "手工计划"
            )
