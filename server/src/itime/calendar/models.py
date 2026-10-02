from datetime import date, datetime, time
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from itime.database import Base


class UserTimeSettings(Base):
    __tablename__ = "user_time_settings"
    __table_args__ = (
        CheckConstraint("length(btrim(time_zone)) > 0", name="nonempty_time_zone"),
        CheckConstraint("week_starts_on = 1", name="week_starts_on"),
        CheckConstraint(
            "activity_day_start_minute = 240",
            name="activity_day_start_minute",
        ),
    )

    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    time_zone: Mapped[str] = mapped_column(String(64))
    week_starts_on: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    activity_day_start_minute: Mapped[int] = mapped_column(
        Integer,
        default=240,
        server_default="240",
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


class AcademicTerm(Base):
    __tablename__ = "academic_terms"
    __table_args__ = (
        CheckConstraint("length(btrim(name)) > 0", name="nonempty_name"),
        CheckConstraint("length(btrim(time_zone)) > 0", name="nonempty_time_zone"),
        CheckConstraint(
            "end_date - start_date BETWEEN 0 AND 399",
            name="date_range",
        ),
        CheckConstraint(
            "start_date - first_week_monday BETWEEN 0 AND 6",
            name="first_week_range",
        ),
        CheckConstraint(
            "EXTRACT(ISODOW FROM first_week_monday) = 1",
            name="first_week_monday",
        ),
        UniqueConstraint("id", "owner_id", name="uq_academic_terms_id_owner"),
        Index("ix_academic_terms_owner_updated", "owner_id", "updated_at", "id"),
        Index(
            "ix_academic_terms_owner_dates",
            "owner_id",
            "start_date",
            "end_date",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    first_week_monday: Mapped[date] = mapped_column(Date)
    time_zone: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class TermPeriod(Base):
    __tablename__ = "term_periods"
    __table_args__ = (
        CheckConstraint("period_number BETWEEN 1 AND 99", name="period_number"),
        CheckConstraint("length(btrim(name)) > 0", name="nonempty_name"),
        CheckConstraint("normalized_name = lower(normalized_name)", name="normalized_name"),
        CheckConstraint("starts_at < ends_at", name="time_range"),
        ForeignKeyConstraint(
            ["term_id", "owner_id"],
            ["academic_terms.id", "academic_terms.owner_id"],
            name="fk_term_periods_term_owner",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "term_id",
            "normalized_name",
            name="uq_term_periods_term_normalized_name",
        ),
        Index(
            "ix_term_periods_owner_term_number",
            "owner_id",
            "term_id",
            "period_number",
        ),
    )

    term_id: Mapped[UUID] = mapped_column(primary_key=True)
    period_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[UUID]
    name: Mapped[str] = mapped_column(String(80))
    normalized_name: Mapped[str] = mapped_column(String(80))
    starts_at: Mapped[time] = mapped_column(Time(timezone=False))
    ends_at: Mapped[time] = mapped_column(Time(timezone=False))
