"""Persistent Agent run records; graph state lives in LangGraph checkpoints."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from itime.database import Base

AGENT_RUN_STATES = (
    "running",
    "waiting_approval",
    "completed",
    "failed",
    "cancelled",
)


class AgentRun(Base):
    __tablename__ = "ai_agent_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running','waiting_approval','completed','failed','cancelled')",
            name="status",
        ),
        CheckConstraint("step >= 0", name="step"),
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint("jsonb_typeof(context) = 'object'", name="context"),
        Index(
            "ix_ai_agent_runs_owner_conversation",
            "owner_id",
            "conversation_id",
            "status",
        ),
        Index("ix_ai_agent_runs_pending_proposal", "pending_proposal_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
    )
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("ai_conversations.id", ondelete="CASCADE"),
    )
    source_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ai_conversation_messages.id", ondelete="SET NULL"),
    )
    last_job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ai_jobs.id", ondelete="SET NULL"),
    )
    pending_proposal_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ai_action_proposals.id", ondelete="SET NULL"),
    )
    thread_id: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(
        String(20),
        default="running",
        server_default=text("'running'"),
    )
    step: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    context: Mapped[dict] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    image_media_id: Mapped[UUID | None]
    last_content: Mapped[str] = mapped_column(
        String(100_000),
        default="",
        server_default=text("''"),
    )
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(String(2000))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
