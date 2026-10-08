"""Unify calendar import sources and recoverable AI batches."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0039_calendar_ai_import_sources"
down_revision = "0038_agent_task_plan"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_calendar_sources_kind"),
        "calendar_sources",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_calendar_sources_kind"),
        "calendar_sources",
        "kind IN ('excel_file', 'ics_file', 'screenshot', 'image', "
        "'public_url', 'text', 'ics_subscription')",
    )

    op.add_column(
        "calendar_import_batches",
        sa.Column("source_type", sa.String(length=24), nullable=True),
    )
    op.add_column(
        "calendar_import_batches",
        sa.Column("source_display", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "calendar_import_batches",
        sa.Column(
            "input_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "calendar_import_batches",
        sa.Column("input_body_ciphertext", sa.LargeBinary(), nullable=True),
    )
    op.add_column(
        "calendar_import_batches",
        sa.Column(
            "input_body_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "calendar_import_batches",
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "calendar_import_batches",
        sa.Column("message_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "calendar_import_batches",
        sa.Column("error_code", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "calendar_import_batches",
        sa.Column("error_message", sa.String(length=500), nullable=True),
    )
    op.drop_constraint(
        op.f("ck_calendar_import_batches_state"),
        "calendar_import_batches",
        type_="check",
    )
    op.execute(
        """
        UPDATE calendar_import_batches
        SET source_type = CASE format
            WHEN 'xlsx' THEN 'xlsx_file'
            WHEN 'ics' THEN 'ics_file'
            WHEN 'screenshot' THEN 'image'
        END,
        state = CASE state
            WHEN 'ready' THEN 'needs_review'
            WHEN 'partially_confirmed' THEN 'needs_review'
            WHEN 'completed' THEN 'confirmed'
            ELSE state
        END
        """
    )
    op.alter_column(
        "calendar_import_batches",
        "source_type",
        existing_type=sa.String(length=24),
        nullable=False,
    )
    op.alter_column(
        "calendar_import_batches",
        "state",
        existing_type=sa.String(length=24),
        server_default="queued",
        existing_nullable=False,
    )

    op.drop_constraint(
        op.f("ck_calendar_import_batches_format"),
        "calendar_import_batches",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_calendar_import_batches_candidate_count"),
        "calendar_import_batches",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_calendar_import_batches_format"),
        "calendar_import_batches",
        "format IN ('xlsx', 'ics', 'screenshot', 'public_url', 'text')",
    )
    op.create_check_constraint(
        op.f("ck_calendar_import_batches_source_type"),
        "calendar_import_batches",
        "source_type IN ('xlsx_file', 'ics_file', 'image', 'public_url', 'text')",
    )
    op.create_check_constraint(
        op.f("ck_calendar_import_batches_format_source"),
        "calendar_import_batches",
        "(source_type = 'xlsx_file' AND format = 'xlsx') "
        "OR (source_type = 'ics_file' AND format = 'ics') "
        "OR (source_type = 'image' AND format = 'screenshot') "
        "OR (source_type = 'public_url' AND format IN ('public_url', 'ics')) "
        "OR (source_type = 'text' AND format = 'text')",
    )
    op.create_check_constraint(
        op.f("ck_calendar_import_batches_state"),
        "calendar_import_batches",
        "state IN ('queued', 'processing', 'needs_review', 'confirmed', "
        "'cancelled', 'failed', 'expired')",
    )
    op.create_check_constraint(
        op.f("ck_calendar_import_batches_candidate_count"),
        "calendar_import_batches",
        "candidate_count BETWEEN 0 AND 500",
    )
    op.create_check_constraint(
        op.f("ck_calendar_import_batches_source_display"),
        "calendar_import_batches",
        "source_display IS NULL OR length(btrim(source_display)) > 0",
    )
    op.create_check_constraint(
        op.f("ck_calendar_import_batches_input_summary"),
        "calendar_import_batches",
        "jsonb_typeof(input_summary) = 'object'",
    )
    op.create_check_constraint(
        op.f("ck_calendar_import_batches_input_body"),
        "calendar_import_batches",
        "(input_body_ciphertext IS NULL) = (input_body_expires_at IS NULL)",
    )
    op.create_check_constraint(
        op.f("ck_calendar_import_batches_error_pair"),
        "calendar_import_batches",
        "(error_code IS NULL) = (error_message IS NULL)",
    )
    op.create_check_constraint(
        op.f("ck_calendar_import_batches_message_conversation"),
        "calendar_import_batches",
        "message_id IS NULL OR conversation_id IS NOT NULL",
    )
    op.create_foreign_key(
        "fk_calendar_import_batches_conversation_owner",
        "calendar_import_batches",
        "ai_conversations",
        ["conversation_id", "owner_id"],
        ["id", "owner_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_calendar_import_batches_message_owner_conversation",
        "calendar_import_batches",
        "ai_conversation_messages",
        ["message_id", "owner_id", "conversation_id"],
        ["id", "owner_id", "conversation_id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_calendar_import_batches_owner_conversation",
        "calendar_import_batches",
        ["owner_id", "conversation_id", "updated_at", "id"],
        unique=False,
    )

    op.drop_constraint(
        op.f("ck_calendar_import_identities_format"),
        "calendar_import_identities",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_calendar_import_identities_format"),
        "calendar_import_identities",
        "format IN ('xlsx', 'ics', 'screenshot', 'public_url', 'text')",
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM calendar_import_batches
                WHERE format IN ('public_url', 'text')
                   OR candidate_count = 0
                   OR state IN ('queued', 'processing', 'cancelled', 'failed')
            ) OR EXISTS (
                SELECT 1
                FROM calendar_import_identities
                WHERE format IN ('public_url', 'text')
            ) OR EXISTS (
                SELECT 1
                FROM calendar_sources
                WHERE kind IN ('public_url', 'text')
            ) THEN
                RAISE EXCEPTION
                    'cannot downgrade 0039_calendar_ai_import_sources '
                    'while unified AI import data exists';
            END IF;
        END
        $$;
        """
    )
    op.drop_constraint(
        op.f("ck_calendar_import_identities_format"),
        "calendar_import_identities",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_calendar_import_identities_format"),
        "calendar_import_identities",
        "format IN ('xlsx', 'ics', 'screenshot')",
    )

    op.drop_index(
        "ix_calendar_import_batches_owner_conversation",
        table_name="calendar_import_batches",
    )
    op.drop_constraint(
        "fk_calendar_import_batches_message_owner_conversation",
        "calendar_import_batches",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_calendar_import_batches_conversation_owner",
        "calendar_import_batches",
        type_="foreignkey",
    )
    for name in (
        "message_conversation",
        "error_pair",
        "input_body",
        "input_summary",
        "source_display",
        "format_source",
        "source_type",
    ):
        op.drop_constraint(
            op.f(f"ck_calendar_import_batches_{name}"),
            "calendar_import_batches",
            type_="check",
        )
    op.drop_constraint(
        op.f("ck_calendar_import_batches_candidate_count"),
        "calendar_import_batches",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_calendar_import_batches_state"),
        "calendar_import_batches",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_calendar_import_batches_format"),
        "calendar_import_batches",
        type_="check",
    )
    op.execute(
        """
        UPDATE calendar_import_batches
        SET state = CASE state
            WHEN 'needs_review' THEN 'ready'
            WHEN 'confirmed' THEN 'completed'
            WHEN 'expired' THEN 'expired'
        END
        """
    )
    op.alter_column(
        "calendar_import_batches",
        "state",
        existing_type=sa.String(length=24),
        server_default="ready",
        existing_nullable=False,
    )
    op.create_check_constraint(
        op.f("ck_calendar_import_batches_format"),
        "calendar_import_batches",
        "format IN ('xlsx', 'ics', 'screenshot')",
    )
    op.create_check_constraint(
        op.f("ck_calendar_import_batches_state"),
        "calendar_import_batches",
        "state IN ('ready', 'partially_confirmed', 'completed', 'expired')",
    )
    op.create_check_constraint(
        op.f("ck_calendar_import_batches_candidate_count"),
        "calendar_import_batches",
        "candidate_count BETWEEN 1 AND 500",
    )
    for column in (
        "error_message",
        "error_code",
        "message_id",
        "conversation_id",
        "input_body_expires_at",
        "input_body_ciphertext",
        "input_summary",
        "source_display",
        "source_type",
    ):
        op.drop_column("calendar_import_batches", column)

    op.drop_constraint(
        op.f("ck_calendar_sources_kind"),
        "calendar_sources",
        type_="check",
    )
    op.execute("UPDATE calendar_sources SET kind = 'screenshot' WHERE kind = 'image'")
    op.create_check_constraint(
        op.f("ck_calendar_sources_kind"),
        "calendar_sources",
        "kind IN ('excel_file', 'ics_file', 'screenshot', 'ics_subscription')",
    )
