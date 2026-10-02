"""persistent AI jobs, attempts, and temporary media"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013_ai_jobs_media"
down_revision = "0012_ai_config_versions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "temporary_media",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(length=40), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=40), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("encrypted_content", sa.LargeBinary(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deletion_reason", sa.String(length=20), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "purpose IN ('calendar_screenshot')",
            name=op.f("ck_temporary_media_purpose"),
        ),
        sa.CheckConstraint(
            "content_type IN ('image/png', 'image/jpeg', 'image/webp')",
            name=op.f("ck_temporary_media_content_type"),
        ),
        sa.CheckConstraint(
            "length(btrim(original_filename)) > 0",
            name=op.f("ck_temporary_media_filename"),
        ),
        sa.CheckConstraint(
            "length(content_sha256) = 64",
            name=op.f("ck_temporary_media_content_sha256"),
        ),
        sa.CheckConstraint(
            "byte_size BETWEEN 1 AND 5242880",
            name=op.f("ck_temporary_media_byte_size"),
        ),
        sa.CheckConstraint(
            "width BETWEEN 1 AND 20000000",
            name=op.f("ck_temporary_media_width"),
        ),
        sa.CheckConstraint(
            "height BETWEEN 1 AND 20000000",
            name=op.f("ck_temporary_media_height"),
        ),
        sa.CheckConstraint(
            "width::bigint * height::bigint <= 20000000",
            name=op.f("ck_temporary_media_pixels"),
        ),
        sa.CheckConstraint(
            "(encrypted_content IS NULL) = "
            "(deleted_at IS NOT NULL AND deletion_reason IS NOT NULL)",
            name=op.f("ck_temporary_media_deletion_state"),
        ),
        sa.CheckConstraint(
            "deletion_reason IS NULL OR deletion_reason IN ('confirmed', 'expired')",
            name=op.f("ck_temporary_media_deletion_reason"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_temporary_media_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_temporary_media")),
        sa.UniqueConstraint("id", "owner_id", name="uq_temporary_media_id_owner"),
    )
    op.create_index(
        "ix_temporary_media_owner_created",
        "temporary_media",
        ["owner_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_temporary_media_expiry",
        "temporary_media",
        ["expires_at"],
        unique=False,
        postgresql_where=sa.text("encrypted_content IS NOT NULL"),
    )

    op.create_table(
        "ai_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("capability", sa.String(length=50), nullable=False),
        sa.Column("config_version_id", sa.Uuid(), nullable=False),
        sa.Column("input_media_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="queued", nullable=False),
        sa.Column(
            "input_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "output",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_id", sa.Uuid(), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
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
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "capability IN ('calendar_screenshot')",
            name=op.f("ck_ai_jobs_capability"),
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', "
            "'timed_out', 'cancelled')",
            name=op.f("ck_ai_jobs_status"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(input_summary) = 'object'",
            name=op.f("ck_ai_jobs_input_summary"),
        ),
        sa.CheckConstraint(
            "output IS NULL OR jsonb_typeof(output) = 'object'",
            name=op.f("ck_ai_jobs_output"),
        ),
        sa.CheckConstraint(
            "attempt_count BETWEEN 0 AND max_attempts",
            name=op.f("ck_ai_jobs_attempt_count"),
        ),
        sa.CheckConstraint(
            "max_attempts BETWEEN 1 AND 5",
            name=op.f("ck_ai_jobs_max_attempts"),
        ),
        sa.CheckConstraint(
            "(status = 'running') = (lease_id IS NOT NULL AND lease_until IS NOT NULL)",
            name=op.f("ck_ai_jobs_lease_state"),
        ),
        sa.CheckConstraint(
            "(status IN ('succeeded', 'failed', 'timed_out', 'cancelled')) "
            "= (finished_at IS NOT NULL)",
            name=op.f("ck_ai_jobs_finished_state"),
        ),
        sa.CheckConstraint(
            "(status = 'succeeded') = (output IS NOT NULL)",
            name=op.f("ck_ai_jobs_output_state"),
        ),
        sa.CheckConstraint(
            "(error_code IS NULL) = (error_message IS NULL)",
            name=op.f("ck_ai_jobs_error_pair"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('succeeded', 'cancelled') OR error_code IS NULL",
            name=op.f("ck_ai_jobs_success_error"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_ai_jobs_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["config_version_id", "capability"],
            ["ai_config_versions.id", "ai_config_versions.capability"],
            name="fk_ai_jobs_config_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["input_media_id", "owner_id"],
            ["temporary_media.id", "temporary_media.owner_id"],
            name="fk_ai_jobs_input_media_owner",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_jobs")),
        sa.UniqueConstraint("id", "owner_id", name="uq_ai_jobs_id_owner"),
        sa.UniqueConstraint("input_media_id", name="uq_ai_jobs_input_media"),
    )
    op.create_index(
        "ix_ai_jobs_owner_created",
        "ai_jobs",
        ["owner_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_ai_jobs_due",
        "ai_jobs",
        ["available_at", "deadline_at"],
        unique=False,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )

    op.create_table(
        "ai_job_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("lease_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("provider_request_id", sa.String(length=255), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_stage", sa.String(length=40), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "attempt_number BETWEEN 1 AND 5",
            name=op.f("ck_ai_job_attempts_attempt_number"),
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'retryable_failed', 'failed', "
            "'timed_out', 'cancelled', 'abandoned')",
            name=op.f("ck_ai_job_attempts_status"),
        ),
        sa.CheckConstraint(
            "(status = 'running') = (finished_at IS NULL)",
            name=op.f("ck_ai_job_attempts_finished_state"),
        ),
        sa.CheckConstraint(
            "(error_code IS NULL) = (error_stage IS NULL)",
            name=op.f("ck_ai_job_attempts_error_pair"),
        ),
        sa.ForeignKeyConstraint(
            ["job_id", "owner_id"],
            ["ai_jobs.id", "ai_jobs.owner_id"],
            name="fk_ai_job_attempts_job_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_job_attempts")),
        sa.UniqueConstraint(
            "job_id",
            "attempt_number",
            name="uq_ai_job_attempts_number",
        ),
        sa.UniqueConstraint(
            "job_id",
            "lease_id",
            name="uq_ai_job_attempts_lease",
        ),
    )
    op.create_index(
        "ix_ai_job_attempts_owner_job",
        "ai_job_attempts",
        ["owner_id", "job_id", "attempt_number"],
        unique=False,
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM user_operations
        WHERE scope LIKE 'ai_job.cancel:%'
        """
    )
    op.drop_index(
        "ix_ai_job_attempts_owner_job",
        table_name="ai_job_attempts",
    )
    op.drop_table("ai_job_attempts")
    op.drop_index("ix_ai_jobs_due", table_name="ai_jobs")
    op.drop_index("ix_ai_jobs_owner_created", table_name="ai_jobs")
    op.drop_table("ai_jobs")
    op.drop_index("ix_temporary_media_expiry", table_name="temporary_media")
    op.drop_index("ix_temporary_media_owner_created", table_name="temporary_media")
    op.drop_table("temporary_media")
