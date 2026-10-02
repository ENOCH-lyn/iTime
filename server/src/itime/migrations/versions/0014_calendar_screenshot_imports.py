"""calendar screenshot import batches"""

import sqlalchemy as sa
from alembic import op

revision = "0014_calendar_screenshot_imports"
down_revision = "0013_ai_jobs_media"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "calendar_import_batches",
        "format",
        existing_type=sa.String(length=8),
        type_=sa.String(length=16),
        existing_nullable=False,
    )
    op.alter_column(
        "calendar_import_identities",
        "format",
        existing_type=sa.String(length=8),
        type_=sa.String(length=16),
        existing_nullable=False,
    )
    op.add_column(
        "calendar_import_batches",
        sa.Column("origin_job_id", sa.Uuid(), nullable=True),
    )
    op.drop_constraint(
        op.f("ck_calendar_import_batches_format"),
        "calendar_import_batches",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_calendar_import_batches_format"),
        "calendar_import_batches",
        "format IN ('xlsx', 'ics', 'screenshot')",
    )
    op.create_foreign_key(
        "fk_calendar_import_batches_origin_job_owner",
        "calendar_import_batches",
        "ai_jobs",
        ["origin_job_id", "owner_id"],
        ["id", "owner_id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_calendar_import_batches_origin_job",
        "calendar_import_batches",
        ["origin_job_id"],
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


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM calendar_import_batches
                WHERE format = 'screenshot'
            ) THEN
                RAISE EXCEPTION
                    'cannot downgrade 0014_calendar_screenshot_imports '
                    'while screenshot batches exist';
            END IF;
        END
        $$;
        """
    )
    op.execute(
        """
        DELETE FROM user_operations
        WHERE scope = 'calendar_screenshot.upload'
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
        "format IN ('xlsx', 'ics')",
    )
    op.drop_constraint(
        "uq_calendar_import_batches_origin_job",
        "calendar_import_batches",
        type_="unique",
    )
    op.drop_constraint(
        "fk_calendar_import_batches_origin_job_owner",
        "calendar_import_batches",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("ck_calendar_import_batches_format"),
        "calendar_import_batches",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_calendar_import_batches_format"),
        "calendar_import_batches",
        "format IN ('xlsx', 'ics')",
    )
    op.drop_column("calendar_import_batches", "origin_job_id")
    op.alter_column(
        "calendar_import_identities",
        "format",
        existing_type=sa.String(length=16),
        type_=sa.String(length=8),
        existing_nullable=False,
    )
    op.alter_column(
        "calendar_import_batches",
        "format",
        existing_type=sa.String(length=16),
        type_=sa.String(length=8),
        existing_nullable=False,
    )
