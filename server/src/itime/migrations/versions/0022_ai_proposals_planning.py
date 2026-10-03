"""AI planning proposals and immutable plan versions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0022_ai_proposals_planning"
down_revision = "0021_ai_user_quota"
branch_labels = None
depends_on = None

REVISION_TRIGGER_TABLES = (
    "user_time_settings",
    "tasks",
    "task_steps",
    "task_schedules",
    "task_occurrence_states",
    "calendar_series",
    "calendar_exceptions",
    "calendar_additions",
    "calendar_subscription_events",
    "calendar_subscription_overrides",
    "plans",
)


def _replace_conversation_mapping() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION enforce_ai_conversation_job_mapping()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            target_job_id uuid;
            job_capability text;
            job_owner_id uuid;
            job_conversation_id uuid;
            job_exists boolean;
            mapping_count integer;
            valid_mapping_count integer;
        BEGIN
            IF TG_TABLE_NAME = 'ai_jobs' THEN
                target_job_id := CASE
                    WHEN TG_OP = 'DELETE' THEN OLD.id
                    ELSE NEW.id
                END;
            ELSE
                target_job_id := CASE
                    WHEN TG_OP = 'DELETE' THEN OLD.job_id
                    ELSE NEW.job_id
                END;
            END IF;

            SELECT capability, owner_id, conversation_id
            INTO job_capability, job_owner_id, job_conversation_id
            FROM ai_jobs
            WHERE id = target_job_id;
            job_exists := FOUND;

            SELECT count(*)
            INTO mapping_count
            FROM ai_chat_generations
            WHERE job_id = target_job_id;

            IF NOT job_exists THEN
                IF mapping_count <> 0 THEN
                    RAISE EXCEPTION 'AI chat generation has no job'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NULL;
            END IF;

            IF job_capability = 'conversation'
               OR (job_capability = 'planning'
                   AND job_conversation_id IS NOT NULL) THEN
                SELECT count(*)
                INTO valid_mapping_count
                FROM ai_chat_generations AS generation
                JOIN ai_conversation_messages AS request_message
                  ON request_message.id = generation.request_message_id
                 AND request_message.owner_id = generation.owner_id
                 AND request_message.conversation_id = generation.conversation_id
                JOIN ai_conversation_messages AS response_message
                  ON response_message.id = generation.response_message_id
                 AND response_message.owner_id = generation.owner_id
                 AND response_message.conversation_id = generation.conversation_id
                WHERE generation.job_id = target_job_id
                  AND generation.owner_id = job_owner_id
                  AND generation.conversation_id = job_conversation_id
                  AND request_message.role = 'user'
                  AND response_message.role = 'assistant';

                IF mapping_count <> 1 OR valid_mapping_count <> 1 THEN
                    RAISE EXCEPTION
                        'conversation-linked AI job requires exactly one valid chat generation'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF mapping_count <> 0 THEN
                RAISE EXCEPTION
                    'AI job without a conversation cannot have a chat generation'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END;
        $$
        """
    )


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_ai_config_capabilities_capability"),
        "ai_config_capabilities",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_ai_config_capabilities_capability"),
        "ai_config_capabilities",
        "capability IN ('calendar_screenshot', 'conversation', 'planning')",
    )
    op.execute(
        """
        INSERT INTO ai_config_capabilities (capability, next_version)
        VALUES ('planning', 1)
        ON CONFLICT (capability) DO NOTHING
        """
    )
    for table_name in (
        "ai_config_versions",
        "ai_jobs",
        "ai_body_logs",
        "ai_quality_samples",
    ):
        op.drop_constraint(
            op.f(f"ck_{table_name}_capability"),
            table_name,
            type_="check",
        )
        op.create_check_constraint(
            op.f(f"ck_{table_name}_capability"),
            table_name,
            "capability IN ('calendar_screenshot', 'conversation', 'planning')",
        )

    op.create_table(
        "planning_input_revisions",
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("revision >= 0", name=op.f("ck_planning_input_revisions_revision")),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_planning_input_revisions_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("owner_id", name=op.f("pk_planning_input_revisions")),
    )
    op.create_index(
        "ix_planning_input_revisions_updated",
        "planning_input_revisions",
        ["updated_at", "owner_id"],
        unique=False,
    )
    op.create_table(
        "planning_input_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("scope", sa.String(length=8), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=False),
        sa.Column("week_start_date", sa.Date(), nullable=False),
        sa.Column("week_end_date", sa.Date(), nullable=False),
        sa.Column("time_zone", sa.String(length=64), nullable=False),
        sa.Column("density", sa.String(length=12), nullable=False),
        sa.Column("buffer_minutes", sa.Integer(), nullable=False),
        sa.Column("availability_start", sa.Time(), nullable=False),
        sa.Column("availability_end", sa.Time(), nullable=False),
        sa.Column("user_instructions", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "selected_task_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "task_candidates",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "fixed_occurrences",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("current_plan_id", sa.Uuid(), nullable=True),
        sa.Column("current_plan_version", sa.Integer(), nullable=True),
        sa.Column(
            "preserved_dates",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "density IN ('loose', 'balanced', 'tight')",
            name=op.f("ck_planning_input_snapshots_density"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(fixed_occurrences) = 'array' "
            "AND jsonb_array_length(fixed_occurrences) <= 2000",
            name=op.f("ck_planning_input_snapshots_fixed_occurrences"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(preserved_dates) = 'array' AND jsonb_array_length(preserved_dates) <= 7",
            name=op.f("ck_planning_input_snapshots_preserved_dates"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(selected_task_ids) = 'array' "
            "AND jsonb_array_length(selected_task_ids) <= 100",
            name=op.f("ck_planning_input_snapshots_selected_task_ids"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(task_candidates) = 'array' "
            "AND jsonb_array_length(task_candidates) <= 100",
            name=op.f("ck_planning_input_snapshots_task_candidates"),
        ),
        sa.CheckConstraint(
            "scope IN ('day', 'week')", name=op.f("ck_planning_input_snapshots_scope")
        ),
        sa.CheckConstraint(
            "(current_plan_id IS NULL) = (current_plan_version IS NULL)",
            name=op.f("ck_planning_input_snapshots_current_plan"),
        ),
        sa.CheckConstraint(
            "EXTRACT(ISODOW FROM week_start_date) = 1",
            name=op.f("ck_planning_input_snapshots_week_start"),
        ),
        sa.CheckConstraint(
            "availability_start < availability_end",
            name=op.f("ck_planning_input_snapshots_availability"),
        ),
        sa.CheckConstraint(
            "buffer_minutes BETWEEN 0 AND 60 AND mod(buffer_minutes, 5) = 0",
            name=op.f("ck_planning_input_snapshots_buffer_minutes"),
        ),
        sa.CheckConstraint(
            "char_length(user_instructions) <= 4000",
            name=op.f("ck_planning_input_snapshots_user_instructions"),
        ),
        sa.CheckConstraint(
            "length(btrim(time_zone)) > 0", name=op.f("ck_planning_input_snapshots_time_zone")
        ),
        sa.CheckConstraint(
            "length(content_sha256) = 64", name=op.f("ck_planning_input_snapshots_content_sha256")
        ),
        sa.CheckConstraint("revision >= 0", name=op.f("ck_planning_input_snapshots_revision")),
        sa.CheckConstraint(
            "week_end_date = week_start_date + 6 "
            "AND target_date BETWEEN week_start_date AND week_end_date",
            name=op.f("ck_planning_input_snapshots_date_range"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_planning_input_snapshots_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_planning_input_snapshots")),
        sa.UniqueConstraint("id", "owner_id", name="uq_planning_input_snapshots_id_owner"),
    )
    op.create_index(
        "ix_planning_input_snapshots_owner_created",
        "planning_input_snapshots",
        ["owner_id", "created_at", "id"],
        unique=False,
    )
    op.create_table(
        "plans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("week_start_date", sa.Date(), nullable=False),
        sa.Column("time_zone", sa.String(length=64), nullable=False),
        sa.Column("current_version_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "EXTRACT(ISODOW FROM week_start_date) = 1", name=op.f("ck_plans_week_start")
        ),
        sa.CheckConstraint("length(btrim(time_zone)) > 0", name=op.f("ck_plans_time_zone")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_plans_version")),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name=op.f("fk_plans_owner_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_plans")),
        sa.UniqueConstraint("id", "owner_id", name="uq_plans_id_owner"),
        sa.UniqueConstraint("owner_id", "week_start_date", name="uq_plans_owner_week"),
    )
    op.create_index(
        "ix_plans_owner_week", "plans", ["owner_id", "week_start_date", "id"], unique=False
    )
    op.create_table(
        "ai_proposals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("source_job_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("source_message_id", sa.Uuid(), nullable=True),
        sa.Column("input_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("summary", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "assumptions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "warnings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_plan_id", sa.Uuid(), nullable=True),
        sa.Column("applied_plan_version_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND applied_at IS NULL "
            "AND rejected_at IS NULL AND applied_plan_id IS NULL "
            "AND applied_plan_version_id IS NULL) "
            "OR (status = 'applied' AND applied_at IS NOT NULL "
            "AND rejected_at IS NULL AND applied_plan_id IS NOT NULL "
            "AND applied_plan_version_id IS NOT NULL) "
            "OR (status = 'rejected' AND rejected_at IS NOT NULL "
            "AND applied_at IS NULL AND applied_plan_id IS NULL "
            "AND applied_plan_version_id IS NULL)",
            name=op.f("ck_ai_proposals_state"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(assumptions) = 'array' AND jsonb_array_length(assumptions) <= 20",
            name=op.f("ck_ai_proposals_assumptions"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'", name=op.f("ck_ai_proposals_payload")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(warnings) = 'array' AND jsonb_array_length(warnings) <= 20",
            name=op.f("ck_ai_proposals_warnings"),
        ),
        sa.CheckConstraint("kind IN ('plan')", name=op.f("ck_ai_proposals_kind")),
        sa.CheckConstraint(
            "status IN ('pending', 'applied', 'rejected')", name=op.f("ck_ai_proposals_status")
        ),
        sa.CheckConstraint(
            "(conversation_id IS NULL) = (source_message_id IS NULL)",
            name=op.f("ck_ai_proposals_conversation_message"),
        ),
        sa.CheckConstraint(
            "char_length(btrim(title)) BETWEEN 1 AND 200", name=op.f("ck_ai_proposals_title")
        ),
        sa.CheckConstraint("char_length(summary) <= 4000", name=op.f("ck_ai_proposals_summary")),
        sa.CheckConstraint("expires_at > created_at", name=op.f("ck_ai_proposals_expires_at")),
        sa.CheckConstraint("schema_version >= 1", name=op.f("ck_ai_proposals_schema_version")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_ai_proposals_version")),
        sa.ForeignKeyConstraint(
            ["conversation_id", "owner_id"],
            ["ai_conversations.id", "ai_conversations.owner_id"],
            name="fk_ai_proposals_conversation_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["input_snapshot_id", "owner_id"],
            ["planning_input_snapshots.id", "planning_input_snapshots.owner_id"],
            name="fk_ai_proposals_input_snapshot_owner",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_ai_proposals_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_job_id", "owner_id"],
            ["ai_jobs.id", "ai_jobs.owner_id"],
            name="fk_ai_proposals_source_job_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_message_id", "owner_id", "conversation_id"],
            [
                "ai_conversation_messages.id",
                "ai_conversation_messages.owner_id",
                "ai_conversation_messages.conversation_id",
            ],
            name="fk_ai_proposals_source_message",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_proposals")),
        sa.UniqueConstraint("id", "owner_id", name="uq_ai_proposals_id_owner"),
        sa.UniqueConstraint("source_job_id", name="uq_ai_proposals_source_job"),
    )
    op.create_index("ix_ai_proposals_expiry", "ai_proposals", ["expires_at", "id"], unique=False)
    op.create_index(
        "ix_ai_proposals_owner_status_created",
        "ai_proposals",
        ["owner_id", "status", "created_at", "id"],
        unique=False,
    )
    op.create_table(
        "ai_proposal_changes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("proposal_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=12), nullable=False),
        sa.Column("entity_type", sa.String(length=24), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=True),
        sa.Column(
            "before_data", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "after_data", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(action = 'create' AND before_data IS NULL "
            "AND after_data IS NOT NULL) "
            "OR (action = 'update' AND before_data IS NOT NULL "
            "AND after_data IS NOT NULL) "
            "OR (action = 'delete' AND before_data IS NOT NULL "
            "AND after_data IS NULL)",
            name=op.f("ck_ai_proposal_changes_data_shape"),
        ),
        sa.CheckConstraint(
            "action IN ('create', 'update', 'delete')", name=op.f("ck_ai_proposal_changes_action")
        ),
        sa.CheckConstraint(
            "after_data IS NULL OR jsonb_typeof(after_data) = 'object'",
            name=op.f("ck_ai_proposal_changes_after_data"),
        ),
        sa.CheckConstraint(
            "before_data IS NULL OR jsonb_typeof(before_data) = 'object'",
            name=op.f("ck_ai_proposal_changes_before_data"),
        ),
        sa.CheckConstraint(
            "entity_type IN ('plan', 'scheduled_block')",
            name=op.f("ck_ai_proposal_changes_entity_type"),
        ),
        sa.CheckConstraint("position >= 0", name=op.f("ck_ai_proposal_changes_position")),
        sa.ForeignKeyConstraint(
            ["proposal_id", "owner_id"],
            ["ai_proposals.id", "ai_proposals.owner_id"],
            name="fk_ai_proposal_changes_proposal_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_proposal_changes")),
        sa.UniqueConstraint(
            "proposal_id", "position", name="uq_ai_proposal_changes_proposal_position"
        ),
    )
    op.create_index(
        "ix_ai_proposal_changes_owner_proposal",
        "ai_proposal_changes",
        ["owner_id", "proposal_id", "position"],
        unique=False,
    )
    op.create_table(
        "plan_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=12), nullable=False),
        sa.Column("proposal_id", sa.Uuid(), nullable=True),
        sa.Column("scope", sa.String(length=8), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=False),
        sa.Column("density", sa.String(length=12), nullable=False),
        sa.Column("buffer_minutes", sa.Integer(), nullable=False),
        sa.Column("availability_start", sa.Time(), nullable=False),
        sa.Column("availability_end", sa.Time(), nullable=False),
        sa.Column("summary", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "assumptions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("input_revision", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(source = 'ai') = (proposal_id IS NOT NULL)",
            name=op.f("ck_plan_versions_proposal_source"),
        ),
        sa.CheckConstraint(
            "density IN ('loose', 'balanced', 'tight')", name=op.f("ck_plan_versions_density")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(assumptions) = 'array' AND jsonb_array_length(assumptions) <= 20",
            name=op.f("ck_plan_versions_assumptions"),
        ),
        sa.CheckConstraint("scope IN ('day', 'week')", name=op.f("ck_plan_versions_scope")),
        sa.CheckConstraint("source IN ('ai', 'manual')", name=op.f("ck_plan_versions_source")),
        sa.CheckConstraint(
            "availability_start < availability_end", name=op.f("ck_plan_versions_availability")
        ),
        sa.CheckConstraint(
            "buffer_minutes BETWEEN 0 AND 60 AND mod(buffer_minutes, 5) = 0",
            name=op.f("ck_plan_versions_buffer_minutes"),
        ),
        sa.CheckConstraint("char_length(summary) <= 4000", name=op.f("ck_plan_versions_summary")),
        sa.CheckConstraint("input_revision >= 0", name=op.f("ck_plan_versions_input_revision")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_plan_versions_version")),
        sa.ForeignKeyConstraint(
            ["plan_id", "owner_id"],
            ["plans.id", "plans.owner_id"],
            name="fk_plan_versions_plan_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id", "owner_id"],
            ["ai_proposals.id", "ai_proposals.owner_id"],
            name="fk_plan_versions_proposal_owner",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_plan_versions")),
        sa.UniqueConstraint("id", "owner_id", name="uq_plan_versions_id_owner"),
        sa.UniqueConstraint("id", "plan_id", "owner_id", name="uq_plan_versions_id_plan_owner"),
        sa.UniqueConstraint(
            "plan_id", "version", "owner_id", name="uq_plan_versions_plan_version_owner"
        ),
        sa.UniqueConstraint("proposal_id", name="uq_plan_versions_proposal"),
    )
    op.create_index(
        "ix_plan_versions_owner_plan_created",
        "plan_versions",
        ["owner_id", "plan_id", "created_at", "id"],
        unique=False,
    )
    op.create_table(
        "plan_unscheduled_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("plan_version_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("occurrence_key", sa.String(length=120), nullable=True),
        sa.Column("task_title_snapshot", sa.String(length=200), nullable=False),
        sa.Column("remaining_minutes", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=24), nullable=False),
        sa.Column("explanation", sa.String(length=500), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "reason IN ('no_capacity', 'deadline_conflict', "
            "'fixed_conflict', 'missing_estimate', 'outside_scope')",
            name=op.f("ck_plan_unscheduled_items_reason"),
        ),
        sa.CheckConstraint(
            "char_length(btrim(explanation)) BETWEEN 1 AND 500",
            name=op.f("ck_plan_unscheduled_items_explanation"),
        ),
        sa.CheckConstraint(
            "char_length(btrim(task_title_snapshot)) BETWEEN 1 AND 200",
            name=op.f("ck_plan_unscheduled_items_task_title_snapshot"),
        ),
        sa.CheckConstraint(
            "occurrence_key IS NULL OR char_length(btrim(occurrence_key)) BETWEEN 1 AND 120",
            name=op.f("ck_plan_unscheduled_items_occurrence_key"),
        ),
        sa.CheckConstraint("position >= 0", name=op.f("ck_plan_unscheduled_items_position")),
        sa.CheckConstraint(
            "remaining_minutes BETWEEN 1 AND 525600",
            name=op.f("ck_plan_unscheduled_items_remaining_minutes"),
        ),
        sa.ForeignKeyConstraint(
            ["plan_version_id", "owner_id"],
            ["plan_versions.id", "plan_versions.owner_id"],
            name="fk_plan_unscheduled_items_plan_version_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id", "owner_id"],
            ["tasks.id", "tasks.owner_id"],
            name="fk_plan_unscheduled_items_task_owner",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_plan_unscheduled_items")),
        sa.UniqueConstraint(
            "plan_version_id", "position", name="uq_plan_unscheduled_items_plan_version_position"
        ),
    )
    op.create_index(
        "ix_plan_unscheduled_items_owner_plan_version",
        "plan_unscheduled_items",
        ["owner_id", "plan_version_id", "position"],
        unique=False,
    )
    op.create_table(
        "scheduled_blocks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("plan_version_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("occurrence_key", sa.String(length=120), nullable=True),
        sa.Column("task_title_snapshot", sa.String(length=200), nullable=False),
        sa.Column(
            "step_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "step_titles_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("local_starts_at", sa.Time(), nullable=False),
        sa.Column("local_ends_at", sa.Time(), nullable=False),
        sa.Column("time_zone", sa.String(length=64), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("estimate_source", sa.String(length=8), nullable=False),
        sa.Column("estimated_minutes", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "estimate_source IN ('user', 'ai')", name=op.f("ck_scheduled_blocks_estimate_source")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(step_ids) = 'array' AND jsonb_array_length(step_ids) <= 100",
            name=op.f("ck_scheduled_blocks_step_ids"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(step_titles_snapshot) = 'array' "
            "AND jsonb_array_length(step_titles_snapshot) <= 100",
            name=op.f("ck_scheduled_blocks_step_titles_snapshot"),
        ),
        sa.CheckConstraint(
            "starts_at < ends_at AND ends_at - starts_at <= interval '3 hours'",
            name=op.f("ck_scheduled_blocks_utc_time"),
        ),
        sa.CheckConstraint(
            "char_length(btrim(task_title_snapshot)) BETWEEN 1 AND 200",
            name=op.f("ck_scheduled_blocks_task_title_snapshot"),
        ),
        sa.CheckConstraint(
            "estimated_minutes BETWEEN 1 AND 525600",
            name=op.f("ck_scheduled_blocks_estimated_minutes"),
        ),
        sa.CheckConstraint(
            "jsonb_array_length(step_ids) = jsonb_array_length(step_titles_snapshot)",
            name=op.f("ck_scheduled_blocks_step_snapshot_length"),
        ),
        sa.CheckConstraint(
            "length(btrim(time_zone)) > 0", name=op.f("ck_scheduled_blocks_time_zone")
        ),
        sa.CheckConstraint(
            "local_starts_at < local_ends_at", name=op.f("ck_scheduled_blocks_local_time")
        ),
        sa.CheckConstraint(
            "occurrence_key IS NULL OR char_length(btrim(occurrence_key)) BETWEEN 1 AND 120",
            name=op.f("ck_scheduled_blocks_occurrence_key"),
        ),
        sa.CheckConstraint("position >= 0", name=op.f("ck_scheduled_blocks_position")),
        sa.ForeignKeyConstraint(
            ["plan_version_id", "owner_id"],
            ["plan_versions.id", "plan_versions.owner_id"],
            name="fk_scheduled_blocks_plan_version_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id", "owner_id"],
            ["tasks.id", "tasks.owner_id"],
            name="fk_scheduled_blocks_task_owner",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scheduled_blocks")),
        sa.UniqueConstraint(
            "plan_version_id", "position", name="uq_scheduled_blocks_plan_version_position"
        ),
    )
    op.create_index(
        "ix_scheduled_blocks_owner_time",
        "scheduled_blocks",
        ["owner_id", "starts_at", "ends_at", "id"],
        unique=False,
    )
    op.add_column("ai_jobs", sa.Column("planning_snapshot_id", sa.Uuid(), nullable=True))
    op.create_unique_constraint("uq_ai_jobs_planning_snapshot", "ai_jobs", ["planning_snapshot_id"])
    op.create_foreign_key(
        "fk_ai_jobs_planning_snapshot_owner",
        "ai_jobs",
        "planning_input_snapshots",
        ["planning_snapshot_id", "owner_id"],
        ["id", "owner_id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        op.f("ck_ai_jobs_input_reference"),
        "ai_jobs",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_ai_jobs_input_reference"),
        "ai_jobs",
        "(capability = 'calendar_screenshot' "
        "AND input_media_id IS NOT NULL AND conversation_id IS NULL "
        "AND planning_snapshot_id IS NULL) "
        "OR (capability = 'conversation' "
        "AND input_media_id IS NULL AND conversation_id IS NOT NULL "
        "AND planning_snapshot_id IS NULL) "
        "OR (capability = 'planning' "
        "AND input_media_id IS NULL AND planning_snapshot_id IS NOT NULL)",
    )
    op.create_foreign_key(
        "fk_planning_input_snapshots_current_plan_version",
        "planning_input_snapshots",
        "plan_versions",
        ["current_plan_id", "current_plan_version", "owner_id"],
        ["plan_id", "version", "owner_id"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "fk_plans_current_version",
        "plans",
        "plan_versions",
        ["current_version_id", "id", "owner_id"],
        ["id", "plan_id", "owner_id"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "fk_ai_proposals_applied_plan_owner",
        "ai_proposals",
        "plans",
        ["applied_plan_id", "owner_id"],
        ["id", "owner_id"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "fk_ai_proposals_applied_plan_version",
        "ai_proposals",
        "plan_versions",
        ["applied_plan_version_id", "applied_plan_id", "owner_id"],
        ["id", "plan_id", "owner_id"],
        deferrable=True,
        initially="DEFERRED",
    )

    _replace_conversation_mapping()
    op.execute(
        """
        CREATE FUNCTION bump_planning_input_revision()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            target_owner_id uuid;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                target_owner_id := OLD.owner_id;
            ELSE
                target_owner_id := NEW.owner_id;
            END IF;

            INSERT INTO planning_input_revisions (owner_id, revision, updated_at)
            VALUES (target_owner_id, 1, now())
            ON CONFLICT (owner_id) DO UPDATE
            SET revision = planning_input_revisions.revision + 1,
                updated_at = now();
            RETURN NULL;
        END;
        $$
        """
    )
    for table_name in REVISION_TRIGGER_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER trg_planning_revision_{table_name}
            AFTER INSERT OR UPDATE OR DELETE ON {table_name}
            FOR EACH ROW
            EXECUTE FUNCTION bump_planning_input_revision()
            """
        )

    op.execute(
        """
        CREATE FUNCTION prevent_planning_history_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'planning history is immutable'
                USING ERRCODE = '23514';
        END;
        $$
        """
    )
    for table_name in (
        "planning_input_snapshots",
        "plan_versions",
        "scheduled_blocks",
        "plan_unscheduled_items",
    ):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_immutable
            BEFORE UPDATE ON {table_name}
            FOR EACH ROW
            EXECUTE FUNCTION prevent_planning_history_mutation()
            """
        )


def downgrade() -> None:
    for table_name in (
        "planning_input_snapshots",
        "plan_versions",
        "scheduled_blocks",
        "plan_unscheduled_items",
    ):
        op.execute(f"DROP TRIGGER trg_{table_name}_immutable ON {table_name}")
    op.execute("DROP FUNCTION prevent_planning_history_mutation()")

    for table_name in reversed(REVISION_TRIGGER_TABLES):
        op.execute(f"DROP TRIGGER trg_planning_revision_{table_name} ON {table_name}")
    op.execute("DROP FUNCTION bump_planning_input_revision()")

    op.drop_constraint(
        "fk_ai_jobs_planning_snapshot_owner",
        "ai_jobs",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_ai_jobs_planning_snapshot",
        "ai_jobs",
        type_="unique",
    )
    op.drop_constraint(
        "fk_planning_input_snapshots_current_plan_version",
        "planning_input_snapshots",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_plans_current_version",
        "plans",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_ai_proposals_applied_plan_version",
        "ai_proposals",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_ai_proposals_applied_plan_owner",
        "ai_proposals",
        type_="foreignkey",
    )

    op.drop_index("ix_scheduled_blocks_owner_time", table_name="scheduled_blocks")
    op.drop_table("scheduled_blocks")
    op.drop_index(
        "ix_plan_unscheduled_items_owner_plan_version",
        table_name="plan_unscheduled_items",
    )
    op.drop_table("plan_unscheduled_items")
    op.drop_index(
        "ix_plan_versions_owner_plan_created",
        table_name="plan_versions",
    )
    op.drop_table("plan_versions")
    op.drop_index(
        "ix_ai_proposal_changes_owner_proposal",
        table_name="ai_proposal_changes",
    )
    op.drop_table("ai_proposal_changes")
    op.drop_index(
        "ix_ai_proposals_owner_status_created",
        table_name="ai_proposals",
    )
    op.drop_index("ix_ai_proposals_expiry", table_name="ai_proposals")
    op.drop_table("ai_proposals")
    op.drop_index("ix_plans_owner_week", table_name="plans")
    op.drop_table("plans")
    op.drop_index(
        "ix_planning_input_snapshots_owner_created",
        table_name="planning_input_snapshots",
    )
    op.drop_table("planning_input_snapshots")
    op.drop_index(
        "ix_planning_input_revisions_updated",
        table_name="planning_input_revisions",
    )
    op.drop_table("planning_input_revisions")

    op.execute(
        """
        DELETE FROM ai_quality_samples
        WHERE capability = 'planning'
        """
    )
    op.execute(
        """
        DELETE FROM ai_quota_ledger_entries
        WHERE job_id IN (
            SELECT id FROM ai_jobs WHERE capability = 'planning'
        )
        """
    )
    op.execute(
        """
        DELETE FROM ai_quota_reservations
        WHERE job_id IN (
            SELECT id FROM ai_jobs WHERE capability = 'planning'
        )
        """
    )
    op.execute("DELETE FROM ai_jobs WHERE capability = 'planning'")
    op.execute(
        """
        DELETE FROM user_operations
        WHERE scope LIKE 'planning.%'
           OR scope LIKE 'proposal.%'
           OR scope LIKE 'plan.%'
        """
    )
    op.execute(
        """
        UPDATE ai_config_capabilities
        SET current_version_id = NULL
        WHERE capability = 'planning'
        """
    )
    op.execute("DELETE FROM ai_config_versions WHERE capability = 'planning'")
    op.execute("DELETE FROM ai_config_capabilities WHERE capability = 'planning'")
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")

    op.drop_constraint(
        op.f("ck_ai_jobs_input_reference"),
        "ai_jobs",
        type_="check",
    )
    op.drop_column("ai_jobs", "planning_snapshot_id")
    op.create_check_constraint(
        op.f("ck_ai_jobs_input_reference"),
        "ai_jobs",
        "(capability = 'calendar_screenshot' "
        "AND input_media_id IS NOT NULL AND conversation_id IS NULL) "
        "OR (capability = 'conversation' "
        "AND input_media_id IS NULL AND conversation_id IS NOT NULL)",
    )

    for table_name in (
        "ai_config_versions",
        "ai_jobs",
        "ai_body_logs",
        "ai_quality_samples",
    ):
        op.drop_constraint(
            op.f(f"ck_{table_name}_capability"),
            table_name,
            type_="check",
        )
        op.create_check_constraint(
            op.f(f"ck_{table_name}_capability"),
            table_name,
            "capability IN ('calendar_screenshot', 'conversation')",
        )
    op.drop_constraint(
        op.f("ck_ai_config_capabilities_capability"),
        "ai_config_capabilities",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_ai_config_capabilities_capability"),
        "ai_config_capabilities",
        "capability IN ('calendar_screenshot', 'conversation')",
    )
