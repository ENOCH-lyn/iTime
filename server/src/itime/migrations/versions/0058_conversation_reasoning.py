"""Store model reasoning traces alongside assistant messages."""

import sqlalchemy as sa
from alembic import op

revision = "0058_conversation_reasoning"
down_revision = "0057_agent_runs"
branch_labels = None
depends_on = None

KIND_OLD = "kind IN ('job_state', 'content_reset', 'content_delta')"
KIND_NEW = "kind IN ('job_state', 'content_reset', 'content_delta', 'reasoning_delta')"


def upgrade() -> None:
    op.add_column(
        "ai_conversation_messages",
        sa.Column(
            "reasoning_content",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
    )
    op.drop_constraint("kind", "ai_job_events", type_="check")
    op.create_check_constraint("kind", "ai_job_events", KIND_NEW)


def downgrade() -> None:
    op.drop_constraint("kind", "ai_job_events", type_="check")
    op.create_check_constraint("kind", "ai_job_events", KIND_OLD)
    op.drop_column("ai_conversation_messages", "reasoning_content")
