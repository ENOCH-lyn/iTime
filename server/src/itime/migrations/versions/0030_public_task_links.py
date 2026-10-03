"""Persist public task-link fetch state and immutable chat source context."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0030_public_task_links"
down_revision = "0029_account_deletions"
branch_labels = None
depends_on = None

CHANGE_EVENT_TYPES = (
    "'project', 'task', 'step', 'label', 'time_settings', 'term', "
    "'calendar_series', 'calendar_exception', 'task_occurrence', "
    "'calendar_addition', 'calendar_source', 'privacy_settings', 'plan', "
    "'focus_session', 'difficulty', 'review_session', 'review_report', "
    "'review_annotation', 'memory', 'memory_settings', 'conversation'"
)


def _replace_change_event_constraint(*, public_link: bool) -> None:
    op.drop_constraint(
        op.f("ck_change_events_entity_type"),
        "change_events",
        type_="check",
    )
    values = (
        f"{CHANGE_EVENT_TYPES}, 'task_link'"
        if public_link
        else CHANGE_EVENT_TYPES
    )
    op.create_check_constraint(
        op.f("ck_change_events_entity_type"),
        "change_events",
        f"entity_type IN ({values})",
    )


def upgrade() -> None:
    op.add_column(
        "task_links",
        sa.Column(
            "fetch_status",
            sa.String(length=12),
            server_default="queued",
            nullable=False,
        ),
    )
    op.add_column(
        "task_links",
        sa.Column(
            "fetch_revision",
            sa.Integer(),
            server_default="1",
            nullable=False,
        ),
    )
    op.add_column(
        "task_links",
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "task_links",
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.add_column("task_links", sa.Column("lease_id", sa.Uuid(), nullable=True))
    op.add_column(
        "task_links",
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "task_links",
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "task_links",
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "task_links",
        sa.Column("final_host", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "task_links",
        sa.Column("media_type", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "task_links",
        sa.Column("page_title", sa.String(length=300), nullable=True),
    )
    op.add_column("task_links", sa.Column("summary", sa.Text(), nullable=True))
    op.add_column(
        "task_links",
        sa.Column("extracted_text", sa.Text(), nullable=True),
    )
    op.add_column(
        "task_links",
        sa.Column("content_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "task_links",
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "task_links",
        sa.Column("last_error_message", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "task_links",
        sa.Column("last_http_status", sa.Integer(), nullable=True),
    )
    op.add_column(
        "task_links",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_task_links_fetch_status"),
        "task_links",
        "fetch_status IN ('queued', 'running', 'ready', 'failed')",
    )
    op.create_check_constraint(
        op.f("ck_task_links_fetch_revision"),
        "task_links",
        "fetch_revision >= 1",
    )
    op.create_check_constraint(
        op.f("ck_task_links_attempt_count"),
        "task_links",
        "attempt_count >= 0",
    )
    op.create_check_constraint(
        op.f("ck_task_links_fetch_lease_state"),
        "task_links",
        "(fetch_status = 'running') = "
        "(lease_id IS NOT NULL AND lease_until IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f("ck_task_links_fetch_error_pair"),
        "task_links",
        "(last_error_code IS NULL) = (last_error_message IS NULL)",
    )
    op.create_check_constraint(
        op.f("ck_task_links_failed_error"),
        "task_links",
        "fetch_status != 'failed' OR last_error_code IS NOT NULL",
    )
    op.create_check_constraint(
        op.f("ck_task_links_ready_without_error"),
        "task_links",
        "fetch_status != 'ready' OR last_error_code IS NULL",
    )
    op.create_check_constraint(
        op.f("ck_task_links_ready_snapshot"),
        "task_links",
        "fetch_status != 'ready' OR last_success_at IS NOT NULL",
    )
    op.create_check_constraint(
        op.f("ck_task_links_fetch_snapshot"),
        "task_links",
        "(last_success_at IS NULL "
        "AND final_host IS NULL "
        "AND media_type IS NULL "
        "AND page_title IS NULL "
        "AND summary IS NULL "
        "AND extracted_text IS NULL "
        "AND content_sha256 IS NULL) "
        "OR (last_success_at IS NOT NULL "
        "AND final_host IS NOT NULL "
        "AND media_type IS NOT NULL "
        "AND summary IS NOT NULL "
        "AND length(summary) > 0 "
        "AND extracted_text IS NOT NULL "
        "AND length(extracted_text) > 0 "
        "AND content_sha256 IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f("ck_task_links_content_sha256"),
        "task_links",
        "content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        op.f("ck_task_links_page_title_length"),
        "task_links",
        "page_title IS NULL OR char_length(page_title) <= 300",
    )
    op.create_check_constraint(
        op.f("ck_task_links_summary_length"),
        "task_links",
        "summary IS NULL OR char_length(summary) <= 2000",
    )
    op.create_check_constraint(
        op.f("ck_task_links_extracted_text_length"),
        "task_links",
        "extracted_text IS NULL OR char_length(extracted_text) <= 24000",
    )
    op.create_index(
        "ix_task_links_due",
        "task_links",
        ["available_at", "lease_until", "owner_id", "id"],
        unique=False,
        postgresql_where=sa.text("fetch_status IN ('queued', 'running')"),
    )
    op.create_index(
        "ix_task_links_owner_attempt",
        "task_links",
        ["owner_id", "last_attempt_at", "id"],
        unique=False,
    )

    op.add_column(
        "ai_chat_generations",
        sa.Column(
            "public_source_context",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_ai_chat_generations_public_source_context"),
        "ai_chat_generations",
        "jsonb_typeof(public_source_context) = 'array' "
        "AND jsonb_array_length(public_source_context) <= 3",
    )

    _replace_change_event_constraint(public_link=True)
    op.execute(
        """
        CREATE TRIGGER trg_planning_revision_task_links
        AFTER INSERT OR UPDATE OR DELETE ON task_links
        FOR EACH ROW
        EXECUTE FUNCTION bump_planning_input_revision()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_planning_revision_task_links ON task_links")
    op.execute("DELETE FROM change_events WHERE entity_type = 'task_link'")
    _replace_change_event_constraint(public_link=False)

    op.drop_constraint(
        op.f("ck_ai_chat_generations_public_source_context"),
        "ai_chat_generations",
        type_="check",
    )
    op.drop_column("ai_chat_generations", "public_source_context")

    op.drop_index("ix_task_links_owner_attempt", table_name="task_links")
    op.drop_index("ix_task_links_due", table_name="task_links")
    for constraint_name in (
        "extracted_text_length",
        "summary_length",
        "page_title_length",
        "content_sha256",
        "fetch_snapshot",
        "ready_snapshot",
        "ready_without_error",
        "failed_error",
        "fetch_error_pair",
        "fetch_lease_state",
        "attempt_count",
        "fetch_revision",
        "fetch_status",
    ):
        op.drop_constraint(
            op.f(f"ck_task_links_{constraint_name}"),
            "task_links",
            type_="check",
        )
    for column_name in (
        "updated_at",
        "last_http_status",
        "last_error_message",
        "last_error_code",
        "content_sha256",
        "extracted_text",
        "summary",
        "page_title",
        "media_type",
        "final_host",
        "last_success_at",
        "last_attempt_at",
        "lease_until",
        "lease_id",
        "available_at",
        "attempt_count",
        "fetch_revision",
        "fetch_status",
    ):
        op.drop_column("task_links", column_name)
