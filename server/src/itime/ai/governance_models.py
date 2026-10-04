from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from itime.database import Base


class UserPrivacySetting(Base):
    __tablename__ = "user_privacy_settings"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version"),
        Index("ix_user_privacy_settings_updated", "updated_at", "owner_id"),
    )

    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    quality_improvement_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=text("true"),
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class AIBodyLog(Base):
    __tablename__ = "ai_body_logs"
    __table_args__ = (
        CheckConstraint("attempt_number BETWEEN 1 AND 5", name="attempt_number"),
        CheckConstraint(
            "capability IN "
            "('calendar_screenshot', 'conversation', 'planning', 'review', "
            "'transcription')",
            name="capability",
        ),
        CheckConstraint(
            "status IN ('running', 'succeeded', 'retryable_failed', 'failed', "
            "'timed_out', 'cancelled', 'abandoned')",
            name="status",
        ),
        CheckConstraint("jsonb_typeof(request_body) = 'object'", name="request_body"),
        CheckConstraint(
            "octet_length(response_body) <= 1048576",
            name="response_body_size",
        ),
        CheckConstraint(
            "(error_code IS NULL) = (error_stage IS NULL)",
            name="error_pair",
        ),
        CheckConstraint(
            "(status = 'running') = (finished_at IS NULL)",
            name="finished_state",
        ),
        CheckConstraint("expires_at >= started_at", name="expires_at"),
        CheckConstraint(
            "request_prepared_at IS NULL OR request_prepared_at >= started_at",
            name="request_prepared_at",
        ),
        ForeignKeyConstraint(
            ["job_id", "owner_id"],
            ["ai_jobs.id", "ai_jobs.owner_id"],
            name="fk_ai_body_logs_job_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["job_id", "attempt_number"],
            ["ai_job_attempts.job_id", "ai_job_attempts.attempt_number"],
            name="fk_ai_body_logs_job_attempt",
            ondelete="CASCADE",
        ),
        UniqueConstraint("job_id", "attempt_number", name="uq_ai_body_logs_job_attempt"),
        Index("ix_ai_body_logs_owner_started", "owner_id", "started_at", "id"),
        Index("ix_ai_body_logs_expiry", "expires_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    job_id: Mapped[UUID]
    owner_id: Mapped[UUID]
    attempt_number: Mapped[int] = mapped_column(Integer)
    capability: Mapped[str] = mapped_column(String(50))
    request_body: Mapped[dict] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    response_body: Mapped[str] = mapped_column(Text, default="", server_default="")
    status: Mapped[str] = mapped_column(String(24))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_stage: Mapped[str | None] = mapped_column(String(40))
    provider_request_id: Mapped[str | None] = mapped_column(String(255))
    http_status: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    request_prepared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AIQualitySample(Base):
    __tablename__ = "ai_quality_samples"
    __table_args__ = (
        CheckConstraint(
            "capability IN "
            "('calendar_screenshot', 'conversation', 'planning', 'review', "
            "'transcription')",
            name="capability",
        ),
        CheckConstraint("status IN ('pending', 'reviewed')", name="status"),
        CheckConstraint(
            "outcome IS NULL OR outcome IN ('acceptable', 'needs_improvement')",
            name="outcome",
        ),
        CheckConstraint("jsonb_typeof(request_body) = 'object'", name="request_body"),
        CheckConstraint(
            "octet_length(response_body) <= 1048576",
            name="response_body_size",
        ),
        CheckConstraint(
            "review_notes IS NULL OR char_length(review_notes) <= 2000",
            name="review_notes",
        ),
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint(
            "(status = 'pending' AND outcome IS NULL "
            "AND reviewed_by_user_id IS NULL AND reviewed_at IS NULL) "
            "OR (status = 'reviewed' AND outcome IS NOT NULL "
            "AND reviewed_by_user_id IS NOT NULL AND reviewed_at IS NOT NULL)",
            name="review_state",
        ),
        CheckConstraint("expires_at >= sampled_at", name="expires_at"),
        UniqueConstraint("source_job_id", name="uq_ai_quality_samples_source_job"),
        Index("ix_ai_quality_samples_owner_sampled", "owner_id", "sampled_at", "id"),
        Index("ix_ai_quality_samples_expiry", "expires_at", "id"),
        Index("ix_ai_quality_samples_status_sampled", "status", "sampled_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    source_job_id: Mapped[UUID]
    source_body_log_id: Mapped[UUID]
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    capability: Mapped[str] = mapped_column(String(50))
    request_body: Mapped[dict] = mapped_column(JSONB)
    response_body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(16),
        default="pending",
        server_default="pending",
    )
    outcome: Mapped[str | None] = mapped_column(String(24))
    review_notes: Mapped[str | None] = mapped_column(Text)
    reviewed_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    sampled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
