"""Default registration to no email verification."""

import sqlalchemy as sa
from alembic import op

revision = "0053_email_optional"
down_revision = "0052_course_teacher_group"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "registration_policies",
        "require_email_verification",
        server_default=sa.false(),
    )
    op.execute(
        "UPDATE registration_policies "
        "SET require_email_verification = false"
    )


def downgrade() -> None:
    op.alter_column(
        "registration_policies",
        "require_email_verification",
        server_default=sa.true(),
    )
