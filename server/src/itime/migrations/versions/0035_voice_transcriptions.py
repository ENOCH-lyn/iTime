"""Add temporary voice transcriptions and audio-priced AI work."""

import sqlalchemy as sa
from alembic import op

revision = "0035_voice_transcriptions"
down_revision = "0034_calendar_lifecycle"
branch_labels = None
depends_on = None

OLD_CAPABILITIES = "('calendar_screenshot', 'conversation', 'planning', 'review')"
NEW_CAPABILITIES = (
    "('calendar_screenshot', 'conversation', 'planning', 'review', "
    "'transcription')"
)


def _replace_capability_constraints(*, expanded: bool) -> None:
    capabilities = NEW_CAPABILITIES if expanded else OLD_CAPABILITIES
    for table_name in (
        "ai_config_capabilities",
        "ai_config_versions",
        "ai_jobs",
        "ai_body_logs",
        "ai_quality_samples",
    ):
        op.drop_constraint(
            op.f(f"ck_{table_name}_capability"),
            table_name,
            type_="check",
        )
        op.create_check_constraint(
            op.f(f"ck_{table_name}_capability"),
            table_name,
            f"capability IN {capabilities}",
        )


def _replace_config_constraints(*, expanded: bool) -> None:
    op.drop_constraint(
        op.f("ck_ai_config_versions_max_output_tokens"),
        "ai_config_versions",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_ai_config_versions_pricing"),
        "ai_config_versions",
        type_="check",
    )
    if expanded:
        op.create_check_constraint(
            op.f("ck_ai_config_versions_pricing_basis"),
            "ai_config_versions",
            "pricing_basis IN ('token', 'audio_minute')",
        )
        op.create_check_constraint(
            op.f("ck_ai_config_versions_max_output_tokens"),
            "ai_config_versions",
            "(capability = 'transcription' AND max_output_tokens IS NULL) "
            "OR (capability <> 'transcription' "
            "AND max_output_tokens BETWEEN 1 AND 32768)",
        )
        op.create_check_constraint(
            op.f("ck_ai_config_versions_pricing"),
            "ai_config_versions",
            "((num_nonnulls(price_currency, "
            "input_price_nanos_per_million_tokens, "
            "cached_input_price_nanos_per_million_tokens, "
            "output_price_nanos_per_million_tokens, "
            "audio_price_nanos_per_minute) = 0"
            ") OR ("
            "capability <> 'transcription' AND pricing_basis = 'token' "
            "AND price_currency ~ '^[A-Z]{3}$' "
            "AND input_price_nanos_per_million_tokens "
            "BETWEEN 0 AND 1000000000000000 "
            "AND cached_input_price_nanos_per_million_tokens "
            "BETWEEN 0 AND 1000000000000000 "
            "AND output_price_nanos_per_million_tokens "
            "BETWEEN 0 AND 1000000000000000 "
            "AND audio_price_nanos_per_minute IS NULL"
            ") OR ("
            "capability = 'transcription' AND pricing_basis = 'audio_minute' "
            "AND price_currency ~ '^[A-Z]{3}$' "
            "AND input_price_nanos_per_million_tokens IS NULL "
            "AND cached_input_price_nanos_per_million_tokens IS NULL "
            "AND output_price_nanos_per_million_tokens IS NULL "
            "AND audio_price_nanos_per_minute BETWEEN 0 AND 1000000000000000"
            ")) IS TRUE",
        )
    else:
        op.create_check_constraint(
            op.f("ck_ai_config_versions_max_output_tokens"),
            "ai_config_versions",
            "max_output_tokens BETWEEN 1 AND 32768",
        )
        op.create_check_constraint(
            op.f("ck_ai_config_versions_pricing"),
            "ai_config_versions",
            "num_nonnulls(price_currency, "
            "input_price_nanos_per_million_tokens, "
            "cached_input_price_nanos_per_million_tokens, "
            "output_price_nanos_per_million_tokens) = 0 "
            "OR (num_nonnulls(price_currency, "
            "input_price_nanos_per_million_tokens, "
            "cached_input_price_nanos_per_million_tokens, "
            "output_price_nanos_per_million_tokens) = 4 "
            "AND price_currency ~ '^[A-Z]{3}$' "
            "AND input_price_nanos_per_million_tokens "
            "BETWEEN 0 AND 1000000000000000 "
            "AND cached_input_price_nanos_per_million_tokens "
            "BETWEEN 0 AND 1000000000000000 "
            "AND output_price_nanos_per_million_tokens "
            "BETWEEN 0 AND 1000000000000000)",
        )


def _replace_media_constraints(*, expanded: bool) -> None:
    for name in (
        "purpose",
        "content_type",
        "byte_size",
        "width",
        "height",
        "pixels",
        "deletion_reason",
    ):
        op.drop_constraint(
            op.f(f"ck_temporary_media_{name}"),
            "temporary_media",
            type_="check",
        )
    if expanded:
        op.create_check_constraint(
            op.f("ck_temporary_media_purpose"),
            "temporary_media",
            "purpose IN ('calendar_screenshot', 'voice_transcription')",
        )
        op.create_check_constraint(
            op.f("ck_temporary_media_content_type"),
            "temporary_media",
            "(purpose = 'calendar_screenshot' "
            "AND content_type IN ('image/png', 'image/jpeg', 'image/webp')) "
            "OR (purpose = 'voice_transcription' AND content_type = 'audio/wav')",
        )
        op.create_check_constraint(
            op.f("ck_temporary_media_byte_size"),
            "temporary_media",
            "(purpose = 'calendar_screenshot' "
            "AND byte_size BETWEEN 1 AND 5242880) "
            "OR (purpose = 'voice_transcription' "
            "AND byte_size BETWEEN 45 AND 2097152)",
        )
        op.create_check_constraint(
            op.f("ck_temporary_media_width"),
            "temporary_media",
            "width IS NULL OR width BETWEEN 1 AND 20000000",
        )
        op.create_check_constraint(
            op.f("ck_temporary_media_height"),
            "temporary_media",
            "height IS NULL OR height BETWEEN 1 AND 20000000",
        )
        op.create_check_constraint(
            op.f("ck_temporary_media_pixels"),
            "temporary_media",
            "width IS NULL OR height IS NULL "
            "OR width::bigint * height::bigint <= 20000000",
        )
        op.create_check_constraint(
            op.f("ck_temporary_media_media_shape"),
            "temporary_media",
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
        )
        op.create_check_constraint(
            op.f("ck_temporary_media_deletion_reason"),
            "temporary_media",
            "deletion_reason IS NULL OR deletion_reason IN "
            "('confirmed', 'accepted', 'discarded', 'cancelled', "
            "'expired', 'account_deletion')",
        )
    else:
        op.create_check_constraint(
            op.f("ck_temporary_media_purpose"),
            "temporary_media",
            "purpose IN ('calendar_screenshot')",
        )
        op.create_check_constraint(
            op.f("ck_temporary_media_content_type"),
            "temporary_media",
            "content_type IN ('image/png', 'image/jpeg', 'image/webp')",
        )
        op.create_check_constraint(
            op.f("ck_temporary_media_byte_size"),
            "temporary_media",
            "byte_size BETWEEN 1 AND 5242880",
        )
        op.create_check_constraint(
            op.f("ck_temporary_media_width"),
            "temporary_media",
            "width BETWEEN 1 AND 20000000",
        )
        op.create_check_constraint(
            op.f("ck_temporary_media_height"),
            "temporary_media",
            "height BETWEEN 1 AND 20000000",
        )
        op.create_check_constraint(
            op.f("ck_temporary_media_pixels"),
            "temporary_media",
            "width::bigint * height::bigint <= 20000000",
        )
        op.create_check_constraint(
            op.f("ck_temporary_media_deletion_reason"),
            "temporary_media",
            "deletion_reason IS NULL OR deletion_reason IN "
            "('confirmed', 'expired', 'account_deletion')",
        )


def _replace_job_input_constraint(*, expanded: bool) -> None:
    op.drop_constraint(
        op.f("ck_ai_jobs_input_reference"),
        "ai_jobs",
        type_="check",
    )
    branches = (
        "(capability = 'calendar_screenshot' "
        "AND input_media_id IS NOT NULL AND conversation_id IS NULL "
        "AND planning_snapshot_id IS NULL AND review_session_id IS NULL "
        "AND review_snapshot_id IS NULL) "
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
        "AND review_snapshot_id IS NOT NULL)"
    )
    if expanded:
        branches += (
            " OR (capability = 'transcription' "
            "AND input_media_id IS NOT NULL AND conversation_id IS NULL "
            "AND planning_snapshot_id IS NULL AND review_session_id IS NULL "
            "AND review_snapshot_id IS NULL)"
        )
    op.create_check_constraint(
        op.f("ck_ai_jobs_input_reference"),
        "ai_jobs",
        branches,
    )


def _replace_provider_cost_constraints(*, expanded: bool) -> None:
    for name in ("usage_source", "estimation_state", "pricing"):
        op.drop_constraint(
            op.f(f"ck_ai_provider_costs_{name}"),
            "ai_provider_costs",
            type_="check",
        )
    if expanded:
        op.create_check_constraint(
            op.f("ck_ai_provider_costs_usage_source"),
            "ai_provider_costs",
            "usage_source IN ('provider', 'estimated', 'measured', 'not_sent')",
        )
        op.create_check_constraint(
            op.f("ck_ai_provider_costs_estimation_state"),
            "ai_provider_costs",
            "(usage_source = 'estimated') = "
            "(estimation_reason IS NOT NULL AND estimator_version IS NOT NULL)",
        )
        op.create_check_constraint(
            op.f("ck_ai_provider_costs_audio_usage"),
            "ai_provider_costs",
            "(billing_basis = 'token' AND audio_duration_ms IS NULL) "
            "OR (billing_basis = 'audio_minute' "
            "AND audio_duration_ms BETWEEN 250 AND 60000)",
        )
        op.create_check_constraint(
            op.f("ck_ai_provider_costs_pricing"),
            "ai_provider_costs",
            "(("
            "num_nonnulls(price_currency, "
            "input_price_nanos_per_million_tokens, "
            "cached_input_price_nanos_per_million_tokens, "
            "output_price_nanos_per_million_tokens, "
            "input_cost_nanos, cached_input_cost_nanos, output_cost_nanos, "
            "audio_price_nanos_per_minute, audio_cost_nanos, "
            "total_cost_nanos) = 0"
            ") OR ("
            "billing_basis = 'token' "
            "AND num_nonnulls(price_currency, "
            "input_price_nanos_per_million_tokens, "
            "cached_input_price_nanos_per_million_tokens, "
            "output_price_nanos_per_million_tokens, "
            "input_cost_nanos, cached_input_cost_nanos, output_cost_nanos, "
            "total_cost_nanos) = 8 "
            "AND audio_price_nanos_per_minute IS NULL "
            "AND audio_cost_nanos IS NULL "
            "AND price_currency ~ '^[A-Z]{3}$' "
            "AND input_price_nanos_per_million_tokens "
            "BETWEEN 0 AND 1000000000000000 "
            "AND cached_input_price_nanos_per_million_tokens "
            "BETWEEN 0 AND 1000000000000000 "
            "AND output_price_nanos_per_million_tokens "
            "BETWEEN 0 AND 1000000000000000 "
            "AND input_cost_nanos >= 0 "
            "AND cached_input_cost_nanos >= 0 "
            "AND output_cost_nanos >= 0 "
            "AND total_cost_nanos = input_cost_nanos "
            "+ cached_input_cost_nanos + output_cost_nanos"
            ") OR ("
            "billing_basis = 'audio_minute' "
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
            "AND total_cost_nanos = audio_cost_nanos"
            ")) IS TRUE",
        )
    else:
        op.create_check_constraint(
            op.f("ck_ai_provider_costs_usage_source"),
            "ai_provider_costs",
            "usage_source IN ('provider', 'estimated', 'not_sent')",
        )
        op.create_check_constraint(
            op.f("ck_ai_provider_costs_estimation_state"),
            "ai_provider_costs",
            "(usage_source = 'estimated') = "
            "(estimation_reason IS NOT NULL AND estimator_version IS NOT NULL)",
        )
        op.create_check_constraint(
            op.f("ck_ai_provider_costs_pricing"),
            "ai_provider_costs",
            "num_nonnulls(price_currency, "
            "input_price_nanos_per_million_tokens, "
            "cached_input_price_nanos_per_million_tokens, "
            "output_price_nanos_per_million_tokens, "
            "input_cost_nanos, cached_input_cost_nanos, "
            "output_cost_nanos, total_cost_nanos) = 0 "
            "OR (num_nonnulls(price_currency, "
            "input_price_nanos_per_million_tokens, "
            "cached_input_price_nanos_per_million_tokens, "
            "output_price_nanos_per_million_tokens, "
            "input_cost_nanos, cached_input_cost_nanos, "
            "output_cost_nanos, total_cost_nanos) = 8 "
            "AND price_currency ~ '^[A-Z]{3}$' "
            "AND input_price_nanos_per_million_tokens "
            "BETWEEN 0 AND 1000000000000000 "
            "AND cached_input_price_nanos_per_million_tokens "
            "BETWEEN 0 AND 1000000000000000 "
            "AND output_price_nanos_per_million_tokens "
            "BETWEEN 0 AND 1000000000000000 "
            "AND input_cost_nanos >= 0 "
            "AND cached_input_cost_nanos >= 0 "
            "AND output_cost_nanos >= 0 "
            "AND total_cost_nanos = input_cost_nanos "
            "+ cached_input_cost_nanos + output_cost_nanos)",
        )


def upgrade() -> None:
    _replace_capability_constraints(expanded=True)
    op.execute(
        """
        INSERT INTO ai_config_capabilities (capability, next_version)
        VALUES ('transcription', 1)
        ON CONFLICT (capability) DO NOTHING
        """
    )

    op.add_column(
        "ai_config_versions",
        sa.Column(
            "pricing_basis",
            sa.String(length=20),
            server_default="token",
            nullable=False,
        ),
    )
    op.add_column(
        "ai_config_versions",
        sa.Column("audio_price_nanos_per_minute", sa.BigInteger(), nullable=True),
    )
    op.alter_column(
        "ai_config_versions",
        "max_output_tokens",
        existing_type=sa.Integer(),
        nullable=True,
    )
    _replace_config_constraints(expanded=True)

    op.add_column(
        "temporary_media",
        sa.Column("duration_ms", sa.Integer(), nullable=True),
    )
    op.add_column(
        "temporary_media",
        sa.Column("sample_rate_hz", sa.Integer(), nullable=True),
    )
    op.add_column(
        "temporary_media",
        sa.Column("channel_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "temporary_media",
        sa.Column("sample_width_bits", sa.Integer(), nullable=True),
    )
    op.alter_column(
        "temporary_media",
        "width",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.alter_column(
        "temporary_media",
        "height",
        existing_type=sa.Integer(),
        nullable=True,
    )
    _replace_media_constraints(expanded=True)
    op.create_unique_constraint(
        "uq_temporary_media_id_purpose_owner",
        "temporary_media",
        ["id", "purpose", "owner_id"],
    )

    _replace_job_input_constraint(expanded=True)
    op.create_unique_constraint(
        "uq_ai_jobs_id_input_media_owner",
        "ai_jobs",
        ["id", "input_media_id", "owner_id"],
    )

    op.add_column(
        "ai_provider_costs",
        sa.Column(
            "billing_basis",
            sa.String(length=20),
            server_default="token",
            nullable=False,
        ),
    )
    op.add_column(
        "ai_provider_costs",
        sa.Column("audio_duration_ms", sa.Integer(), nullable=True),
    )
    op.add_column(
        "ai_provider_costs",
        sa.Column("audio_price_nanos_per_minute", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "ai_provider_costs",
        sa.Column("audio_cost_nanos", sa.BigInteger(), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_ai_provider_costs_billing_basis"),
        "ai_provider_costs",
        "billing_basis IN ('token', 'audio_minute')",
    )
    _replace_provider_cost_constraints(expanded=True)

    op.create_table(
        "voice_transcriptions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("media_id", sa.Uuid(), nullable=False),
        sa.Column(
            "capability",
            sa.String(length=50),
            server_default="transcription",
            nullable=False,
        ),
        sa.Column(
            "media_purpose",
            sa.String(length=40),
            server_default="voice_transcription",
            nullable=False,
        ),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("transcript", sa.Text(), nullable=True),
        sa.Column("transcript_sha256", sa.String(length=64), nullable=True),
        sa.Column("language", sa.String(length=32), nullable=True),
        sa.Column(
            "resolution",
            sa.String(length=16),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("text_purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "capability = 'transcription'",
            name=op.f("ck_voice_transcriptions_capability"),
        ),
        sa.CheckConstraint(
            "media_purpose = 'voice_transcription'",
            name=op.f("ck_voice_transcriptions_media_purpose"),
        ),
        sa.CheckConstraint(
            "purpose IN ('task_title', 'difficulty_content')",
            name=op.f("ck_voice_transcriptions_purpose"),
        ),
        sa.CheckConstraint(
            "resolution IN ('pending', 'accepted', 'discarded', 'expired')",
            name=op.f("ck_voice_transcriptions_resolution"),
        ),
        sa.CheckConstraint(
            "(transcript IS NULL) = (transcript_sha256 IS NULL)",
            name=op.f("ck_voice_transcriptions_transcript_pair"),
        ),
        sa.CheckConstraint(
            "transcript IS NULL OR char_length(btrim(transcript)) BETWEEN 1 AND 4000",
            name=op.f("ck_voice_transcriptions_transcript"),
        ),
        sa.CheckConstraint(
            "transcript_sha256 IS NULL OR length(transcript_sha256) = 64",
            name=op.f("ck_voice_transcriptions_transcript_sha256"),
        ),
        sa.CheckConstraint(
            "language IS NULL OR char_length(btrim(language)) BETWEEN 1 AND 32",
            name=op.f("ck_voice_transcriptions_language"),
        ),
        sa.CheckConstraint(
            "(transcript IS NULL OR ready_at IS NOT NULL) "
            "AND (resolution <> 'accepted' OR ready_at IS NOT NULL)",
            name=op.f("ck_voice_transcriptions_ready_state"),
        ),
        sa.CheckConstraint(
            "(resolution = 'pending' AND resolved_at IS NULL) "
            "OR (resolution <> 'pending' AND resolved_at IS NOT NULL)",
            name=op.f("ck_voice_transcriptions_resolution_state"),
        ),
        sa.CheckConstraint(
            "resolution NOT IN ('discarded', 'expired') "
            "OR (transcript IS NULL AND text_purged_at IS NOT NULL)",
            name=op.f("ck_voice_transcriptions_discard_state"),
        ),
        sa.CheckConstraint(
            "text_purged_at IS NULL OR transcript IS NULL",
            name=op.f("ck_voice_transcriptions_text_purged"),
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name=op.f("ck_voice_transcriptions_expiry"),
        ),
        sa.CheckConstraint(
            "version >= 1",
            name=op.f("ck_voice_transcriptions_version"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_voice_transcriptions_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_id", "capability", "owner_id"],
            ["ai_jobs.id", "ai_jobs.capability", "ai_jobs.owner_id"],
            name="fk_voice_transcriptions_job_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_id", "media_id", "owner_id"],
            ["ai_jobs.id", "ai_jobs.input_media_id", "ai_jobs.owner_id"],
            name="fk_voice_transcriptions_job_media_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["media_id", "media_purpose", "owner_id"],
            ["temporary_media.id", "temporary_media.purpose", "temporary_media.owner_id"],
            name="fk_voice_transcriptions_media_owner",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_voice_transcriptions")),
        sa.UniqueConstraint("id", "owner_id", name="uq_voice_transcriptions_id_owner"),
        sa.UniqueConstraint("job_id", name="uq_voice_transcriptions_job"),
        sa.UniqueConstraint("media_id", name="uq_voice_transcriptions_media"),
    )
    op.create_index(
        "ix_voice_transcriptions_owner_purpose_created",
        "voice_transcriptions",
        ["owner_id", "purpose", "created_at", "id"],
    )
    op.create_index(
        "ix_voice_transcriptions_expiry",
        "voice_transcriptions",
        ["expires_at", "id"],
        postgresql_where=sa.text(
            "resolution = 'pending' OR transcript IS NOT NULL"
        ),
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM voice_transcriptions)
               OR EXISTS (
                   SELECT 1 FROM ai_jobs WHERE capability='transcription'
               )
               OR EXISTS (
                   SELECT 1 FROM ai_config_versions
                   WHERE capability='transcription'
               )
               OR EXISTS (
                   SELECT 1 FROM temporary_media
                   WHERE purpose='voice_transcription'
               )
               OR EXISTS (
                   SELECT 1 FROM ai_provider_costs
                   WHERE billing_basis='audio_minute'
               )
            THEN
                RAISE EXCEPTION
                    'voice transcription data must be cleared before downgrade';
            END IF;
        END
        $$;
        """
    )
    op.drop_index(
        "ix_voice_transcriptions_expiry",
        table_name="voice_transcriptions",
    )
    op.drop_index(
        "ix_voice_transcriptions_owner_purpose_created",
        table_name="voice_transcriptions",
    )
    op.drop_table("voice_transcriptions")

    _replace_provider_cost_constraints(expanded=False)
    op.drop_constraint(
        op.f("ck_ai_provider_costs_audio_usage"),
        "ai_provider_costs",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_ai_provider_costs_billing_basis"),
        "ai_provider_costs",
        type_="check",
    )
    op.drop_column("ai_provider_costs", "audio_cost_nanos")
    op.drop_column("ai_provider_costs", "audio_price_nanos_per_minute")
    op.drop_column("ai_provider_costs", "audio_duration_ms")
    op.drop_column("ai_provider_costs", "billing_basis")

    op.drop_constraint(
        "uq_ai_jobs_id_input_media_owner",
        "ai_jobs",
        type_="unique",
    )
    _replace_job_input_constraint(expanded=False)

    op.drop_constraint(
        "uq_temporary_media_id_purpose_owner",
        "temporary_media",
        type_="unique",
    )
    _replace_media_constraints(expanded=False)
    op.alter_column(
        "temporary_media",
        "height",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.alter_column(
        "temporary_media",
        "width",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.drop_constraint(
        op.f("ck_temporary_media_media_shape"),
        "temporary_media",
        type_="check",
    )
    op.drop_column("temporary_media", "sample_width_bits")
    op.drop_column("temporary_media", "channel_count")
    op.drop_column("temporary_media", "sample_rate_hz")
    op.drop_column("temporary_media", "duration_ms")

    op.drop_constraint(
        op.f("ck_ai_config_versions_pricing_basis"),
        "ai_config_versions",
        type_="check",
    )
    _replace_config_constraints(expanded=False)
    op.drop_column("ai_config_versions", "audio_price_nanos_per_minute")
    op.drop_column("ai_config_versions", "pricing_basis")
    op.alter_column(
        "ai_config_versions",
        "max_output_tokens",
        existing_type=sa.Integer(),
        nullable=False,
    )

    op.execute(
        "DELETE FROM ai_config_capabilities WHERE capability='transcription'"
    )
    _replace_capability_constraints(expanded=False)
