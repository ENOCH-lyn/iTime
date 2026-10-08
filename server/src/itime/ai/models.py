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
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from itime.database import Base
from itime.planning import models as planning_models  # noqa: F401
from itime.review import models as review_models  # noqa: F401


class AIConfigCapability(Base):
    __tablename__ = "ai_config_capabilities"
    __table_args__ = (
        CheckConstraint(
            "capability IN "
            "('calendar_screenshot', 'conversation', 'planning', 'review', "
            "'transcription')",
            name="capability",
        ),
        CheckConstraint("next_version >= 1", name="next_version"),
        CheckConstraint("routing_version >= 1", name="routing_version"),
        ForeignKeyConstraint(
            ["current_version_id", "capability"],
            ["ai_config_versions.id", "ai_config_versions.capability"],
            name="fk_ai_config_capabilities_current_version",
            ondelete="RESTRICT",
            use_alter=True,
        ),
    )

    capability: Mapped[str] = mapped_column(String(50), primary_key=True)
    next_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    current_version_id: Mapped[UUID | None]
    routing_version: Mapped[int] = mapped_column(
        Integer,
        default=1,
        server_default="1",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AIConfigVersion(Base):
    __tablename__ = "ai_config_versions"
    __table_args__ = (
        CheckConstraint(
            "capability IN "
            "('calendar_screenshot', 'conversation', 'planning', 'review', "
            "'transcription')",
            name="capability",
        ),
        CheckConstraint(
            "status IN ('draft', 'tested', 'published', 'retired')",
            name="status",
        ),
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint(
            "length(btrim(endpoint_url)) BETWEEN 1 AND 2048",
            name="endpoint_url_length",
        ),
        CheckConstraint(
            "model ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,254}$'",
            name="model",
        ),
        CheckConstraint(
            "octet_length(api_key_ciphertext) > 0",
            name="api_key_ciphertext",
        ),
        CheckConstraint(
            "timeout_seconds BETWEEN 1 AND 300",
            name="timeout_seconds",
        ),
        CheckConstraint(
            "(capability = 'transcription' AND max_output_tokens IS NULL) "
            "OR (capability <> 'transcription' "
            "AND max_output_tokens BETWEEN 1 AND 32768)",
            name="max_output_tokens",
        ),
        CheckConstraint(
            "pricing_basis IN ('token', 'audio_minute')",
            name="pricing_basis",
        ),
        CheckConstraint(
            "((num_nonnulls(price_currency, "
            "input_price_nanos_per_million_tokens, "
            "cached_input_price_nanos_per_million_tokens, "
            "output_price_nanos_per_million_tokens, "
            "audio_price_nanos_per_minute) = 0) "
            "OR (capability <> 'transcription' AND pricing_basis = 'token' "
            "AND price_currency ~ '^[A-Z]{3}$' "
            "AND input_price_nanos_per_million_tokens BETWEEN 0 AND 1000000000000000 "
            "AND cached_input_price_nanos_per_million_tokens "
            "BETWEEN 0 AND 1000000000000000 "
            "AND output_price_nanos_per_million_tokens "
            "BETWEEN 0 AND 1000000000000000 "
            "AND audio_price_nanos_per_minute IS NULL) "
            "OR (capability = 'transcription' "
            "AND pricing_basis = 'audio_minute' "
            "AND price_currency ~ '^[A-Z]{3}$' "
            "AND input_price_nanos_per_million_tokens IS NULL "
            "AND cached_input_price_nanos_per_million_tokens IS NULL "
            "AND output_price_nanos_per_million_tokens IS NULL "
            "AND audio_price_nanos_per_minute BETWEEN 0 AND 1000000000000000)) "
            "IS TRUE",
            name="pricing",
        ),
        CheckConstraint(
            "(last_test_error_code IS NULL) = (last_test_error_stage IS NULL)",
            name="test_error_pair",
        ),
        CheckConstraint(
            "(last_test_id IS NULL) = (last_test_started_at IS NULL)",
            name="test_started",
        ),
        CheckConstraint(
            "last_test_completed_at IS NULL OR last_test_started_at IS NOT NULL",
            name="test_completed",
        ),
        CheckConstraint(
            "status NOT IN ('tested', 'published', 'retired') OR "
            "(last_test_completed_at IS NOT NULL AND last_test_error_code IS NULL)",
            name="successful_status",
        ),
        CheckConstraint(
            "(status IN ('published', 'retired')) = (published_at IS NOT NULL)",
            name="published_at",
        ),
        CheckConstraint(
            "(status = 'retired') = (retired_at IS NOT NULL)",
            name="retired_at",
        ),
        ForeignKeyConstraint(
            ["capability"],
            ["ai_config_capabilities.capability"],
            name="fk_ai_config_versions_capability",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("capability", "version", name="uq_ai_config_versions_version"),
        UniqueConstraint("id", "capability", name="uq_ai_config_versions_id_capability"),
        Index(
            "uq_ai_config_versions_published_capability",
            "capability",
            unique=True,
            postgresql_where=text("status = 'published'"),
        ),
        Index(
            "ix_ai_config_versions_capability_created",
            "capability",
            "created_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    capability: Mapped[str] = mapped_column(String(50))
    version: Mapped[int] = mapped_column(Integer)
    endpoint_url: Mapped[str] = mapped_column(String(2048))
    model: Mapped[str] = mapped_column(String(255))
    api_key_ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    timeout_seconds: Mapped[int] = mapped_column(Integer)
    max_output_tokens: Mapped[int | None] = mapped_column(Integer)
    pricing_basis: Mapped[str] = mapped_column(
        String(20),
        default="token",
        server_default="token",
    )
    price_currency: Mapped[str | None] = mapped_column(String(3))
    input_price_nanos_per_million_tokens: Mapped[int | None] = mapped_column(
        BigInteger
    )
    cached_input_price_nanos_per_million_tokens: Mapped[int | None] = mapped_column(
        BigInteger
    )
    output_price_nanos_per_million_tokens: Mapped[int | None] = mapped_column(
        BigInteger
    )
    audio_price_nanos_per_minute: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(20), default="draft", server_default="draft")
    last_test_id: Mapped[UUID | None]
    last_test_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_error_code: Mapped[str | None] = mapped_column(String(80))
    last_test_error_stage: Mapped[str | None] = mapped_column(String(40))
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    last_tested_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    published_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AIConfigFallback(Base):
    __tablename__ = "ai_config_fallbacks"
    __table_args__ = (
        CheckConstraint("position BETWEEN 1 AND 2", name="position"),
        ForeignKeyConstraint(
            ["capability"],
            ["ai_config_capabilities.capability"],
            name="fk_ai_config_fallbacks_capability",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["config_version_id", "capability"],
            ["ai_config_versions.id", "ai_config_versions.capability"],
            name="fk_ai_config_fallbacks_config",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "capability",
            "config_version_id",
            name="uq_ai_config_fallbacks_config",
        ),
    )

    capability: Mapped[str] = mapped_column(String(50), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    config_version_id: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class TemporaryMedia(Base):
    __tablename__ = "temporary_media"
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('calendar_screenshot', 'voice_transcription')",
            name="purpose",
        ),
        CheckConstraint(
            "(purpose = 'calendar_screenshot' "
            "AND content_type IN ('image/png', 'image/jpeg', 'image/webp')) "
            "OR (purpose = 'voice_transcription' AND content_type = 'audio/wav')",
            name="content_type",
        ),
        CheckConstraint("length(btrim(original_filename)) > 0", name="filename"),
        CheckConstraint("length(content_sha256) = 64", name="content_sha256"),
        CheckConstraint(
            "(purpose = 'calendar_screenshot' "
            "AND byte_size BETWEEN 1 AND 5242880) "
            "OR (purpose = 'voice_transcription' "
            "AND byte_size BETWEEN 45 AND 2097152)",
            name="byte_size",
        ),
        CheckConstraint(
            "width IS NULL OR width BETWEEN 1 AND 20000000",
            name="width",
        ),
        CheckConstraint(
            "height IS NULL OR height BETWEEN 1 AND 20000000",
            name="height",
        ),
        CheckConstraint("width::bigint * height::bigint <= 20000000", name="pixels"),
        CheckConstraint(
            "(purpose = 'calendar_screenshot' "
            "AND width IS NOT NULL AND height IS NOT NULL "
            "AND duration_ms IS NULL AND sample_rate_hz IS NULL "
            "AND channel_count IS NULL AND sample_width_bits IS NULL) "
            "OR (purpose = 'voice_transcription' "
            "AND width IS NULL AND height IS NULL "
            "AND duration_ms BETWEEN 250 AND 60000 "
            "AND sample_rate_hz = 16000 "
            "AND channel_count = 1 "
            "AND sample_width_bits = 16)",
            name="media_shape",
        ),
        CheckConstraint(
            "(encrypted_content IS NULL) = "
            "(deleted_at IS NOT NULL AND deletion_reason IS NOT NULL)",
            name="deletion_state",
        ),
        CheckConstraint(
            "deletion_reason IS NULL OR deletion_reason IN "
            "('confirmed', 'accepted', 'discarded', 'cancelled', "
            "'expired', 'account_deletion')",
            name="deletion_reason",
        ),
        UniqueConstraint("id", "owner_id", name="uq_temporary_media_id_owner"),
        UniqueConstraint(
            "id",
            "purpose",
            "owner_id",
            name="uq_temporary_media_id_purpose_owner",
        ),
        Index("ix_temporary_media_owner_created", "owner_id", "created_at", "id"),
        Index(
            "ix_temporary_media_expiry",
            "expires_at",
            postgresql_where=text("encrypted_content IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    purpose: Mapped[str] = mapped_column(String(40))
    original_filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(40))
    content_sha256: Mapped[str] = mapped_column(String(64))
    byte_size: Mapped[int] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    sample_rate_hz: Mapped[int | None] = mapped_column(Integer)
    channel_count: Mapped[int | None] = mapped_column(Integer)
    sample_width_bits: Mapped[int | None] = mapped_column(Integer)
    encrypted_content: Mapped[bytes | None] = mapped_column(LargeBinary)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deletion_reason: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AIJob(Base):
    __tablename__ = "ai_jobs"
    __table_args__ = (
        CheckConstraint(
            "capability IN "
            "('calendar_screenshot', 'conversation', 'planning', 'review', "
            "'transcription')",
            name="capability",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'timed_out', 'cancelled')",
            name="status",
        ),
        CheckConstraint(
            "quota_mode IN ('platform', 'user')",
            name="quota_mode",
        ),
        CheckConstraint("jsonb_typeof(input_summary) = 'object'", name="input_summary"),
        CheckConstraint(
            "output IS NULL OR jsonb_typeof(output) = 'object'",
            name="output",
        ),
        CheckConstraint("attempt_count BETWEEN 0 AND max_attempts", name="attempt_count"),
        CheckConstraint("max_attempts BETWEEN 1 AND 5", name="max_attempts"),
        CheckConstraint("last_event_sequence >= 0", name="last_event_sequence"),
        CheckConstraint(
            "(capability = 'calendar_screenshot' "
            "AND conversation_id IS NULL "
            "AND planning_snapshot_id IS NULL AND review_session_id IS NULL "
            "AND review_snapshot_id IS NULL "
            "AND (input_media_id IS NOT NULL "
            "OR (input_media_id IS NULL "
            "AND input_summary->>'source_type' IN ('public_url', 'text')))) "
            "OR (capability = 'conversation' "
            "AND input_media_id IS NULL AND conversation_id IS NOT NULL "
            "AND planning_snapshot_id IS NULL AND review_session_id IS NULL "
            "AND review_snapshot_id IS NULL) "
            "OR (capability = 'planning' "
            "AND input_media_id IS NULL AND planning_snapshot_id IS NOT NULL "
            "AND review_session_id IS NULL AND review_snapshot_id IS NULL) "
            "OR (capability = 'review' "
            "AND input_media_id IS NULL AND conversation_id IS NULL "
            "AND planning_snapshot_id IS NULL AND review_session_id IS NOT NULL "
            "AND review_snapshot_id IS NOT NULL) "
            "OR (capability = 'transcription' "
            "AND input_media_id IS NOT NULL AND conversation_id IS NULL "
            "AND planning_snapshot_id IS NULL AND review_session_id IS NULL "
            "AND review_snapshot_id IS NULL)",
            name="input_reference",
        ),
        CheckConstraint(
            "(status = 'running') = (lease_id IS NOT NULL AND lease_until IS NOT NULL)",
            name="lease_state",
        ),
        CheckConstraint(
            "(status IN ('succeeded', 'failed', 'timed_out', 'cancelled')) "
            "= (finished_at IS NOT NULL)",
            name="finished_state",
        ),
        CheckConstraint(
            "(status = 'succeeded') = (output IS NOT NULL)",
            name="output_state",
        ),
        CheckConstraint(
            "(error_code IS NULL) = (error_message IS NULL)",
            name="error_pair",
        ),
        CheckConstraint(
            "status NOT IN ('succeeded', 'cancelled') OR error_code IS NULL",
            name="success_error",
        ),
        ForeignKeyConstraint(
            ["config_version_id", "capability"],
            ["ai_config_versions.id", "ai_config_versions.capability"],
            name="fk_ai_jobs_config_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["input_media_id", "owner_id"],
            ["temporary_media.id", "temporary_media.owner_id"],
            name="fk_ai_jobs_input_media_owner",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["conversation_id", "owner_id"],
            ["ai_conversations.id", "ai_conversations.owner_id"],
            name="fk_ai_jobs_conversation_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["planning_snapshot_id", "owner_id"],
            ["planning_input_snapshots.id", "planning_input_snapshots.owner_id"],
            name="fk_ai_jobs_planning_snapshot_owner",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["review_session_id", "owner_id"],
            ["review_sessions.id", "review_sessions.owner_id"],
            name="fk_ai_jobs_review_session_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["review_snapshot_id", "owner_id"],
            ["review_input_snapshots.id", "review_input_snapshots.owner_id"],
            name="fk_ai_jobs_review_snapshot_owner",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", "owner_id", name="uq_ai_jobs_id_owner"),
        UniqueConstraint(
            "id",
            "capability",
            "owner_id",
            name="uq_ai_jobs_id_capability_owner",
        ),
        UniqueConstraint(
            "id",
            "input_media_id",
            "owner_id",
            name="uq_ai_jobs_id_input_media_owner",
        ),
        UniqueConstraint("input_media_id", name="uq_ai_jobs_input_media"),
        UniqueConstraint(
            "planning_snapshot_id",
            name="uq_ai_jobs_planning_snapshot",
        ),
        UniqueConstraint(
            "review_snapshot_id",
            name="uq_ai_jobs_review_snapshot",
        ),
        Index("ix_ai_jobs_owner_created", "owner_id", "created_at", "id"),
        Index(
            "ix_ai_jobs_due",
            "available_at",
            "deadline_at",
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
        Index(
            "ix_ai_jobs_capability_due",
            "capability",
            "available_at",
            "deadline_at",
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
        Index(
            "uq_ai_jobs_active_conversation",
            "conversation_id",
            unique=True,
            postgresql_where=text(
                "conversation_id IS NOT NULL AND status IN ('queued', 'running')"
            ),
        ),
        Index(
            "uq_ai_jobs_active_review_session",
            "review_session_id",
            unique=True,
            postgresql_where=text(
                "review_session_id IS NOT NULL AND status IN ('queued', 'running')"
            ),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    capability: Mapped[str] = mapped_column(String(50))
    config_version_id: Mapped[UUID]
    input_media_id: Mapped[UUID | None]
    conversation_id: Mapped[UUID | None]
    planning_snapshot_id: Mapped[UUID | None]
    review_session_id: Mapped[UUID | None]
    review_snapshot_id: Mapped[UUID | None]
    quota_mode: Mapped[str] = mapped_column(
        String(16),
        default="platform",
        server_default="platform",
    )
    status: Mapped[str] = mapped_column(String(20), default="queued", server_default="queued")
    input_summary: Mapped[dict] = mapped_column(JSONB)
    output: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    last_event_sequence: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
    )
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_id: Mapped[UUID | None]
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VoiceTranscription(Base):
    __tablename__ = "voice_transcriptions"
    __table_args__ = (
        CheckConstraint("capability = 'transcription'", name="capability"),
        CheckConstraint(
            "media_purpose = 'voice_transcription'",
            name="media_purpose",
        ),
        CheckConstraint(
            "purpose IN ('task_title', 'difficulty_content')",
            name="purpose",
        ),
        CheckConstraint(
            "resolution IN ('pending', 'accepted', 'discarded', 'expired')",
            name="resolution",
        ),
        CheckConstraint(
            "(transcript IS NULL) = (transcript_sha256 IS NULL)",
            name="transcript_pair",
        ),
        CheckConstraint(
            "transcript IS NULL OR char_length(btrim(transcript)) BETWEEN 1 AND 4000",
            name="transcript",
        ),
        CheckConstraint(
            "transcript_sha256 IS NULL OR length(transcript_sha256) = 64",
            name="transcript_sha256",
        ),
        CheckConstraint(
            "language IS NULL OR char_length(btrim(language)) BETWEEN 1 AND 32",
            name="language",
        ),
        CheckConstraint(
            "(transcript IS NULL OR ready_at IS NOT NULL) "
            "AND (resolution <> 'accepted' OR ready_at IS NOT NULL)",
            name="ready_state",
        ),
        CheckConstraint(
            "(resolution = 'pending' AND resolved_at IS NULL) "
            "OR (resolution <> 'pending' AND resolved_at IS NOT NULL)",
            name="resolution_state",
        ),
        CheckConstraint(
            "resolution NOT IN ('discarded', 'expired') "
            "OR (transcript IS NULL AND text_purged_at IS NOT NULL)",
            name="discard_state",
        ),
        CheckConstraint(
            "text_purged_at IS NULL OR transcript IS NULL",
            name="text_purged",
        ),
        CheckConstraint("expires_at > created_at", name="expiry"),
        CheckConstraint("version >= 1", name="version"),
        ForeignKeyConstraint(
            ["job_id", "capability", "owner_id"],
            ["ai_jobs.id", "ai_jobs.capability", "ai_jobs.owner_id"],
            name="fk_voice_transcriptions_job_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["job_id", "media_id", "owner_id"],
            ["ai_jobs.id", "ai_jobs.input_media_id", "ai_jobs.owner_id"],
            name="fk_voice_transcriptions_job_media_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["media_id", "media_purpose", "owner_id"],
            ["temporary_media.id", "temporary_media.purpose", "temporary_media.owner_id"],
            name="fk_voice_transcriptions_media_owner",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "id",
            "owner_id",
            name="uq_voice_transcriptions_id_owner",
        ),
        UniqueConstraint("job_id", name="uq_voice_transcriptions_job"),
        UniqueConstraint("media_id", name="uq_voice_transcriptions_media"),
        Index(
            "ix_voice_transcriptions_owner_purpose_created",
            "owner_id",
            "purpose",
            "created_at",
            "id",
        ),
        Index(
            "ix_voice_transcriptions_expiry",
            "expires_at",
            "id",
            postgresql_where=text(
                "resolution = 'pending' OR transcript IS NOT NULL"
            ),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    job_id: Mapped[UUID]
    media_id: Mapped[UUID]
    capability: Mapped[str] = mapped_column(
        String(50),
        default="transcription",
        server_default="transcription",
    )
    media_purpose: Mapped[str] = mapped_column(
        String(40),
        default="voice_transcription",
        server_default="voice_transcription",
    )
    purpose: Mapped[str] = mapped_column(String(32))
    transcript: Mapped[str | None] = mapped_column(Text)
    transcript_sha256: Mapped[str | None] = mapped_column(String(64))
    language: Mapped[str | None] = mapped_column(String(32))
    resolution: Mapped[str] = mapped_column(
        String(16),
        default="pending",
        server_default="pending",
    )
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    text_purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class AIJobConfigRoute(Base):
    __tablename__ = "ai_job_config_routes"
    __table_args__ = (
        CheckConstraint("attempt_number BETWEEN 1 AND 5", name="attempt_number"),
        ForeignKeyConstraint(
            ["job_id", "capability", "owner_id"],
            ["ai_jobs.id", "ai_jobs.capability", "ai_jobs.owner_id"],
            name="fk_ai_job_config_routes_job",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["config_version_id", "capability"],
            ["ai_config_versions.id", "ai_config_versions.capability"],
            name="fk_ai_job_config_routes_config",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "job_id",
            "attempt_number",
            "config_version_id",
            name="uq_ai_job_config_routes_attempt_config",
        ),
        Index(
            "ix_ai_job_config_routes_owner_job",
            "owner_id",
            "job_id",
            "attempt_number",
        ),
    )

    job_id: Mapped[UUID] = mapped_column(primary_key=True)
    attempt_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[UUID]
    capability: Mapped[str] = mapped_column(String(50))
    config_version_id: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class AIJobAttempt(Base):
    __tablename__ = "ai_job_attempts"
    __table_args__ = (
        CheckConstraint("attempt_number BETWEEN 1 AND 5", name="attempt_number"),
        CheckConstraint(
            "status IN ('running', 'succeeded', 'retryable_failed', 'failed', "
            "'timed_out', 'cancelled', 'abandoned')",
            name="status",
        ),
        CheckConstraint(
            "(status = 'running') = (finished_at IS NULL)",
            name="finished_state",
        ),
        CheckConstraint(
            "(error_code IS NULL) = (error_stage IS NULL)",
            name="error_pair",
        ),
        ForeignKeyConstraint(
            ["job_id", "owner_id"],
            ["ai_jobs.id", "ai_jobs.owner_id"],
            name="fk_ai_job_attempts_job_owner",
            ondelete="CASCADE",
        ),
        UniqueConstraint("job_id", "attempt_number", name="uq_ai_job_attempts_number"),
        UniqueConstraint("job_id", "lease_id", name="uq_ai_job_attempts_lease"),
        Index("ix_ai_job_attempts_owner_job", "owner_id", "job_id", "attempt_number"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    job_id: Mapped[UUID]
    owner_id: Mapped[UUID]
    attempt_number: Mapped[int] = mapped_column(Integer)
    lease_id: Mapped[UUID]
    status: Mapped[str] = mapped_column(String(24))
    provider_request_id: Mapped[str | None] = mapped_column(String(255))
    http_status: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_stage: Mapped[str | None] = mapped_column(String(40))
    provider_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AIProviderCost(Base):
    __tablename__ = "ai_provider_costs"
    __table_args__ = (
        CheckConstraint("attempt_number BETWEEN 1 AND 5", name="attempt_number"),
        CheckConstraint(
            "attempt_status IN ('succeeded', 'retryable_failed', 'failed', "
            "'timed_out', 'cancelled', 'abandoned')",
            name="attempt_status",
        ),
        CheckConstraint(
            "usage_source IN ('provider', 'estimated', 'measured', 'not_sent')",
            name="usage_source",
        ),
        CheckConstraint(
            "billing_basis IN ('token', 'audio_minute')",
            name="billing_basis",
        ),
        CheckConstraint(
            "(usage_source = 'not_sent') = (provider_started_at IS NULL)",
            name="provider_started_state",
        ),
        CheckConstraint(
            "(usage_source = 'estimated') = "
            "(estimation_reason IS NOT NULL AND estimator_version IS NOT NULL)",
            name="estimation_state",
        ),
        CheckConstraint(
            "input_tokens BETWEEN 0 AND 1000000000 "
            "AND cached_input_tokens BETWEEN 0 AND 1000000000 "
            "AND output_tokens BETWEEN 0 AND 1000000000 "
            "AND total_tokens BETWEEN 0 AND 2000000000 "
            "AND cached_input_tokens <= input_tokens",
            name="tokens",
        ),
        CheckConstraint(
            "usage_source <> 'not_sent' OR "
            "(input_tokens = 0 AND cached_input_tokens = 0 "
            "AND output_tokens = 0 AND total_tokens = 0)",
            name="not_sent_tokens",
        ),
        CheckConstraint(
            "jsonb_typeof(usage_details) = 'object'",
            name="usage_details",
        ),
        CheckConstraint(
            "(billing_basis = 'token' AND audio_duration_ms IS NULL) "
            "OR (billing_basis = 'audio_minute' "
            "AND audio_duration_ms BETWEEN 250 AND 60000)",
            name="audio_usage",
        ),
        CheckConstraint(
            "(num_nonnulls(price_currency, "
            "input_price_nanos_per_million_tokens, "
            "cached_input_price_nanos_per_million_tokens, "
            "output_price_nanos_per_million_tokens, "
            "input_cost_nanos, cached_input_cost_nanos, "
            "output_cost_nanos, audio_price_nanos_per_minute, "
            "audio_cost_nanos, total_cost_nanos) = 0 "
            "OR (billing_basis = 'token' "
            "AND num_nonnulls(price_currency, "
            "input_price_nanos_per_million_tokens, "
            "cached_input_price_nanos_per_million_tokens, "
            "output_price_nanos_per_million_tokens, "
            "input_cost_nanos, cached_input_cost_nanos, "
            "output_cost_nanos, total_cost_nanos) = 8 "
            "AND price_currency ~ '^[A-Z]{3}$' "
            "AND input_price_nanos_per_million_tokens BETWEEN 0 AND 1000000000000000 "
            "AND cached_input_price_nanos_per_million_tokens "
            "BETWEEN 0 AND 1000000000000000 "
            "AND output_price_nanos_per_million_tokens "
            "BETWEEN 0 AND 1000000000000000 "
            "AND input_cost_nanos >= 0 "
            "AND cached_input_cost_nanos >= 0 "
            "AND output_cost_nanos >= 0 "
            "AND total_cost_nanos = input_cost_nanos "
            "+ cached_input_cost_nanos + output_cost_nanos "
            "AND audio_price_nanos_per_minute IS NULL "
            "AND audio_cost_nanos IS NULL) "
            "OR (billing_basis = 'audio_minute' "
            "AND price_currency ~ '^[A-Z]{3}$' "
            "AND input_price_nanos_per_million_tokens IS NULL "
            "AND cached_input_price_nanos_per_million_tokens IS NULL "
            "AND output_price_nanos_per_million_tokens IS NULL "
            "AND input_cost_nanos IS NULL "
            "AND cached_input_cost_nanos IS NULL "
            "AND output_cost_nanos IS NULL "
            "AND audio_price_nanos_per_minute "
            "BETWEEN 0 AND 1000000000000000 "
            "AND audio_cost_nanos >= 0 "
            "AND total_cost_nanos = audio_cost_nanos)) "
            "IS TRUE",
            name="pricing",
        ),
        ForeignKeyConstraint(
            ["job_id", "owner_id"],
            ["ai_jobs.id", "ai_jobs.owner_id"],
            name="fk_ai_provider_costs_job_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["job_id", "attempt_number"],
            ["ai_job_attempts.job_id", "ai_job_attempts.attempt_number"],
            name="fk_ai_provider_costs_job_attempt",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "job_id",
            "attempt_number",
            name="uq_ai_provider_costs_job_attempt",
        ),
        UniqueConstraint(
            "id",
            "job_id",
            name="uq_ai_provider_costs_id_job",
        ),
        Index(
            "ix_ai_provider_costs_owner_created",
            "owner_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_ai_provider_costs_created",
            "created_at",
            "id",
        ),
        Index(
            "uq_ai_provider_costs_succeeded_job",
            "job_id",
            unique=True,
            postgresql_where=text("attempt_status = 'succeeded'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    job_id: Mapped[UUID]
    owner_id: Mapped[UUID]
    attempt_number: Mapped[int] = mapped_column(Integer)
    attempt_status: Mapped[str] = mapped_column(String(24))
    provider_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    usage_source: Mapped[str] = mapped_column(String(16))
    estimation_reason: Mapped[str | None] = mapped_column(String(40))
    estimator_version: Mapped[str | None] = mapped_column(String(40))
    input_tokens: Mapped[int] = mapped_column(BigInteger)
    cached_input_tokens: Mapped[int] = mapped_column(BigInteger)
    output_tokens: Mapped[int] = mapped_column(BigInteger)
    total_tokens: Mapped[int] = mapped_column(BigInteger)
    usage_details: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    billing_basis: Mapped[str] = mapped_column(
        String(20),
        default="token",
        server_default="token",
    )
    audio_duration_ms: Mapped[int | None] = mapped_column(Integer)
    price_currency: Mapped[str | None] = mapped_column(String(3))
    input_price_nanos_per_million_tokens: Mapped[int | None] = mapped_column(
        BigInteger
    )
    cached_input_price_nanos_per_million_tokens: Mapped[int | None] = mapped_column(
        BigInteger
    )
    output_price_nanos_per_million_tokens: Mapped[int | None] = mapped_column(
        BigInteger
    )
    audio_price_nanos_per_minute: Mapped[int | None] = mapped_column(BigInteger)
    input_cost_nanos: Mapped[int | None] = mapped_column(BigInteger)
    cached_input_cost_nanos: Mapped[int | None] = mapped_column(BigInteger)
    output_cost_nanos: Mapped[int | None] = mapped_column(BigInteger)
    audio_cost_nanos: Mapped[int | None] = mapped_column(BigInteger)
    total_cost_nanos: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
