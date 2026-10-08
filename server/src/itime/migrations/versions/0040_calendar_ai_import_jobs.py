"""Allow text and public URL calendar import jobs."""

from alembic import op

revision = "0040_calendar_ai_import_jobs"
down_revision = "0039_calendar_ai_import_sources"
branch_labels = None
depends_on = None


def _drop_input_reference() -> None:
    op.drop_constraint(
        op.f("ck_ai_jobs_input_reference"),
        "ai_jobs",
        type_="check",
    )


def upgrade() -> None:
    _drop_input_reference()
    op.create_check_constraint(
        op.f("ck_ai_jobs_input_reference"),
        "ai_jobs",
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
    )
    for table in (
        "ai_quota_reservations",
        "ai_quota_ledger_entries",
    ):
        op.drop_constraint(
            op.f(f"ck_{table}_usage_source"),
            table,
            type_="check",
        )
        op.create_check_constraint(
            op.f(f"ck_{table}_usage_source"),
            table,
            "usage_source IS NULL OR usage_source IN "
            "('provider', 'estimated', 'measured', 'not_sent')",
        )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM ai_jobs
                WHERE capability = 'calendar_screenshot'
                  AND input_media_id IS NULL
            ) THEN
                RAISE EXCEPTION
                    'cannot downgrade 0040_calendar_ai_import_jobs '
                    'while text or public URL import jobs exist';
            END IF;
        END
        $$;
        """
    )
    for table in (
        "ai_quota_ledger_entries",
        "ai_quota_reservations",
    ):
        op.drop_constraint(
            op.f(f"ck_{table}_usage_source"),
            table,
            type_="check",
        )
        op.create_check_constraint(
            op.f(f"ck_{table}_usage_source"),
            table,
            "usage_source IS NULL OR usage_source IN "
            "('provider', 'estimated', 'measured')",
        )
    _drop_input_reference()
    op.create_check_constraint(
        op.f("ck_ai_jobs_input_reference"),
        "ai_jobs",
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
        "AND review_snapshot_id IS NOT NULL) "
        "OR (capability = 'transcription' "
        "AND input_media_id IS NOT NULL AND conversation_id IS NULL "
        "AND planning_snapshot_id IS NULL AND review_session_id IS NULL "
        "AND review_snapshot_id IS NULL)",
    )
