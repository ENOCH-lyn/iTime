from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
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
    response: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChangeEvent(Base):
    __tablename__ = "change_events"
    __table_args__ = (
        CheckConstraint(
            "entity_type IN "
            "('project', 'task', 'step', 'label', 'time_settings', 'term', "
            "'calendar_series', 'calendar_exception')",
            name="entity_type",
        ),
        CheckConstraint(
            "action IN ('created', 'updated', 'completed', 'reopened', 'deleted')",
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
