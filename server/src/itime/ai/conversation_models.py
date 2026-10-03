from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
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


class Conversation(Base):
    __tablename__ = "ai_conversations"
    __table_args__ = (
        CheckConstraint(
            "char_length(btrim(title)) BETWEEN 1 AND 120",
            name="title",
        ),
        CheckConstraint("title_source IN ('auto', 'user')", name="title_source"),
        CheckConstraint("version >= 1", name="version"),
        UniqueConstraint("id", "owner_id", name="uq_ai_conversations_id_owner"),
        Index(
            "ix_ai_conversations_owner_archived_updated",
            "owner_id",
            "archived_at",
            "updated_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(120))
    title_source: Mapped[str] = mapped_column(
        String(8),
        default="auto",
        server_default="auto",
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class ConversationMessage(Base):
    __tablename__ = "ai_conversation_messages"
    __table_args__ = (
        CheckConstraint("sequence >= 1", name="sequence"),
        CheckConstraint("role IN ('user', 'assistant')", name="role"),
        CheckConstraint(
            "status IN "
            "('complete', 'queued', 'running', 'failed', 'timed_out', 'cancelled')",
            name="status",
        ),
        CheckConstraint("char_length(content) <= 100000", name="content_length"),
        CheckConstraint(
            "(error_code IS NULL) = (error_message IS NULL)",
            name="error_pair",
        ),
        CheckConstraint(
            "(role = 'user' AND status = 'complete' "
            "AND char_length(btrim(content)) BETWEEN 1 AND 12000 "
            "AND error_code IS NULL AND completed_at IS NOT NULL) "
            "OR (role = 'assistant' AND status IN ('queued', 'running') "
            "AND error_code IS NULL AND completed_at IS NULL) "
            "OR (role = 'assistant' AND status = 'complete' "
            "AND char_length(btrim(content)) BETWEEN 1 AND 100000 "
            "AND error_code IS NULL AND completed_at IS NOT NULL) "
            "OR (role = 'assistant' AND status IN ('failed', 'timed_out') "
            "AND error_code IS NOT NULL AND completed_at IS NOT NULL) "
            "OR (role = 'assistant' AND status = 'cancelled' "
            "AND error_code IS NULL AND completed_at IS NOT NULL)",
            name="state",
        ),
        ForeignKeyConstraint(
            ["conversation_id", "owner_id"],
            ["ai_conversations.id", "ai_conversations.owner_id"],
            name="fk_ai_conversation_messages_conversation_owner",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "id",
            "owner_id",
            name="uq_ai_conversation_messages_id_owner",
        ),
        UniqueConstraint(
            "id",
            "owner_id",
            "conversation_id",
            name="uq_ai_conversation_messages_id_owner_conversation",
        ),
        UniqueConstraint(
            "conversation_id",
            "sequence",
            name="uq_ai_conversation_messages_sequence",
        ),
        Index(
            "ix_ai_conversation_messages_owner_created",
            "owner_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID]
    conversation_id: Mapped[UUID]
    sequence: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(12))
    content: Mapped[str] = mapped_column(Text, default="", server_default="")
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChatGeneration(Base):
    __tablename__ = "ai_chat_generations"
    __table_args__ = (
        CheckConstraint("history_through_sequence >= 1", name="history_sequence"),
        CheckConstraint(
            "jsonb_typeof(context_message_ids) = 'array' "
            "AND jsonb_array_length(context_message_ids) <= 40",
            name="context_message_ids",
        ),
        CheckConstraint(
            "jsonb_typeof(public_source_context) = 'array' "
            "AND jsonb_array_length(public_source_context) <= 3",
            name="public_source_context",
        ),
        CheckConstraint(
            "stream_generation BETWEEN 0 AND 5",
            name="stream_generation",
        ),
        CheckConstraint(
            "request_message_id <> response_message_id",
            name="different_messages",
        ),
        ForeignKeyConstraint(
            ["job_id", "owner_id"],
            ["ai_jobs.id", "ai_jobs.owner_id"],
            name="fk_ai_chat_generations_job_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["conversation_id", "owner_id"],
            ["ai_conversations.id", "ai_conversations.owner_id"],
            name="fk_ai_chat_generations_conversation_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["request_message_id", "owner_id", "conversation_id"],
            [
                "ai_conversation_messages.id",
                "ai_conversation_messages.owner_id",
                "ai_conversation_messages.conversation_id",
            ],
            name="fk_ai_chat_generations_request_message",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["response_message_id", "owner_id", "conversation_id"],
            [
                "ai_conversation_messages.id",
                "ai_conversation_messages.owner_id",
                "ai_conversation_messages.conversation_id",
            ],
            name="fk_ai_chat_generations_response_message",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "request_message_id",
            name="uq_ai_chat_generations_request_message",
        ),
        UniqueConstraint(
            "response_message_id",
            name="uq_ai_chat_generations_response_message",
        ),
        Index(
            "ix_ai_chat_generations_owner_conversation",
            "owner_id",
            "conversation_id",
            "job_id",
        ),
    )

    job_id: Mapped[UUID] = mapped_column(primary_key=True)
    owner_id: Mapped[UUID]
    conversation_id: Mapped[UUID]
    request_message_id: Mapped[UUID]
    response_message_id: Mapped[UUID]
    history_through_sequence: Mapped[int] = mapped_column(Integer)
    context_message_ids: Mapped[list[str]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    public_source_context: Mapped[list[dict]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    stream_generation: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class AIJobEvent(Base):
    __tablename__ = "ai_job_events"
    __table_args__ = (
        CheckConstraint("sequence >= 1", name="sequence"),
        CheckConstraint("generation BETWEEN 0 AND 5", name="generation"),
        CheckConstraint(
            "kind IN ('job_state', 'content_reset', 'content_delta')",
            name="kind",
        ),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="payload"),
        CheckConstraint("expires_at >= created_at", name="expires_at"),
        ForeignKeyConstraint(
            ["job_id", "owner_id"],
            ["ai_jobs.id", "ai_jobs.owner_id"],
            name="fk_ai_job_events_job_owner",
            ondelete="CASCADE",
        ),
        UniqueConstraint("job_id", "sequence", name="uq_ai_job_events_sequence"),
        Index(
            "ix_ai_job_events_owner_job_sequence",
            "owner_id",
            "job_id",
            "sequence",
        ),
        Index("ix_ai_job_events_expiry", "expires_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    job_id: Mapped[UUID]
    owner_id: Mapped[UUID]
    sequence: Mapped[int] = mapped_column(Integer)
    generation: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(24))
    payload: Mapped[dict] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
