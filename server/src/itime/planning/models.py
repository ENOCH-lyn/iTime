from datetime import date, datetime, time
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


class PlanningInputRevision(Base):
    __tablename__ = "planning_input_revisions"
    __table_args__ = (
        CheckConstraint("revision >= 0", name="revision"),
        Index("ix_planning_input_revisions_updated", "updated_at", "owner_id"),
    )

    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    revision: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        server_default="0",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class PlanningInputSnapshot(Base):
    __tablename__ = "planning_input_snapshots"
    __table_args__ = (
        CheckConstraint("revision >= 0", name="revision"),
        CheckConstraint("scope IN ('day', 'week')", name="scope"),
        CheckConstraint(
            "week_end_date = week_start_date + 6 "
            "AND target_date BETWEEN week_start_date AND week_end_date",
            name="date_range",
        ),
        CheckConstraint("EXTRACT(ISODOW FROM week_start_date) = 1", name="week_start"),
        CheckConstraint("length(btrim(time_zone)) > 0", name="time_zone"),
        CheckConstraint(
            "density IN ('loose', 'balanced', 'tight')",
            name="density",
        ),
        CheckConstraint(
            "buffer_minutes BETWEEN 0 AND 60 AND mod(buffer_minutes, 5) = 0",
            name="buffer_minutes",
        ),
        CheckConstraint(
            "availability_start < availability_end",
            name="availability",
        ),
        CheckConstraint(
            "char_length(user_instructions) <= 4000",
            name="user_instructions",
        ),
        CheckConstraint(
            "jsonb_typeof(selected_task_ids) = 'array' "
            "AND jsonb_array_length(selected_task_ids) <= 100",
            name="selected_task_ids",
        ),
        CheckConstraint(
            "jsonb_typeof(task_candidates) = 'array' "
            "AND jsonb_array_length(task_candidates) <= 100",
            name="task_candidates",
        ),
        CheckConstraint(
            "jsonb_typeof(fixed_occurrences) = 'array' "
            "AND jsonb_array_length(fixed_occurrences) <= 2000",
            name="fixed_occurrences",
        ),
        CheckConstraint(
            "jsonb_typeof(preserved_dates) = 'array' AND jsonb_array_length(preserved_dates) <= 7",
            name="preserved_dates",
        ),
        CheckConstraint("length(content_sha256) = 64", name="content_sha256"),
        CheckConstraint(
            "(current_plan_id IS NULL) = (current_plan_version IS NULL)",
            name="current_plan",
        ),
        ForeignKeyConstraint(
            ["current_plan_id", "current_plan_version", "owner_id"],
            ["plan_versions.plan_id", "plan_versions.version", "plan_versions.owner_id"],
            name="fk_planning_input_snapshots_current_plan_version",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        UniqueConstraint(
            "id",
            "owner_id",
            name="uq_planning_input_snapshots_id_owner",
        ),
        Index(
            "ix_planning_input_snapshots_owner_created",
            "owner_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    revision: Mapped[int] = mapped_column(BigInteger)
    scope: Mapped[str] = mapped_column(String(8))
    target_date: Mapped[date] = mapped_column(Date)
    week_start_date: Mapped[date] = mapped_column(Date)
    week_end_date: Mapped[date] = mapped_column(Date)
    time_zone: Mapped[str] = mapped_column(String(64))
    density: Mapped[str] = mapped_column(String(12))
    buffer_minutes: Mapped[int] = mapped_column(Integer)
    availability_start: Mapped[time] = mapped_column(Time(timezone=False))
    availability_end: Mapped[time] = mapped_column(Time(timezone=False))
    user_instructions: Mapped[str] = mapped_column(Text, default="", server_default="")
    selected_task_ids: Mapped[list[str]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    task_candidates: Mapped[list[dict]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    fixed_occurrences: Mapped[list[dict]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    current_plan_id: Mapped[UUID | None]
    current_plan_version: Mapped[int | None] = mapped_column(Integer)
    preserved_dates: Mapped[list[str]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    content_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class Proposal(Base):
    __tablename__ = "ai_proposals"
    __table_args__ = (
        CheckConstraint("kind IN ('plan')", name="kind"),
        CheckConstraint("schema_version >= 1", name="schema_version"),
        CheckConstraint(
            "status IN ('pending', 'applied', 'rejected')",
            name="status",
        ),
        CheckConstraint(
            "char_length(btrim(title)) BETWEEN 1 AND 200",
            name="title",
        ),
        CheckConstraint("char_length(summary) <= 4000", name="summary"),
        CheckConstraint(
            "jsonb_typeof(assumptions) = 'array' AND jsonb_array_length(assumptions) <= 20",
            name="assumptions",
        ),
        CheckConstraint(
            "jsonb_typeof(warnings) = 'array' AND jsonb_array_length(warnings) <= 20",
            name="warnings",
        ),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="payload"),
        CheckConstraint("expires_at > created_at", name="expires_at"),
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint(
            "(conversation_id IS NULL) = (source_message_id IS NULL)",
            name="conversation_message",
        ),
        CheckConstraint(
            "(status = 'pending' AND applied_at IS NULL AND rejected_at IS NULL "
            "AND applied_plan_id IS NULL AND applied_plan_version_id IS NULL) "
            "OR (status = 'applied' AND applied_at IS NOT NULL "
            "AND rejected_at IS NULL AND applied_plan_id IS NOT NULL "
            "AND applied_plan_version_id IS NOT NULL) "
            "OR (status = 'rejected' AND rejected_at IS NOT NULL "
            "AND applied_at IS NULL AND applied_plan_id IS NULL "
            "AND applied_plan_version_id IS NULL)",
            name="state",
        ),
        ForeignKeyConstraint(
            ["source_job_id", "owner_id"],
            ["ai_jobs.id", "ai_jobs.owner_id"],
            name="fk_ai_proposals_source_job_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["input_snapshot_id", "owner_id"],
            ["planning_input_snapshots.id", "planning_input_snapshots.owner_id"],
            name="fk_ai_proposals_input_snapshot_owner",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["conversation_id", "owner_id"],
            ["ai_conversations.id", "ai_conversations.owner_id"],
            name="fk_ai_proposals_conversation_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["source_message_id", "owner_id", "conversation_id"],
            [
                "ai_conversation_messages.id",
                "ai_conversation_messages.owner_id",
                "ai_conversation_messages.conversation_id",
            ],
            name="fk_ai_proposals_source_message",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["applied_plan_id", "owner_id"],
            ["plans.id", "plans.owner_id"],
            name="fk_ai_proposals_applied_plan_owner",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["applied_plan_version_id", "applied_plan_id", "owner_id"],
            ["plan_versions.id", "plan_versions.plan_id", "plan_versions.owner_id"],
            name="fk_ai_proposals_applied_plan_version",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        UniqueConstraint("id", "owner_id", name="uq_ai_proposals_id_owner"),
        UniqueConstraint("source_job_id", name="uq_ai_proposals_source_job"),
        Index(
            "ix_ai_proposals_owner_status_created",
            "owner_id",
            "status",
            "created_at",
            "id",
        ),
        Index("ix_ai_proposals_expiry", "expires_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(20))
    source_job_id: Mapped[UUID]
    conversation_id: Mapped[UUID | None]
    source_message_id: Mapped[UUID | None]
    input_snapshot_id: Mapped[UUID]
    schema_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        String(16),
        default="pending",
        server_default="pending",
    )
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(Text, default="", server_default="")
    assumptions: Mapped[list[str]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    warnings: Mapped[list[str]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    payload: Mapped[dict] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    applied_plan_id: Mapped[UUID | None]
    applied_plan_version_id: Mapped[UUID | None]
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class ProposalChange(Base):
    __tablename__ = "ai_proposal_changes"
    __table_args__ = (
        CheckConstraint("position >= 0", name="position"),
        CheckConstraint(
            "action IN ('create', 'update', 'delete')",
            name="action",
        ),
        CheckConstraint(
            "entity_type IN ('plan', 'scheduled_block')",
            name="entity_type",
        ),
        CheckConstraint(
            "before_data IS NULL OR jsonb_typeof(before_data) = 'object'",
            name="before_data",
        ),
        CheckConstraint(
            "after_data IS NULL OR jsonb_typeof(after_data) = 'object'",
            name="after_data",
        ),
        CheckConstraint(
            "(action = 'create' AND before_data IS NULL AND after_data IS NOT NULL) "
            "OR (action = 'update' AND before_data IS NOT NULL "
            "AND after_data IS NOT NULL) "
            "OR (action = 'delete' AND before_data IS NOT NULL "
            "AND after_data IS NULL)",
            name="data_shape",
        ),
        ForeignKeyConstraint(
            ["proposal_id", "owner_id"],
            ["ai_proposals.id", "ai_proposals.owner_id"],
            name="fk_ai_proposal_changes_proposal_owner",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "proposal_id",
            "position",
            name="uq_ai_proposal_changes_proposal_position",
        ),
        Index(
            "ix_ai_proposal_changes_owner_proposal",
            "owner_id",
            "proposal_id",
            "position",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    proposal_id: Mapped[UUID]
    owner_id: Mapped[UUID]
    position: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(12))
    entity_type: Mapped[str] = mapped_column(String(24))
    target_id: Mapped[UUID | None]
    before_data: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    after_data: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class Plan(Base):
    __tablename__ = "plans"
    __table_args__ = (
        CheckConstraint("EXTRACT(ISODOW FROM week_start_date) = 1", name="week_start"),
        CheckConstraint("length(btrim(time_zone)) > 0", name="time_zone"),
        CheckConstraint("version >= 1", name="version"),
        ForeignKeyConstraint(
            ["current_version_id", "id", "owner_id"],
            ["plan_versions.id", "plan_versions.plan_id", "plan_versions.owner_id"],
            name="fk_plans_current_version",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        UniqueConstraint("id", "owner_id", name="uq_plans_id_owner"),
        UniqueConstraint(
            "owner_id",
            "week_start_date",
            name="uq_plans_owner_week",
        ),
        Index(
            "ix_plans_owner_week",
            "owner_id",
            "week_start_date",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    week_start_date: Mapped[date] = mapped_column(Date)
    time_zone: Mapped[str] = mapped_column(String(64))
    current_version_id: Mapped[UUID]
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class PlanVersion(Base):
    __tablename__ = "plan_versions"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint("source IN ('ai', 'manual')", name="source"),
        CheckConstraint(
            "(source = 'ai') = (proposal_id IS NOT NULL)",
            name="proposal_source",
        ),
        CheckConstraint("scope IN ('day', 'week')", name="scope"),
        CheckConstraint(
            "density IN ('loose', 'balanced', 'tight')",
            name="density",
        ),
        CheckConstraint(
            "buffer_minutes BETWEEN 0 AND 60 AND mod(buffer_minutes, 5) = 0",
            name="buffer_minutes",
        ),
        CheckConstraint(
            "availability_start < availability_end",
            name="availability",
        ),
        CheckConstraint("char_length(summary) <= 4000", name="summary"),
        CheckConstraint(
            "jsonb_typeof(assumptions) = 'array' AND jsonb_array_length(assumptions) <= 20",
            name="assumptions",
        ),
        CheckConstraint("input_revision >= 0", name="input_revision"),
        ForeignKeyConstraint(
            ["plan_id", "owner_id"],
            ["plans.id", "plans.owner_id"],
            name="fk_plan_versions_plan_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["proposal_id", "owner_id"],
            ["ai_proposals.id", "ai_proposals.owner_id"],
            name="fk_plan_versions_proposal_owner",
            deferrable=True,
            initially="DEFERRED",
        ),
        UniqueConstraint("id", "owner_id", name="uq_plan_versions_id_owner"),
        UniqueConstraint(
            "id",
            "plan_id",
            "owner_id",
            name="uq_plan_versions_id_plan_owner",
        ),
        UniqueConstraint(
            "plan_id",
            "version",
            "owner_id",
            name="uq_plan_versions_plan_version_owner",
        ),
        UniqueConstraint("proposal_id", name="uq_plan_versions_proposal"),
        Index(
            "ix_plan_versions_owner_plan_created",
            "owner_id",
            "plan_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    plan_id: Mapped[UUID]
    owner_id: Mapped[UUID]
    version: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(12))
    proposal_id: Mapped[UUID | None]
    scope: Mapped[str] = mapped_column(String(8))
    target_date: Mapped[date] = mapped_column(Date)
    density: Mapped[str] = mapped_column(String(12))
    buffer_minutes: Mapped[int] = mapped_column(Integer)
    availability_start: Mapped[time] = mapped_column(Time(timezone=False))
    availability_end: Mapped[time] = mapped_column(Time(timezone=False))
    summary: Mapped[str] = mapped_column(Text, default="", server_default="")
    assumptions: Mapped[list[str]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    input_revision: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class ScheduledBlock(Base):
    __tablename__ = "scheduled_blocks"
    __table_args__ = (
        CheckConstraint("position >= 0", name="position"),
        CheckConstraint(
            "occurrence_key IS NULL OR char_length(btrim(occurrence_key)) BETWEEN 1 AND 120",
            name="occurrence_key",
        ),
        CheckConstraint(
            "char_length(btrim(task_title_snapshot)) BETWEEN 1 AND 200",
            name="task_title_snapshot",
        ),
        CheckConstraint(
            "jsonb_typeof(step_ids) = 'array' AND jsonb_array_length(step_ids) <= 100",
            name="step_ids",
        ),
        CheckConstraint(
            "jsonb_typeof(step_titles_snapshot) = 'array' "
            "AND jsonb_array_length(step_titles_snapshot) <= 100",
            name="step_titles_snapshot",
        ),
        CheckConstraint(
            "jsonb_array_length(step_ids) = jsonb_array_length(step_titles_snapshot)",
            name="step_snapshot_length",
        ),
        CheckConstraint("local_starts_at < local_ends_at", name="local_time"),
        CheckConstraint("length(btrim(time_zone)) > 0", name="time_zone"),
        CheckConstraint(
            "starts_at < ends_at AND ends_at - starts_at <= interval '3 hours'",
            name="utc_time",
        ),
        CheckConstraint(
            "estimate_source IN ('user', 'ai')",
            name="estimate_source",
        ),
        CheckConstraint(
            "estimated_minutes BETWEEN 1 AND 525600",
            name="estimated_minutes",
        ),
        ForeignKeyConstraint(
            ["plan_version_id", "owner_id"],
            ["plan_versions.id", "plan_versions.owner_id"],
            name="fk_scheduled_blocks_plan_version_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["task_id", "owner_id"],
            ["tasks.id", "tasks.owner_id"],
            name="fk_scheduled_blocks_task_owner",
            deferrable=True,
            initially="DEFERRED",
        ),
        UniqueConstraint(
            "id",
            "owner_id",
            name="uq_scheduled_blocks_id_owner",
        ),
        UniqueConstraint(
            "plan_version_id",
            "position",
            name="uq_scheduled_blocks_plan_version_position",
        ),
        Index(
            "ix_scheduled_blocks_owner_time",
            "owner_id",
            "starts_at",
            "ends_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    plan_version_id: Mapped[UUID]
    owner_id: Mapped[UUID]
    position: Mapped[int] = mapped_column(Integer)
    task_id: Mapped[UUID]
    occurrence_key: Mapped[str | None] = mapped_column(String(120))
    task_title_snapshot: Mapped[str] = mapped_column(String(200))
    step_ids: Mapped[list[str]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    step_titles_snapshot: Mapped[list[str]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    local_date: Mapped[date] = mapped_column(Date)
    local_starts_at: Mapped[time] = mapped_column(Time(timezone=False))
    local_ends_at: Mapped[time] = mapped_column(Time(timezone=False))
    time_zone: Mapped[str] = mapped_column(String(64))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    estimate_source: Mapped[str] = mapped_column(String(8))
    estimated_minutes: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class PlanUnscheduledItem(Base):
    __tablename__ = "plan_unscheduled_items"
    __table_args__ = (
        CheckConstraint("position >= 0", name="position"),
        CheckConstraint(
            "occurrence_key IS NULL OR char_length(btrim(occurrence_key)) BETWEEN 1 AND 120",
            name="occurrence_key",
        ),
        CheckConstraint(
            "char_length(btrim(task_title_snapshot)) BETWEEN 1 AND 200",
            name="task_title_snapshot",
        ),
        CheckConstraint(
            "remaining_minutes BETWEEN 1 AND 525600",
            name="remaining_minutes",
        ),
        CheckConstraint(
            "reason IN ('no_capacity', 'deadline_conflict', 'fixed_conflict', "
            "'missing_estimate', 'outside_scope')",
            name="reason",
        ),
        CheckConstraint(
            "char_length(btrim(explanation)) BETWEEN 1 AND 500",
            name="explanation",
        ),
        ForeignKeyConstraint(
            ["plan_version_id", "owner_id"],
            ["plan_versions.id", "plan_versions.owner_id"],
            name="fk_plan_unscheduled_items_plan_version_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["task_id", "owner_id"],
            ["tasks.id", "tasks.owner_id"],
            name="fk_plan_unscheduled_items_task_owner",
            deferrable=True,
            initially="DEFERRED",
        ),
        UniqueConstraint(
            "plan_version_id",
            "position",
            name="uq_plan_unscheduled_items_plan_version_position",
        ),
        Index(
            "ix_plan_unscheduled_items_owner_plan_version",
            "owner_id",
            "plan_version_id",
            "position",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    plan_version_id: Mapped[UUID]
    owner_id: Mapped[UUID]
    position: Mapped[int] = mapped_column(Integer)
    task_id: Mapped[UUID]
    occurrence_key: Mapped[str | None] = mapped_column(String(120))
    task_title_snapshot: Mapped[str] = mapped_column(String(200))
    remaining_minutes: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(24))
    explanation: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
