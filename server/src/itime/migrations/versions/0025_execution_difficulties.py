"""Execution sessions, immutable events, revisions, and difficulties."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0025_execution_difficulties"
down_revision = "0024_planning_lifecycle"
branch_labels = None
depends_on = None

OLD_ENTITY_TYPES = (
    "('project', 'task', 'step', 'label', 'time_settings', 'term', "
    "'calendar_series', 'calendar_exception', 'task_occurrence', "
    "'calendar_addition', 'calendar_source', 'privacy_settings', 'plan')"
)
NEW_ENTITY_TYPES = OLD_ENTITY_TYPES[:-1] + ", 'focus_session', 'difficulty')"

OLD_ACTIONS = (
    "('created', 'updated', 'completed', 'reopened', 'deleted', "
    "'skipped', 'rescheduled', 'archived', 'unarchived', 'restored', "
    "'purged')"
)
NEW_ACTIONS = OLD_ACTIONS[:-1] + (
    ", 'started', 'paused', 'resumed', 'finished', 'abandoned', 'revised')"
)


def _replace_change_event_constraints(*, expanded: bool) -> None:
    op.drop_constraint(
        op.f("ck_change_events_entity_type"),
        "change_events",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_change_events_action"),
        "change_events",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_change_events_entity_type"),
        "change_events",
        f"entity_type IN {NEW_ENTITY_TYPES if expanded else OLD_ENTITY_TYPES}",
    )
    op.create_check_constraint(
        op.f("ck_change_events_action"),
        "change_events",
        f"action IN {NEW_ACTIONS if expanded else OLD_ACTIONS}",
    )


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_scheduled_blocks_id_owner",
        "scheduled_blocks",
        ["id", "owner_id"],
    )
    _replace_change_event_constraints(expanded=True)

    op.create_table(
        "focus_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(length=8), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("occurrence_key", sa.String(length=120), nullable=True),
        sa.Column("scheduled_block_id", sa.Uuid(), nullable=True),
        sa.Column("title_snapshot", sa.String(length=200), nullable=False),
        sa.Column("task_title_snapshot", sa.String(length=200), nullable=True),
        sa.Column(
            "step_snapshots",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("mode", sa.String(length=10), nullable=False),
        sa.Column("target_seconds", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "accumulated_seconds",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("effective_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_duration_seconds", sa.BigInteger(), nullable=True),
        sa.Column("time_zone", sa.String(length=64), nullable=False),
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
            "source IN ('timer', 'manual')",
            name=op.f("ck_focus_sessions_source"),
        ),
        sa.CheckConstraint(
            "char_length(btrim(title_snapshot)) BETWEEN 1 AND 200",
            name=op.f("ck_focus_sessions_title"),
        ),
        sa.CheckConstraint(
            "task_title_snapshot IS NULL "
            "OR char_length(btrim(task_title_snapshot)) BETWEEN 1 AND 200",
            name=op.f("ck_focus_sessions_task_title"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(step_snapshots) = 'array' "
            "AND jsonb_array_length(step_snapshots) <= 100",
            name=op.f("ck_focus_sessions_step_snapshots"),
        ),
        sa.CheckConstraint(
            "occurrence_key IS NULL "
            "OR char_length(btrim(occurrence_key)) BETWEEN 1 AND 120",
            name=op.f("ck_focus_sessions_occurrence_key"),
        ),
        sa.CheckConstraint(
            "(task_id IS NULL AND occurrence_key IS NULL "
            "AND scheduled_block_id IS NULL AND task_title_snapshot IS NULL) "
            "OR (task_id IS NOT NULL AND task_title_snapshot IS NOT NULL)",
            name=op.f("ck_focus_sessions_task_reference"),
        ),
        sa.CheckConstraint(
            "(mode = 'stopwatch' AND target_seconds IS NULL) "
            "OR (mode = 'countdown' AND target_seconds BETWEEN 60 AND 86400)",
            name=op.f("ck_focus_sessions_mode_target"),
        ),
        sa.CheckConstraint(
            "status IN ('running', 'paused', 'finished', 'abandoned')",
            name=op.f("ck_focus_sessions_status"),
        ),
        sa.CheckConstraint(
            "(status = 'running' AND ended_at IS NULL "
            "AND active_started_at IS NOT NULL "
            "AND effective_started_at IS NULL "
            "AND effective_ended_at IS NULL "
            "AND effective_duration_seconds IS NULL) "
            "OR (status = 'paused' AND ended_at IS NULL "
            "AND active_started_at IS NULL "
            "AND effective_started_at IS NULL "
            "AND effective_ended_at IS NULL "
            "AND effective_duration_seconds IS NULL) "
            "OR (status IN ('finished', 'abandoned') "
            "AND ended_at IS NOT NULL AND active_started_at IS NULL "
            "AND effective_started_at IS NOT NULL "
            "AND effective_ended_at IS NOT NULL "
            "AND effective_duration_seconds IS NOT NULL)",
            name=op.f("ck_focus_sessions_state"),
        ),
        sa.CheckConstraint(
            "source = 'timer' OR status = 'finished'",
            name=op.f("ck_focus_sessions_manual_terminal"),
        ),
        sa.CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at",
            name=op.f("ck_focus_sessions_raw_interval"),
        ),
        sa.CheckConstraint(
            "active_started_at IS NULL OR active_started_at >= started_at",
            name=op.f("ck_focus_sessions_active_interval"),
        ),
        sa.CheckConstraint(
            "accumulated_seconds >= 0",
            name=op.f("ck_focus_sessions_accumulated_seconds"),
        ),
        sa.CheckConstraint(
            "effective_started_at IS NULL "
            "OR effective_ended_at >= effective_started_at",
            name=op.f("ck_focus_sessions_effective_interval"),
        ),
        sa.CheckConstraint(
            "effective_duration_seconds IS NULL "
            "OR effective_duration_seconds >= 0",
            name=op.f("ck_focus_sessions_effective_duration"),
        ),
        sa.CheckConstraint(
            "length(btrim(time_zone)) > 0",
            name=op.f("ck_focus_sessions_time_zone"),
        ),
        sa.CheckConstraint(
            "version >= 1",
            name=op.f("ck_focus_sessions_version"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_focus_sessions_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id", "owner_id"],
            ["tasks.id", "tasks.owner_id"],
            name="fk_focus_sessions_task_owner",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["scheduled_block_id", "owner_id"],
            ["scheduled_blocks.id", "scheduled_blocks.owner_id"],
            name="fk_focus_sessions_scheduled_block_owner",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_focus_sessions")),
        sa.UniqueConstraint(
            "id",
            "owner_id",
            name="uq_focus_sessions_id_owner",
        ),
    )
    op.create_index(
        "ix_focus_sessions_owner_started",
        "focus_sessions",
        ["owner_id", "started_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_focus_sessions_owner_updated",
        "focus_sessions",
        ["owner_id", "updated_at", "id"],
        unique=False,
    )
    op.create_index(
        "uq_focus_sessions_owner_open",
        "focus_sessions",
        ["owner_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('running', 'paused')"),
    )

    op.create_table(
        "focus_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("focus_session_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("client_recorded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(length=8), nullable=False),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "sequence >= 1",
            name=op.f("ck_focus_events_sequence"),
        ),
        sa.CheckConstraint(
            "kind IN "
            "('start', 'pause', 'resume', 'finish', 'abandon', "
            "'progress', 'task_completed')",
            name=op.f("ck_focus_events_kind"),
        ),
        sa.CheckConstraint(
            "source IN ('online', 'offline')",
            name=op.f("ck_focus_events_source"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(details) = 'object'",
            name=op.f("ck_focus_events_details"),
        ),
        sa.ForeignKeyConstraint(
            ["focus_session_id", "owner_id"],
            ["focus_sessions.id", "focus_sessions.owner_id"],
            name="fk_focus_events_focus_session_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_focus_events")),
        sa.UniqueConstraint(
            "focus_session_id",
            "sequence",
            name="uq_focus_events_session_sequence",
        ),
    )
    op.create_index(
        "ix_focus_events_owner_session_sequence",
        "focus_events",
        ["owner_id", "focus_session_id", "sequence"],
        unique=False,
    )

    op.create_table(
        "execution_revisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("focus_session_id", sa.Uuid(), nullable=False),
        sa.Column("session_version", sa.Integer(), nullable=False),
        sa.Column(
            "before_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "after_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "session_version >= 2",
            name=op.f("ck_execution_revisions_session_version"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(before_data) = 'object'",
            name=op.f("ck_execution_revisions_before_data"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(after_data) = 'object'",
            name=op.f("ck_execution_revisions_after_data"),
        ),
        sa.CheckConstraint(
            "char_length(btrim(reason)) BETWEEN 1 AND 500",
            name=op.f("ck_execution_revisions_reason"),
        ),
        sa.ForeignKeyConstraint(
            ["focus_session_id", "owner_id"],
            ["focus_sessions.id", "focus_sessions.owner_id"],
            name="fk_execution_revisions_focus_session_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_execution_revisions"),
        ),
        sa.UniqueConstraint(
            "focus_session_id",
            "session_version",
            name="uq_execution_revisions_session_version",
        ),
    )
    op.create_index(
        "ix_execution_revisions_owner_session_version",
        "execution_revisions",
        ["owner_id", "focus_session_id", "session_version"],
        unique=False,
    )

    op.create_table(
        "difficulty_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("focus_session_id", sa.Uuid(), nullable=True),
        sa.Column("occurrence_key", sa.String(length=120), nullable=True),
        sa.Column("task_title_snapshot", sa.String(length=200), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=40), nullable=True),
        sa.Column("emotion", sa.String(length=40), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("time_zone", sa.String(length=64), nullable=False),
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
            "occurrence_key IS NULL "
            "OR char_length(btrim(occurrence_key)) BETWEEN 1 AND 120",
            name=op.f("ck_difficulty_records_occurrence_key"),
        ),
        sa.CheckConstraint(
            "task_title_snapshot IS NULL "
            "OR char_length(btrim(task_title_snapshot)) BETWEEN 1 AND 200",
            name=op.f("ck_difficulty_records_task_title"),
        ),
        sa.CheckConstraint(
            "category IS NULL "
            "OR char_length(btrim(category)) BETWEEN 1 AND 40",
            name=op.f("ck_difficulty_records_category"),
        ),
        sa.CheckConstraint(
            "emotion IS NULL "
            "OR char_length(btrim(emotion)) BETWEEN 1 AND 40",
            name=op.f("ck_difficulty_records_emotion"),
        ),
        sa.CheckConstraint(
            "(purged_at IS NULL "
            "AND content IS NOT NULL "
            "AND char_length(btrim(content)) BETWEEN 1 AND 2000) "
            "OR (purged_at IS NOT NULL "
            "AND content IS NULL "
            "AND category IS NULL "
            "AND emotion IS NULL "
            "AND task_id IS NULL "
            "AND focus_session_id IS NULL "
            "AND occurrence_key IS NULL "
            "AND task_title_snapshot IS NULL)",
            name=op.f("ck_difficulty_records_content_lifecycle"),
        ),
        sa.CheckConstraint(
            "(deleted_at IS NULL AND purge_after IS NULL AND purged_at IS NULL) "
            "OR (deleted_at IS NOT NULL AND purge_after IS NOT NULL "
            "AND purge_after >= deleted_at "
            "AND (purged_at IS NULL OR purged_at >= deleted_at))",
            name=op.f("ck_difficulty_records_lifecycle"),
        ),
        sa.CheckConstraint(
            "length(btrim(time_zone)) > 0",
            name=op.f("ck_difficulty_records_time_zone"),
        ),
        sa.CheckConstraint(
            "version >= 1",
            name=op.f("ck_difficulty_records_version"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_difficulty_records_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id", "owner_id"],
            ["tasks.id", "tasks.owner_id"],
            name="fk_difficulty_records_task_owner",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["focus_session_id", "owner_id"],
            ["focus_sessions.id", "focus_sessions.owner_id"],
            name="fk_difficulty_records_focus_session_owner",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_difficulty_records"),
        ),
        sa.UniqueConstraint(
            "id",
            "owner_id",
            name="uq_difficulty_records_id_owner",
        ),
    )
    op.create_index(
        "ix_difficulty_records_owner_occurred",
        "difficulty_records",
        ["owner_id", "occurred_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_difficulty_records_owner_lifecycle",
        "difficulty_records",
        ["owner_id", "deleted_at", "updated_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_difficulty_records_purge_due",
        "difficulty_records",
        ["purge_after", "id"],
        unique=False,
        postgresql_where=sa.text(
            "deleted_at IS NOT NULL AND purged_at IS NULL"
        ),
    )

    op.execute(
        """
        CREATE FUNCTION prevent_execution_history_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE'
               AND NOT EXISTS (
                   SELECT 1
                   FROM focus_sessions
                   WHERE id = OLD.focus_session_id
                     AND owner_id = OLD.owner_id
               ) THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION 'execution history is immutable'
                USING ERRCODE = 'P0001';
        END;
        $$
        """
    )
    for table_name in ("focus_events", "execution_revisions"):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_immutable
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW
            EXECUTE FUNCTION prevent_execution_history_mutation()
            """
        )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM user_operations
        WHERE scope LIKE 'focus.%'
           OR scope LIKE 'difficulty.%'
        """
    )
    op.execute(
        """
        DELETE FROM change_events
        WHERE entity_type IN ('focus_session', 'difficulty')
        """
    )
    for table_name in ("focus_events", "execution_revisions"):
        op.execute(f"DROP TRIGGER trg_{table_name}_immutable ON {table_name}")
    op.execute("DROP FUNCTION prevent_execution_history_mutation()")

    op.drop_index(
        "ix_difficulty_records_purge_due",
        table_name="difficulty_records",
    )
    op.drop_index(
        "ix_difficulty_records_owner_lifecycle",
        table_name="difficulty_records",
    )
    op.drop_index(
        "ix_difficulty_records_owner_occurred",
        table_name="difficulty_records",
    )
    op.drop_table("difficulty_records")
    op.drop_index(
        "ix_execution_revisions_owner_session_version",
        table_name="execution_revisions",
    )
    op.drop_table("execution_revisions")
    op.drop_index(
        "ix_focus_events_owner_session_sequence",
        table_name="focus_events",
    )
    op.drop_table("focus_events")
    op.drop_index("uq_focus_sessions_owner_open", table_name="focus_sessions")
    op.drop_index(
        "ix_focus_sessions_owner_updated",
        table_name="focus_sessions",
    )
    op.drop_index(
        "ix_focus_sessions_owner_started",
        table_name="focus_sessions",
    )
    op.drop_table("focus_sessions")
    _replace_change_event_constraints(expanded=False)
    op.drop_constraint(
        "uq_scheduled_blocks_id_owner",
        "scheduled_blocks",
        type_="unique",
    )
