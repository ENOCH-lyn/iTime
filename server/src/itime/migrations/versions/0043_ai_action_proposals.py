"""Introduce ai_action_proposals for tool-driven write previews."""

import sqlalchemy as sa
from alembic import op

revision = "0043_ai_action_proposals"
down_revision = "0042_memory_ai_tool"
branch_labels = None
depends_on = None


TOOL_NAMES = (
    "create_task",
    "update_task",
    "complete_task",
    "start_review",
    "write_review_report",
    "save_memory",
    "update_memory",
    "trash_memory",
    "create_difficulty",
    "propose_plan",
    "confirm_plan",
    "update_plan_block",
)

STATUSES = ("pending", "applied", "rejected", "expired")


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{item}'" for item in values)


def upgrade() -> None:
    op.create_table(
        "ai_action_proposals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "owner_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("source_message_id", sa.Uuid(), nullable=True),
        sa.Column("tool_name", sa.String(length=40), nullable=False),
        sa.Column("operation_id", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("preview", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("payload", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("expected_version", sa.Integer(), nullable=True),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now() + interval '7 days'"),
        ),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_resource_id", sa.Uuid(), nullable=True),
        sa.Column("applied_resource_version", sa.Integer(), nullable=True),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["ai_conversations.id"],
            ondelete="CASCADE",
            name=op.f("fk_ai_action_proposals_conversation_id"),
        ),
        sa.CheckConstraint(
            f"tool_name IN ({_quoted(TOOL_NAMES)})",
            name=op.f("ck_ai_action_proposals_tool_name"),
        ),
        sa.CheckConstraint(
            f"status IN ({_quoted(STATUSES)})",
            name=op.f("ck_ai_action_proposals_status"),
        ),
        sa.CheckConstraint(
            "char_length(btrim(operation_id)) BETWEEN 1 AND 64",
            name=op.f("ck_ai_action_proposals_operation_id"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_ai_action_proposals_version")),
        sa.UniqueConstraint(
            "owner_id",
            "conversation_id",
            "source_message_id",
            "tool_name",
            "operation_id",
            name=op.f("uq_ai_action_proposals_idempotency"),
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(
        "ix_ai_action_proposals_owner_status",
        "ai_action_proposals",
        ["owner_id", "status", sa.text("created_at DESC"), "id"],
        unique=False,
    )
    op.create_index(
        "ix_ai_action_proposals_expiry",
        "ai_action_proposals",
        ["expires_at"],
        unique=False,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ai_action_proposals_expiry",
        table_name="ai_action_proposals",
    )
    op.drop_index(
        "ix_ai_action_proposals_owner_status",
        table_name="ai_action_proposals",
    )
    op.drop_table("ai_action_proposals")
