"""Allow the batch occurrence-exception proposal tool name."""

from alembic import op

revision = "0060_occurrence_batch_tool"
down_revision = "0059_timetable_courses_tool"
branch_labels = None
depends_on = None

CONSTRAINT = "ck_ai_action_proposals_tool_name"
TOOL_NAMES = (
    "'create_task', 'update_task', 'complete_task', 'delete_task', "
    "'delete_calendar_series', 'update_calendar_series', "
    "'create_calendar_exception', 'create_calendar_exception_batch', "
    "'create_calendar_series', "
    "'create_term', 'update_term', 'start_review', 'write_review_report', "
    "'save_memory', 'update_memory', 'trash_memory', 'create_difficulty', "
    "'propose_plan', 'confirm_plan', 'update_plan_block', 'create_plan_block', "
    "'delete_plan_block', 'batch_update_plan_blocks', "
    "'create_timetable_courses'"
)
TOOL_NAMES_OLD = TOOL_NAMES.replace("'create_calendar_exception_batch', ", "")


def upgrade() -> None:
    op.execute(
        f"ALTER TABLE ai_action_proposals DROP CONSTRAINT IF EXISTS {CONSTRAINT}"
    )
    op.execute(
        f"ALTER TABLE ai_action_proposals ADD CONSTRAINT {CONSTRAINT} "
        f"CHECK (tool_name IN ({TOOL_NAMES}))"
    )


def downgrade() -> None:
    op.execute(
        f"ALTER TABLE ai_action_proposals DROP CONSTRAINT IF EXISTS {CONSTRAINT}"
    )
    op.execute(
        f"ALTER TABLE ai_action_proposals ADD CONSTRAINT {CONSTRAINT} "
        f"CHECK (tool_name IN ({TOOL_NAMES_OLD}))"
    )
