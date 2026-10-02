from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from itime.database import Base


class RegistrationPolicy(Base):
    __tablename__ = "registration_policies"
    __table_args__ = (
        CheckConstraint("id = 1", name="singleton"),
        CheckConstraint("mode IN ('open', 'invite')", name="mode"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    mode: Mapped[str] = mapped_column(String(10), default="open", server_default="open")
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class RegistrationInvitation(Base):
    __tablename__ = "registration_invitations"
    __table_args__ = (
        CheckConstraint("email = lower(email)", name="normalized_email"),
        Index("ix_registration_invitations_email_created", "email", "created_at", "id"),
        Index("ix_registration_invitations_expires", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(254))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    issued_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AdminCredential(Base):
    __tablename__ = "admin_credentials"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    totp_secret_ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    totp_enabled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_totp_counter: Mapped[int | None] = mapped_column(Integer)


class AdminEnrollment(Base):
    __tablename__ = "admin_enrollments"
    __table_args__ = (Index("ix_admin_enrollments_expires", "expires_at"),)

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    secret_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AdminRecoveryCode(Base):
    __tablename__ = "admin_recovery_codes"
    __table_args__ = (Index("ix_admin_recovery_codes_user_unused", "user_id", "used_at"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AdminSession(Base):
    __tablename__ = "admin_sessions"
    __table_args__ = (
        CheckConstraint("auth_method IN ('totp', 'recovery')", name="auth_method"),
        Index("ix_admin_sessions_user_created", "user_id", "created_at", "id"),
        Index("ix_admin_sessions_expires", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    auth_epoch: Mapped[int] = mapped_column(Integer)
    auth_method: Mapped[str] = mapped_column(String(10))
    device_name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
