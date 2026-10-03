"""AI body logs, quality samples, privacy settings, and audit details"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0018_ai_governance"
down_revision = "0017_ai_conversations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_privacy_settings",
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column(
            "quality_improvement_enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
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
            "version >= 1",
            name=op.f("ck_user_privacy_settings_version"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_user_privacy_settings_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "owner_id",
            name=op.f("pk_user_privacy_settings"),
        ),
    )
    op.create_index(
        "ix_user_privacy_settings_updated",
        "user_privacy_settings",
        ["updated_at", "owner_id"],
    )
    op.execute(
        """
        INSERT INTO user_privacy_settings (owner_id)
        SELECT id FROM users
        ON CONFLICT (owner_id) DO NOTHING
        """
    )

    op.create_table(
        "ai_body_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("capability", sa.String(length=50), nullable=False),
        sa.Column(
            "request_body",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("response_body", sa.Text(), server_default="", nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_stage", sa.String(length=40), nullable=True),
        sa.Column("provider_request_id", sa.String(length=255), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_prepared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "attempt_number BETWEEN 1 AND 5",
            name=op.f("ck_ai_body_logs_attempt_number"),
        ),
        sa.CheckConstraint(
            "capability IN ('calendar_screenshot', 'conversation')",
            name=op.f("ck_ai_body_logs_capability"),
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'retryable_failed', 'failed', "
            "'timed_out', 'cancelled', 'abandoned')",
            name=op.f("ck_ai_body_logs_status"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(request_body) = 'object'",
            name=op.f("ck_ai_body_logs_request_body"),
        ),
        sa.CheckConstraint(
            "octet_length(response_body) <= 1048576",
            name=op.f("ck_ai_body_logs_response_body_size"),
        ),
        sa.CheckConstraint(
            "(error_code IS NULL) = (error_stage IS NULL)",
            name=op.f("ck_ai_body_logs_error_pair"),
        ),
        sa.CheckConstraint(
            "(status = 'running') = (finished_at IS NULL)",
            name=op.f("ck_ai_body_logs_finished_state"),
        ),
        sa.CheckConstraint(
            "expires_at >= started_at",
            name=op.f("ck_ai_body_logs_expires_at"),
        ),
        sa.CheckConstraint(
            "request_prepared_at IS NULL OR request_prepared_at >= started_at",
            name=op.f("ck_ai_body_logs_request_prepared_at"),
        ),
        sa.ForeignKeyConstraint(
            ["job_id", "owner_id"],
            ["ai_jobs.id", "ai_jobs.owner_id"],
            name="fk_ai_body_logs_job_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_id", "attempt_number"],
            ["ai_job_attempts.job_id", "ai_job_attempts.attempt_number"],
            name="fk_ai_body_logs_job_attempt",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_body_logs")),
        sa.UniqueConstraint(
            "job_id",
            "attempt_number",
            name="uq_ai_body_logs_job_attempt",
        ),
    )
    op.create_index(
        "ix_ai_body_logs_owner_started",
        "ai_body_logs",
        ["owner_id", "started_at", "id"],
    )
    op.create_index(
        "ix_ai_body_logs_expiry",
        "ai_body_logs",
        ["expires_at", "id"],
    )

    op.create_table(
        "ai_quality_samples",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_job_id", sa.Uuid(), nullable=False),
        sa.Column("source_body_log_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("capability", sa.String(length=50), nullable=False),
        sa.Column(
            "request_body",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("response_body", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("outcome", sa.String(length=24), nullable=True),
        sa.Column("review_notes", sa.Text(), nullable=True),
        sa.Column("reviewed_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("sampled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "capability IN ('calendar_screenshot', 'conversation')",
            name=op.f("ck_ai_quality_samples_capability"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'reviewed')",
            name=op.f("ck_ai_quality_samples_status"),
        ),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN ('acceptable', 'needs_improvement')",
            name=op.f("ck_ai_quality_samples_outcome"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(request_body) = 'object'",
            name=op.f("ck_ai_quality_samples_request_body"),
        ),
        sa.CheckConstraint(
            "octet_length(response_body) <= 1048576",
            name=op.f("ck_ai_quality_samples_response_body_size"),
        ),
        sa.CheckConstraint(
            "review_notes IS NULL OR char_length(review_notes) <= 2000",
            name=op.f("ck_ai_quality_samples_review_notes"),
        ),
        sa.CheckConstraint(
            "version >= 1",
            name=op.f("ck_ai_quality_samples_version"),
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND outcome IS NULL "
            "AND reviewed_by_user_id IS NULL AND reviewed_at IS NULL) "
            "OR (status = 'reviewed' AND outcome IS NOT NULL "
            "AND reviewed_by_user_id IS NOT NULL AND reviewed_at IS NOT NULL)",
            name=op.f("ck_ai_quality_samples_review_state"),
        ),
        sa.CheckConstraint(
            "expires_at >= sampled_at",
            name=op.f("ck_ai_quality_samples_expires_at"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_ai_quality_samples_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_user_id"],
            ["users.id"],
            name=op.f("fk_ai_quality_samples_reviewed_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_quality_samples")),
        sa.UniqueConstraint(
            "source_job_id",
            name="uq_ai_quality_samples_source_job",
        ),
    )
    op.create_index(
        "ix_ai_quality_samples_owner_sampled",
        "ai_quality_samples",
        ["owner_id", "sampled_at", "id"],
    )
    op.create_index(
        "ix_ai_quality_samples_expiry",
        "ai_quality_samples",
        ["expires_at", "id"],
    )
    op.create_index(
        "ix_ai_quality_samples_status_sampled",
        "ai_quality_samples",
        ["status", "sampled_at", "id"],
    )

    op.add_column(
        "security_events",
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_security_events_details"),
        "security_events",
        "jsonb_typeof(details) = 'object'",
    )
    op.create_index(
        "ix_security_events_action_created",
        "security_events",
        ["action", "created_at", "id"],
    )

    op.drop_constraint(
        op.f("ck_change_events_entity_type"),
        "change_events",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_change_events_entity_type"),
        "change_events",
        "entity_type IN "
        "('project', 'task', 'step', 'label', 'time_settings', 'term', "
        "'calendar_series', 'calendar_exception', 'task_occurrence', "
        "'calendar_addition', 'calendar_source', 'privacy_settings')",
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM ai_body_logs)
               OR EXISTS (SELECT 1 FROM ai_quality_samples)
               OR EXISTS (
                   SELECT 1 FROM user_privacy_settings
                   WHERE quality_improvement_enabled IS NOT TRUE OR version <> 1
               )
               OR EXISTS (
                   SELECT 1 FROM security_events
                   WHERE details <> '{}'::jsonb
               )
               OR EXISTS (
                   SELECT 1 FROM change_events
                   WHERE entity_type = 'privacy_settings'
               ) THEN
                RAISE EXCEPTION
                    'cannot downgrade 0018_ai_governance while governance data exists';
            END IF;
        END
        $$;
        """
    )
    op.drop_constraint(
        op.f("ck_change_events_entity_type"),
        "change_events",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_change_events_entity_type"),
        "change_events",
        "entity_type IN "
        "('project', 'task', 'step', 'label', 'time_settings', 'term', "
        "'calendar_series', 'calendar_exception', 'task_occurrence', "
        "'calendar_addition', 'calendar_source')",
    )

    op.drop_index(
        "ix_security_events_action_created",
        table_name="security_events",
    )
    op.drop_constraint(
        op.f("ck_security_events_details"),
        "security_events",
        type_="check",
    )
    op.drop_column("security_events", "details")

    op.drop_index(
        "ix_ai_quality_samples_status_sampled",
        table_name="ai_quality_samples",
    )
    op.drop_index(
        "ix_ai_quality_samples_expiry",
        table_name="ai_quality_samples",
    )
    op.drop_index(
        "ix_ai_quality_samples_owner_sampled",
        table_name="ai_quality_samples",
    )
    op.drop_table("ai_quality_samples")

    op.drop_index("ix_ai_body_logs_expiry", table_name="ai_body_logs")
    op.drop_index("ix_ai_body_logs_owner_started", table_name="ai_body_logs")
    op.drop_table("ai_body_logs")

    op.drop_index(
        "ix_user_privacy_settings_updated",
        table_name="user_privacy_settings",
    )
    op.drop_table("user_privacy_settings")
