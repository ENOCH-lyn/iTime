"""registration invitations and user management states"""

import sqlalchemy as sa
from alembic import op

revision = "0004_user_management"
down_revision = "0003_admin_auth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("ai_access", sa.String(length=20), server_default="allowed", nullable=False),
    )
    op.add_column(
        "users",
        sa.Column("management_version", sa.Integer(), server_default="1", nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_users_ai_access"),
        "users",
        "ai_access IN ('allowed', 'frozen')",
    )
    op.create_index("ix_users_created", "users", ["created_at", "id"], unique=False)

    op.create_table(
        "registration_policies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=10), server_default="open", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint("id = 1", name=op.f("ck_registration_policies_singleton")),
        sa.CheckConstraint(
            "mode IN ('open', 'invite')",
            name=op.f("ck_registration_policies_mode"),
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["users.id"],
            name=op.f("fk_registration_policies_updated_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_registration_policies")),
    )
    op.execute(
        "INSERT INTO registration_policies (id, mode, version) VALUES (1, 'open', 1)"
    )

    op.create_table(
        "registration_invitations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("issued_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "email = lower(email)",
            name=op.f("ck_registration_invitations_normalized_email"),
        ),
        sa.ForeignKeyConstraint(
            ["consumed_by_user_id"],
            ["users.id"],
            name=op.f("fk_registration_invitations_consumed_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["issued_by_user_id"],
            ["users.id"],
            name=op.f("fk_registration_invitations_issued_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_registration_invitations")),
        sa.UniqueConstraint(
            "token_hash",
            name=op.f("uq_registration_invitations_token_hash"),
        ),
    )
    op.create_index(
        "ix_registration_invitations_email_created",
        "registration_invitations",
        ["email", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_registration_invitations_expires",
        "registration_invitations",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_registration_invitations_expires",
        table_name="registration_invitations",
    )
    op.drop_index(
        "ix_registration_invitations_email_created",
        table_name="registration_invitations",
    )
    op.drop_table("registration_invitations")
    op.drop_table("registration_policies")
    op.drop_index("ix_users_created", table_name="users")
    op.drop_constraint(op.f("ck_users_ai_access"), "users", type_="check")
    op.drop_column("users", "management_version")
    op.drop_column("users", "ai_access")
