"""Allow term action proposals."""

from alembic import op

revision = "0056_term_proposals"
down_revision = "0055_manual_admin_totp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE ai_action_proposals DROP CONSTRAINT ck_ai_action_proposals_tool_name"
    )
    op.execute(
        "ALTER TABLE ai_action_proposals ADD CONSTRAINT ck_ai_action_proposals_tool_name "
        "CHECK (tool_name IN ("
        "'create_task','update_task','complete_task','delete_task',"
        "'delete_calendar_series','update_calendar_series',"
        "'create_calendar_exception','create_calendar_series',"
        "'create_term','update_term',"
        "'start_review','write_review_report','save_memory','update_memory',"
        "'trash_memory','create_difficulty','propose_plan','confirm_plan',"
        "'update_plan_block','create_plan_block','delete_plan_block',"
        "'batch_update_plan_blocks'))"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE ai_action_proposals DROP CONSTRAINT ck_ai_action_proposals_tool_name"
    )
    op.execute(
        "ALTER TABLE ai_action_proposals ADD CONSTRAINT ck_ai_action_proposals_tool_name "
        "CHECK (tool_name IN ("
        "'create_task','update_task','complete_task','delete_task',"
        "'delete_calendar_series','update_calendar_series',"
        "'create_calendar_exception','create_calendar_series',"
        "'start_review','write_review_report','save_memory','update_memory',"
        "'trash_memory','create_difficulty','propose_plan','confirm_plan',"
        "'update_plan_block','create_plan_block','delete_plan_block',"
        "'batch_update_plan_blocks'))"
    )
