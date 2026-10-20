"""Add course teacher, notes grouping fields."""

import sqlalchemy as sa
from alembic import op

revision = "0052_course_teacher_group"
down_revision = "0051_ai_plan_block_batch"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "calendar_series",
        sa.Column(
            "teacher",
            sa.String(length=200),
            nullable=False,
            server_default="",
        ),
    )
    op.add_column(
        "calendar_series",
        sa.Column("course_group_id", sa.Uuid(), nullable=True),
    )
    op.execute(
        "UPDATE calendar_series SET course_group_id = id "
        "WHERE kind = 'course'"
    )
    op.create_index(
        "ix_calendar_series_owner_course_group",
        "calendar_series",
        ["owner_id", "course_group_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_calendar_series_owner_course_group",
        table_name="calendar_series",
    )
    op.drop_column("calendar_series", "course_group_id")
    op.drop_column("calendar_series", "teacher")
