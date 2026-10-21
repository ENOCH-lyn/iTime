"""Add an optional weather location to user time settings."""

import sqlalchemy as sa
from alembic import op

revision = "0054_weather_location"
down_revision = "0053_email_optional"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_time_settings",
        sa.Column("weather_location", sa.String(length=120), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_time_settings", "weather_location")
