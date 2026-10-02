"""calendar series and structured recurrence rules"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_calendar_series"
down_revision = "0006_time_terms"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "calendar_series",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("term_id", sa.Uuid(), nullable=True),
        sa.Column("predecessor_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("location", sa.String(length=300), server_default="", nullable=False),
        sa.Column("busy", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("source_type", sa.String(length=20), server_default="manual", nullable=False),
        sa.Column("time_zone", sa.String(length=64), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("starts_at", sa.Time(), nullable=False),
        sa.Column("ends_at", sa.Time(), nullable=False),
        sa.Column("end_day_offset", sa.Integer(), server_default="0", nullable=False),
        sa.Column("start_period_number", sa.Integer(), nullable=True),
        sa.Column("end_period_number", sa.Integer(), nullable=True),
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
        sa.Column("course_first_week_monday", sa.Date(), nullable=True),
        sa.Column(
            "course_week_numbers",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("ambiguous_offset_minutes", sa.Integer(), nullable=True),
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
            "kind IN ('course', 'fixed')",
            name=op.f("ck_calendar_series_kind"),
        ),
        sa.CheckConstraint(
            "length(btrim(name)) > 0",
            name=op.f("ck_calendar_series_nonempty_name"),
        ),
        sa.CheckConstraint(
            "length(btrim(time_zone)) > 0",
            name=op.f("ck_calendar_series_nonempty_time_zone"),
        ),
        sa.CheckConstraint(
            "source_type = 'manual'",
            name=op.f("ck_calendar_series_source_type"),
        ),
        sa.CheckConstraint(
            "end_day_offset BETWEEN 0 AND 1",
            name=op.f("ck_calendar_series_end_day_offset"),
        ),
        sa.CheckConstraint(
            "(start_period_number IS NULL) = (end_period_number IS NULL)",
            name=op.f("ck_calendar_series_period_reference_consistency"),
        ),
        sa.CheckConstraint(
            "start_period_number IS NULL OR "
            "(start_period_number BETWEEN 1 AND 99 "
            "AND end_period_number BETWEEN start_period_number AND 99)",
            name=op.f("ck_calendar_series_period_reference_range"),
        ),
        sa.CheckConstraint(
            "recurrence_frequency IN "
            "('none', 'daily', 'weekdays', 'weekly', 'monthly', 'yearly')",
            name=op.f("ck_calendar_series_recurrence_frequency"),
        ),
        sa.CheckConstraint(
            "recurrence_interval BETWEEN 1 AND 366",
            name=op.f("ck_calendar_series_recurrence_interval"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(recurrence_weekdays) = 'array'",
            name=op.f("ck_calendar_series_recurrence_weekdays"),
        ),
        sa.CheckConstraint(
            "recurrence_month IS NULL OR recurrence_month BETWEEN 1 AND 12",
            name=op.f("ck_calendar_series_recurrence_month"),
        ),
        sa.CheckConstraint(
            "recurrence_day_of_month IS NULL "
            "OR recurrence_day_of_month BETWEEN 1 AND 31",
            name=op.f("ck_calendar_series_recurrence_day_of_month"),
        ),
        sa.CheckConstraint(
            "recurrence_count IS NULL OR recurrence_count BETWEEN 1 AND 366",
            name=op.f("ck_calendar_series_recurrence_count"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(course_week_numbers) = 'array'",
            name=op.f("ck_calendar_series_course_week_numbers"),
        ),
        sa.CheckConstraint(
            "ambiguous_offset_minutes IS NULL "
            "OR ambiguous_offset_minutes BETWEEN -840 AND 840",
            name=op.f("ck_calendar_series_ambiguous_offset_minutes"),
        ),
        sa.CheckConstraint(
            "(kind = 'course') = "
            "(term_id IS NOT NULL AND course_first_week_monday IS NOT NULL)",
            name=op.f("ck_calendar_series_course_term"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_calendar_series_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["term_id", "owner_id"],
            ["academic_terms.id", "academic_terms.owner_id"],
            name="fk_calendar_series_term_owner",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["predecessor_id", "owner_id"],
            ["calendar_series.id", "calendar_series.owner_id"],
            name="fk_calendar_series_predecessor_owner",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_calendar_series")),
        sa.UniqueConstraint("id", "owner_id", name="uq_calendar_series_id_owner"),
    )
    op.create_index(
        "ix_calendar_series_owner_updated",
        "calendar_series",
        ["owner_id", "updated_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_calendar_series_owner_dates",
        "calendar_series",
        ["owner_id", "start_date", "recurrence_until", "id"],
        unique=False,
    )
    op.create_index(
        "ix_calendar_series_owner_term_updated",
        "calendar_series",
        ["owner_id", "term_id", "updated_at", "id"],
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
        "'calendar_series')",
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            DELETE FROM user_operations
            WHERE scope = 'calendar_series.create'
               OR scope LIKE 'calendar_series.update:%'
            """
        )
    )
    op.execute(
        sa.text(
            """
            DELETE FROM change_events
            WHERE entity_type = 'calendar_series'
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
        "('project', 'task', 'step', 'label', 'time_settings', 'term')",
    )
    op.drop_index(
        "ix_calendar_series_owner_term_updated",
        table_name="calendar_series",
    )
    op.drop_index(
        "ix_calendar_series_owner_dates",
        table_name="calendar_series",
    )
    op.drop_index(
        "ix_calendar_series_owner_updated",
        table_name="calendar_series",
    )
    op.drop_table("calendar_series")
