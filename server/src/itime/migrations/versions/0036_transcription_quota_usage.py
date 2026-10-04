"""Allow measured audio usage in quota settlement records."""

from alembic import op

revision = "0036_transcription_quota_usage"
down_revision = "0035_voice_transcriptions"
branch_labels = None
depends_on = None


def _replace_usage_source_constraints(*, measured: bool) -> None:
    values = (
        "('provider', 'estimated', 'measured')"
        if measured
        else "('provider', 'estimated')"
    )
    for table_name in (
        "ai_quota_reservations",
        "ai_quota_ledger_entries",
    ):
        op.drop_constraint(
            op.f(f"ck_{table_name}_usage_source"),
            table_name,
            type_="check",
        )
        op.create_check_constraint(
            op.f(f"ck_{table_name}_usage_source"),
            table_name,
            f"usage_source IS NULL OR usage_source IN {values}",
        )


def upgrade() -> None:
    _replace_usage_source_constraints(measured=True)


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM ai_quota_reservations
                WHERE usage_source = 'measured'
            ) OR EXISTS (
                SELECT 1
                FROM ai_quota_ledger_entries
                WHERE usage_source = 'measured'
            ) THEN
                RAISE EXCEPTION
                    'cannot downgrade 0036_transcription_quota_usage with measured quota data';
            END IF;
        END
        $$;
        """
    )
    _replace_usage_source_constraints(measured=False)
