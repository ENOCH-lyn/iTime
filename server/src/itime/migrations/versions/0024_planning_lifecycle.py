"""Allow account cascades across applied planning proposals."""

from alembic import op

revision = "0024_planning_lifecycle"
down_revision = "0023_planning_integration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "fk_plan_versions_proposal_owner",
        "plan_versions",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_plan_versions_proposal_owner",
        "plan_versions",
        "ai_proposals",
        ["proposal_id", "owner_id"],
        ["id", "owner_id"],
        deferrable=True,
        initially="DEFERRED",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_plan_versions_proposal_owner",
        "plan_versions",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_plan_versions_proposal_owner",
        "plan_versions",
        "ai_proposals",
        ["proposal_id", "owner_id"],
        ["id", "owner_id"],
        ondelete="RESTRICT",
    )
