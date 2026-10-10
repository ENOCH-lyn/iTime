from datetime import date, datetime
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
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from itime.database import Base


class ReviewSession(Base):
    __tablename__ = "review_sessions"
    __table_args__ = (
        CheckConstraint(
            "char_length(btrim(time_zone)) BETWEEN 1 AND 64",
            name="time_zone",
        ),
        CheckConstraint(
            "char_length(supplemental_facts) <= 8000",
            name="supplemental_facts",
        ),
        CheckConstraint("input_revision >= 0", name="input_revision"),
        CheckConstraint("question_count BETWEEN 0 AND 3", name="question_count"),
        CheckConstraint("version >= 1", name="version"),
        UniqueConstraint("id", "owner_id", name="uq_review_sessions_id_owner"),
        UniqueConstraint(
            "owner_id",
            "activity_date",
            name="uq_review_sessions_owner_activity_date",
        ),
        Index(
            "ix_review_sessions_owner_activity",
            "owner_id",
            "activity_date",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    activity_date: Mapped[date] = mapped_column(Date)
    time_zone: Mapped[str] = mapped_column(String(64))
    supplemental_facts: Mapped[str] = mapped_column(
        Text,
        default="",
        server_default="",
    )
    input_revision: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        server_default="0",
    )
    question_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
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


class ReviewInputSnapshot(Base):
    __tablename__ = "review_input_snapshots"
    __table_args__ = (
        CheckConstraint("purpose IN ('question', 'report')", name="purpose"),
        CheckConstraint(
            "char_length(btrim(time_zone)) BETWEEN 1 AND 64",
            name="time_zone",
        ),
        CheckConstraint(
            "activity_started_at < activity_ended_at",
            name="activity_window",
        ),
        CheckConstraint(
            "session_input_revision >= 0",
            name="session_input_revision",
        ),
        CheckConstraint("length(facts_sha256) = 64", name="facts_sha256"),
        CheckConstraint("jsonb_typeof(facts) = 'object'", name="facts"),
        CheckConstraint(
            "jsonb_typeof(user_context) = 'object'",
            name="user_context",
        ),
        CheckConstraint(
            "jsonb_typeof(memory_context) = 'array' AND jsonb_array_length(memory_context) <= 30",
            name="memory_context",
        ),
        CheckConstraint(
            "char_length(generation_instruction) <= 2000",
            name="generation_instruction",
        ),
        ForeignKeyConstraint(
            ["review_session_id", "owner_id"],
            ["review_sessions.id", "review_sessions.owner_id"],
            name="fk_review_input_snapshots_session_owner",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "id",
            "owner_id",
            name="uq_review_input_snapshots_id_owner",
        ),
        Index(
            "ix_review_input_snapshots_owner_session_created",
            "owner_id",
            "review_session_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    review_session_id: Mapped[UUID]
    purpose: Mapped[str] = mapped_column(String(12))
    activity_date: Mapped[date] = mapped_column(Date)
    time_zone: Mapped[str] = mapped_column(String(64))
    activity_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    activity_ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    session_input_revision: Mapped[int] = mapped_column(BigInteger)
    facts_sha256: Mapped[str] = mapped_column(String(64))
    facts: Mapped[dict] = mapped_column(JSONB)
    user_context: Mapped[dict] = mapped_column(JSONB)
    memory_context: Mapped[list[dict]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    generation_instruction: Mapped[str] = mapped_column(
        Text,
        default="",
        server_default="",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class ReviewMessage(Base):
    __tablename__ = "review_messages"
    __table_args__ = (
        CheckConstraint("sequence >= 1", name="sequence"),
        CheckConstraint("role IN ('user', 'assistant')", name="role"),
        CheckConstraint("kind IN ('answer', 'question')", name="kind"),
        CheckConstraint(
            "(role = 'user' AND kind = 'answer' "
            "AND char_length(btrim(content)) BETWEEN 1 AND 8000 "
            "AND source_job_id IS NULL) "
            "OR (role = 'assistant' AND kind = 'question' "
            "AND char_length(btrim(content)) BETWEEN 1 AND 2000 "
            "AND source_job_id IS NOT NULL)",
            name="shape",
        ),
        ForeignKeyConstraint(
            ["review_session_id", "owner_id"],
            ["review_sessions.id", "review_sessions.owner_id"],
            name="fk_review_messages_session_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["source_job_id", "owner_id"],
            ["ai_jobs.id", "ai_jobs.owner_id"],
            name="fk_review_messages_source_job_owner",
            ondelete="CASCADE",
        ),
        UniqueConstraint("id", "owner_id", name="uq_review_messages_id_owner"),
        UniqueConstraint(
            "review_session_id",
            "sequence",
            name="uq_review_messages_session_sequence",
        ),
        UniqueConstraint("source_job_id", name="uq_review_messages_source_job"),
        Index(
            "ix_review_messages_owner_session_sequence",
            "owner_id",
            "review_session_id",
            "sequence",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID]
    review_session_id: Mapped[UUID]
    sequence: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(10))
    kind: Mapped[str] = mapped_column(String(10))
    content: Mapped[str] = mapped_column(Text)
    source_job_id: Mapped[UUID | None]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class ReviewReportVersion(Base):
    __tablename__ = "review_report_versions"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint(
            "char_length(btrim(summary)) BETWEEN 1 AND 4000",
            name="summary",
        ),
        CheckConstraint(
            "jsonb_typeof(facts) = 'array' AND jsonb_array_length(facts) <= 30",
            name="facts",
        ),
        CheckConstraint(
            "jsonb_typeof(user_statements) = 'array' AND jsonb_array_length(user_statements) <= 20",
            name="user_statements",
        ),
        CheckConstraint(
            "jsonb_typeof(insights) = 'array' AND jsonb_array_length(insights) <= 20",
            name="insights",
        ),
        CheckConstraint(
            "jsonb_typeof(next_actions) = 'array' AND jsonb_array_length(next_actions) <= 20",
            name="next_actions",
        ),
        CheckConstraint(
            "jsonb_typeof(uncertainties) = 'array' AND jsonb_array_length(uncertainties) <= 20",
            name="uncertainties",
        ),
        ForeignKeyConstraint(
            ["review_session_id", "owner_id"],
            ["review_sessions.id", "review_sessions.owner_id"],
            name="fk_review_report_versions_session_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["source_job_id", "owner_id"],
            ["ai_jobs.id", "ai_jobs.owner_id"],
            name="fk_review_report_versions_source_job_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["input_snapshot_id", "owner_id"],
            ["review_input_snapshots.id", "review_input_snapshots.owner_id"],
            name="fk_review_report_versions_snapshot_owner",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "id",
            "owner_id",
            name="uq_review_report_versions_id_owner",
        ),
        UniqueConstraint(
            "review_session_id",
            "version",
            name="uq_review_report_versions_session_version",
        ),
        UniqueConstraint(
            "source_job_id",
            name="uq_review_report_versions_source_job",
        ),
        UniqueConstraint(
            "input_snapshot_id",
            name="uq_review_report_versions_snapshot",
        ),
        Index(
            "ix_review_report_versions_owner_session_version",
            "owner_id",
            "review_session_id",
            "version",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID]
    review_session_id: Mapped[UUID]
    version: Mapped[int] = mapped_column(Integer)
    source_job_id: Mapped[UUID]
    input_snapshot_id: Mapped[UUID]
    summary: Mapped[str] = mapped_column(Text)
    facts: Mapped[list[dict]] = mapped_column(JSONB)
    user_statements: Mapped[list[str]] = mapped_column(JSONB)
    insights: Mapped[list[dict]] = mapped_column(JSONB)
    next_actions: Mapped[list[str]] = mapped_column(JSONB)
    uncertainties: Mapped[list[str]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class ReviewAnnotation(Base):
    __tablename__ = "review_annotations"
    __table_args__ = (
        CheckConstraint(
            "char_length(btrim(content)) BETWEEN 1 AND 4000",
            name="content",
        ),
        CheckConstraint("version >= 1", name="version"),
        ForeignKeyConstraint(
            ["report_version_id", "owner_id"],
            ["review_report_versions.id", "review_report_versions.owner_id"],
            name="fk_review_annotations_report_owner",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "id",
            "owner_id",
            name="uq_review_annotations_id_owner",
        ),
        Index(
            "ix_review_annotations_owner_report_created",
            "owner_id",
            "report_version_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID]
    report_version_id: Mapped[UUID]
    content: Mapped[str] = mapped_column(Text)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class MemoryProposal(Base):
    __tablename__ = "memory_proposals"
    __table_args__ = (
        CheckConstraint("position >= 0", name="position"),
        CheckConstraint(
            "kind IN ('preference', 'constraint', 'context', 'goal')",
            name="kind",
        ),
        CheckConstraint(
            "char_length(btrim(title)) BETWEEN 1 AND 120",
            name="title",
        ),
        CheckConstraint(
            "char_length(btrim(content)) BETWEEN 1 AND 2000",
            name="content",
        ),
        CheckConstraint(
            "char_length(btrim(rationale)) BETWEEN 1 AND 1000",
            name="rationale",
        ),
        CheckConstraint(
            "status IN ('pending', 'confirmed', 'rejected')",
            name="status",
        ),
        CheckConstraint(
            "(status = 'pending' AND confirmed_at IS NULL AND rejected_at IS NULL) "
            "OR (status = 'confirmed' AND confirmed_at IS NOT NULL "
            "AND rejected_at IS NULL) "
            "OR (status = 'rejected' AND rejected_at IS NOT NULL "
            "AND confirmed_at IS NULL)",
            name="state",
        ),
        CheckConstraint("version >= 1", name="version"),
        ForeignKeyConstraint(
            ["report_version_id", "owner_id"],
            ["review_report_versions.id", "review_report_versions.owner_id"],
            name="fk_memory_proposals_report_owner",
            ondelete="CASCADE",
        ),
        UniqueConstraint("id", "owner_id", name="uq_memory_proposals_id_owner"),
        UniqueConstraint(
            "report_version_id",
            "position",
            name="uq_memory_proposals_report_position",
        ),
        Index(
            "ix_memory_proposals_owner_status_created",
            "owner_id",
            "status",
            "created_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID]
    report_version_id: Mapped[UUID]
    position: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(120))
    content: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(
        String(12),
        default="pending",
        server_default="pending",
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class Memory(Base):
    __tablename__ = "memories"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('preference', 'constraint', 'context', 'goal')",
            name="kind",
        ),
        CheckConstraint(
            "source_type IN ('review_proposal', 'manual', 'ai_tool')",
            name="source_type",
        ),
        CheckConstraint(
            "purged_at IS NOT NULL "
            "OR (char_length(btrim(title)) BETWEEN 1 AND 120 "
            "AND char_length(btrim(content)) BETWEEN 1 AND 2000)",
            name="content",
        ),
        CheckConstraint(
            "(purged_at IS NOT NULL AND title IS NULL AND content IS NULL "
            "AND source_proposal_id IS NULL AND source_conversation_id IS NULL) "
            "OR (purged_at IS NULL AND "
            "((source_type = 'manual' "
            "AND source_proposal_id IS NULL "
            "AND source_conversation_id IS NULL) "
            "OR (source_type = 'review_proposal' "
            "AND source_proposal_id IS NOT NULL "
            "AND source_conversation_id IS NULL) "
            "OR (source_type = 'ai_tool' "
            "AND source_proposal_id IS NULL "
            "AND source_conversation_id IS NOT NULL)))",
            name="source",
        ),
        CheckConstraint(
            "cardinality(tags) <= 10",
            name="tags",
        ),
        CheckConstraint(
            "(deleted_at IS NULL AND purge_after IS NULL AND purged_at IS NULL) "
            "OR (deleted_at IS NOT NULL AND purge_after IS NOT NULL "
            "AND purge_after >= deleted_at "
            "AND (purged_at IS NULL OR purged_at >= deleted_at))",
            name="lifecycle",
        ),
        CheckConstraint("version >= 1", name="version"),
        ForeignKeyConstraint(
            ["source_proposal_id", "owner_id"],
            ["memory_proposals.id", "memory_proposals.owner_id"],
            name="fk_memories_source_proposal_owner",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", "owner_id", name="uq_memories_id_owner"),
        UniqueConstraint(
            "source_proposal_id",
            name="uq_memories_source_proposal",
        ),
        Index(
            "ix_memories_owner_lifecycle_updated",
            "owner_id",
            "deleted_at",
            "updated_at",
            "id",
        ),
        Index(
            "ix_memories_purge_due",
            "purge_after",
            "id",
            postgresql_where=text("deleted_at IS NOT NULL AND purged_at IS NULL"),
        ),
        Index(
            "ix_memories_tags",
            "tags",
            postgresql_using="gin",
        ),
        Index(
            "ix_memories_owner_last_used",
            "owner_id",
            text("last_used_at DESC NULLS LAST"),
            "id",
            postgresql_where=text("deleted_at IS NULL AND purged_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))
    title: Mapped[str | None] = mapped_column(String(120))
    content: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(20))
    source_proposal_id: Mapped[UUID | None]
    source_conversation_id: Mapped[UUID | None]
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text()),
        default=list,
        server_default=text("ARRAY[]::text[]"),
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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


class MemorySettings(Base):
    __tablename__ = "memory_settings"
    __table_args__ = (CheckConstraint("version >= 1", name="version"),)

    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=text("true"),
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
