from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from itime.database import Base


class AccountDeletion(Base):
    __tablename__ = "account_deletions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'processing', 'cancelled', 'completed')",
            name="status",
        ),
        CheckConstraint(
            "subject_digest ~ '^[0-9a-f]{64}$'",
            name="subject_digest",
        ),
        CheckConstraint("purge_after > requested_at", name="purge_after"),
        CheckConstraint("available_at >= requested_at", name="available_at"),
        CheckConstraint("attempt_count >= 0", name="attempt_count"),
        CheckConstraint(
            "(last_error_code IS NULL) = (last_error_message IS NULL)",
            name="error_pair",
        ),
        CheckConstraint(
            "(cancelled_at IS NULL OR "
            "(cancelled_at >= requested_at AND cancelled_at < purge_after)) "
            "AND (started_at IS NULL OR started_at >= requested_at) "
            "AND (completed_at IS NULL OR completed_at >= purge_after)",
            name="event_times",
        ),
        CheckConstraint(
            "(status = 'pending' "
            "AND user_id IS NOT NULL "
            "AND lease_id IS NULL AND lease_until IS NULL "
            "AND cancelled_at IS NULL AND completed_at IS NULL) "
            "OR (status = 'processing' "
            "AND user_id IS NOT NULL "
            "AND lease_id IS NOT NULL AND lease_until IS NOT NULL "
            "AND started_at IS NOT NULL "
            "AND cancelled_at IS NULL AND completed_at IS NULL) "
            "OR (status = 'cancelled' "
            "AND lease_id IS NULL AND lease_until IS NULL "
            "AND cancelled_at IS NOT NULL AND completed_at IS NULL) "
            "OR (status = 'completed' "
            "AND user_id IS NULL "
            "AND lease_id IS NULL AND lease_until IS NULL "
            "AND cancelled_at IS NULL AND completed_at IS NOT NULL)",
            name="state",
        ),
        Index(
            "uq_account_deletions_user_active",
            "user_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'processing')"),
        ),
        Index(
            "ix_account_deletions_due",
            "available_at",
            "purge_after",
            "lease_until",
            "id",
            postgresql_where=text("status IN ('pending', 'processing')"),
        ),
        Index(
            "ix_account_deletions_subject_digest",
            "subject_digest",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    subject_digest: Mapped[str] = mapped_column(String(64))
    operation_id: Mapped[UUID] = mapped_column(unique=True)
    status: Mapped[str] = mapped_column(String(16))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    purge_after: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_id: Mapped[UUID | None]
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(80))
    last_error_message: Mapped[str | None] = mapped_column(String(300))
    request_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
