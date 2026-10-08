"""Allow conversation agent task-and-plan proposals."""

import sqlalchemy as sa
from alembic import op

revision = "0038_agent_task_plan"
down_revision = "0037_email_verification_policy"
branch_labels = None
depends_on = None


def _drop_constraints() -> None:
    op.drop_constraint(op.f("ck_ai_proposals_kind"), "ai_proposals", type_="check")
    op.drop_constraint(op.f("ck_ai_proposals_state"), "ai_proposals", type_="check")
    op.drop_constraint(
        op.f("ck_ai_proposal_changes_entity_type"),
        "ai_proposal_changes",
        type_="check",
    )


def upgrade() -> None:
    _drop_constraints()
    op.alter_column(
        "ai_proposals",
        "input_snapshot_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )
    op.create_check_constraint(
        op.f("ck_ai_proposals_kind"),
        "ai_proposals",
        "kind IN ('plan', 'task_plan')",
    )
    op.create_check_constraint(
        op.f("ck_ai_proposals_state"),
        "ai_proposals",
        "(status = 'pending' AND applied_at IS NULL AND rejected_at IS NULL "
        "AND applied_plan_id IS NULL AND applied_plan_version_id IS NULL) "
        "OR (status = 'applied' AND applied_at IS NOT NULL "
        "AND rejected_at IS NULL AND applied_plan_id IS NOT NULL "
        "AND applied_plan_version_id IS NOT NULL "
        "AND input_snapshot_id IS NOT NULL) "
        "OR (status = 'rejected' AND rejected_at IS NOT NULL "
        "AND applied_at IS NULL AND applied_plan_id IS NULL "
        "AND applied_plan_version_id IS NULL)",
    )
    op.create_check_constraint(
        op.f("ck_ai_proposal_changes_entity_type"),
        "ai_proposal_changes",
        "entity_type IN ('plan', 'scheduled_block', 'task')",
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM ai_proposals WHERE kind <> 'plan'
            ) OR EXISTS (
                SELECT 1 FROM ai_proposals WHERE input_snapshot_id IS NULL
            ) OR EXISTS (
                SELECT 1 FROM ai_proposal_changes
                WHERE entity_type NOT IN ('plan', 'scheduled_block')
            ) THEN
                RAISE EXCEPTION
                    'cannot downgrade 0038_agent_task_plan with agent proposal data';
            END IF;
        END
        $$;
        """
    )
    _drop_constraints()
    op.alter_column(
        "ai_proposals",
        "input_snapshot_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )
    op.create_check_constraint(
        op.f("ck_ai_proposals_kind"),
        "ai_proposals",
        "kind IN ('plan')",
    )
    op.create_check_constraint(
        op.f("ck_ai_proposals_state"),
        "ai_proposals",
        "(status = 'pending' AND applied_at IS NULL AND rejected_at IS NULL "
        "AND applied_plan_id IS NULL AND applied_plan_version_id IS NULL) "
        "OR (status = 'applied' AND applied_at IS NOT NULL "
        "AND rejected_at IS NULL AND applied_plan_id IS NOT NULL "
        "AND applied_plan_version_id IS NOT NULL) "
        "OR (status = 'rejected' AND rejected_at IS NOT NULL "
        "AND applied_at IS NULL AND applied_plan_id IS NULL "
        "AND applied_plan_version_id IS NULL)",
    )
    op.create_check_constraint(
        op.f("ck_ai_proposal_changes_entity_type"),
        "ai_proposal_changes",
        "entity_type IN ('plan', 'scheduled_block')",
    )
