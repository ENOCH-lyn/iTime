"""Allow password-only admin sessions until TOTP is enabled manually."""

from alembic import op

revision = "0055_manual_admin_totp"
down_revision = "0054_weather_location"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE admin_sessions DROP CONSTRAINT ck_admin_sessions_auth_method"
    )
    op.execute(
        "ALTER TABLE admin_sessions ADD CONSTRAINT ck_admin_sessions_auth_method "
        "CHECK (auth_method IN ('password', 'totp', 'recovery'))"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE admin_sessions DROP CONSTRAINT ck_admin_sessions_auth_method"
    )
    op.execute(
        "ALTER TABLE admin_sessions ADD CONSTRAINT ck_admin_sessions_auth_method "
        "CHECK (auth_method IN ('totp', 'recovery'))"
    )
