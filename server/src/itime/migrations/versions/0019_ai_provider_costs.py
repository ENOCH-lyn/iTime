"""AI provider prices, usage, and per-attempt costs"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0019_ai_provider_costs"
down_revision = "0018_ai_governance"
branch_labels = None
depends_on = None

MAX_PRICE_NANOS_PER_MILLION = 1_000_000_000_000_000


def upgrade() -> None:
    op.add_column(
        "ai_config_versions",
        sa.Column("price_currency", sa.String(length=3), nullable=True),
    )
    op.add_column(
        "ai_config_versions",
        sa.Column(
            "input_price_nanos_per_million_tokens",
            sa.BigInteger(),
            nullable=True,
        ),
    )
    op.add_column(
        "ai_config_versions",
        sa.Column(
            "cached_input_price_nanos_per_million_tokens",
            sa.BigInteger(),
            nullable=True,
        ),
    )
    op.add_column(
        "ai_config_versions",
        sa.Column(
            "output_price_nanos_per_million_tokens",
            sa.BigInteger(),
            nullable=True,
        ),
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
        f"AND input_price_nanos_per_million_tokens BETWEEN 0 AND "
        f"{MAX_PRICE_NANOS_PER_MILLION} "
        f"AND cached_input_price_nanos_per_million_tokens BETWEEN 0 AND "
        f"{MAX_PRICE_NANOS_PER_MILLION} "
        f"AND output_price_nanos_per_million_tokens BETWEEN 0 AND "
        f"{MAX_PRICE_NANOS_PER_MILLION})",
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_ai_config_version_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF ROW(
                NEW.id,
                NEW.capability,
                NEW.version,
                NEW.endpoint_url,
                NEW.model,
                NEW.api_key_ciphertext,
                NEW.timeout_seconds,
                NEW.max_output_tokens,
                NEW.price_currency,
                NEW.input_price_nanos_per_million_tokens,
                NEW.cached_input_price_nanos_per_million_tokens,
                NEW.output_price_nanos_per_million_tokens,
                NEW.created_at
            ) IS DISTINCT FROM ROW(
                OLD.id,
                OLD.capability,
                OLD.version,
                OLD.endpoint_url,
                OLD.model,
                OLD.api_key_ciphertext,
                OLD.timeout_seconds,
                OLD.max_output_tokens,
                OLD.price_currency,
                OLD.input_price_nanos_per_million_tokens,
                OLD.cached_input_price_nanos_per_million_tokens,
                OLD.output_price_nanos_per_million_tokens,
                OLD.created_at
            ) THEN
                RAISE EXCEPTION 'AI configuration version fields are immutable'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )

    op.add_column(
        "ai_job_attempts",
        sa.Column(
            "provider_started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        op.f("ck_ai_job_attempts_provider_started_at"),
        "ai_job_attempts",
        "provider_started_at IS NULL OR provider_started_at >= started_at",
    )

    op.create_table(
        "ai_provider_costs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("attempt_status", sa.String(length=24), nullable=False),
        sa.Column(
            "provider_started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("usage_source", sa.String(length=16), nullable=False),
        sa.Column("estimation_reason", sa.String(length=40), nullable=True),
        sa.Column("estimator_version", sa.String(length=40), nullable=True),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False),
        sa.Column("cached_input_tokens", sa.BigInteger(), nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False),
        sa.Column("total_tokens", sa.BigInteger(), nullable=False),
        sa.Column(
            "usage_details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("price_currency", sa.String(length=3), nullable=True),
        sa.Column(
            "input_price_nanos_per_million_tokens",
            sa.BigInteger(),
            nullable=True,
        ),
        sa.Column(
            "cached_input_price_nanos_per_million_tokens",
            sa.BigInteger(),
            nullable=True,
        ),
        sa.Column(
            "output_price_nanos_per_million_tokens",
            sa.BigInteger(),
            nullable=True,
        ),
        sa.Column("input_cost_nanos", sa.BigInteger(), nullable=True),
        sa.Column("cached_input_cost_nanos", sa.BigInteger(), nullable=True),
        sa.Column("output_cost_nanos", sa.BigInteger(), nullable=True),
        sa.Column("total_cost_nanos", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "attempt_number BETWEEN 1 AND 5",
            name=op.f("ck_ai_provider_costs_attempt_number"),
        ),
        sa.CheckConstraint(
            "attempt_status IN ('succeeded', 'retryable_failed', 'failed', "
            "'timed_out', 'cancelled', 'abandoned')",
            name=op.f("ck_ai_provider_costs_attempt_status"),
        ),
        sa.CheckConstraint(
            "usage_source IN ('provider', 'estimated', 'not_sent')",
            name=op.f("ck_ai_provider_costs_usage_source"),
        ),
        sa.CheckConstraint(
            "(usage_source = 'estimated') = "
            "(estimation_reason IS NOT NULL AND estimator_version IS NOT NULL)",
            name=op.f("ck_ai_provider_costs_estimation_state"),
        ),
        sa.CheckConstraint(
            "input_tokens >= 0 AND cached_input_tokens >= 0 "
            "AND output_tokens >= 0 AND total_tokens >= 0 "
            "AND cached_input_tokens <= input_tokens",
            name=op.f("ck_ai_provider_costs_tokens"),
        ),
        sa.CheckConstraint(
            "usage_source <> 'not_sent' OR "
            "(input_tokens = 0 AND cached_input_tokens = 0 "
            "AND output_tokens = 0 AND total_tokens = 0)",
            name=op.f("ck_ai_provider_costs_not_sent_tokens"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(usage_details) = 'object'",
            name=op.f("ck_ai_provider_costs_usage_details"),
        ),
        sa.CheckConstraint(
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
            f"AND input_price_nanos_per_million_tokens BETWEEN 0 AND "
            f"{MAX_PRICE_NANOS_PER_MILLION} "
            f"AND cached_input_price_nanos_per_million_tokens BETWEEN 0 AND "
            f"{MAX_PRICE_NANOS_PER_MILLION} "
            f"AND output_price_nanos_per_million_tokens BETWEEN 0 AND "
            f"{MAX_PRICE_NANOS_PER_MILLION} "
            "AND input_cost_nanos >= 0 "
            "AND cached_input_cost_nanos >= 0 "
            "AND output_cost_nanos >= 0 "
            "AND total_cost_nanos = input_cost_nanos "
            "+ cached_input_cost_nanos + output_cost_nanos)",
            name=op.f("ck_ai_provider_costs_pricing"),
        ),
        sa.ForeignKeyConstraint(
            ["job_id", "owner_id"],
            ["ai_jobs.id", "ai_jobs.owner_id"],
            name="fk_ai_provider_costs_job_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_id", "attempt_number"],
            ["ai_job_attempts.job_id", "ai_job_attempts.attempt_number"],
            name="fk_ai_provider_costs_job_attempt",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_provider_costs")),
        sa.UniqueConstraint(
            "job_id",
            "attempt_number",
            name="uq_ai_provider_costs_job_attempt",
        ),
    )
    op.create_index(
        "ix_ai_provider_costs_owner_created",
        "ai_provider_costs",
        ["owner_id", "created_at", "id"],
    )
    op.create_index(
        "ix_ai_provider_costs_created",
        "ai_provider_costs",
        ["created_at", "id"],
    )
    op.execute(
        """
        CREATE FUNCTION prevent_ai_provider_cost_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'AI provider cost facts are immutable'
                USING ERRCODE = '23514';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_ai_provider_costs_immutable
        BEFORE UPDATE ON ai_provider_costs
        FOR EACH ROW
        EXECUTE FUNCTION prevent_ai_provider_cost_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM ai_provider_costs)
               OR EXISTS (
                   SELECT 1 FROM ai_job_attempts
                   WHERE provider_started_at IS NOT NULL
               )
               OR EXISTS (
                   SELECT 1 FROM ai_config_versions
                   WHERE price_currency IS NOT NULL
                      OR input_price_nanos_per_million_tokens IS NOT NULL
                      OR cached_input_price_nanos_per_million_tokens IS NOT NULL
                      OR output_price_nanos_per_million_tokens IS NOT NULL
               ) THEN
                RAISE EXCEPTION
                    'cannot downgrade 0019_ai_provider_costs while provider cost data exists';
            END IF;
        END
        $$;
        """
    )
    op.execute(
        "DROP TRIGGER trg_ai_provider_costs_immutable ON ai_provider_costs"
    )
    op.execute("DROP FUNCTION prevent_ai_provider_cost_mutation()")
    op.drop_index(
        "ix_ai_provider_costs_created",
        table_name="ai_provider_costs",
    )
    op.drop_index(
        "ix_ai_provider_costs_owner_created",
        table_name="ai_provider_costs",
    )
    op.drop_table("ai_provider_costs")

    op.drop_constraint(
        op.f("ck_ai_job_attempts_provider_started_at"),
        "ai_job_attempts",
        type_="check",
    )
    op.drop_column("ai_job_attempts", "provider_started_at")

    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_ai_config_version_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF ROW(
                NEW.id,
                NEW.capability,
                NEW.version,
                NEW.endpoint_url,
                NEW.model,
                NEW.api_key_ciphertext,
                NEW.timeout_seconds,
                NEW.max_output_tokens,
                NEW.created_at
            ) IS DISTINCT FROM ROW(
                OLD.id,
                OLD.capability,
                OLD.version,
                OLD.endpoint_url,
                OLD.model,
                OLD.api_key_ciphertext,
                OLD.timeout_seconds,
                OLD.max_output_tokens,
                OLD.created_at
            ) THEN
                RAISE EXCEPTION 'AI configuration version fields are immutable'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.drop_constraint(
        op.f("ck_ai_config_versions_pricing"),
        "ai_config_versions",
        type_="check",
    )
    op.drop_column(
        "ai_config_versions",
        "output_price_nanos_per_million_tokens",
    )
    op.drop_column(
        "ai_config_versions",
        "cached_input_price_nanos_per_million_tokens",
    )
    op.drop_column(
        "ai_config_versions",
        "input_price_nanos_per_million_tokens",
    )
    op.drop_column("ai_config_versions", "price_currency")
