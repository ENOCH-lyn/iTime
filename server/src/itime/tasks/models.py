from datetime import date, datetime, time
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
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


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint("kind IN ('project', 'goal')", name="kind"),
        CheckConstraint("length(btrim(name)) > 0", name="nonempty_name"),
        UniqueConstraint("id", "owner_id", name="uq_projects_id_owner"),
        Index("ix_projects_owner_updated", "owner_id", "updated_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(10))
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint("length(btrim(title)) > 0", name="nonempty_title"),
        CheckConstraint("priority BETWEEN 0 AND 3", name="priority"),
        CheckConstraint("status IN ('open', 'completed')", name="status"),
        CheckConstraint(
            "estimated_minutes IS NULL OR estimated_minutes BETWEEN 1 AND 525600",
            name="estimated_minutes",
        ),
        CheckConstraint(
            "estimate_source IS NULL OR estimate_source IN ('user', 'ai')",
            name="estimate_source",
        ),
        CheckConstraint(
            "(estimated_minutes IS NULL) = (estimate_source IS NULL)",
            name="estimate_source_consistency",
        ),
        CheckConstraint(
            "(status = 'completed') = (completed_at IS NOT NULL)",
            name="completion_consistency",
        ),
        CheckConstraint(
            "(deleted_at IS NULL AND purge_after IS NULL AND purged_at IS NULL) "
            "OR (deleted_at IS NOT NULL AND purge_after IS NOT NULL "
            "AND purge_after >= deleted_at "
            "AND (purged_at IS NULL OR purged_at >= deleted_at))",
            name="lifecycle_consistency",
        ),
        UniqueConstraint("id", "owner_id", name="uq_tasks_id_owner"),
        ForeignKeyConstraint(
            ["project_id", "owner_id"],
            ["projects.id", "projects.owner_id"],
            name="fk_tasks_project_owner",
            ondelete="RESTRICT",
        ),
        Index("ix_tasks_owner_updated", "owner_id", "updated_at", "id"),
        Index("ix_tasks_owner_status_updated", "owner_id", "status", "updated_at", "id"),
        Index("ix_tasks_owner_project_updated", "owner_id", "project_id", "updated_at", "id"),
        Index("ix_tasks_owner_priority_updated", "owner_id", "priority", "updated_at", "id"),
        Index(
            "ix_tasks_owner_lifecycle_updated",
            "owner_id",
            "deleted_at",
            "archived_at",
            "updated_at",
            "id",
        ),
        Index(
            "ix_tasks_purge_due",
            "purge_after",
            "id",
            postgresql_where=text("deleted_at IS NOT NULL AND purged_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    project_id: Mapped[UUID | None]
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    estimated_minutes: Mapped[int | None]
    estimate_source: Mapped[str | None] = mapped_column(String(10))
    priority: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    status: Mapped[str] = mapped_column(String(12), default="open", server_default="open")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purge_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TaskSchedule(Base):
    __tablename__ = "task_schedules"
    __table_args__ = (
        CheckConstraint(
            "due_kind IN ('none', 'date', 'datetime')",
            name="due_kind",
        ),
        CheckConstraint(
            "(due_kind = 'none' AND start_date IS NULL "
            "AND last_occurrence_date IS NULL "
            "AND due_local_time IS NULL AND due_at IS NULL AND time_zone IS NULL) "
            "OR (due_kind = 'date' AND start_date IS NOT NULL "
            "AND last_occurrence_date IS NOT NULL "
            "AND due_local_time IS NULL AND due_at IS NULL AND time_zone IS NOT NULL) "
            "OR (due_kind = 'datetime' AND start_date IS NOT NULL "
            "AND last_occurrence_date IS NOT NULL "
            "AND due_local_time IS NOT NULL AND due_at IS NOT NULL "
            "AND time_zone IS NOT NULL)",
            name="due_fields",
        ),
        CheckConstraint(
            "due_kind != 'none' OR recurrence_frequency = 'none'",
            name="no_due_no_recurrence",
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
            "last_occurrence_date IS NULL OR last_occurrence_date >= start_date",
            name="last_occurrence_date",
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
            "ambiguous_offset_minutes IS NULL OR "
            "ambiguous_offset_minutes BETWEEN -840 AND 840",
            name="ambiguous_offset_minutes",
        ),
        CheckConstraint(
            "minimum_block_minutes BETWEEN 1 AND 1440",
            name="minimum_block_minutes",
        ),
        ForeignKeyConstraint(
            ["task_id", "owner_id"],
            ["tasks.id", "tasks.owner_id"],
            name="fk_task_schedules_task_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["predecessor_task_id", "owner_id"],
            ["tasks.id", "tasks.owner_id"],
            name="fk_task_schedules_predecessor_owner",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_task_schedules_owner_start",
            "owner_id",
            "start_date",
            "last_occurrence_date",
            "task_id",
        ),
        Index(
            "uq_task_schedules_predecessor",
            "predecessor_task_id",
            unique=True,
            postgresql_where=text("predecessor_task_id IS NOT NULL"),
        ),
    )

    task_id: Mapped[UUID] = mapped_column(primary_key=True)
    owner_id: Mapped[UUID]
    predecessor_task_id: Mapped[UUID | None]
    due_kind: Mapped[str] = mapped_column(String(10), default="none", server_default="none")
    start_date: Mapped[date | None] = mapped_column(Date)
    last_occurrence_date: Mapped[date | None] = mapped_column(Date)
    due_local_time: Mapped[time | None] = mapped_column(Time(timezone=False))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    time_zone: Mapped[str | None] = mapped_column(String(64))
    ambiguous_offset_minutes: Mapped[int | None] = mapped_column(Integer)
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
    splittable: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    minimum_block_minutes: Mapped[int] = mapped_column(
        Integer,
        default=15,
        server_default="15",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TaskOccurrenceState(Base):
    __tablename__ = "task_occurrence_states"
    __table_args__ = (
        CheckConstraint("kind IN ('base', 'makeup')", name="kind"),
        CheckConstraint(
            "status IN ('open', 'completed', 'skipped')",
            name="status",
        ),
        CheckConstraint(
            "(status = 'completed') = (completed_at IS NOT NULL)",
            name="completion_consistency",
        ),
        CheckConstraint(
            "(kind = 'makeup') = "
            "(makeup_for_key IS NOT NULL AND makeup_for_date IS NOT NULL)",
            name="makeup_consistency",
        ),
        CheckConstraint(
            "(effective_local_time IS NULL) = (effective_at IS NULL)",
            name="effective_time_consistency",
        ),
        CheckConstraint(
            "(kind = 'makeup' OR rescheduled) = (effective_time_zone IS NOT NULL)",
            name="effective_time_zone_consistency",
        ),
        CheckConstraint(
            "jsonb_typeof(completed_step_ids) = 'array'",
            name="completed_step_ids",
        ),
        CheckConstraint("step_count_snapshot >= 0", name="step_count_snapshot"),
        CheckConstraint(
            "jsonb_array_length(completed_step_ids) <= step_count_snapshot",
            name="completed_steps_within_snapshot",
        ),
        ForeignKeyConstraint(
            ["task_id", "owner_id"],
            ["tasks.id", "tasks.owner_id"],
            name="fk_task_occurrence_states_task_owner",
            ondelete="CASCADE",
        ),
        UniqueConstraint("id", "owner_id", name="uq_task_occurrence_states_id_owner"),
        UniqueConstraint(
            "task_id",
            "occurrence_key",
            name="uq_task_occurrence_states_task_occurrence",
        ),
        Index(
            "uq_task_occurrence_states_task_base_date",
            "task_id",
            "original_date",
            unique=True,
            postgresql_where=text("kind = 'base'"),
        ),
        Index(
            "ix_task_occurrence_states_owner_effective",
            "owner_id",
            "effective_date",
            "task_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    task_id: Mapped[UUID]
    owner_id: Mapped[UUID]
    occurrence_key: Mapped[str] = mapped_column(String(80))
    original_date: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(10), default="base", server_default="base")
    effective_date: Mapped[date] = mapped_column(Date)
    effective_local_time: Mapped[time | None] = mapped_column(Time(timezone=False))
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    effective_time_zone: Mapped[str | None] = mapped_column(String(64))
    ambiguous_offset_minutes: Mapped[int | None] = mapped_column(Integer)
    rescheduled: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("false"),
    )
    status: Mapped[str] = mapped_column(String(12), default="open", server_default="open")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_step_ids: Mapped[list[str]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    step_count_snapshot: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
    )
    makeup_for_key: Mapped[str | None] = mapped_column(String(80))
    makeup_for_date: Mapped[date | None] = mapped_column(Date)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Label(Base):
    __tablename__ = "labels"
    __table_args__ = (
        CheckConstraint("length(btrim(name)) > 0", name="nonempty_name"),
        CheckConstraint("normalized_name = lower(normalized_name)", name="normalized_name"),
        UniqueConstraint("id", "owner_id", name="uq_labels_id_owner"),
        UniqueConstraint("owner_id", "normalized_name", name="uq_labels_owner_normalized"),
        Index("ix_labels_owner_name", "owner_id", "normalized_name", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(40))
    normalized_name: Mapped[str] = mapped_column(String(40))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TaskLabel(Base):
    __tablename__ = "task_labels"
    __table_args__ = (
        ForeignKeyConstraint(
            ["task_id", "owner_id"],
            ["tasks.id", "tasks.owner_id"],
            name="fk_task_labels_task_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["label_id", "owner_id"],
            ["labels.id", "labels.owner_id"],
            name="fk_task_labels_label_owner",
            ondelete="CASCADE",
        ),
        Index("ix_task_labels_owner_label", "owner_id", "label_id", "task_id"),
    )

    task_id: Mapped[UUID] = mapped_column(primary_key=True)
    label_id: Mapped[UUID] = mapped_column(primary_key=True)
    owner_id: Mapped[UUID]


class TaskStep(Base):
    __tablename__ = "task_steps"
    __table_args__ = (
        CheckConstraint("length(btrim(title)) > 0", name="nonempty_title"),
        CheckConstraint("position >= 0", name="position"),
        ForeignKeyConstraint(
            ["task_id", "owner_id"],
            ["tasks.id", "tasks.owner_id"],
            name="fk_task_steps_task_owner",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "task_id",
            "position",
            name="uq_task_steps_task_position",
            deferrable=True,
            initially="DEFERRED",
        ),
        Index("ix_task_steps_owner_task", "owner_id", "task_id", "position"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    task_id: Mapped[UUID]
    owner_id: Mapped[UUID]
    title: Mapped[str] = mapped_column(String(300))
    position: Mapped[int] = mapped_column(Integer)
    completed: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TaskLink(Base):
    __tablename__ = "task_links"
    __table_args__ = (
        CheckConstraint("position >= 0", name="position"),
        CheckConstraint("url ~ '^https?://'", name="http_url"),
        ForeignKeyConstraint(
            ["task_id", "owner_id"],
            ["tasks.id", "tasks.owner_id"],
            name="fk_task_links_task_owner",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "task_id",
            "position",
            name="uq_task_links_task_position",
            deferrable=True,
            initially="DEFERRED",
        ),
        Index("ix_task_links_owner_task", "owner_id", "task_id", "position"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    task_id: Mapped[UUID]
    owner_id: Mapped[UUID]
    title: Mapped[str] = mapped_column(String(120), default="", server_default="")
    url: Mapped[str] = mapped_column(String(2048))
    position: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserOperation(Base):
    __tablename__ = "user_operations"
    __table_args__ = (Index("ix_user_operations_owner_created", "owner_id", "created_at"),)

    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    operation_id: Mapped[UUID] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(String(120))
    request_hash: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict | None] = mapped_column(JSONB)
    response_purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChangeEvent(Base):
    __tablename__ = "change_events"
    __table_args__ = (
        CheckConstraint(
            "entity_type IN "
            "('project', 'task', 'step', 'label', 'time_settings', 'term', "
            "'calendar_series', 'calendar_exception', 'task_occurrence', "
            "'calendar_addition', 'calendar_source', 'privacy_settings', 'plan', "
            "'focus_session', 'difficulty', 'review_session', 'review_report', "
            "'review_annotation', 'memory', 'memory_settings', 'conversation')",
            name="entity_type",
        ),
        CheckConstraint(
            "action IN "
            "('created', 'updated', 'completed', 'reopened', 'deleted', "
            "'skipped', 'rescheduled', 'archived', 'unarchived', 'restored', "
            "'purged', 'started', 'paused', 'resumed', 'finished', 'abandoned', "
            "'revised')",
            name="action",
        ),
        Index("ix_change_events_owner_sequence", "owner_id", "sequence"),
    )

    sequence: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    entity_type: Mapped[str] = mapped_column(String(20))
    entity_id: Mapped[UUID]
    action: Mapped[str] = mapped_column(String(20))
    entity_version: Mapped[int] = mapped_column(Integer)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
