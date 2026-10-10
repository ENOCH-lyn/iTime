"""Allow AI-tool-sourced memories with tags and last-used tracking."""

import sqlalchemy as sa
from alembic import op

revision = "0042_memory_ai_tool"
down_revision = "0041_ai_import_source_check"
branch_labels = None
depends_on = None


OLD_SOURCE_TYPES = "('review_proposal', 'manual')"
NEW_SOURCE_TYPES = "('review_proposal', 'manual', 'ai_tool')"

OLD_SOURCE_PREDICATE = (
    "(purged_at IS NOT NULL AND title IS NULL AND content IS NULL "
    "AND source_proposal_id IS NULL) "
    "OR (purged_at IS NULL AND "
    "((source_type = 'manual' AND source_proposal_id IS NULL) "
    "OR (source_type = 'review_proposal' "
    "AND source_proposal_id IS NOT NULL)))"
)

NEW_SOURCE_PREDICATE = (
    "(purged_at IS NOT NULL AND title IS NULL AND content IS NULL "
    "AND source_proposal_id IS NULL AND source_conversation_id IS NULL) "
    "OR (purged_at IS NULL AND "
    "((source_type = 'manual' "
    "AND source_proposal_id IS NULL "
    "AND source_conversation_id IS NULL) "
    "OR (source_type = 'review_proposal' "
    "AND source_proposal_id IS NOT NULL "
    "AND source_conversation_id IS NULL) "
    "OR (source_type = 'ai_tool' "
    "AND source_proposal_id IS NULL "
    "AND source_conversation_id IS NOT NULL)))"
)


def upgrade() -> None:
    op.add_column(
        "memories",
        sa.Column(
            "tags",
            sa.ARRAY(sa.Text()),
            server_default=sa.text("ARRAY[]::text[]"),
            nullable=False,
        ),
    )
    op.add_column(
        "memories",
        sa.Column(
            "last_used_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "memories",
        sa.Column(
            "source_conversation_id",
            sa.Uuid(),
            nullable=True,
        ),
    )

    op.drop_constraint(
        op.f("ck_memories_source_type"),
        "memories",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_memories_source_type"),
        "memories",
        f"source_type IN {NEW_SOURCE_TYPES}",
    )

    op.drop_constraint(
        op.f("ck_memories_source"),
        "memories",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_memories_source"),
        "memories",
        NEW_SOURCE_PREDICATE,
    )

    op.create_check_constraint(
        op.f("ck_memories_tags"),
        "memories",
        "cardinality(tags) <= 10",
    )

    op.create_index(
        "ix_memories_tags",
        "memories",
        ["tags"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        "ix_memories_owner_last_used",
        "memories",
        ["owner_id", sa.text("last_used_at DESC NULLS LAST"), "id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL AND purged_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_memories_owner_last_used",
        table_name="memories",
    )
    op.drop_index(
        "ix_memories_tags",
        table_name="memories",
    )
    op.drop_constraint(
        op.f("ck_memories_tags"),
        "memories",
        type_="check",
    )

    op.drop_constraint(
        op.f("ck_memories_source"),
        "memories",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_memories_source"),
        "memories",
        OLD_SOURCE_PREDICATE,
    )

    op.drop_constraint(
        op.f("ck_memories_source_type"),
        "memories",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_memories_source_type"),
        "memories",
        f"source_type IN {OLD_SOURCE_TYPES}",
    )

    op.drop_column("memories", "source_conversation_id")
    op.drop_column("memories", "last_used_at")
    op.drop_column("memories", "tags")
