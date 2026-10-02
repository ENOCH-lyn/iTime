"""calendar subscriptions"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0015_calendar_subscriptions"
down_revision = "0014_calendar_screenshot_imports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "calendar_subscriptions",
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("encrypted_url", sa.LargeBinary(), nullable=False),
        sa.Column("url_hmac", sa.String(length=64), nullable=False),
        sa.Column("display_url", sa.String(length=2048), nullable=False),
        sa.Column("display_host", sa.String(length=255), nullable=False),
        sa.Column("default_time_zone", sa.String(length=64), nullable=False),
        sa.Column(
            "lifecycle_status",
            sa.String(length=12),
            server_default="active",
            nullable=False,
        ),
        sa.Column(
            "refresh_status",
            sa.String(length=12),
            server_default="queued",
            nullable=False,
        ),
        sa.Column(
            "refresh_interval_minutes",
            sa.Integer(),
            server_default="360",
            nullable=False,
        ),
        sa.Column("next_refresh_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fetch_revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("generation", sa.Integer(), server_default="0", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lease_id", sa.Uuid(), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_change_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("etag", sa.String(length=1000), nullable=True),
        sa.Column("last_modified", sa.String(length=1000), nullable=True),
        sa.Column("encrypted_final_url", sa.LargeBinary(), nullable=True),
        sa.Column("final_url_hmac", sa.String(length=64), nullable=True),
        sa.Column("final_host", sa.String(length=255), nullable=True),
        sa.Column("content_sha256", sa.String(length=64), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("last_error_message", sa.String(length=500), nullable=True),
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
            "lifecycle_status IN ('active', 'paused')",
            name=op.f("ck_calendar_subscriptions_lifecycle_status"),
        ),
        sa.CheckConstraint(
            "refresh_status IN ('idle', 'queued', 'running', 'error')",
            name=op.f("ck_calendar_subscriptions_refresh_status"),
        ),
        sa.CheckConstraint(
            "refresh_interval_minutes BETWEEN 15 AND 10080",
            name=op.f("ck_calendar_subscriptions_refresh_interval_minutes"),
        ),
        sa.CheckConstraint(
            "length(url_hmac) = 64",
            name=op.f("ck_calendar_subscriptions_url_hmac"),
        ),
        sa.CheckConstraint(
            "length(btrim(display_url)) > 0",
            name=op.f("ck_calendar_subscriptions_display_url"),
        ),
        sa.CheckConstraint(
            "length(btrim(display_host)) > 0",
            name=op.f("ck_calendar_subscriptions_display_host"),
        ),
        sa.CheckConstraint(
            "length(btrim(default_time_zone)) > 0",
            name=op.f("ck_calendar_subscriptions_default_time_zone"),
        ),
        sa.CheckConstraint(
            "(refresh_status = 'running') = "
            "(lease_id IS NOT NULL AND lease_until IS NOT NULL)",
            name=op.f("ck_calendar_subscriptions_lease_state"),
        ),
        sa.CheckConstraint(
            "lifecycle_status = 'active' OR refresh_status NOT IN ('queued', 'running')",
            name=op.f("ck_calendar_subscriptions_paused_state"),
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name=op.f("ck_calendar_subscriptions_attempt_count"),
        ),
        sa.CheckConstraint(
            "generation >= 0",
            name=op.f("ck_calendar_subscriptions_generation"),
        ),
        sa.CheckConstraint(
            "fetch_revision >= 1",
            name=op.f("ck_calendar_subscriptions_fetch_revision"),
        ),
        sa.CheckConstraint(
            "consecutive_failures >= 0",
            name=op.f("ck_calendar_subscriptions_consecutive_failures"),
        ),
        sa.ForeignKeyConstraint(
            ["source_id", "owner_id"],
            ["calendar_sources.id", "calendar_sources.owner_id"],
            name="fk_calendar_subscriptions_source_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("source_id", name=op.f("pk_calendar_subscriptions")),
        sa.UniqueConstraint(
            "source_id",
            "owner_id",
            name="uq_calendar_subscriptions_source_owner",
        ),
    )
    op.create_index(
        "ix_calendar_subscriptions_due",
        "calendar_subscriptions",
        ["lifecycle_status", "refresh_status", "next_refresh_at", "source_id"],
    )
    op.create_index(
        "ix_calendar_subscriptions_owner_updated",
        "calendar_subscriptions",
        ["owner_id", "updated_at", "source_id"],
    )

    op.create_table(
        "calendar_subscription_refreshes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("lease_id", sa.Uuid(), nullable=False),
        sa.Column("fetch_revision", sa.Integer(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("trigger", sa.String(length=12), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="running", nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("final_host", sa.String(length=255), nullable=True),
        sa.Column("content_sha256", sa.String(length=64), nullable=True),
        sa.Column("event_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("changed_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cancelled_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "item_errors",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "trigger IN ('initial', 'manual', 'automatic')",
            name=op.f("ck_calendar_subscription_refreshes_trigger"),
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'not_modified', 'failed', 'stale')",
            name=op.f("ck_calendar_subscription_refreshes_status"),
        ),
        sa.CheckConstraint(
            "attempt_number >= 1",
            name=op.f("ck_calendar_subscription_refreshes_attempt_number"),
        ),
        sa.CheckConstraint(
            "fetch_revision >= 1",
            name=op.f("ck_calendar_subscription_refreshes_fetch_revision"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(item_errors) = 'array'",
            name=op.f("ck_calendar_subscription_refreshes_item_errors"),
        ),
        sa.ForeignKeyConstraint(
            ["source_id", "owner_id"],
            ["calendar_subscriptions.source_id", "calendar_subscriptions.owner_id"],
            name="fk_calendar_subscription_refreshes_source_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_calendar_subscription_refreshes")),
        sa.UniqueConstraint(
            "source_id",
            "attempt_number",
            name="uq_subscription_refresh_attempt",
        ),
    )
    op.create_index(
        "ix_calendar_subscription_refreshes_owner_source",
        "calendar_subscription_refreshes",
        ["owner_id", "source_id", "started_at"],
    )

    op.create_table(
        "calendar_subscription_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("uid", sa.String(length=512), nullable=False),
        sa.Column("recurrence_id", sa.String(length=512), server_default="", nullable=False),
        sa.Column(
            "baseline",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("parse_status", sa.String(length=12), server_default="valid", nullable=False),
        sa.Column("source_status", sa.String(length=12), server_default="active", nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("first_seen_generation", sa.Integer(), nullable=False),
        sa.Column("last_seen_generation", sa.Integer(), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("missing_at", sa.DateTime(timezone=True), nullable=True),
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
            "length(btrim(uid)) > 0",
            name=op.f("ck_calendar_subscription_events_uid"),
        ),
        sa.CheckConstraint(
            "parse_status IN ('valid', 'stale_error', 'error')",
            name=op.f("ck_calendar_subscription_events_parse_status"),
        ),
        sa.CheckConstraint(
            "source_status IN ('active', 'cancelled')",
            name=op.f("ck_calendar_subscription_events_source_status"),
        ),
        sa.CheckConstraint(
            "(parse_status = 'error') = (baseline IS NULL)",
            name=op.f("ck_calendar_subscription_events_baseline_state"),
        ),
        sa.CheckConstraint(
            "baseline IS NULL OR jsonb_typeof(baseline) = 'object'",
            name=op.f("ck_calendar_subscription_events_baseline"),
        ),
        sa.CheckConstraint(
            "length(content_sha256) = 64",
            name=op.f("ck_calendar_subscription_events_content_sha256"),
        ),
        sa.CheckConstraint(
            "first_seen_generation >= 1",
            name=op.f("ck_calendar_subscription_events_first_seen_generation"),
        ),
        sa.CheckConstraint(
            "last_seen_generation >= 1",
            name=op.f("ck_calendar_subscription_events_last_seen_generation"),
        ),
        sa.ForeignKeyConstraint(
            ["source_id", "owner_id"],
            ["calendar_subscriptions.source_id", "calendar_subscriptions.owner_id"],
            name="fk_calendar_subscription_events_source_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_calendar_subscription_events")),
        sa.UniqueConstraint(
            "id",
            "owner_id",
            name="uq_calendar_subscription_events_id_owner",
        ),
        sa.UniqueConstraint(
            "source_id",
            "uid",
            "recurrence_id",
            name="uq_calendar_subscription_events_identity",
        ),
    )
    op.create_index(
        "ix_calendar_subscription_events_owner_source",
        "calendar_subscription_events",
        ["owner_id", "source_id", "uid"],
    )

    op.create_table(
        "calendar_subscription_overrides",
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column(
            "values",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "source_values",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
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
            "jsonb_typeof(values) = 'object'",
            name=op.f("ck_calendar_subscription_overrides_values"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(source_values) = 'object'",
            name=op.f("ck_calendar_subscription_overrides_source_values"),
        ),
        sa.ForeignKeyConstraint(
            ["event_id", "owner_id"],
            ["calendar_subscription_events.id", "calendar_subscription_events.owner_id"],
            name="fk_calendar_subscription_overrides_event_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "event_id",
            name=op.f("pk_calendar_subscription_overrides"),
        ),
        sa.UniqueConstraint(
            "event_id",
            "owner_id",
            name="uq_subscription_overrides_event_owner",
        ),
    )

    op.drop_constraint(
        op.f("ck_change_events_entity_type"),
        "change_events",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_change_events_entity_type"),
        "change_events",
        "entity_type IN "
        "('project', 'task', 'step', 'label', 'time_settings', 'term', "
        "'calendar_series', 'calendar_exception', 'task_occurrence', "
        "'calendar_addition', 'calendar_source')",
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM calendar_subscriptions) THEN
                RAISE EXCEPTION
                    'cannot downgrade 0015_calendar_subscriptions '
                    'while calendar subscriptions exist';
            END IF;
        END
        $$;
        """
    )
    op.execute(
        """
        DELETE FROM change_events WHERE entity_type = 'calendar_source';
        DELETE FROM user_operations WHERE scope LIKE 'calendar_subscriptions.%';
        """
    )
    op.drop_constraint(
        op.f("ck_change_events_entity_type"),
        "change_events",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_change_events_entity_type"),
        "change_events",
        "entity_type IN "
        "('project', 'task', 'step', 'label', 'time_settings', 'term', "
        "'calendar_series', 'calendar_exception', 'task_occurrence', "
        "'calendar_addition')",
    )
    op.drop_table("calendar_subscription_overrides")
    op.drop_index(
        "ix_calendar_subscription_events_owner_source",
        table_name="calendar_subscription_events",
    )
    op.drop_table("calendar_subscription_events")
    op.drop_index(
        "ix_calendar_subscription_refreshes_owner_source",
        table_name="calendar_subscription_refreshes",
    )
    op.drop_table("calendar_subscription_refreshes")
    op.drop_index(
        "ix_calendar_subscriptions_owner_updated",
        table_name="calendar_subscriptions",
    )
    op.drop_index(
        "ix_calendar_subscriptions_due",
        table_name="calendar_subscriptions",
    )
    op.drop_table("calendar_subscriptions")
