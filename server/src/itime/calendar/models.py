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


class CalendarSource(Base):
    __tablename__ = "calendar_sources"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('excel_file', 'ics_file', 'screenshot', 'ics_subscription')",
            name="kind",
        ),
        CheckConstraint("length(btrim(name)) > 0", name="nonempty_name"),
        UniqueConstraint("id", "owner_id", name="uq_calendar_sources_id_owner"),
        Index("ix_calendar_sources_owner_created", "owner_id", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(24))
    name: Mapped[str] = mapped_column(String(255))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class CalendarSeries(Base):
    __tablename__ = "calendar_series"
    __table_args__ = (
        CheckConstraint("kind IN ('course', 'fixed')", name="kind"),
        CheckConstraint("length(btrim(name)) > 0", name="nonempty_name"),
        CheckConstraint("length(btrim(time_zone)) > 0", name="nonempty_time_zone"),
        CheckConstraint(
            "source_type IN ('manual', 'import', 'screenshot', 'subscription')",
            name="source_type",
        ),
        CheckConstraint(
            "(source_type = 'manual') = (source_id IS NULL)",
            name="source_reference",
        ),
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
            "recurrence_frequency IN ('none', 'daily', 'weekdays', 'weekly', 'monthly', 'yearly')",
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
            "(recurrence_frequency = 'none' "
            "AND recurrence_interval = 1 "
            "AND recurrence_weekdays = '[]'::jsonb "
            "AND recurrence_month IS NULL "
            "AND recurrence_day_of_month IS NULL "
            "AND recurrence_until IS NULL "
            "AND recurrence_count IS NULL) "
            "OR (recurrence_frequency != 'none' "
            "AND ((recurrence_until IS NULL) != (recurrence_count IS NULL)))",
            name="recurrence_end_shape",
        ),
        CheckConstraint(
            "recurrence_until IS NULL OR recurrence_until >= start_date",
            name="recurrence_until",
        ),
        CheckConstraint(
            "recurrence_frequency != 'weekdays' OR recurrence_interval = 1",
            name="weekdays_interval",
        ),
        CheckConstraint(
            "(recurrence_frequency = 'weekly' "
            "AND jsonb_array_length(recurrence_weekdays) > 0 "
            "AND recurrence_month IS NULL "
            "AND recurrence_day_of_month IS NULL) "
            "OR (recurrence_frequency = 'monthly' "
            "AND recurrence_weekdays = '[]'::jsonb "
            "AND recurrence_month IS NULL "
            "AND recurrence_day_of_month IS NOT NULL) "
            "OR (recurrence_frequency = 'yearly' "
            "AND recurrence_weekdays = '[]'::jsonb "
            "AND recurrence_month IS NOT NULL "
            "AND recurrence_day_of_month IS NOT NULL) "
            "OR (recurrence_frequency IN ('none', 'daily', 'weekdays') "
            "AND recurrence_weekdays = '[]'::jsonb "
            "AND recurrence_month IS NULL "
            "AND recurrence_day_of_month IS NULL)",
            name="recurrence_parameters",
        ),
        CheckConstraint(
            "jsonb_typeof(course_week_numbers) = 'array'",
            name="course_week_numbers",
        ),
        CheckConstraint(
            "ambiguous_offset_minutes IS NULL OR ambiguous_offset_minutes BETWEEN -840 AND 840",
            name="ambiguous_offset_minutes",
        ),
        CheckConstraint(
            "(kind = 'course') = (term_id IS NOT NULL AND course_first_week_monday IS NOT NULL)",
            name="course_term",
        ),
        CheckConstraint(
            "(kind = 'course' "
            "AND recurrence_frequency = 'weekly' "
            "AND recurrence_interval = 1 "
            "AND jsonb_array_length(course_week_numbers) > 0) "
            "OR (kind = 'fixed' AND course_week_numbers = '[]'::jsonb)",
            name="course_recurrence",
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
        ForeignKeyConstraint(
            ["source_id", "owner_id"],
            ["calendar_sources.id", "calendar_sources.owner_id"],
            name="fk_calendar_series_source_owner",
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
    source_id: Mapped[UUID | None]
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


class CalendarImportBatch(Base):
    __tablename__ = "calendar_import_batches"
    __table_args__ = (
        CheckConstraint("format IN ('xlsx', 'ics')", name="format"),
        CheckConstraint(
            "state IN ('ready', 'partially_confirmed', 'completed', 'expired')",
            name="state",
        ),
        CheckConstraint("length(btrim(filename)) > 0", name="nonempty_filename"),
        CheckConstraint("candidate_count BETWEEN 1 AND 500", name="candidate_count"),
        CheckConstraint("length(content_sha256) = 64", name="content_sha256"),
        ForeignKeyConstraint(
            ["source_id", "owner_id"],
            ["calendar_sources.id", "calendar_sources.owner_id"],
            name="fk_calendar_import_batches_source_owner",
            ondelete="CASCADE",
        ),
        UniqueConstraint("id", "owner_id", name="uq_calendar_import_batches_id_owner"),
        UniqueConstraint("source_id", name="uq_calendar_import_batches_source"),
        Index(
            "ix_calendar_import_batches_owner_updated",
            "owner_id",
            "updated_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    source_id: Mapped[UUID]
    format: Mapped[str] = mapped_column(String(8))
    filename: Mapped[str] = mapped_column(String(255))
    content_sha256: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(24), default="ready", server_default="ready")
    candidate_count: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class CalendarImportCandidate(Base):
    __tablename__ = "calendar_import_candidates"
    __table_args__ = (
        CheckConstraint(
            "state IN ('valid', 'invalid', 'duplicate', 'confirmed', 'skipped')",
            name="state",
        ),
        CheckConstraint("position BETWEEN 1 AND 500", name="position"),
        CheckConstraint("jsonb_typeof(raw_data) = 'object'", name="raw_data"),
        CheckConstraint(
            "draft IS NULL OR jsonb_typeof(draft) = 'object'",
            name="draft",
        ),
        CheckConstraint("jsonb_typeof(warnings) = 'array'", name="warnings"),
        CheckConstraint(
            "error_details IS NULL OR jsonb_typeof(error_details) = 'object'",
            name="error_details",
        ),
        CheckConstraint(
            "state NOT IN ('valid', 'duplicate', 'confirmed') OR draft IS NOT NULL",
            name="draft_state",
        ),
        CheckConstraint(
            "(state = 'confirmed') = (confirmed_series_id IS NOT NULL)",
            name="confirmed_series",
        ),
        ForeignKeyConstraint(
            ["batch_id", "owner_id"],
            ["calendar_import_batches.id", "calendar_import_batches.owner_id"],
            name="fk_calendar_import_candidates_batch_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["duplicate_series_id", "owner_id"],
            ["calendar_series.id", "calendar_series.owner_id"],
            name="fk_calendar_import_candidates_duplicate_series_owner",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["confirmed_series_id", "owner_id"],
            ["calendar_series.id", "calendar_series.owner_id"],
            name="fk_calendar_import_candidates_confirmed_series_owner",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", "owner_id", name="uq_calendar_import_candidates_id_owner"),
        UniqueConstraint(
            "batch_id",
            "position",
            name="uq_calendar_import_candidates_batch_position",
        ),
        Index(
            "ix_calendar_import_candidates_owner_batch",
            "owner_id",
            "batch_id",
            "position",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    batch_id: Mapped[UUID]
    owner_id: Mapped[UUID]
    position: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(16))
    raw_data: Mapped[dict] = mapped_column(JSONB)
    draft: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    identity_key: Mapped[str | None] = mapped_column(String(64))
    content_fingerprint: Mapped[str | None] = mapped_column(String(64))
    external_uid: Mapped[str | None] = mapped_column(String(512))
    external_recurrence_id: Mapped[str | None] = mapped_column(String(255))
    warnings: Mapped[list[dict]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(String(500))
    error_details: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    duplicate_series_id: Mapped[UUID | None]
    confirmed_series_id: Mapped[UUID | None]
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class CalendarImportIdentity(Base):
    __tablename__ = "calendar_import_identities"
    __table_args__ = (
        CheckConstraint("format IN ('xlsx', 'ics')", name="format"),
        CheckConstraint("length(identity_key) = 64", name="identity_key"),
        CheckConstraint("length(content_fingerprint) = 64", name="content_fingerprint"),
        ForeignKeyConstraint(
            ["series_id", "owner_id"],
            ["calendar_series.id", "calendar_series.owner_id"],
            name="fk_calendar_import_identities_series_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["source_id", "owner_id"],
            ["calendar_sources.id", "calendar_sources.owner_id"],
            name="fk_calendar_import_identities_source_owner",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "owner_id",
            "identity_key",
            name="uq_calendar_import_identities_owner_key",
        ),
        UniqueConstraint(
            "owner_id",
            "series_id",
            name="uq_calendar_import_identities_owner_series",
        ),
        Index(
            "ix_calendar_import_identities_owner_series",
            "owner_id",
            "series_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID]
    source_id: Mapped[UUID]
    series_id: Mapped[UUID]
    format: Mapped[str] = mapped_column(String(8))
    identity_key: Mapped[str] = mapped_column(String(64))
    content_fingerprint: Mapped[str] = mapped_column(String(64))
    external_uid: Mapped[str | None] = mapped_column(String(512))
    external_recurrence_id: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
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
            "AND moved_time_zone IS NULL "
            "AND moved_ambiguous_offset_minutes IS NULL)",
            name="non_moved_fields",
        ),
        CheckConstraint(
            "(action = 'moved') = (moved_time_zone IS NOT NULL)",
            name="moved_time_zone",
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
    moved_time_zone: Mapped[str | None] = mapped_column(String(64))
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


class CalendarAddition(Base):
    __tablename__ = "calendar_additions"
    __table_args__ = (
        CheckConstraint("status IN ('scheduled', 'cancelled')", name="status"),
        CheckConstraint(
            "length(btrim(occurrence_key)) > 0",
            name="nonempty_occurrence_key",
        ),
        CheckConstraint(
            "length(btrim(source_occurrence_key)) > 0",
            name="nonempty_source_occurrence_key",
        ),
        CheckConstraint("length(btrim(time_zone)) > 0", name="nonempty_time_zone"),
        CheckConstraint("end_day_offset BETWEEN 0 AND 1", name="end_day_offset"),
        CheckConstraint(
            "ambiguous_offset_minutes IS NULL OR ambiguous_offset_minutes BETWEEN -840 AND 840",
            name="ambiguous_offset_minutes",
        ),
        CheckConstraint(
            "override_name IS NULL OR length(btrim(override_name)) > 0",
            name="nonempty_override_name",
        ),
        ForeignKeyConstraint(
            ["series_id", "owner_id"],
            ["calendar_series.id", "calendar_series.owner_id"],
            name="fk_calendar_additions_series_owner",
            ondelete="CASCADE",
        ),
        UniqueConstraint("id", "owner_id", name="uq_calendar_additions_id_owner"),
        UniqueConstraint(
            "series_id",
            "occurrence_key",
            name="uq_calendar_additions_series_occurrence",
        ),
        UniqueConstraint(
            "series_id",
            "source_original_start_date",
            name="uq_calendar_additions_series_source_date",
        ),
        Index(
            "ix_calendar_additions_owner_series",
            "owner_id",
            "series_id",
            "source_original_start_date",
        ),
        Index(
            "ix_calendar_additions_owner_local_date",
            "owner_id",
            "local_start_date",
            "series_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    series_id: Mapped[UUID]
    owner_id: Mapped[UUID]
    occurrence_key: Mapped[str] = mapped_column(String(80))
    source_occurrence_key: Mapped[str] = mapped_column(String(64))
    source_original_start_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(
        String(12),
        default="scheduled",
        server_default="scheduled",
    )
    local_start_date: Mapped[date] = mapped_column(Date)
    starts_at: Mapped[time] = mapped_column(Time(timezone=False))
    ends_at: Mapped[time] = mapped_column(Time(timezone=False))
    end_day_offset: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    time_zone: Mapped[str] = mapped_column(String(64))
    ambiguous_offset_minutes: Mapped[int | None] = mapped_column(Integer)
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
