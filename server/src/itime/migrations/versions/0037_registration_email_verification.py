"""Add the registration email verification policy."""

import sqlalchemy as sa
from alembic import op

revision = "0037_email_verification_policy"
down_revision = "0036_transcription_quota_usage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "registration_policies",
        sa.Column(
            "require_email_verification",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column(
        "registration_policies",
        "require_email_verification",
    )
