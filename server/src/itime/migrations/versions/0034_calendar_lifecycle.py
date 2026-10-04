"""Add recoverable lifecycle state to calendar aggregates."""

import sqlalchemy as sa
from alembic import op

revision = "0034_calendar_lifecycle"
down_revision = "0033_ai_fallback_routes"
branch_labels = None
depends_on = None


def _add_lifecycle(table_name: str) -> None:
    op.add_column(
        table_name,
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        table_name,
        sa.Column("purge_after", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        table_name,
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        op.f(f"ck_{table_name}_lifecycle_consistency"),
        table_name,
        "(deleted_at IS NULL AND purge_after IS NULL AND purged_at IS NULL) "
        "OR (deleted_at IS NOT NULL AND purge_after IS NOT NULL "
        "AND purge_after >= deleted_at "
        "AND (purged_at IS NULL OR purged_at >= deleted_at))",
    )
    op.create_index(
        f"ix_{table_name}_owner_lifecycle_updated",
        table_name,
        ["owner_id", "deleted_at", "updated_at", "id"],
    )
    op.create_index(
        f"ix_{table_name}_purge_due",
        table_name,
        ["purge_after", "id"],
        postgresql_where=sa.text(
            "deleted_at IS NOT NULL AND purged_at IS NULL"
        ),
    )


def upgrade() -> None:
    _add_lifecycle("calendar_series")
    _add_lifecycle("calendar_sources")
    op.execute(
        """
        CREATE TRIGGER trg_planning_revision_calendar_sources_lifecycle
        AFTER UPDATE OF deleted_at, purge_after, purged_at ON calendar_sources
        FOR EACH ROW
        WHEN (
            OLD.deleted_at IS DISTINCT FROM NEW.deleted_at
            OR OLD.purge_after IS DISTINCT FROM NEW.purge_after
            OR OLD.purged_at IS DISTINCT FROM NEW.purged_at
        )
        EXECUTE FUNCTION bump_planning_input_revision()
        """
    )


def _drop_lifecycle(table_name: str) -> None:
    op.drop_index(
        f"ix_{table_name}_purge_due",
        table_name=table_name,
    )
    op.drop_index(
        f"ix_{table_name}_owner_lifecycle_updated",
        table_name=table_name,
    )
    op.drop_constraint(
        op.f(f"ck_{table_name}_lifecycle_consistency"),
        table_name,
        type_="check",
    )
    op.drop_column(table_name, "purged_at")
    op.drop_column(table_name, "purge_after")
    op.drop_column(table_name, "deleted_at")


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM calendar_series
                WHERE deleted_at IS NOT NULL
                   OR purge_after IS NOT NULL
                   OR purged_at IS NOT NULL
            ) OR EXISTS (
                SELECT 1
                FROM calendar_sources
                WHERE deleted_at IS NOT NULL
                   OR purge_after IS NOT NULL
                   OR purged_at IS NOT NULL
            ) THEN
                RAISE EXCEPTION
                    'calendar lifecycle data must be cleared before downgrade';
            END IF;
        END
        $$;
        """
    )
    op.execute(
        """
        DROP TRIGGER trg_planning_revision_calendar_sources_lifecycle
        ON calendar_sources
        """
    )
    _drop_lifecycle("calendar_sources")
    _drop_lifecycle("calendar_series")
