"""Integrate plan changes and defer task history references."""

from alembic import op

revision = "0023_planning_integration"
down_revision = "0022_ai_proposals_planning"
branch_labels = None
depends_on = None


def _replace_revision_function() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION bump_planning_input_revision()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            target_owner_id uuid;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                target_owner_id := OLD.owner_id;
            ELSE
                target_owner_id := NEW.owner_id;
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM users WHERE id = target_owner_id
            ) THEN
                RETURN NULL;
            END IF;

            INSERT INTO planning_input_revisions (owner_id, revision, updated_at)
            VALUES (target_owner_id, 1, now())
            ON CONFLICT (owner_id) DO UPDATE
            SET revision = planning_input_revisions.revision + 1,
                updated_at = now();
            RETURN NULL;
        END;
        $$
        """
    )


def _replace_task_foreign_keys(*, deferred: bool) -> None:
    for table_name, constraint_name in (
        ("scheduled_blocks", "fk_scheduled_blocks_task_owner"),
        ("plan_unscheduled_items", "fk_plan_unscheduled_items_task_owner"),
    ):
        op.drop_constraint(constraint_name, table_name, type_="foreignkey")
        op.create_foreign_key(
            constraint_name,
            table_name,
            "tasks",
            ["task_id", "owner_id"],
            ["id", "owner_id"],
            ondelete=None if deferred else "RESTRICT",
            deferrable=True if deferred else None,
            initially="DEFERRED" if deferred else None,
        )


def upgrade() -> None:
    _replace_revision_function()
    _replace_task_foreign_keys(deferred=True)
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
        "'calendar_addition', 'calendar_source', 'privacy_settings', 'plan')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM change_events WHERE entity_type = 'plan'")
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
    _replace_task_foreign_keys(deferred=False)
