"""immutable AI configuration versions"""

import sqlalchemy as sa
from alembic import op

revision = "0012_ai_config_versions"
down_revision = "0011_calendar_file_imports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_config_capabilities",
        sa.Column("capability", sa.String(length=50), nullable=False),
        sa.Column("next_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("current_version_id", sa.Uuid(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "capability IN ('calendar_screenshot')",
            name=op.f("ck_ai_config_capabilities_capability"),
        ),
        sa.CheckConstraint(
            "next_version >= 1",
            name=op.f("ck_ai_config_capabilities_next_version"),
        ),
        sa.PrimaryKeyConstraint(
            "capability",
            name=op.f("pk_ai_config_capabilities"),
        ),
    )
    op.create_table(
        "ai_config_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("capability", sa.String(length=50), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("endpoint_url", sa.String(length=2048), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("api_key_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="draft", nullable=False),
        sa.Column("last_test_id", sa.Uuid(), nullable=True),
        sa.Column("last_test_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_error_code", sa.String(length=80), nullable=True),
        sa.Column("last_test_error_stage", sa.String(length=40), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("last_tested_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("published_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "capability IN ('calendar_screenshot')",
            name=op.f("ck_ai_config_versions_capability"),
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'tested', 'published', 'retired')",
            name=op.f("ck_ai_config_versions_status"),
        ),
        sa.CheckConstraint(
            "version >= 1",
            name=op.f("ck_ai_config_versions_version"),
        ),
        sa.CheckConstraint(
            "length(btrim(endpoint_url)) BETWEEN 1 AND 2048",
            name=op.f("ck_ai_config_versions_endpoint_url_length"),
        ),
        sa.CheckConstraint(
            "model ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,254}$'",
            name=op.f("ck_ai_config_versions_model"),
        ),
        sa.CheckConstraint(
            "octet_length(api_key_ciphertext) > 0",
            name=op.f("ck_ai_config_versions_api_key_ciphertext"),
        ),
        sa.CheckConstraint(
            "timeout_seconds BETWEEN 1 AND 300",
            name=op.f("ck_ai_config_versions_timeout_seconds"),
        ),
        sa.CheckConstraint(
            "max_output_tokens BETWEEN 1 AND 32768",
            name=op.f("ck_ai_config_versions_max_output_tokens"),
        ),
        sa.CheckConstraint(
            "(last_test_error_code IS NULL) = (last_test_error_stage IS NULL)",
            name=op.f("ck_ai_config_versions_test_error_pair"),
        ),
        sa.CheckConstraint(
            "(last_test_id IS NULL) = (last_test_started_at IS NULL)",
            name=op.f("ck_ai_config_versions_test_started"),
        ),
        sa.CheckConstraint(
            "last_test_completed_at IS NULL OR last_test_started_at IS NOT NULL",
            name=op.f("ck_ai_config_versions_test_completed"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('tested', 'published', 'retired') OR "
            "(last_test_completed_at IS NOT NULL AND last_test_error_code IS NULL)",
            name=op.f("ck_ai_config_versions_successful_status"),
        ),
        sa.CheckConstraint(
            "(status IN ('published', 'retired')) = (published_at IS NOT NULL)",
            name=op.f("ck_ai_config_versions_published_at"),
        ),
        sa.CheckConstraint(
            "(status = 'retired') = (retired_at IS NOT NULL)",
            name=op.f("ck_ai_config_versions_retired_at"),
        ),
        sa.ForeignKeyConstraint(
            ["capability"],
            ["ai_config_capabilities.capability"],
            name="fk_ai_config_versions_capability",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_ai_config_versions_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["last_tested_by_user_id"],
            ["users.id"],
            name=op.f("fk_ai_config_versions_last_tested_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["published_by_user_id"],
            ["users.id"],
            name=op.f("fk_ai_config_versions_published_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_config_versions")),
        sa.UniqueConstraint(
            "capability",
            "version",
            name="uq_ai_config_versions_version",
        ),
        sa.UniqueConstraint(
            "id",
            "capability",
            name="uq_ai_config_versions_id_capability",
        ),
    )
    op.create_index(
        "ix_ai_config_versions_capability_created",
        "ai_config_versions",
        ["capability", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "uq_ai_config_versions_published_capability",
        "ai_config_versions",
        ["capability"],
        unique=True,
        postgresql_where=sa.text("status = 'published'"),
    )
    op.create_foreign_key(
        "fk_ai_config_capabilities_current_version",
        "ai_config_capabilities",
        "ai_config_versions",
        ["current_version_id", "capability"],
        ["id", "capability"],
        ondelete="RESTRICT",
    )
    op.execute(
        """
        INSERT INTO ai_config_capabilities (capability, next_version)
        VALUES ('calendar_screenshot', 1)
        """
    )
    op.execute(
        """
        CREATE FUNCTION prevent_ai_config_version_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF ROW(
                NEW.id,
                NEW.capability,
                NEW.version,
                NEW.endpoint_url,
                NEW.model,
                NEW.api_key_ciphertext,
                NEW.timeout_seconds,
                NEW.max_output_tokens,
                NEW.created_at
            ) IS DISTINCT FROM ROW(
                OLD.id,
                OLD.capability,
                OLD.version,
                OLD.endpoint_url,
                OLD.model,
                OLD.api_key_ciphertext,
                OLD.timeout_seconds,
                OLD.max_output_tokens,
                OLD.created_at
            ) THEN
                RAISE EXCEPTION 'AI configuration version fields are immutable'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_ai_config_versions_immutable
        BEFORE UPDATE ON ai_config_versions
        FOR EACH ROW
        EXECUTE FUNCTION prevent_ai_config_version_mutation()
        """
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_ai_config_capabilities_current_version",
        "ai_config_capabilities",
        type_="foreignkey",
    )
    op.execute(
        "DROP TRIGGER trg_ai_config_versions_immutable ON ai_config_versions"
    )
    op.execute("DROP FUNCTION prevent_ai_config_version_mutation()")
    op.drop_index(
        "uq_ai_config_versions_published_capability",
        table_name="ai_config_versions",
    )
    op.drop_index(
        "ix_ai_config_versions_capability_created",
        table_name="ai_config_versions",
    )
    op.drop_table("ai_config_versions")
    op.drop_table("ai_config_capabilities")
