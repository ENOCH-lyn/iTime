from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from itime.database import Base


class SyncState(Base):
    __tablename__ = "sync_states"
    __table_args__ = (
        CheckConstraint(
            "discarded_through_sequence >= 0",
            name="discarded_through_sequence",
        ),
    )

    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    discarded_through_sequence: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        server_default="0",
    )
    last_cleanup_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
