"""Review sessions, report versions, and confirmed memory."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0026_review_memory"
down_revision = "0025_execution_difficulties"
branch_labels = None
depends_on = None

OLD_CAPABILITIES = "('calendar_screenshot', 'conversation', 'planning')"
NEW_CAPABILITIES = "('calendar_screenshot', 'conversation', 'planning', 'review')"
OLD_ENTITY_TYPES = (
    "('project', 'task', 'step', 'label', 'time_settings', 'term', "
    "'calendar_series', 'calendar_exception', 'task_occurrence', "
    "'calendar_addition', 'calendar_source', 'privacy_settings', 'plan', "
    "'focus_session', 'difficulty')"
)
NEW_ENTITY_TYPES = OLD_ENTITY_TYPES[:-1] + (
    ", 'review_session', 'review_report', 'review_annotation', 'memory', 'memory_settings')"
)


def _replace_capability_constraints(*, expanded: bool) -> None:
    capabilities = NEW_CAPABILITIES if expanded else OLD_CAPABILITIES
    for table_name in (
        "ai_config_capabilities",
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
            f"capability IN {capabilities}",
        )


def _replace_change_entity_types(*, expanded: bool) -> None:
    op.drop_constraint(
        op.f("ck_change_events_entity_type"),
        "change_events",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_change_events_entity_type"),
        "change_events",
        f"entity_type IN {NEW_ENTITY_TYPES if expanded else OLD_ENTITY_TYPES}",
    )


def upgrade() -> None:
    _replace_capability_constraints(expanded=True)
    _replace_change_entity_types(expanded=True)
    op.execute(
        """
        INSERT INTO ai_config_capabilities (capability, next_version)
        VALUES ('review', 1)
        ON CONFLICT (capability) DO NOTHING
        """
    )
    op.create_table(
        "memory_settings",
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
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
        sa.CheckConstraint("version >= 1", name=op.f("ck_memory_settings_version")),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_memory_settings_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("owner_id", name=op.f("pk_memory_settings")),
    )
    op.execute(
        """
        INSERT INTO memory_settings (owner_id)
        SELECT id FROM users
        ON CONFLICT (owner_id) DO NOTHING
        """
    )
    op.create_table(
        "review_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("activity_date", sa.Date(), nullable=False),
        sa.Column("time_zone", sa.String(length=64), nullable=False),
        sa.Column("supplemental_facts", sa.Text(), server_default="", nullable=False),
        sa.Column("input_revision", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("question_count", sa.Integer(), server_default="0", nullable=False),
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
            "char_length(btrim(time_zone)) BETWEEN 1 AND 64",
            name=op.f("ck_review_sessions_time_zone"),
        ),
        sa.CheckConstraint(
            "char_length(supplemental_facts) <= 8000",
            name=op.f("ck_review_sessions_supplemental_facts"),
        ),
        sa.CheckConstraint("input_revision >= 0", name=op.f("ck_review_sessions_input_revision")),
        sa.CheckConstraint(
            "question_count BETWEEN 0 AND 3", name=op.f("ck_review_sessions_question_count")
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_review_sessions_version")),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_review_sessions_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_review_sessions")),
        sa.UniqueConstraint("id", "owner_id", name="uq_review_sessions_id_owner"),
        sa.UniqueConstraint(
            "owner_id", "activity_date", name="uq_review_sessions_owner_activity_date"
        ),
    )
    op.create_index(
        "ix_review_sessions_owner_activity",
        "review_sessions",
        ["owner_id", "activity_date", "id"],
        unique=False,
    )
    op.create_table(
        "review_input_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("review_session_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(length=12), nullable=False),
        sa.Column("activity_date", sa.Date(), nullable=False),
        sa.Column("time_zone", sa.String(length=64), nullable=False),
        sa.Column("activity_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activity_ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session_input_revision", sa.BigInteger(), nullable=False),
        sa.Column("facts_sha256", sa.String(length=64), nullable=False),
        sa.Column("facts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("user_context", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "memory_context",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("generation_instruction", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "jsonb_typeof(facts) = 'object'", name=op.f("ck_review_input_snapshots_facts")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(memory_context) = 'array' AND jsonb_array_length(memory_context) <= 30",
            name=op.f("ck_review_input_snapshots_memory_context"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(user_context) = 'object'",
            name=op.f("ck_review_input_snapshots_user_context"),
        ),
        sa.CheckConstraint(
            "purpose IN ('question', 'report')", name=op.f("ck_review_input_snapshots_purpose")
        ),
        sa.CheckConstraint(
            "activity_started_at < activity_ended_at",
            name=op.f("ck_review_input_snapshots_activity_window"),
        ),
        sa.CheckConstraint(
            "char_length(btrim(time_zone)) BETWEEN 1 AND 64",
            name=op.f("ck_review_input_snapshots_time_zone"),
        ),
        sa.CheckConstraint(
            "char_length(generation_instruction) <= 2000",
            name=op.f("ck_review_input_snapshots_generation_instruction"),
        ),
        sa.CheckConstraint(
            "length(facts_sha256) = 64", name=op.f("ck_review_input_snapshots_facts_sha256")
        ),
        sa.CheckConstraint(
            "session_input_revision >= 0",
            name=op.f("ck_review_input_snapshots_session_input_revision"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_review_input_snapshots_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["review_session_id", "owner_id"],
            ["review_sessions.id", "review_sessions.owner_id"],
            name="fk_review_input_snapshots_session_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_review_input_snapshots")),
        sa.UniqueConstraint("id", "owner_id", name="uq_review_input_snapshots_id_owner"),
    )
    op.create_index(
        "ix_review_input_snapshots_owner_session_created",
        "review_input_snapshots",
        ["owner_id", "review_session_id", "created_at", "id"],
        unique=False,
    )
    op.create_table(
        "review_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("review_session_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=10), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source_job_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(role = 'user' AND kind = 'answer' "
            "AND char_length(btrim(content)) BETWEEN 1 AND 8000 "
            "AND source_job_id IS NULL) "
            "OR (role = 'assistant' AND kind = 'question' "
            "AND char_length(btrim(content)) BETWEEN 1 AND 2000 "
            "AND source_job_id IS NOT NULL)",
            name=op.f("ck_review_messages_shape"),
        ),
        sa.CheckConstraint("kind IN ('answer', 'question')", name=op.f("ck_review_messages_kind")),
        sa.CheckConstraint("role IN ('user', 'assistant')", name=op.f("ck_review_messages_role")),
        sa.CheckConstraint("sequence >= 1", name=op.f("ck_review_messages_sequence")),
        sa.ForeignKeyConstraint(
            ["review_session_id", "owner_id"],
            ["review_sessions.id", "review_sessions.owner_id"],
            name="fk_review_messages_session_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_job_id", "owner_id"],
            ["ai_jobs.id", "ai_jobs.owner_id"],
            name="fk_review_messages_source_job_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_review_messages")),
        sa.UniqueConstraint("id", "owner_id", name="uq_review_messages_id_owner"),
        sa.UniqueConstraint(
            "review_session_id", "sequence", name="uq_review_messages_session_sequence"
        ),
        sa.UniqueConstraint("source_job_id", name="uq_review_messages_source_job"),
    )
    op.create_index(
        "ix_review_messages_owner_session_sequence",
        "review_messages",
        ["owner_id", "review_session_id", "sequence"],
        unique=False,
    )
    op.create_table(
        "review_report_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("review_session_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source_job_id", sa.Uuid(), nullable=False),
        sa.Column("input_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("facts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("user_statements", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("insights", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("next_actions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("uncertainties", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "jsonb_typeof(facts) = 'array' AND jsonb_array_length(facts) <= 30",
            name=op.f("ck_review_report_versions_facts"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(insights) = 'array' AND jsonb_array_length(insights) <= 20",
            name=op.f("ck_review_report_versions_insights"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(next_actions) = 'array' AND jsonb_array_length(next_actions) <= 20",
            name=op.f("ck_review_report_versions_next_actions"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(uncertainties) = 'array' AND jsonb_array_length(uncertainties) <= 20",
            name=op.f("ck_review_report_versions_uncertainties"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(user_statements) = 'array' AND jsonb_array_length(user_statements) <= 20",
            name=op.f("ck_review_report_versions_user_statements"),
        ),
        sa.CheckConstraint(
            "char_length(btrim(summary)) BETWEEN 1 AND 4000",
            name=op.f("ck_review_report_versions_summary"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_review_report_versions_version")),
        sa.ForeignKeyConstraint(
            ["input_snapshot_id", "owner_id"],
            ["review_input_snapshots.id", "review_input_snapshots.owner_id"],
            name="fk_review_report_versions_snapshot_owner",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["review_session_id", "owner_id"],
            ["review_sessions.id", "review_sessions.owner_id"],
            name="fk_review_report_versions_session_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_job_id", "owner_id"],
            ["ai_jobs.id", "ai_jobs.owner_id"],
            name="fk_review_report_versions_source_job_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_review_report_versions")),
        sa.UniqueConstraint("id", "owner_id", name="uq_review_report_versions_id_owner"),
        sa.UniqueConstraint("input_snapshot_id", name="uq_review_report_versions_snapshot"),
        sa.UniqueConstraint(
            "review_session_id", "version", name="uq_review_report_versions_session_version"
        ),
        sa.UniqueConstraint("source_job_id", name="uq_review_report_versions_source_job"),
    )
    op.create_index(
        "ix_review_report_versions_owner_session_version",
        "review_report_versions",
        ["owner_id", "review_session_id", "version"],
        unique=False,
    )
    op.create_table(
        "memory_proposals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("report_version_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("rationale", sa.String(length=1000), nullable=False),
        sa.Column("status", sa.String(length=12), server_default="pending", nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
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
            "(status = 'pending' AND confirmed_at IS NULL "
            "AND rejected_at IS NULL) "
            "OR (status = 'confirmed' AND confirmed_at IS NOT NULL "
            "AND rejected_at IS NULL) "
            "OR (status = 'rejected' AND rejected_at IS NOT NULL "
            "AND confirmed_at IS NULL)",
            name=op.f("ck_memory_proposals_state"),
        ),
        sa.CheckConstraint(
            "kind IN ('preference', 'constraint', 'context', 'goal')",
            name=op.f("ck_memory_proposals_kind"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'confirmed', 'rejected')",
            name=op.f("ck_memory_proposals_status"),
        ),
        sa.CheckConstraint(
            "char_length(btrim(content)) BETWEEN 1 AND 2000",
            name=op.f("ck_memory_proposals_content"),
        ),
        sa.CheckConstraint(
            "char_length(btrim(rationale)) BETWEEN 1 AND 1000",
            name=op.f("ck_memory_proposals_rationale"),
        ),
        sa.CheckConstraint(
            "char_length(btrim(title)) BETWEEN 1 AND 120", name=op.f("ck_memory_proposals_title")
        ),
        sa.CheckConstraint("position >= 0", name=op.f("ck_memory_proposals_position")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_memory_proposals_version")),
        sa.ForeignKeyConstraint(
            ["report_version_id", "owner_id"],
            ["review_report_versions.id", "review_report_versions.owner_id"],
            name="fk_memory_proposals_report_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memory_proposals")),
        sa.UniqueConstraint("id", "owner_id", name="uq_memory_proposals_id_owner"),
        sa.UniqueConstraint(
            "report_version_id", "position", name="uq_memory_proposals_report_position"
        ),
    )
    op.create_index(
        "ix_memory_proposals_owner_status_created",
        "memory_proposals",
        ["owner_id", "status", "created_at", "id"],
        unique=False,
    )
    op.create_table(
        "review_annotations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("report_version_id", sa.Uuid(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
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
            "char_length(btrim(content)) BETWEEN 1 AND 4000",
            name=op.f("ck_review_annotations_content"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_review_annotations_version")),
        sa.ForeignKeyConstraint(
            ["report_version_id", "owner_id"],
            ["review_report_versions.id", "review_report_versions.owner_id"],
            name="fk_review_annotations_report_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_review_annotations")),
        sa.UniqueConstraint("id", "owner_id", name="uq_review_annotations_id_owner"),
    )
    op.create_index(
        "ix_review_annotations_owner_report_created",
        "review_annotations",
        ["owner_id", "report_version_id", "created_at", "id"],
        unique=False,
    )
    op.create_table(
        "memories",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("source_type", sa.String(length=20), nullable=False),
        sa.Column("source_proposal_id", sa.Uuid(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("purge_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
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
            "(purged_at IS NOT NULL AND title IS NULL AND content IS NULL "
            "AND source_proposal_id IS NULL) "
            "OR (purged_at IS NULL AND "
            "((source_type = 'manual' AND source_proposal_id IS NULL) "
            "OR (source_type = 'review_proposal' "
            "AND source_proposal_id IS NOT NULL)))",
            name=op.f("ck_memories_source"),
        ),
        sa.CheckConstraint(
            "kind IN ('preference', 'constraint', 'context', 'goal')", name=op.f("ck_memories_kind")
        ),
        sa.CheckConstraint(
            "source_type IN ('review_proposal', 'manual')", name=op.f("ck_memories_source_type")
        ),
        sa.CheckConstraint(
            "(deleted_at IS NULL AND purge_after IS NULL "
            "AND purged_at IS NULL) "
            "OR (deleted_at IS NOT NULL AND purge_after IS NOT NULL "
            "AND purge_after >= deleted_at "
            "AND (purged_at IS NULL OR purged_at >= deleted_at))",
            name=op.f("ck_memories_lifecycle"),
        ),
        sa.CheckConstraint(
            "purged_at IS NOT NULL "
            "OR (char_length(btrim(title)) BETWEEN 1 AND 120 "
            "AND char_length(btrim(content)) BETWEEN 1 AND 2000)",
            name=op.f("ck_memories_content"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_memories_version")),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name=op.f("fk_memories_owner_id_users"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_proposal_id", "owner_id"],
            ["memory_proposals.id", "memory_proposals.owner_id"],
            name="fk_memories_source_proposal_owner",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memories")),
        sa.UniqueConstraint("id", "owner_id", name="uq_memories_id_owner"),
        sa.UniqueConstraint("source_proposal_id", name="uq_memories_source_proposal"),
    )
    op.create_index(
        "ix_memories_owner_lifecycle_updated",
        "memories",
        ["owner_id", "deleted_at", "updated_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_memories_purge_due",
        "memories",
        ["purge_after", "id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NOT NULL AND purged_at IS NULL"),
    )
    op.add_column("ai_jobs", sa.Column("review_session_id", sa.Uuid(), nullable=True))
    op.add_column("ai_jobs", sa.Column("review_snapshot_id", sa.Uuid(), nullable=True))
    op.create_index(
        "uq_ai_jobs_active_review_session",
        "ai_jobs",
        ["review_session_id"],
        unique=True,
        postgresql_where=sa.text(
            "review_session_id IS NOT NULL AND status IN ('queued', 'running')"
        ),
    )
    op.create_unique_constraint("uq_ai_jobs_review_snapshot", "ai_jobs", ["review_snapshot_id"])
    op.create_foreign_key(
        "fk_ai_jobs_review_session_owner",
        "ai_jobs",
        "review_sessions",
        ["review_session_id", "owner_id"],
        ["id", "owner_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_ai_jobs_review_snapshot_owner",
        "ai_jobs",
        "review_input_snapshots",
        ["review_snapshot_id", "owner_id"],
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
        "AND planning_snapshot_id IS NULL AND review_session_id IS NULL "
        "AND review_snapshot_id IS NULL) "
        "OR (capability = 'conversation' "
        "AND input_media_id IS NULL AND conversation_id IS NOT NULL "
        "AND planning_snapshot_id IS NULL AND review_session_id IS NULL "
        "AND review_snapshot_id IS NULL) "
        "OR (capability = 'planning' "
        "AND input_media_id IS NULL AND planning_snapshot_id IS NOT NULL "
        "AND review_session_id IS NULL AND review_snapshot_id IS NULL) "
        "OR (capability = 'review' "
        "AND input_media_id IS NULL AND conversation_id IS NULL "
        "AND planning_snapshot_id IS NULL AND review_session_id IS NOT NULL "
        "AND review_snapshot_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.execute("DELETE FROM memories")
    op.execute("DELETE FROM memory_proposals")
    op.execute("DELETE FROM review_annotations")
    op.execute("DELETE FROM review_report_versions")
    op.execute("DELETE FROM review_messages")
    op.execute("DELETE FROM ai_jobs WHERE capability = 'review'")
    op.drop_constraint(
        op.f("ck_ai_jobs_input_reference"),
        "ai_jobs",
        type_="check",
    )
    op.drop_constraint("fk_ai_jobs_review_snapshot_owner", "ai_jobs", type_="foreignkey")
    op.drop_constraint("fk_ai_jobs_review_session_owner", "ai_jobs", type_="foreignkey")
    op.drop_constraint("uq_ai_jobs_review_snapshot", "ai_jobs", type_="unique")
    op.drop_index(
        "uq_ai_jobs_active_review_session",
        table_name="ai_jobs",
        postgresql_where=sa.text(
            "review_session_id IS NOT NULL AND status IN ('queued', 'running')"
        ),
    )
    op.drop_column("ai_jobs", "review_snapshot_id")
    op.drop_column("ai_jobs", "review_session_id")
    op.drop_index(
        "ix_memories_purge_due",
        table_name="memories",
        postgresql_where=sa.text("deleted_at IS NOT NULL AND purged_at IS NULL"),
    )
    op.drop_index("ix_memories_owner_lifecycle_updated", table_name="memories")
    op.drop_table("memories")
    op.drop_index("ix_review_annotations_owner_report_created", table_name="review_annotations")
    op.drop_table("review_annotations")
    op.drop_index("ix_memory_proposals_owner_status_created", table_name="memory_proposals")
    op.drop_table("memory_proposals")
    op.drop_index(
        "ix_review_report_versions_owner_session_version", table_name="review_report_versions"
    )
    op.drop_table("review_report_versions")
    op.drop_index("ix_review_messages_owner_session_sequence", table_name="review_messages")
    op.drop_table("review_messages")
    op.drop_index(
        "ix_review_input_snapshots_owner_session_created", table_name="review_input_snapshots"
    )
    op.drop_table("review_input_snapshots")
    op.drop_index("ix_review_sessions_owner_activity", table_name="review_sessions")
    op.drop_table("review_sessions")
    op.drop_table("memory_settings")
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
    op.execute(
        "UPDATE ai_config_capabilities SET current_version_id=NULL WHERE capability='review'"
    )
    op.execute("DELETE FROM ai_config_versions WHERE capability='review'")
    op.execute("DELETE FROM ai_config_capabilities WHERE capability='review'")
    _replace_capability_constraints(expanded=False)
    _replace_change_entity_types(expanded=False)
