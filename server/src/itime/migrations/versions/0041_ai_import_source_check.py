"""Require a source type for media-less calendar import jobs."""

from alembic import op

revision = "0041_ai_import_source_check"
down_revision = "0040_calendar_ai_import_jobs"
branch_labels = None
depends_on = None


def _replace_input_reference(source_condition: str) -> None:
    op.drop_constraint(
        op.f("ck_ai_jobs_input_reference"),
        "ai_jobs",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_ai_jobs_input_reference"),
        "ai_jobs",
        "(capability = 'calendar_screenshot' "
        "AND conversation_id IS NULL "
        "AND planning_snapshot_id IS NULL AND review_session_id IS NULL "
        "AND review_snapshot_id IS NULL "
        "AND (input_media_id IS NOT NULL "
        f"OR (input_media_id IS NULL AND {source_condition}))) "
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


def upgrade() -> None:
    _replace_input_reference(
        "COALESCE(input_summary->>'source_type' "
        "IN ('public_url', 'text'), false)"
    )


def downgrade() -> None:
    _replace_input_reference(
        "input_summary->>'source_type' IN ('public_url', 'text')"
    )
