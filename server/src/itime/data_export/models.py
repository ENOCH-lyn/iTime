from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from itime.database import Base


class DataExportJob(Base):
    __tablename__ = "data_export_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'expired')",
            name="status",
        ),
        CheckConstraint("schema_version = 1", name="schema_version"),
        CheckConstraint(
            "attempt_count BETWEEN 0 AND max_attempts",
            name="attempt_count",
        ),
        CheckConstraint("max_attempts BETWEEN 1 AND 3", name="max_attempts"),
        CheckConstraint(
            "(status = 'running') = "
            "(lease_id IS NOT NULL AND lease_until IS NOT NULL)",
            name="lease_state",
        ),
        CheckConstraint(
            "(error_code IS NULL) = (error_message IS NULL)",
            name="error_pair",
        ),
        CheckConstraint(
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
            name="state",
        ),
        CheckConstraint(
            "artifact_sha256 IS NULL OR length(artifact_sha256) = 64",
            name="artifact_sha256",
        ),
        CheckConstraint(
            "artifact_byte_size IS NULL OR artifact_byte_size > 0",
            name="artifact_byte_size",
        ),
        CheckConstraint(
            "expires_at IS NULL OR expires_at > finished_at",
            name="expires_at",
        ),
        UniqueConstraint("id", "owner_id", name="uq_data_export_jobs_id_owner"),
        Index(
            "uq_data_export_jobs_owner_active",
            "owner_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
        Index(
            "ix_data_export_jobs_due",
            "status",
            "available_at",
            "created_at",
            "id",
        ),
        Index(
            "ix_data_export_jobs_owner_created",
            "owner_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_data_export_jobs_artifact_expiry",
            "expires_at",
            "id",
            postgresql_where=text(
                "status = 'succeeded' AND artifact_ciphertext IS NOT NULL"
            ),
        ),
        Index(
            "ix_data_export_jobs_metadata_expiry",
            "finished_at",
            "id",
            postgresql_where=text("status IN ('failed', 'expired')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    status: Mapped[str] = mapped_column(
        String(12),
        default="queued",
        server_default="queued",
    )
    schema_version: Mapped[int] = mapped_column(
        Integer,
        default=1,
        server_default="1",
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer,
        default=3,
        server_default="3",
    )
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_id: Mapped[UUID | None]
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    snapshot_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    artifact_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    artifact_sha256: Mapped[str | None] = mapped_column(String(64))
    artifact_byte_size: Mapped[int | None] = mapped_column(BigInteger)
    filename: Mapped[str | None] = mapped_column(String(100))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
