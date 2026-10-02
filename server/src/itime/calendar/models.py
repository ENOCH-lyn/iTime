from datetime import date, datetime, time
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
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


class CalendarSeries(Base):
    __tablename__ = "calendar_series"
    __table_args__ = (
        CheckConstraint("kind IN ('course', 'fixed')", name="kind"),
        CheckConstraint("length(btrim(name)) > 0", name="nonempty_name"),
        CheckConstraint("length(btrim(time_zone)) > 0", name="nonempty_time_zone"),
        CheckConstraint("source_type = 'manual'", name="source_type"),
        CheckConstraint("end_day_offset BETWEEN 0 AND 1", name="end_day_offset"),
        CheckConstraint(
            "(start_period_number IS NULL) = (end_period_number IS NULL)",
            name="period_reference_consistency",
        ),
        CheckConstraint(
            "start_period_number IS NULL OR "
            "(start_period_number BETWEEN 1 AND 99 "
            "AND end_period_number BETWEEN start_period_number AND 99)",
            name="period_reference_range",
        ),
        CheckConstraint(
            "recurrence_frequency IN "
            "('none', 'daily', 'weekdays', 'weekly', 'monthly', 'yearly')",
            name="recurrence_frequency",
        ),
        CheckConstraint(
            "recurrence_interval BETWEEN 1 AND 366",
            name="recurrence_interval",
        ),
        CheckConstraint(
            "jsonb_typeof(recurrence_weekdays) = 'array'",
            name="recurrence_weekdays",
        ),
        CheckConstraint(
            "recurrence_month IS NULL OR recurrence_month BETWEEN 1 AND 12",
            name="recurrence_month",
        ),
        CheckConstraint(
            "recurrence_day_of_month IS NULL OR recurrence_day_of_month BETWEEN 1 AND 31",
            name="recurrence_day_of_month",
        ),
        CheckConstraint(
            "recurrence_count IS NULL OR recurrence_count BETWEEN 1 AND 366",
            name="recurrence_count",
        ),
        CheckConstraint(
            "jsonb_typeof(course_week_numbers) = 'array'",
            name="course_week_numbers",
        ),
        CheckConstraint(
            "ambiguous_offset_minutes IS NULL OR "
            "ambiguous_offset_minutes BETWEEN -840 AND 840",
            name="ambiguous_offset_minutes",
        ),
        CheckConstraint(
            "(kind = 'course') = "
            "(term_id IS NOT NULL AND course_first_week_monday IS NOT NULL)",
            name="course_term",
        ),
        UniqueConstraint("id", "owner_id", name="uq_calendar_series_id_owner"),
        ForeignKeyConstraint(
            ["term_id", "owner_id"],
            ["academic_terms.id", "academic_terms.owner_id"],
            name="fk_calendar_series_term_owner",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["predecessor_id", "owner_id"],
            ["calendar_series.id", "calendar_series.owner_id"],
            name="fk_calendar_series_predecessor_owner",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_calendar_series_owner_updated",
            "owner_id",
            "updated_at",
            "id",
        ),
        Index(
            "ix_calendar_series_owner_dates",
            "owner_id",
            "start_date",
            "recurrence_until",
            "id",
        ),
        Index(
            "ix_calendar_series_owner_term_updated",
            "owner_id",
            "term_id",
            "updated_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    term_id: Mapped[UUID | None]
    predecessor_id: Mapped[UUID | None]
    kind: Mapped[str] = mapped_column(String(10))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    location: Mapped[str] = mapped_column(String(300), default="", server_default="")
    busy: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    source_type: Mapped[str] = mapped_column(String(20), default="manual", server_default="manual")
    time_zone: Mapped[str] = mapped_column(String(64))
    start_date: Mapped[date] = mapped_column(Date)
    starts_at: Mapped[time] = mapped_column(Time(timezone=False))
    ends_at: Mapped[time] = mapped_column(Time(timezone=False))
    end_day_offset: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    start_period_number: Mapped[int | None] = mapped_column(Integer)
    end_period_number: Mapped[int | None] = mapped_column(Integer)
    recurrence_frequency: Mapped[str] = mapped_column(
        String(12),
        default="none",
        server_default="none",
    )
    recurrence_interval: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    recurrence_weekdays: Mapped[list[int]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    recurrence_month: Mapped[int | None] = mapped_column(Integer)
    recurrence_day_of_month: Mapped[int | None] = mapped_column(Integer)
    recurrence_until: Mapped[date | None] = mapped_column(Date)
    recurrence_count: Mapped[int | None] = mapped_column(Integer)
    course_first_week_monday: Mapped[date | None] = mapped_column(Date)
    course_week_numbers: Mapped[list[int]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    ambiguous_offset_minutes: Mapped[int | None] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class CalendarException(Base):
    __tablename__ = "calendar_exceptions"
    __table_args__ = (
        CheckConstraint(
            "action IN ('cancelled', 'moved', 'overridden')",
            name="action",
        ),
        CheckConstraint(
            "(action = 'moved') = "
            "(moved_start_date IS NOT NULL AND moved_starts_at IS NOT NULL "
            "AND moved_ends_at IS NOT NULL AND moved_end_day_offset IS NOT NULL)",
            name="moved_fields",
        ),
        CheckConstraint(
            "action = 'moved' OR "
            "(moved_start_date IS NULL AND moved_starts_at IS NULL "
            "AND moved_ends_at IS NULL AND moved_end_day_offset IS NULL "
            "AND moved_ambiguous_offset_minutes IS NULL)",
            name="non_moved_fields",
        ),
        CheckConstraint(
            "moved_end_day_offset IS NULL OR moved_end_day_offset BETWEEN 0 AND 1",
            name="moved_end_day_offset",
        ),
        CheckConstraint(
            "moved_ambiguous_offset_minutes IS NULL OR "
            "moved_ambiguous_offset_minutes BETWEEN -840 AND 840",
            name="moved_ambiguous_offset_minutes",
        ),
        CheckConstraint(
            "(action = 'overridden') = "
            "(override_name IS NOT NULL OR override_description IS NOT NULL "
            "OR override_location IS NOT NULL OR override_busy IS NOT NULL)",
            name="override_fields",
        ),
        CheckConstraint(
            "override_name IS NULL OR length(btrim(override_name)) > 0",
            name="nonempty_override_name",
        ),
        ForeignKeyConstraint(
            ["series_id", "owner_id"],
            ["calendar_series.id", "calendar_series.owner_id"],
            name="fk_calendar_exceptions_series_owner",
            ondelete="CASCADE",
        ),
        UniqueConstraint("id", "owner_id", name="uq_calendar_exceptions_id_owner"),
        UniqueConstraint(
            "series_id",
            "occurrence_key",
            name="uq_calendar_exceptions_series_occurrence",
        ),
        UniqueConstraint(
            "series_id",
            "original_start_date",
            name="uq_calendar_exceptions_series_original_date",
        ),
        Index(
            "ix_calendar_exceptions_owner_series",
            "owner_id",
            "series_id",
            "original_start_date",
        ),
        Index(
            "ix_calendar_exceptions_owner_moved_date",
            "owner_id",
            "moved_start_date",
            "series_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    series_id: Mapped[UUID]
    owner_id: Mapped[UUID]
    occurrence_key: Mapped[str] = mapped_column(String(64))
    original_start_date: Mapped[date] = mapped_column(Date)
    action: Mapped[str] = mapped_column(String(12))
    moved_start_date: Mapped[date | None] = mapped_column(Date)
    moved_starts_at: Mapped[time | None] = mapped_column(Time(timezone=False))
    moved_ends_at: Mapped[time | None] = mapped_column(Time(timezone=False))
    moved_end_day_offset: Mapped[int | None] = mapped_column(Integer)
    moved_ambiguous_offset_minutes: Mapped[int | None] = mapped_column(Integer)
    override_name: Mapped[str | None] = mapped_column(String(200))
    override_description: Mapped[str | None] = mapped_column(Text)
    override_location: Mapped[str | None] = mapped_column(String(300))
    override_busy: Mapped[bool | None] = mapped_column(Boolean)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
