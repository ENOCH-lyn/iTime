from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from itime.database import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'suspended')", name="status"),
        CheckConstraint("ai_access IN ('allowed', 'frozen')", name="ai_access"),
        CheckConstraint("email = lower(email)", name="normalized_email"),
        Index("ix_users_created", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="active", server_default="active")
    ai_access: Mapped[str] = mapped_column(
        String(20), default="allowed", server_default="allowed"
    )
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    auth_epoch: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    management_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DeviceSession(Base):
    __tablename__ = "device_sessions"
    __table_args__ = (
        Index("ix_device_sessions_user_created", "user_id", "created_at", "id"),
        Index("ix_device_sessions_expires", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    auth_epoch: Mapped[int] = mapped_column(Integer)
    device_name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EmailToken(Base):
    __tablename__ = "email_tokens"
    __table_args__ = (
        CheckConstraint("purpose IN ('verify', 'reset')", name="purpose"),
        Index("ix_email_tokens_user_purpose_created", "user_id", "purpose", "created_at"),
        Index("ix_email_tokens_expires", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    purpose: Mapped[str] = mapped_column(String(10))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MailOutbox(Base):
    __tablename__ = "mail_outbox"
    __table_args__ = (
        Index("ix_mail_outbox_due", "available_at", postgresql_where=text("finished_at IS NULL")),
        Index("ix_mail_outbox_token_id", "token_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    token_id: Mapped[UUID] = mapped_column(ForeignKey("email_tokens.id", ondelete="CASCADE"))
    encrypted_message: Mapped[bytes | None] = mapped_column(LargeBinary)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_id: Mapped[UUID | None]
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(50))


class AuthRateBucket(Base):
    __tablename__ = "auth_rate_buckets"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    hits: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class SecurityEvent(Base):
    __tablename__ = "security_events"
    __table_args__ = (Index("ix_security_events_user_created", "user_id", "created_at"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(50))
    request_id: Mapped[str] = mapped_column(String(36))
    target_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
