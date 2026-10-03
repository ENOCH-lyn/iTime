from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
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


class FocusSession(Base):
    __tablename__ = "focus_sessions"
    __table_args__ = (
        CheckConstraint("source IN ('timer', 'manual')", name="source"),
        CheckConstraint(
            "char_length(btrim(title_snapshot)) BETWEEN 1 AND 200",
            name="title",
        ),
        CheckConstraint(
            "task_title_snapshot IS NULL "
            "OR char_length(btrim(task_title_snapshot)) BETWEEN 1 AND 200",
            name="task_title",
        ),
        CheckConstraint(
            "jsonb_typeof(step_snapshots) = 'array' "
            "AND jsonb_array_length(step_snapshots) <= 100",
            name="step_snapshots",
        ),
        CheckConstraint(
            "occurrence_key IS NULL "
            "OR char_length(btrim(occurrence_key)) BETWEEN 1 AND 120",
            name="occurrence_key",
        ),
        CheckConstraint(
            "(task_id IS NULL AND occurrence_key IS NULL "
            "AND scheduled_block_id IS NULL AND task_title_snapshot IS NULL) "
            "OR (task_id IS NOT NULL AND task_title_snapshot IS NOT NULL)",
            name="task_reference",
        ),
        CheckConstraint(
            "(mode = 'stopwatch' AND target_seconds IS NULL) "
            "OR (mode = 'countdown' AND target_seconds BETWEEN 60 AND 86400)",
            name="mode_target",
        ),
        CheckConstraint(
            "status IN ('running', 'paused', 'finished', 'abandoned')",
            name="status",
        ),
        CheckConstraint(
            "(status = 'running' AND ended_at IS NULL "
            "AND active_started_at IS NOT NULL "
            "AND effective_started_at IS NULL "
            "AND effective_ended_at IS NULL "
            "AND effective_duration_seconds IS NULL) "
            "OR (status = 'paused' AND ended_at IS NULL "
            "AND active_started_at IS NULL "
            "AND effective_started_at IS NULL "
            "AND effective_ended_at IS NULL "
            "AND effective_duration_seconds IS NULL) "
            "OR (status IN ('finished', 'abandoned') "
            "AND ended_at IS NOT NULL AND active_started_at IS NULL "
            "AND effective_started_at IS NOT NULL "
            "AND effective_ended_at IS NOT NULL "
            "AND effective_duration_seconds IS NOT NULL)",
            name="state",
        ),
        CheckConstraint(
            "source = 'timer' OR status = 'finished'",
            name="manual_terminal",
        ),
        CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at",
            name="raw_interval",
        ),
        CheckConstraint(
            "active_started_at IS NULL OR active_started_at >= started_at",
            name="active_interval",
        ),
        CheckConstraint("accumulated_seconds >= 0", name="accumulated_seconds"),
        CheckConstraint(
            "effective_started_at IS NULL "
            "OR effective_ended_at >= effective_started_at",
            name="effective_interval",
        ),
        CheckConstraint(
            "effective_duration_seconds IS NULL "
            "OR effective_duration_seconds >= 0",
            name="effective_duration",
        ),
        CheckConstraint("length(btrim(time_zone)) > 0", name="time_zone"),
        CheckConstraint("version >= 1", name="version"),
        ForeignKeyConstraint(
            ["task_id", "owner_id"],
            ["tasks.id", "tasks.owner_id"],
            name="fk_focus_sessions_task_owner",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["scheduled_block_id", "owner_id"],
            ["scheduled_blocks.id", "scheduled_blocks.owner_id"],
            name="fk_focus_sessions_scheduled_block_owner",
            deferrable=True,
            initially="DEFERRED",
        ),
        UniqueConstraint("id", "owner_id", name="uq_focus_sessions_id_owner"),
        Index(
            "ix_focus_sessions_owner_started",
            "owner_id",
            "started_at",
            "id",
        ),
        Index(
            "ix_focus_sessions_owner_updated",
            "owner_id",
            "updated_at",
            "id",
        ),
        Index(
            "uq_focus_sessions_owner_open",
            "owner_id",
            unique=True,
            postgresql_where=text("status IN ('running', 'paused')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    source: Mapped[str] = mapped_column(String(8))
    task_id: Mapped[UUID | None]
    occurrence_key: Mapped[str | None] = mapped_column(String(120))
    scheduled_block_id: Mapped[UUID | None]
    title_snapshot: Mapped[str] = mapped_column(String(200))
    task_title_snapshot: Mapped[str | None] = mapped_column(String(200))
    step_snapshots: Mapped[list[dict]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    mode: Mapped[str] = mapped_column(String(10))
    target_seconds: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    accumulated_seconds: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        server_default="0",
    )
    effective_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    effective_ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    effective_duration_seconds: Mapped[int | None] = mapped_column(BigInteger)
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


class FocusEvent(Base):
    __tablename__ = "focus_events"
    __table_args__ = (
        CheckConstraint("sequence >= 1", name="sequence"),
        CheckConstraint(
            "kind IN "
            "('start', 'pause', 'resume', 'finish', 'abandon', "
            "'progress', 'task_completed')",
            name="kind",
        ),
        CheckConstraint("source IN ('online', 'offline')", name="source"),
        CheckConstraint("jsonb_typeof(details) = 'object'", name="details"),
        ForeignKeyConstraint(
            ["focus_session_id", "owner_id"],
            ["focus_sessions.id", "focus_sessions.owner_id"],
            name="fk_focus_events_focus_session_owner",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "focus_session_id",
            "sequence",
            name="uq_focus_events_session_sequence",
        ),
        Index(
            "ix_focus_events_owner_session_sequence",
            "owner_id",
            "focus_session_id",
            "sequence",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID]
    focus_session_id: Mapped[UUID]
    sequence: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    client_recorded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    source: Mapped[str] = mapped_column(String(8))
    details: Mapped[dict] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class ExecutionRevision(Base):
    __tablename__ = "execution_revisions"
    __table_args__ = (
        CheckConstraint("session_version >= 2", name="session_version"),
        CheckConstraint("jsonb_typeof(before_data) = 'object'", name="before_data"),
        CheckConstraint("jsonb_typeof(after_data) = 'object'", name="after_data"),
        CheckConstraint(
            "char_length(btrim(reason)) BETWEEN 1 AND 500",
            name="reason",
        ),
        ForeignKeyConstraint(
            ["focus_session_id", "owner_id"],
            ["focus_sessions.id", "focus_sessions.owner_id"],
            name="fk_execution_revisions_focus_session_owner",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "focus_session_id",
            "session_version",
            name="uq_execution_revisions_session_version",
        ),
        Index(
            "ix_execution_revisions_owner_session_version",
            "owner_id",
            "focus_session_id",
            "session_version",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID]
    focus_session_id: Mapped[UUID]
    session_version: Mapped[int] = mapped_column(Integer)
    before_data: Mapped[dict] = mapped_column(JSONB)
    after_data: Mapped[dict] = mapped_column(JSONB)
    reason: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class DifficultyRecord(Base):
    __tablename__ = "difficulty_records"
    __table_args__ = (
        CheckConstraint(
            "occurrence_key IS NULL "
            "OR char_length(btrim(occurrence_key)) BETWEEN 1 AND 120",
            name="occurrence_key",
        ),
        CheckConstraint(
            "task_title_snapshot IS NULL "
            "OR char_length(btrim(task_title_snapshot)) BETWEEN 1 AND 200",
            name="task_title",
        ),
        CheckConstraint(
            "category IS NULL OR char_length(btrim(category)) BETWEEN 1 AND 40",
            name="category",
        ),
        CheckConstraint(
            "emotion IS NULL OR char_length(btrim(emotion)) BETWEEN 1 AND 40",
            name="emotion",
        ),
        CheckConstraint(
            "(purged_at IS NULL "
            "AND content IS NOT NULL "
            "AND char_length(btrim(content)) BETWEEN 1 AND 2000) "
            "OR (purged_at IS NOT NULL "
            "AND content IS NULL "
            "AND category IS NULL "
            "AND emotion IS NULL "
            "AND task_id IS NULL "
            "AND focus_session_id IS NULL "
            "AND occurrence_key IS NULL "
            "AND task_title_snapshot IS NULL)",
            name="content_lifecycle",
        ),
        CheckConstraint(
            "(deleted_at IS NULL AND purge_after IS NULL AND purged_at IS NULL) "
            "OR (deleted_at IS NOT NULL AND purge_after IS NOT NULL "
            "AND purge_after >= deleted_at "
            "AND (purged_at IS NULL OR purged_at >= deleted_at))",
            name="lifecycle",
        ),
        CheckConstraint("length(btrim(time_zone)) > 0", name="time_zone"),
        CheckConstraint("version >= 1", name="version"),
        ForeignKeyConstraint(
            ["task_id", "owner_id"],
            ["tasks.id", "tasks.owner_id"],
            name="fk_difficulty_records_task_owner",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["focus_session_id", "owner_id"],
            ["focus_sessions.id", "focus_sessions.owner_id"],
            name="fk_difficulty_records_focus_session_owner",
            deferrable=True,
            initially="DEFERRED",
        ),
        UniqueConstraint(
            "id",
            "owner_id",
            name="uq_difficulty_records_id_owner",
        ),
        Index(
            "ix_difficulty_records_owner_occurred",
            "owner_id",
            "occurred_at",
            "id",
        ),
        Index(
            "ix_difficulty_records_owner_lifecycle",
            "owner_id",
            "deleted_at",
            "updated_at",
            "id",
        ),
        Index(
            "ix_difficulty_records_purge_due",
            "purge_after",
            "id",
            postgresql_where=text(
                "deleted_at IS NOT NULL AND purged_at IS NULL"
            ),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    task_id: Mapped[UUID | None]
    focus_session_id: Mapped[UUID | None]
    occurrence_key: Mapped[str | None] = mapped_column(String(120))
    task_title_snapshot: Mapped[str | None] = mapped_column(String(200))
    content: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(40))
    emotion: Mapped[str | None] = mapped_column(String(40))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    time_zone: Mapped[str] = mapped_column(String(64))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purge_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
