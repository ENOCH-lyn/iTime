"""calendar additions and stricter recurrence invariants"""

import sqlalchemy as sa
from alembic import op

revision = "0010_calendar_additions"
down_revision = "0009_task_schedules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "calendar_exceptions",
        sa.Column("moved_time_zone", sa.String(length=64), nullable=True),
    )
    op.execute(
        """
        UPDATE calendar_exceptions AS exception
        SET moved_time_zone = series.time_zone
        FROM calendar_series AS series
        WHERE exception.series_id = series.id
          AND exception.action = 'moved'
        """
    )
    op.drop_constraint(
        op.f("ck_calendar_exceptions_non_moved_fields"),
        "calendar_exceptions",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_calendar_exceptions_non_moved_fields"),
        "calendar_exceptions",
        "action = 'moved' OR "
        "(moved_start_date IS NULL AND moved_starts_at IS NULL "
        "AND moved_ends_at IS NULL AND moved_end_day_offset IS NULL "
        "AND moved_time_zone IS NULL "
        "AND moved_ambiguous_offset_minutes IS NULL)",
    )
    op.create_check_constraint(
        op.f("ck_calendar_exceptions_moved_time_zone"),
        "calendar_exceptions",
        "(action = 'moved') = (moved_time_zone IS NOT NULL)",
    )

    op.create_check_constraint(
        op.f("ck_calendar_series_recurrence_end_shape"),
        "calendar_series",
        "(recurrence_frequency = 'none' "
        "AND recurrence_interval = 1 "
        "AND recurrence_weekdays = '[]'::jsonb "
        "AND recurrence_month IS NULL "
        "AND recurrence_day_of_month IS NULL "
        "AND recurrence_until IS NULL "
        "AND recurrence_count IS NULL) "
        "OR (recurrence_frequency != 'none' "
        "AND ((recurrence_until IS NULL) != (recurrence_count IS NULL)))",
    )
    op.create_check_constraint(
        op.f("ck_calendar_series_recurrence_until"),
        "calendar_series",
        "recurrence_until IS NULL OR recurrence_until >= start_date",
    )
    op.create_check_constraint(
        op.f("ck_calendar_series_weekdays_interval"),
        "calendar_series",
        "recurrence_frequency != 'weekdays' OR recurrence_interval = 1",
    )
    op.create_check_constraint(
        op.f("ck_calendar_series_recurrence_parameters"),
        "calendar_series",
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
    )
    op.create_check_constraint(
        op.f("ck_calendar_series_course_recurrence"),
        "calendar_series",
        "(kind = 'course' "
        "AND recurrence_frequency = 'weekly' "
        "AND recurrence_interval = 1 "
        "AND jsonb_array_length(course_week_numbers) > 0) "
        "OR (kind = 'fixed' AND course_week_numbers = '[]'::jsonb)",
    )
    op.create_table(
        "calendar_additions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("series_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("occurrence_key", sa.String(length=80), nullable=False),
        sa.Column("source_occurrence_key", sa.String(length=64), nullable=False),
        sa.Column("source_original_start_date", sa.Date(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=12),
            server_default="scheduled",
            nullable=False,
        ),
        sa.Column("local_start_date", sa.Date(), nullable=False),
        sa.Column("starts_at", sa.Time(), nullable=False),
        sa.Column("ends_at", sa.Time(), nullable=False),
        sa.Column("end_day_offset", sa.Integer(), server_default="0", nullable=False),
        sa.Column("time_zone", sa.String(length=64), nullable=False),
        sa.Column("ambiguous_offset_minutes", sa.Integer(), nullable=True),
        sa.Column("override_name", sa.String(length=200), nullable=True),
        sa.Column("override_description", sa.Text(), nullable=True),
        sa.Column("override_location", sa.String(length=300), nullable=True),
        sa.Column("override_busy", sa.Boolean(), nullable=True),
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
            "status IN ('scheduled', 'cancelled')",
            name=op.f("ck_calendar_additions_status"),
        ),
        sa.CheckConstraint(
            "length(btrim(occurrence_key)) > 0",
            name=op.f("ck_calendar_additions_nonempty_occurrence_key"),
        ),
        sa.CheckConstraint(
            "length(btrim(source_occurrence_key)) > 0",
            name=op.f("ck_calendar_additions_nonempty_source_occurrence_key"),
        ),
        sa.CheckConstraint(
            "length(btrim(time_zone)) > 0",
            name=op.f("ck_calendar_additions_nonempty_time_zone"),
        ),
        sa.CheckConstraint(
            "end_day_offset BETWEEN 0 AND 1",
            name=op.f("ck_calendar_additions_end_day_offset"),
        ),
        sa.CheckConstraint(
            "ambiguous_offset_minutes IS NULL "
            "OR ambiguous_offset_minutes BETWEEN -840 AND 840",
            name=op.f("ck_calendar_additions_ambiguous_offset_minutes"),
        ),
        sa.CheckConstraint(
            "override_name IS NULL OR length(btrim(override_name)) > 0",
            name=op.f("ck_calendar_additions_nonempty_override_name"),
        ),
        sa.ForeignKeyConstraint(
            ["series_id", "owner_id"],
            ["calendar_series.id", "calendar_series.owner_id"],
            name="fk_calendar_additions_series_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_calendar_additions")),
        sa.UniqueConstraint(
            "id",
            "owner_id",
            name="uq_calendar_additions_id_owner",
        ),
        sa.UniqueConstraint(
            "series_id",
            "occurrence_key",
            name="uq_calendar_additions_series_occurrence",
        ),
        sa.UniqueConstraint(
            "series_id",
            "source_original_start_date",
            name="uq_calendar_additions_series_source_date",
        ),
    )
    op.create_index(
        "ix_calendar_additions_owner_series",
        "calendar_additions",
        ["owner_id", "series_id", "source_original_start_date"],
        unique=False,
    )
    op.create_index(
        "ix_calendar_additions_owner_local_date",
        "calendar_additions",
        ["owner_id", "local_start_date", "series_id"],
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
        "'calendar_series', 'calendar_exception', 'task_occurrence', "
        "'calendar_addition')",
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM calendar_additions) THEN
                RAISE EXCEPTION
                    'cannot downgrade 0010_calendar_additions while additions exist';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM calendar_exceptions AS exception
                JOIN calendar_series AS series ON series.id = exception.series_id
                WHERE exception.action = 'moved'
                  AND exception.moved_time_zone IS DISTINCT FROM series.time_zone
            ) THEN
                RAISE EXCEPTION
                    'cannot downgrade 0010_calendar_additions while moved time zones differ';
            END IF;
        END
        $$;
        """
    )
    op.execute(
        sa.text(
            """
            DELETE FROM user_operations
            WHERE scope LIKE 'calendar_additions.%'
            """
        )
    )
    op.execute(
        sa.text(
            """
            DELETE FROM change_events
            WHERE entity_type = 'calendar_addition'
            """
        )
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
    op.drop_index(
        "ix_calendar_additions_owner_local_date",
        table_name="calendar_additions",
    )
    op.drop_index(
        "ix_calendar_additions_owner_series",
        table_name="calendar_additions",
    )
    op.drop_table("calendar_additions")
    op.execute("DROP INDEX IF EXISTS uq_calendar_series_predecessor")

    op.drop_constraint(
        op.f("ck_calendar_series_course_recurrence"),
        "calendar_series",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_calendar_series_recurrence_parameters"),
        "calendar_series",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_calendar_series_weekdays_interval"),
        "calendar_series",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_calendar_series_recurrence_until"),
        "calendar_series",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_calendar_series_recurrence_end_shape"),
        "calendar_series",
        type_="check",
    )

    op.drop_constraint(
        op.f("ck_calendar_exceptions_moved_time_zone"),
        "calendar_exceptions",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_calendar_exceptions_non_moved_fields"),
        "calendar_exceptions",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_calendar_exceptions_non_moved_fields"),
        "calendar_exceptions",
        "action = 'moved' OR "
        "(moved_start_date IS NULL AND moved_starts_at IS NULL "
        "AND moved_ends_at IS NULL AND moved_end_day_offset IS NULL "
        "AND moved_ambiguous_offset_minutes IS NULL)",
    )
    op.drop_column("calendar_exceptions", "moved_time_zone")
