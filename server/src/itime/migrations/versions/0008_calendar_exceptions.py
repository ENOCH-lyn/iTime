"""calendar occurrence exceptions"""

import sqlalchemy as sa
from alembic import op

revision = "0008_calendar_exceptions"
down_revision = "0007_calendar_series"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "calendar_exceptions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("series_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("occurrence_key", sa.String(length=64), nullable=False),
        sa.Column("original_start_date", sa.Date(), nullable=False),
        sa.Column("action", sa.String(length=12), nullable=False),
        sa.Column("moved_start_date", sa.Date(), nullable=True),
        sa.Column("moved_starts_at", sa.Time(), nullable=True),
        sa.Column("moved_ends_at", sa.Time(), nullable=True),
        sa.Column("moved_end_day_offset", sa.Integer(), nullable=True),
        sa.Column("moved_ambiguous_offset_minutes", sa.Integer(), nullable=True),
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
            "action IN ('cancelled', 'moved', 'overridden')",
            name=op.f("ck_calendar_exceptions_action"),
        ),
        sa.CheckConstraint(
            "(action = 'moved') = "
            "(moved_start_date IS NOT NULL AND moved_starts_at IS NOT NULL "
            "AND moved_ends_at IS NOT NULL AND moved_end_day_offset IS NOT NULL)",
            name=op.f("ck_calendar_exceptions_moved_fields"),
        ),
        sa.CheckConstraint(
            "action = 'moved' OR "
            "(moved_start_date IS NULL AND moved_starts_at IS NULL "
            "AND moved_ends_at IS NULL AND moved_end_day_offset IS NULL "
            "AND moved_ambiguous_offset_minutes IS NULL)",
            name=op.f("ck_calendar_exceptions_non_moved_fields"),
        ),
        sa.CheckConstraint(
            "moved_end_day_offset IS NULL OR moved_end_day_offset BETWEEN 0 AND 1",
            name=op.f("ck_calendar_exceptions_moved_end_day_offset"),
        ),
        sa.CheckConstraint(
            "moved_ambiguous_offset_minutes IS NULL "
            "OR moved_ambiguous_offset_minutes BETWEEN -840 AND 840",
            name=op.f("ck_calendar_exceptions_moved_ambiguous_offset_minutes"),
        ),
        sa.CheckConstraint(
            "(action = 'overridden') = "
            "(override_name IS NOT NULL OR override_description IS NOT NULL "
            "OR override_location IS NOT NULL OR override_busy IS NOT NULL)",
            name=op.f("ck_calendar_exceptions_override_fields"),
        ),
        sa.CheckConstraint(
            "override_name IS NULL OR length(btrim(override_name)) > 0",
            name=op.f("ck_calendar_exceptions_nonempty_override_name"),
        ),
        sa.ForeignKeyConstraint(
            ["series_id", "owner_id"],
            ["calendar_series.id", "calendar_series.owner_id"],
            name="fk_calendar_exceptions_series_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_calendar_exceptions")),
        sa.UniqueConstraint(
            "id",
            "owner_id",
            name="uq_calendar_exceptions_id_owner",
        ),
        sa.UniqueConstraint(
            "series_id",
            "occurrence_key",
            name="uq_calendar_exceptions_series_occurrence",
        ),
        sa.UniqueConstraint(
            "series_id",
            "original_start_date",
            name="uq_calendar_exceptions_series_original_date",
        ),
    )
    op.create_index(
        "ix_calendar_exceptions_owner_series",
        "calendar_exceptions",
        ["owner_id", "series_id", "original_start_date"],
        unique=False,
    )
    op.create_index(
        "ix_calendar_exceptions_owner_moved_date",
        "calendar_exceptions",
        ["owner_id", "moved_start_date", "series_id"],
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
        "'calendar_series', 'calendar_exception')",
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            DELETE FROM user_operations
            WHERE scope LIKE 'calendar_exceptions.%'
               OR scope LIKE 'calendar_series.split:%'
            """
        )
    )
    op.execute(
        sa.text(
            """
            DELETE FROM change_events
            WHERE entity_type = 'calendar_exception'
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
        "'calendar_series')",
    )
    op.drop_index(
        "ix_calendar_exceptions_owner_moved_date",
        table_name="calendar_exceptions",
    )
    op.drop_index(
        "ix_calendar_exceptions_owner_series",
        table_name="calendar_exceptions",
    )
    op.drop_table("calendar_exceptions")
