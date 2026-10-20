"""Allow delete_plan_block in AI action proposals."""

from alembic import op

revision = "0049_ai_plan_block_delete"
down_revision = "0048_ai_plan_block_create"
branch_labels = None
depends_on = None

TOOL_NAMES = (
    "create_task",
    "update_task",
    "complete_task",
    "delete_task",
    "delete_calendar_series",
    "update_calendar_series",
    "create_calendar_exception",
    "start_review",
    "write_review_report",
    "save_memory",
    "update_memory",
    "trash_memory",
    "create_difficulty",
    "propose_plan",
    "confirm_plan",
    "update_plan_block",
    "create_plan_block",
    "delete_plan_block",
)

PREVIOUS_TOOL_NAMES = tuple(
    name for name in TOOL_NAMES if name != "delete_plan_block"
)


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{item}'" for item in values)


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_ai_action_proposals_tool_name"),
        "ai_action_proposals",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_ai_action_proposals_tool_name"),
        "ai_action_proposals",
        f"tool_name IN ({_quoted(TOOL_NAMES)})",
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM ai_action_proposals WHERE tool_name = 'delete_plan_block'"
    )
    op.drop_constraint(
        op.f("ck_ai_action_proposals_tool_name"),
        "ai_action_proposals",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_ai_action_proposals_tool_name"),
        "ai_action_proposals",
        f"tool_name IN ({_quoted(PREVIOUS_TOOL_NAMES)})",
    )
