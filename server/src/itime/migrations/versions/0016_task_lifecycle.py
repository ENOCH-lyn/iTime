"""task archive, trash, and purge tombstones"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0016_task_lifecycle"
down_revision = "0015_calendar_subscriptions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("purge_after", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_tasks_lifecycle_consistency"),
        "tasks",
        "(deleted_at IS NULL AND purge_after IS NULL AND purged_at IS NULL) "
        "OR (deleted_at IS NOT NULL AND purge_after IS NOT NULL "
        "AND purge_after >= deleted_at "
        "AND (purged_at IS NULL OR purged_at >= deleted_at))",
    )
    op.create_index(
        "ix_tasks_owner_lifecycle_updated",
        "tasks",
        ["owner_id", "deleted_at", "archived_at", "updated_at", "id"],
    )
    op.create_index(
        "ix_tasks_purge_due",
        "tasks",
        ["purge_after", "id"],
        postgresql_where=sa.text("deleted_at IS NOT NULL AND purged_at IS NULL"),
    )

    op.alter_column(
        "user_operations",
        "response",
        existing_type=postgresql.JSONB(),
        nullable=True,
    )
    op.add_column(
        "user_operations",
        sa.Column("response_purged_at", sa.DateTime(timezone=True), nullable=True),
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
        "'skipped', 'rescheduled', 'archived', 'unarchived', 'restored', "
        "'purged')",
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM tasks
                WHERE archived_at IS NOT NULL
                   OR deleted_at IS NOT NULL
                   OR purge_after IS NOT NULL
                   OR purged_at IS NOT NULL
            ) OR EXISTS (
                SELECT 1 FROM user_operations
                WHERE response IS NULL OR response_purged_at IS NOT NULL
            ) OR EXISTS (
                SELECT 1 FROM change_events
                WHERE action IN ('archived', 'unarchived', 'restored', 'purged')
            ) THEN
                RAISE EXCEPTION
                    'cannot downgrade 0016_task_lifecycle while lifecycle data exists';
            END IF;
        END
        $$;
        """
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
    op.drop_column("user_operations", "response_purged_at")
    op.alter_column(
        "user_operations",
        "response",
        existing_type=postgresql.JSONB(),
        nullable=False,
    )
    op.drop_index("ix_tasks_purge_due", table_name="tasks")
    op.drop_index("ix_tasks_owner_lifecycle_updated", table_name="tasks")
    op.drop_constraint(
        op.f("ck_tasks_lifecycle_consistency"),
        "tasks",
        type_="check",
    )
    op.drop_column("tasks", "purged_at")
    op.drop_column("tasks", "purge_after")
    op.drop_column("tasks", "deleted_at")
    op.drop_column("tasks", "archived_at")
