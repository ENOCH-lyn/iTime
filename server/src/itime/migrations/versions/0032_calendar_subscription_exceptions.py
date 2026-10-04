"""Persist local exceptions for subscription occurrences."""

import sqlalchemy as sa
from alembic import op

revision = "0032_subscription_exceptions"
down_revision = "0031_plan_source_conflicts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "calendar_subscription_exceptions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("master_event_id", sa.Uuid(), nullable=False),
        sa.Column("source_occurrence_key", sa.String(length=512), nullable=False),
        sa.Column("original_start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("original_end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("original_all_day", sa.Boolean(), nullable=False),
        sa.Column("action", sa.String(length=12), nullable=False),
        sa.Column("moved_all_day", sa.Boolean(), nullable=True),
        sa.Column("moved_start_date", sa.Date(), nullable=True),
        sa.Column("moved_end_date", sa.Date(), nullable=True),
        sa.Column("moved_starts_at", sa.Time(), nullable=True),
        sa.Column("moved_ends_at", sa.Time(), nullable=True),
        sa.Column("moved_time_zone", sa.String(length=64), nullable=True),
        sa.Column("moved_ambiguous_offset_minutes", sa.Integer(), nullable=True),
        sa.Column("moved_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("moved_end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("override_name", sa.String(length=200), nullable=True),
        sa.Column("override_description", sa.Text(), nullable=True),
        sa.Column("override_location", sa.String(length=300), nullable=True),
        sa.Column("override_busy", sa.Boolean(), nullable=True),
        sa.Column(
            "version",
            sa.Integer(),
            server_default="1",
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
            "length(btrim(source_occurrence_key)) > 0",
            name=op.f(
                "ck_calendar_subscription_exceptions_"
                "nonempty_source_occurrence_key"
            ),
        ),
        sa.CheckConstraint(
            "original_end_at > original_start_at",
            name=op.f(
                "ck_calendar_subscription_exceptions_original_interval"
            ),
        ),
        sa.CheckConstraint(
            "action IN ('cancelled', 'moved', 'overridden')",
            name=op.f("ck_calendar_subscription_exceptions_action"),
        ),
        sa.CheckConstraint(
            "(action = 'moved') = "
            "(moved_all_day IS NOT NULL "
            "AND moved_start_date IS NOT NULL "
            "AND moved_end_date IS NOT NULL "
            "AND moved_time_zone IS NOT NULL "
            "AND moved_start_at IS NOT NULL "
            "AND moved_end_at IS NOT NULL)",
            name=op.f("ck_calendar_subscription_exceptions_moved_fields"),
        ),
        sa.CheckConstraint(
            "action = 'moved' OR "
            "(moved_all_day IS NULL "
            "AND moved_start_date IS NULL "
            "AND moved_end_date IS NULL "
            "AND moved_starts_at IS NULL "
            "AND moved_ends_at IS NULL "
            "AND moved_time_zone IS NULL "
            "AND moved_ambiguous_offset_minutes IS NULL "
            "AND moved_start_at IS NULL "
            "AND moved_end_at IS NULL)",
            name=op.f(
                "ck_calendar_subscription_exceptions_non_moved_fields"
            ),
        ),
        sa.CheckConstraint(
            "action != 'moved' OR "
            "(moved_end_at > moved_start_at "
            "AND length(btrim(moved_time_zone)) > 0 "
            "AND ("
            "(moved_all_day "
            "AND moved_starts_at IS NULL "
            "AND moved_ends_at IS NULL "
            "AND moved_ambiguous_offset_minutes IS NULL "
            "AND moved_end_date > moved_start_date "
            "AND moved_end_date <= moved_start_date + 366)"
            " OR "
            "(NOT moved_all_day "
            "AND moved_starts_at IS NOT NULL "
            "AND moved_ends_at IS NOT NULL "
            "AND moved_end_date >= moved_start_date "
            "AND moved_end_date <= moved_start_date + 1)"
            "))",
            name=op.f("ck_calendar_subscription_exceptions_moved_shape"),
        ),
        sa.CheckConstraint(
            "moved_ambiguous_offset_minutes IS NULL "
            "OR moved_ambiguous_offset_minutes BETWEEN -840 AND 840",
            name=op.f(
                "ck_calendar_subscription_exceptions_"
                "moved_ambiguous_offset_minutes"
            ),
        ),
        sa.CheckConstraint(
            "(action = 'overridden') = "
            "(override_name IS NOT NULL "
            "OR override_description IS NOT NULL "
            "OR override_location IS NOT NULL "
            "OR override_busy IS NOT NULL)",
            name=op.f("ck_calendar_subscription_exceptions_override_fields"),
        ),
        sa.CheckConstraint(
            "override_name IS NULL OR length(btrim(override_name)) > 0",
            name=op.f(
                "ck_calendar_subscription_exceptions_nonempty_override_name"
            ),
        ),
        sa.CheckConstraint(
            "version >= 1",
            name=op.f("ck_calendar_subscription_exceptions_version"),
        ),
        sa.ForeignKeyConstraint(
            ["master_event_id", "owner_id"],
            [
                "calendar_subscription_events.id",
                "calendar_subscription_events.owner_id",
            ],
            name="fk_calendar_subscription_exceptions_event_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_calendar_subscription_exceptions"),
        ),
        sa.UniqueConstraint(
            "id",
            "owner_id",
            name="uq_calendar_subscription_exceptions_id_owner",
        ),
        sa.UniqueConstraint(
            "master_event_id",
            "source_occurrence_key",
            name="uq_subscription_exceptions_event_occurrence",
        ),
    )
    op.create_index(
        "ix_subscription_exceptions_owner_original",
        "calendar_subscription_exceptions",
        ["owner_id", "original_start_at", "master_event_id"],
    )
    op.create_index(
        "ix_subscription_exceptions_owner_moved",
        "calendar_subscription_exceptions",
        ["owner_id", "moved_start_at", "master_event_id"],
        postgresql_where=sa.text("action = 'moved'"),
    )
    op.execute(
        """
        CREATE TRIGGER trg_planning_revision_calendar_subscription_exceptions
        AFTER INSERT OR UPDATE OR DELETE ON calendar_subscription_exceptions
        FOR EACH ROW EXECUTE FUNCTION bump_planning_input_revision()
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM calendar_subscription_exceptions
            ) THEN
                RAISE EXCEPTION
                    'cannot downgrade 0032_calendar_subscription_exceptions '
                    'while local subscription exceptions exist';
            END IF;
        END
        $$;
        """
    )
    op.execute(
        """
        DELETE FROM user_operations
        WHERE scope LIKE 'calendar_subscription_exceptions.%'
        """
    )
    op.execute(
        """
        DROP TRIGGER trg_planning_revision_calendar_subscription_exceptions
        ON calendar_subscription_exceptions
        """
    )
    op.drop_index(
        "ix_subscription_exceptions_owner_moved",
        table_name="calendar_subscription_exceptions",
    )
    op.drop_index(
        "ix_subscription_exceptions_owner_original",
        table_name="calendar_subscription_exceptions",
    )
    op.drop_table("calendar_subscription_exceptions")
