"""task due dates, recurrence schedules, and occurrence states"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009_task_schedules"
down_revision = "0008_calendar_exceptions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_schedules",
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("predecessor_task_id", sa.Uuid(), nullable=True),
        sa.Column("due_kind", sa.String(length=10), server_default="none", nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("last_occurrence_date", sa.Date(), nullable=True),
        sa.Column("due_local_time", sa.Time(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("time_zone", sa.String(length=64), nullable=True),
        sa.Column("ambiguous_offset_minutes", sa.Integer(), nullable=True),
        sa.Column(
            "recurrence_frequency",
            sa.String(length=12),
            server_default="none",
            nullable=False,
        ),
        sa.Column(
            "recurrence_interval",
            sa.Integer(),
            server_default="1",
            nullable=False,
        ),
        sa.Column(
            "recurrence_weekdays",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("recurrence_month", sa.Integer(), nullable=True),
        sa.Column("recurrence_day_of_month", sa.Integer(), nullable=True),
        sa.Column("recurrence_until", sa.Date(), nullable=True),
        sa.Column("recurrence_count", sa.Integer(), nullable=True),
        sa.Column("splittable", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "minimum_block_minutes",
            sa.Integer(),
            server_default="15",
            nullable=False,
        ),
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
            "due_kind IN ('none', 'date', 'datetime')",
            name=op.f("ck_task_schedules_due_kind"),
        ),
        sa.CheckConstraint(
            "(due_kind = 'none' AND start_date IS NULL "
            "AND last_occurrence_date IS NULL "
            "AND due_local_time IS NULL AND due_at IS NULL AND time_zone IS NULL) "
            "OR (due_kind = 'date' AND start_date IS NOT NULL "
            "AND last_occurrence_date IS NOT NULL "
            "AND due_local_time IS NULL AND due_at IS NULL AND time_zone IS NOT NULL) "
            "OR (due_kind = 'datetime' AND start_date IS NOT NULL "
            "AND last_occurrence_date IS NOT NULL "
            "AND due_local_time IS NOT NULL AND due_at IS NOT NULL "
            "AND time_zone IS NOT NULL)",
            name=op.f("ck_task_schedules_due_fields"),
        ),
        sa.CheckConstraint(
            "due_kind != 'none' OR recurrence_frequency = 'none'",
            name=op.f("ck_task_schedules_no_due_no_recurrence"),
        ),
        sa.CheckConstraint(
            "recurrence_frequency IN "
            "('none', 'daily', 'weekdays', 'weekly', 'monthly', 'yearly')",
            name=op.f("ck_task_schedules_recurrence_frequency"),
        ),
        sa.CheckConstraint(
            "recurrence_interval BETWEEN 1 AND 366",
            name=op.f("ck_task_schedules_recurrence_interval"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(recurrence_weekdays) = 'array'",
            name=op.f("ck_task_schedules_recurrence_weekdays"),
        ),
        sa.CheckConstraint(
            "recurrence_month IS NULL OR recurrence_month BETWEEN 1 AND 12",
            name=op.f("ck_task_schedules_recurrence_month"),
        ),
        sa.CheckConstraint(
            "recurrence_day_of_month IS NULL "
            "OR recurrence_day_of_month BETWEEN 1 AND 31",
            name=op.f("ck_task_schedules_recurrence_day_of_month"),
        ),
        sa.CheckConstraint(
            "recurrence_count IS NULL OR recurrence_count BETWEEN 1 AND 366",
            name=op.f("ck_task_schedules_recurrence_count"),
        ),
        sa.CheckConstraint(
            "(recurrence_frequency = 'none' "
            "AND recurrence_interval = 1 "
            "AND recurrence_weekdays = '[]'::jsonb "
            "AND recurrence_month IS NULL "
            "AND recurrence_day_of_month IS NULL "
            "AND recurrence_until IS NULL "
            "AND recurrence_count IS NULL) "
            "OR (recurrence_frequency != 'none' "
            "AND ((recurrence_until IS NULL) != (recurrence_count IS NULL)))",
            name=op.f("ck_task_schedules_recurrence_end_shape"),
        ),
        sa.CheckConstraint(
            "recurrence_until IS NULL OR recurrence_until >= start_date",
            name=op.f("ck_task_schedules_recurrence_until"),
        ),
        sa.CheckConstraint(
            "last_occurrence_date IS NULL OR last_occurrence_date >= start_date",
            name=op.f("ck_task_schedules_last_occurrence_date"),
        ),
        sa.CheckConstraint(
            "recurrence_frequency != 'weekdays' OR recurrence_interval = 1",
            name=op.f("ck_task_schedules_weekdays_interval"),
        ),
        sa.CheckConstraint(
            "(recurrence_frequency = 'weekly' "
            "AND jsonb_array_length(recurrence_weekdays) > 0 "
            "AND recurrence_month IS NULL "
            "AND recurrence_day_of_month IS NULL) "
            "OR (recurrence_frequency = 'monthly' "
            "AND recurrence_weekdays = '[]'::jsonb "
            "AND recurrence_month IS NULL "
            "AND recurrence_day_of_month IS NOT NULL) "
            "OR (recurrence_frequency = 'yearly' "
            "AND recurrence_weekdays = '[]'::jsonb "
            "AND recurrence_month IS NOT NULL "
            "AND recurrence_day_of_month IS NOT NULL) "
            "OR (recurrence_frequency IN ('none', 'daily', 'weekdays') "
            "AND recurrence_weekdays = '[]'::jsonb "
            "AND recurrence_month IS NULL "
            "AND recurrence_day_of_month IS NULL)",
            name=op.f("ck_task_schedules_recurrence_parameters"),
        ),
        sa.CheckConstraint(
            "ambiguous_offset_minutes IS NULL "
            "OR ambiguous_offset_minutes BETWEEN -840 AND 840",
            name=op.f("ck_task_schedules_ambiguous_offset_minutes"),
        ),
        sa.CheckConstraint(
            "minimum_block_minutes BETWEEN 1 AND 1440",
            name=op.f("ck_task_schedules_minimum_block_minutes"),
        ),
        sa.ForeignKeyConstraint(
            ["task_id", "owner_id"],
            ["tasks.id", "tasks.owner_id"],
            name="fk_task_schedules_task_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["predecessor_task_id", "owner_id"],
            ["tasks.id", "tasks.owner_id"],
            name="fk_task_schedules_predecessor_owner",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("task_id", name=op.f("pk_task_schedules")),
    )
    op.create_index(
        "ix_task_schedules_owner_start",
        "task_schedules",
        ["owner_id", "start_date", "last_occurrence_date", "task_id"],
        unique=False,
    )
    op.create_index(
        "uq_task_schedules_predecessor",
        "task_schedules",
        ["predecessor_task_id"],
        unique=True,
        postgresql_where=sa.text("predecessor_task_id IS NOT NULL"),
    )

    op.create_table(
        "task_occurrence_states",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("occurrence_key", sa.String(length=80), nullable=False),
        sa.Column("original_date", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(length=10), server_default="base", nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("effective_local_time", sa.Time(), nullable=True),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_time_zone", sa.String(length=64), nullable=True),
        sa.Column("ambiguous_offset_minutes", sa.Integer(), nullable=True),
        sa.Column(
            "rescheduled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=12), server_default="open", nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "completed_step_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "step_count_snapshot",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("makeup_for_key", sa.String(length=80), nullable=True),
        sa.Column("makeup_for_date", sa.Date(), nullable=True),
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
            "kind IN ('base', 'makeup')",
            name=op.f("ck_task_occurrence_states_kind"),
        ),
        sa.CheckConstraint(
            "status IN ('open', 'completed', 'skipped')",
            name=op.f("ck_task_occurrence_states_status"),
        ),
        sa.CheckConstraint(
            "(status = 'completed') = (completed_at IS NOT NULL)",
            name=op.f("ck_task_occurrence_states_completion_consistency"),
        ),
        sa.CheckConstraint(
            "(kind = 'makeup') = "
            "(makeup_for_key IS NOT NULL AND makeup_for_date IS NOT NULL)",
            name=op.f("ck_task_occurrence_states_makeup_consistency"),
        ),
        sa.CheckConstraint(
            "(effective_local_time IS NULL) = (effective_at IS NULL)",
            name=op.f("ck_task_occurrence_states_effective_time_consistency"),
        ),
        sa.CheckConstraint(
            "(kind = 'makeup' OR rescheduled) = (effective_time_zone IS NOT NULL)",
            name=op.f(
                "ck_task_occurrence_states_effective_time_zone_consistency"
            ),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(completed_step_ids) = 'array'",
            name=op.f("ck_task_occurrence_states_completed_step_ids"),
        ),
        sa.CheckConstraint(
            "step_count_snapshot >= 0",
            name=op.f("ck_task_occurrence_states_step_count_snapshot"),
        ),
        sa.CheckConstraint(
            "jsonb_array_length(completed_step_ids) <= step_count_snapshot",
            name=op.f(
                "ck_task_occurrence_states_completed_steps_within_snapshot"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["task_id", "owner_id"],
            ["tasks.id", "tasks.owner_id"],
            name="fk_task_occurrence_states_task_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_occurrence_states")),
        sa.UniqueConstraint(
            "id",
            "owner_id",
            name="uq_task_occurrence_states_id_owner",
        ),
        sa.UniqueConstraint(
            "task_id",
            "occurrence_key",
            name="uq_task_occurrence_states_task_occurrence",
        ),
    )
    op.create_index(
        "uq_task_occurrence_states_task_base_date",
        "task_occurrence_states",
        ["task_id", "original_date"],
        unique=True,
        postgresql_where=sa.text("kind = 'base'"),
    )
    op.create_index(
        "ix_task_occurrence_states_owner_effective",
        "task_occurrence_states",
        ["owner_id", "effective_date", "task_id"],
        unique=False,
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
        "'calendar_series', 'calendar_exception', 'task_occurrence')",
    )
    op.drop_constraint(
        op.f("ck_change_events_action"),
        "change_events",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_change_events_action"),
        "change_events",
        "action IN "
        "('created', 'updated', 'completed', 'reopened', 'deleted', "
        "'skipped', 'rescheduled')",
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM task_schedules
                WHERE predecessor_task_id IS NOT NULL
            ) THEN
                RAISE EXCEPTION
                    'cannot downgrade 0009_task_schedules while split successor tasks exist';
            END IF;
        END
        $$;
        """
    )
    op.execute(
        sa.text(
            """
            DELETE FROM user_operations
            WHERE scope LIKE 'tasks.schedule:%'
               OR scope LIKE 'task_occurrences.%'
            """
        )
    )
    op.execute(
        sa.text(
            """
            DELETE FROM change_events
            WHERE entity_type = 'task_occurrence'
            """
        )
    )
    op.drop_constraint(
        op.f("ck_change_events_action"),
        "change_events",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_change_events_action"),
        "change_events",
        "action IN ('created', 'updated', 'completed', 'reopened', 'deleted')",
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
        "'calendar_series', 'calendar_exception')",
    )
    op.drop_index(
        "ix_task_occurrence_states_owner_effective",
        table_name="task_occurrence_states",
    )
    op.drop_index(
        "uq_task_occurrence_states_task_base_date",
        table_name="task_occurrence_states",
    )
    op.drop_table("task_occurrence_states")
    op.execute("DROP INDEX IF EXISTS uq_task_schedules_predecessor")
    op.drop_index(
        "ix_task_schedules_owner_start",
        table_name="task_schedules",
    )
    op.drop_table("task_schedules")
