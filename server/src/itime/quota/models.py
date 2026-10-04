from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from itime.database import Base


class AIQuotaPolicyState(Base):
    __tablename__ = "ai_quota_policy_state"
    __table_args__ = (
        CheckConstraint("id = 1", name="singleton"),
        CheckConstraint("next_version >= 1", name="next_version"),
        ForeignKeyConstraint(
            ["current_version_id"],
            ["ai_quota_policy_versions.id"],
            name="fk_ai_quota_policy_state_current_version",
            ondelete="RESTRICT",
            use_alter=True,
        ),
    )

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=1)
    next_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    current_version_id: Mapped[UUID | None]
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class AIQuotaPolicyVersion(Base):
    __tablename__ = "ai_quota_policy_versions"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency"),
        CheckConstraint(
            "daily_allowance_nanos BETWEEN 0 AND 9000000000000000000",
            name="daily_allowance",
        ),
        CheckConstraint(
            "length(btrim(reservation_estimator_version)) BETWEEN 1 AND 80",
            name="estimator_version",
        ),
        CheckConstraint(
            "status IN ('draft', 'published', 'retired')",
            name="status",
        ),
        CheckConstraint(
            "(status IN ('published', 'retired')) = (published_at IS NOT NULL)",
            name="published_at",
        ),
        CheckConstraint(
            "(status = 'retired') = (retired_at IS NOT NULL)",
            name="retired_at",
        ),
        UniqueConstraint("version", name="uq_ai_quota_policy_versions_version"),
        Index(
            "uq_ai_quota_policy_versions_published",
            "status",
            unique=True,
            postgresql_where=text("status = 'published'"),
        ),
        Index(
            "ix_ai_quota_policy_versions_created",
            "created_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    version: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    daily_allowance_nanos: Mapped[int] = mapped_column(BigInteger)
    reservation_estimator_version: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(
        String(20),
        default="draft",
        server_default="draft",
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    published_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AIDailyAllowance(Base):
    __tablename__ = "ai_daily_allowances"
    __table_args__ = (
        CheckConstraint("window_start_at < window_end_at", name="window"),
        CheckConstraint(
            "activity_day_start_minute = 240",
            name="activity_day_start_minute",
        ),
        CheckConstraint("length(btrim(time_zone)) > 0", name="time_zone"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency"),
        CheckConstraint(
            "base_amount_nanos BETWEEN 0 AND 9000000000000000000",
            name="base_amount",
        ),
        CheckConstraint("version >= 1", name="version"),
        UniqueConstraint("id", "owner_id", name="uq_ai_daily_allowances_id_owner"),
        UniqueConstraint(
            "owner_id",
            "window_start_at",
            "currency",
            name="uq_ai_daily_allowances_owner_window_currency",
        ),
        Index(
            "ix_ai_daily_allowances_owner_window",
            "owner_id",
            "window_end_at",
            "window_start_at",
        ),
        Index(
            "ix_ai_daily_allowances_activity_date",
            "activity_date",
            "currency",
            "owner_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    activity_date: Mapped[date] = mapped_column(Date)
    window_start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    time_zone: Mapped[str] = mapped_column(String(64))
    activity_day_start_minute: Mapped[int] = mapped_column(
        Integer,
        default=240,
        server_default="240",
    )
    policy_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("ai_quota_policy_versions.id", ondelete="RESTRICT")
    )
    currency: Mapped[str] = mapped_column(String(3))
    base_amount_nanos: Mapped[int] = mapped_column(BigInteger)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class AIQuotaReservation(Base):
    __tablename__ = "ai_quota_reservations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('reserved', 'settled', 'released')",
            name="status",
        ),
        CheckConstraint(
            "reserved_nanos BETWEEN 0 AND 9000000000000000000",
            name="reserved",
        ),
        CheckConstraint(
            "length(btrim(estimator_version)) BETWEEN 1 AND 80",
            name="estimator_version",
        ),
        CheckConstraint(
            "usage_source IS NULL OR "
            "usage_source IN ('provider', 'estimated', 'measured')",
            name="usage_source",
        ),
        CheckConstraint(
            "release_reason IS NULL OR "
            "release_reason IN ('failed', 'timed_out', 'cancelled')",
            name="release_reason",
        ),
        CheckConstraint(
            "(status = 'reserved' "
            "AND terminal_at IS NULL "
            "AND actual_cost_nanos IS NULL "
            "AND charged_nanos IS NULL "
            "AND platform_overage_nanos IS NULL "
            "AND usage_source IS NULL "
            "AND provider_cost_id IS NULL "
            "AND release_reason IS NULL) "
            "OR (status = 'settled' "
            "AND terminal_at IS NOT NULL "
            "AND actual_cost_nanos >= 0 "
            "AND charged_nanos BETWEEN 0 AND reserved_nanos "
            "AND platform_overage_nanos = actual_cost_nanos - charged_nanos "
            "AND usage_source IS NOT NULL "
            "AND provider_cost_id IS NOT NULL "
            "AND release_reason IS NULL) "
            "OR (status = 'released' "
            "AND terminal_at IS NOT NULL "
            "AND actual_cost_nanos IS NULL "
            "AND charged_nanos IS NULL "
            "AND platform_overage_nanos IS NULL "
            "AND usage_source IS NULL "
            "AND provider_cost_id IS NULL "
            "AND release_reason IS NOT NULL)",
            name="state",
        ),
        ForeignKeyConstraint(
            ["job_id", "owner_id"],
            ["ai_jobs.id", "ai_jobs.owner_id"],
            name="fk_ai_quota_reservations_job_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["daily_allowance_id", "owner_id"],
            ["ai_daily_allowances.id", "ai_daily_allowances.owner_id"],
            name="fk_ai_quota_reservations_daily_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["provider_cost_id", "job_id"],
            ["ai_provider_costs.id", "ai_provider_costs.job_id"],
            name="fk_ai_quota_reservations_provider_cost_job",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", "owner_id", name="uq_ai_quota_reservations_id_owner"),
        UniqueConstraint("job_id", name="uq_ai_quota_reservations_job"),
        Index(
            "ix_ai_quota_reservations_active_daily",
            "daily_allowance_id",
            "accepted_at",
            "id",
            postgresql_where=text("status = 'reserved'"),
        ),
        Index(
            "ix_ai_quota_reservations_owner_accepted",
            "owner_id",
            "accepted_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    job_id: Mapped[UUID]
    owner_id: Mapped[UUID]
    daily_allowance_id: Mapped[UUID]
    status: Mapped[str] = mapped_column(
        String(20),
        default="reserved",
        server_default="reserved",
    )
    reserved_nanos: Mapped[int] = mapped_column(BigInteger)
    estimator_version: Mapped[str] = mapped_column(String(80))
    actual_cost_nanos: Mapped[int | None] = mapped_column(BigInteger)
    charged_nanos: Mapped[int | None] = mapped_column(BigInteger)
    platform_overage_nanos: Mapped[int | None] = mapped_column(BigInteger)
    usage_source: Mapped[str | None] = mapped_column(String(16))
    provider_cost_id: Mapped[UUID | None]
    release_reason: Mapped[str | None] = mapped_column(String(24))
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class AIQuotaAdminAdjustment(Base):
    __tablename__ = "ai_quota_admin_adjustments"
    __table_args__ = (
        CheckConstraint("length(request_hash) = 64", name="request_hash"),
        CheckConstraint(
            "delta_nanos BETWEEN -9000000000000000000 AND 9000000000000000000 "
            "AND delta_nanos <> 0",
            name="delta",
        ),
        CheckConstraint(
            "char_length(btrim(reason)) BETWEEN 1 AND 500",
            name="reason",
        ),
        CheckConstraint("char_length(request_id) = 36", name="request_id"),
        ForeignKeyConstraint(
            ["daily_allowance_id", "target_owner_id"],
            ["ai_daily_allowances.id", "ai_daily_allowances.owner_id"],
            name="fk_ai_quota_admin_adjustments_daily_owner",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "id",
            "target_owner_id",
            name="uq_ai_quota_admin_adjustments_id_owner",
        ),
        UniqueConstraint(
            "operation_id",
            name="uq_ai_quota_admin_adjustments_operation",
        ),
        Index(
            "ix_ai_quota_admin_adjustments_target_created",
            "target_owner_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_ai_quota_admin_adjustments_actor_created",
            "actor_admin_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    operation_id: Mapped[UUID]
    request_hash: Mapped[str] = mapped_column(String(64))
    target_owner_id: Mapped[UUID]
    daily_allowance_id: Mapped[UUID]
    actor_admin_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    delta_nanos: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[str] = mapped_column(Text)
    request_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class AIQuotaLedgerEntry(Base):
    __tablename__ = "ai_quota_ledger_entries"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('usage_charge', 'admin_credit', 'admin_debit')",
            name="kind",
        ),
        CheckConstraint(
            "delta_nanos BETWEEN -9000000000000000000 AND 9000000000000000000",
            name="delta",
        ),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency"),
        CheckConstraint(
            "usage_source IS NULL OR "
            "usage_source IN ('provider', 'estimated', 'measured')",
            name="usage_source",
        ),
        CheckConstraint(
            "(kind = 'usage_charge' "
            "AND delta_nanos <= 0 "
            "AND reservation_id IS NOT NULL "
            "AND adjustment_id IS NULL "
            "AND job_id IS NOT NULL "
            "AND provider_cost_id IS NOT NULL "
            "AND usage_source IS NOT NULL) "
            "OR (kind = 'admin_credit' "
            "AND delta_nanos > 0 "
            "AND reservation_id IS NULL "
            "AND adjustment_id IS NOT NULL "
            "AND job_id IS NULL "
            "AND provider_cost_id IS NULL "
            "AND usage_source IS NULL) "
            "OR (kind = 'admin_debit' "
            "AND delta_nanos < 0 "
            "AND reservation_id IS NULL "
            "AND adjustment_id IS NOT NULL "
            "AND job_id IS NULL "
            "AND provider_cost_id IS NULL "
            "AND usage_source IS NULL)",
            name="source",
        ),
        ForeignKeyConstraint(
            ["daily_allowance_id", "owner_id"],
            ["ai_daily_allowances.id", "ai_daily_allowances.owner_id"],
            name="fk_ai_quota_ledger_entries_daily_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["reservation_id", "owner_id"],
            ["ai_quota_reservations.id", "ai_quota_reservations.owner_id"],
            name="fk_ai_quota_ledger_entries_reservation_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["adjustment_id", "owner_id"],
            ["ai_quota_admin_adjustments.id", "ai_quota_admin_adjustments.target_owner_id"],
            name="fk_ai_quota_ledger_entries_adjustment_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["provider_cost_id", "job_id"],
            ["ai_provider_costs.id", "ai_provider_costs.job_id"],
            name="fk_ai_quota_ledger_entries_provider_cost_job",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_ai_quota_ledger_entries_usage_reservation",
            "reservation_id",
            unique=True,
            postgresql_where=text("kind = 'usage_charge'"),
        ),
        Index(
            "uq_ai_quota_ledger_entries_adjustment",
            "adjustment_id",
            unique=True,
            postgresql_where=text("adjustment_id IS NOT NULL"),
        ),
        Index(
            "ix_ai_quota_ledger_entries_owner_created",
            "owner_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_ai_quota_ledger_entries_daily_created",
            "daily_allowance_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID]
    daily_allowance_id: Mapped[UUID]
    reservation_id: Mapped[UUID | None]
    adjustment_id: Mapped[UUID | None]
    job_id: Mapped[UUID | None]
    provider_cost_id: Mapped[UUID | None]
    kind: Mapped[str] = mapped_column(String(24))
    delta_nanos: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    usage_source: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
