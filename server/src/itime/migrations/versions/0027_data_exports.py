"""Personal data export jobs."""

import sqlalchemy as sa
from alembic import op

revision = "0027_data_exports"
down_revision = "0026_review_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "data_export_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=12), server_default="queued", nullable=False),
        sa.Column("schema_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_id", sa.Uuid(), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("artifact_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("artifact_sha256", sa.String(length=64), nullable=True),
        sa.Column("artifact_byte_size", sa.BigInteger(), nullable=True),
        sa.Column("filename", sa.String(length=100), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
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
            "status IN ('queued', 'running', 'succeeded', 'failed', 'expired')",
            name=op.f("ck_data_export_jobs_status"),
        ),
        sa.CheckConstraint(
            "schema_version = 1",
            name=op.f("ck_data_export_jobs_schema_version"),
        ),
        sa.CheckConstraint(
            "attempt_count BETWEEN 0 AND max_attempts",
            name=op.f("ck_data_export_jobs_attempt_count"),
        ),
        sa.CheckConstraint(
            "max_attempts BETWEEN 1 AND 3",
            name=op.f("ck_data_export_jobs_max_attempts"),
        ),
        sa.CheckConstraint(
            "(status = 'running') = "
            "(lease_id IS NOT NULL AND lease_until IS NOT NULL)",
            name=op.f("ck_data_export_jobs_lease_state"),
        ),
        sa.CheckConstraint(
            "(error_code IS NULL) = (error_message IS NULL)",
            name=op.f("ck_data_export_jobs_error_pair"),
        ),
        sa.CheckConstraint(
            "(status IN ('queued', 'running') "
            "AND snapshot_at IS NULL "
            "AND artifact_ciphertext IS NULL "
            "AND artifact_sha256 IS NULL "
            "AND artifact_byte_size IS NULL "
            "AND filename IS NULL "
            "AND expires_at IS NULL "
            "AND error_code IS NULL "
            "AND finished_at IS NULL) "
            "OR (status = 'succeeded' "
            "AND snapshot_at IS NOT NULL "
            "AND artifact_ciphertext IS NOT NULL "
            "AND artifact_sha256 IS NOT NULL "
            "AND artifact_byte_size IS NOT NULL "
            "AND filename IS NOT NULL "
            "AND expires_at IS NOT NULL "
            "AND error_code IS NULL "
            "AND finished_at IS NOT NULL) "
            "OR (status = 'failed' "
            "AND snapshot_at IS NULL "
            "AND artifact_ciphertext IS NULL "
            "AND artifact_sha256 IS NULL "
            "AND artifact_byte_size IS NULL "
            "AND filename IS NULL "
            "AND expires_at IS NULL "
            "AND error_code IS NOT NULL "
            "AND finished_at IS NOT NULL) "
            "OR (status = 'expired' "
            "AND snapshot_at IS NOT NULL "
            "AND artifact_ciphertext IS NULL "
            "AND artifact_sha256 IS NOT NULL "
            "AND artifact_byte_size IS NOT NULL "
            "AND filename IS NOT NULL "
            "AND expires_at IS NOT NULL "
            "AND error_code IS NULL "
            "AND finished_at IS NOT NULL)",
            name=op.f("ck_data_export_jobs_state"),
        ),
        sa.CheckConstraint(
            "artifact_sha256 IS NULL OR length(artifact_sha256) = 64",
            name=op.f("ck_data_export_jobs_artifact_sha256"),
        ),
        sa.CheckConstraint(
            "artifact_byte_size IS NULL OR artifact_byte_size > 0",
            name=op.f("ck_data_export_jobs_artifact_byte_size"),
        ),
        sa.CheckConstraint(
            "expires_at IS NULL OR expires_at > finished_at",
            name=op.f("ck_data_export_jobs_expires_at"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_data_export_jobs_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_data_export_jobs")),
        sa.UniqueConstraint(
            "id",
            "owner_id",
            name="uq_data_export_jobs_id_owner",
        ),
    )
    op.create_index(
        "uq_data_export_jobs_owner_active",
        "data_export_jobs",
        ["owner_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_index(
        "ix_data_export_jobs_due",
        "data_export_jobs",
        ["status", "available_at", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_data_export_jobs_owner_created",
        "data_export_jobs",
        ["owner_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_data_export_jobs_artifact_expiry",
        "data_export_jobs",
        ["expires_at", "id"],
        unique=False,
        postgresql_where=sa.text(
            "status = 'succeeded' AND artifact_ciphertext IS NOT NULL"
        ),
    )
    op.create_index(
        "ix_data_export_jobs_metadata_expiry",
        "data_export_jobs",
        ["finished_at", "id"],
        unique=False,
        postgresql_where=sa.text("status IN ('failed', 'expired')"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_data_export_jobs_metadata_expiry",
        table_name="data_export_jobs",
        postgresql_where=sa.text("status IN ('failed', 'expired')"),
    )
    op.drop_index(
        "ix_data_export_jobs_artifact_expiry",
        table_name="data_export_jobs",
        postgresql_where=sa.text(
            "status = 'succeeded' AND artifact_ciphertext IS NOT NULL"
        ),
    )
    op.drop_index(
        "ix_data_export_jobs_owner_created",
        table_name="data_export_jobs",
    )
    op.drop_index("ix_data_export_jobs_due", table_name="data_export_jobs")
    op.drop_index(
        "uq_data_export_jobs_owner_active",
        table_name="data_export_jobs",
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.drop_table("data_export_jobs")
