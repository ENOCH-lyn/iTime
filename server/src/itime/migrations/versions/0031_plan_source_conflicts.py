"""Persist subscription-source conflicts for confirmed plan versions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0031_plan_source_conflicts"
down_revision = "0030_public_task_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plan_source_conflicts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("plan_version_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("source_generation", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=12),
            server_default="open",
            nullable=False,
        ),
        sa.Column("resolution", sa.String(length=12), nullable=True),
        sa.Column(
            "changes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "affected_block_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "details_limited",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "version",
            sa.Integer(),
            server_default="1",
            nullable=False,
        ),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "jsonb_typeof(affected_block_ids) = 'array' "
            "AND jsonb_array_length(affected_block_ids) <= 100",
            name=op.f("ck_plan_source_conflicts_affected_block_ids"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(changes) = 'array' "
            "AND jsonb_array_length(changes) <= 2000 "
            "AND (details_limited OR jsonb_array_length(changes) >= 1)",
            name=op.f("ck_plan_source_conflicts_changes"),
        ),
        sa.CheckConstraint(
            "resolution IS NULL "
            "OR resolution IN ('kept', 'replanned', 'superseded')",
            name=op.f("ck_plan_source_conflicts_resolution"),
        ),
        sa.CheckConstraint(
            "source_generation >= 1",
            name=op.f("ck_plan_source_conflicts_source_generation"),
        ),
        sa.CheckConstraint(
            "(status = 'open' AND resolution IS NULL AND resolved_at IS NULL) "
            "OR (status = 'resolved' AND resolution IS NOT NULL "
            "AND resolved_at IS NOT NULL)",
            name=op.f("ck_plan_source_conflicts_state"),
        ),
        sa.CheckConstraint(
            "status IN ('open', 'resolved')",
            name=op.f("ck_plan_source_conflicts_status"),
        ),
        sa.CheckConstraint(
            "version >= 1",
            name=op.f("ck_plan_source_conflicts_version"),
        ),
        sa.ForeignKeyConstraint(
            ["plan_id", "owner_id"],
            ["plans.id", "plans.owner_id"],
            name="fk_plan_source_conflicts_plan_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["plan_version_id", "plan_id", "owner_id"],
            ["plan_versions.id", "plan_versions.plan_id", "plan_versions.owner_id"],
            name="fk_plan_source_conflicts_plan_version",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_id", "owner_id"],
            ["calendar_sources.id", "calendar_sources.owner_id"],
            name="fk_plan_source_conflicts_source_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_plan_source_conflicts"),
        ),
        sa.UniqueConstraint(
            "plan_version_id",
            "source_id",
            "source_generation",
            name="uq_plan_source_conflicts_version_source_generation",
        ),
    )
    op.create_index(
        "ix_plan_source_conflicts_owner_status_detected",
        "plan_source_conflicts",
        ["owner_id", "status", "detected_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_plan_source_conflicts_plan_version_status",
        "plan_source_conflicts",
        ["plan_version_id", "status", "detected_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_plan_source_conflicts_source_generation",
        "plan_source_conflicts",
        ["source_id", "source_generation"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_plan_source_conflicts_source_generation",
        table_name="plan_source_conflicts",
    )
    op.drop_index(
        "ix_plan_source_conflicts_plan_version_status",
        table_name="plan_source_conflicts",
    )
    op.drop_index(
        "ix_plan_source_conflicts_owner_status_detected",
        table_name="plan_source_conflicts",
    )
    op.drop_table("plan_source_conflicts")
