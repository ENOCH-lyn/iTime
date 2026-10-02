"""administrator TOTP credentials and sessions"""

import sqlalchemy as sa
from alembic import op

revision = "0003_admin_auth"
down_revision = "0002_accounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_credentials",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("totp_secret_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("totp_enabled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_totp_counter", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name=op.f("fk_admin_credentials_user_id_users"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_admin_credentials")),
    )
    op.create_table(
        "admin_enrollments",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("secret_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name=op.f("fk_admin_enrollments_user_id_users"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_admin_enrollments")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_admin_enrollments_token_hash")),
    )
    op.create_index(
        "ix_admin_enrollments_expires", "admin_enrollments", ["expires_at"], unique=False
    )
    op.create_table(
        "admin_recovery_codes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name=op.f("fk_admin_recovery_codes_user_id_users"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_recovery_codes")),
        sa.UniqueConstraint("code_hash", name=op.f("uq_admin_recovery_codes_code_hash")),
    )
    op.create_index(
        "ix_admin_recovery_codes_user_unused",
        "admin_recovery_codes",
        ["user_id", "used_at"],
        unique=False,
    )
    op.create_table(
        "admin_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("auth_epoch", sa.Integer(), nullable=False),
        sa.Column("auth_method", sa.String(length=10), nullable=False),
        sa.Column("device_name", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "auth_method IN ('totp', 'recovery')",
            name=op.f("ck_admin_sessions_auth_method"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name=op.f("fk_admin_sessions_user_id_users"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_admin_sessions_token_hash")),
    )
    op.create_index("ix_admin_sessions_expires", "admin_sessions", ["expires_at"], unique=False)
    op.create_index(
        "ix_admin_sessions_user_created",
        "admin_sessions",
        ["user_id", "created_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_admin_sessions_user_created", table_name="admin_sessions")
    op.drop_index("ix_admin_sessions_expires", table_name="admin_sessions")
    op.drop_table("admin_sessions")
    op.drop_index("ix_admin_recovery_codes_user_unused", table_name="admin_recovery_codes")
    op.drop_table("admin_recovery_codes")
    op.drop_index("ix_admin_enrollments_expires", table_name="admin_enrollments")
    op.drop_table("admin_enrollments")
    op.drop_table("admin_credentials")
