"""calendar file import batches and candidates"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011_calendar_file_imports"
down_revision = "0010_calendar_additions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "calendar_sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
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
            "kind IN ('excel_file', 'ics_file', 'screenshot', 'ics_subscription')",
            name=op.f("ck_calendar_sources_kind"),
        ),
        sa.CheckConstraint(
            "length(btrim(name)) > 0",
            name=op.f("ck_calendar_sources_nonempty_name"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_calendar_sources_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_calendar_sources")),
        sa.UniqueConstraint("id", "owner_id", name="uq_calendar_sources_id_owner"),
    )
    op.create_index(
        "ix_calendar_sources_owner_created",
        "calendar_sources",
        ["owner_id", "created_at", "id"],
        unique=False,
    )

    op.add_column("calendar_series", sa.Column("source_id", sa.Uuid(), nullable=True))
    op.drop_constraint(
        op.f("ck_calendar_series_source_type"),
        "calendar_series",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_calendar_series_source_type"),
        "calendar_series",
        "source_type IN ('manual', 'import', 'screenshot', 'subscription')",
    )
    op.create_check_constraint(
        op.f("ck_calendar_series_source_reference"),
        "calendar_series",
        "(source_type = 'manual') = (source_id IS NULL)",
    )
    op.create_foreign_key(
        "fk_calendar_series_source_owner",
        "calendar_series",
        "calendar_sources",
        ["source_id", "owner_id"],
        ["id", "owner_id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "calendar_import_batches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("format", sa.String(length=8), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=24), server_default="ready", nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
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
            "format IN ('xlsx', 'ics')",
            name=op.f("ck_calendar_import_batches_format"),
        ),
        sa.CheckConstraint(
            "state IN ('ready', 'partially_confirmed', 'completed', 'expired')",
            name=op.f("ck_calendar_import_batches_state"),
        ),
        sa.CheckConstraint(
            "length(btrim(filename)) > 0",
            name=op.f("ck_calendar_import_batches_nonempty_filename"),
        ),
        sa.CheckConstraint(
            "candidate_count BETWEEN 1 AND 500",
            name=op.f("ck_calendar_import_batches_candidate_count"),
        ),
        sa.CheckConstraint(
            "length(content_sha256) = 64",
            name=op.f("ck_calendar_import_batches_content_sha256"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_calendar_import_batches_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_id", "owner_id"],
            ["calendar_sources.id", "calendar_sources.owner_id"],
            name="fk_calendar_import_batches_source_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_calendar_import_batches")),
        sa.UniqueConstraint(
            "id",
            "owner_id",
            name="uq_calendar_import_batches_id_owner",
        ),
        sa.UniqueConstraint(
            "source_id",
            name="uq_calendar_import_batches_source",
        ),
    )
    op.create_index(
        "ix_calendar_import_batches_owner_updated",
        "calendar_import_batches",
        ["owner_id", "updated_at", "id"],
        unique=False,
    )

    op.create_table(
        "calendar_import_candidates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column(
            "raw_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "draft",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("identity_key", sa.String(length=64), nullable=True),
        sa.Column("content_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("external_uid", sa.String(length=512), nullable=True),
        sa.Column("external_recurrence_id", sa.String(length=255), nullable=True),
        sa.Column(
            "warnings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column(
            "error_details",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("duplicate_series_id", sa.Uuid(), nullable=True),
        sa.Column("confirmed_series_id", sa.Uuid(), nullable=True),
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
            "state IN ('valid', 'invalid', 'duplicate', 'confirmed', 'skipped')",
            name=op.f("ck_calendar_import_candidates_state"),
        ),
        sa.CheckConstraint(
            "position BETWEEN 1 AND 500",
            name=op.f("ck_calendar_import_candidates_position"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(raw_data) = 'object'",
            name=op.f("ck_calendar_import_candidates_raw_data"),
        ),
        sa.CheckConstraint(
            "draft IS NULL OR jsonb_typeof(draft) = 'object'",
            name=op.f("ck_calendar_import_candidates_draft"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(warnings) = 'array'",
            name=op.f("ck_calendar_import_candidates_warnings"),
        ),
        sa.CheckConstraint(
            "error_details IS NULL OR jsonb_typeof(error_details) = 'object'",
            name=op.f("ck_calendar_import_candidates_error_details"),
        ),
        sa.CheckConstraint(
            "state NOT IN ('valid', 'duplicate', 'confirmed') OR draft IS NOT NULL",
            name=op.f("ck_calendar_import_candidates_draft_state"),
        ),
        sa.CheckConstraint(
            "(state = 'confirmed') = (confirmed_series_id IS NOT NULL)",
            name=op.f("ck_calendar_import_candidates_confirmed_series"),
        ),
        sa.ForeignKeyConstraint(
            ["batch_id", "owner_id"],
            ["calendar_import_batches.id", "calendar_import_batches.owner_id"],
            name="fk_calendar_import_candidates_batch_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["duplicate_series_id", "owner_id"],
            ["calendar_series.id", "calendar_series.owner_id"],
            name="fk_calendar_import_candidates_duplicate_series_owner",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_series_id", "owner_id"],
            ["calendar_series.id", "calendar_series.owner_id"],
            name="fk_calendar_import_candidates_confirmed_series_owner",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_calendar_import_candidates")),
        sa.UniqueConstraint(
            "id",
            "owner_id",
            name="uq_calendar_import_candidates_id_owner",
        ),
        sa.UniqueConstraint(
            "batch_id",
            "position",
            name="uq_calendar_import_candidates_batch_position",
        ),
    )
    op.create_index(
        "ix_calendar_import_candidates_owner_batch",
        "calendar_import_candidates",
        ["owner_id", "batch_id", "position"],
        unique=False,
    )

    op.create_table(
        "calendar_import_identities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("series_id", sa.Uuid(), nullable=False),
        sa.Column("format", sa.String(length=8), nullable=False),
        sa.Column("identity_key", sa.String(length=64), nullable=False),
        sa.Column("content_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("external_uid", sa.String(length=512), nullable=True),
        sa.Column("external_recurrence_id", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "format IN ('xlsx', 'ics')",
            name=op.f("ck_calendar_import_identities_format"),
        ),
        sa.CheckConstraint(
            "length(identity_key) = 64",
            name=op.f("ck_calendar_import_identities_identity_key"),
        ),
        sa.CheckConstraint(
            "length(content_fingerprint) = 64",
            name=op.f("ck_calendar_import_identities_content_fingerprint"),
        ),
        sa.ForeignKeyConstraint(
            ["series_id", "owner_id"],
            ["calendar_series.id", "calendar_series.owner_id"],
            name="fk_calendar_import_identities_series_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_id", "owner_id"],
            ["calendar_sources.id", "calendar_sources.owner_id"],
            name="fk_calendar_import_identities_source_owner",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_calendar_import_identities")),
        sa.UniqueConstraint(
            "owner_id",
            "identity_key",
            name="uq_calendar_import_identities_owner_key",
        ),
        sa.UniqueConstraint(
            "owner_id",
            "series_id",
            name="uq_calendar_import_identities_owner_series",
        ),
    )
    op.create_index(
        "ix_calendar_import_identities_owner_series",
        "calendar_import_identities",
        ["owner_id", "series_id"],
        unique=False,
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM calendar_series WHERE source_type != 'manual'
            ) THEN
                RAISE EXCEPTION
                    'cannot downgrade 0011_calendar_file_imports while imported series exist';
            END IF;
        END
        $$;
        """
    )
    op.execute(
        sa.text(
            """
            DELETE FROM user_operations
            WHERE scope = 'calendar_import.upload'
               OR scope LIKE 'calendar_import.update:%'
               OR scope LIKE 'calendar_import.confirm:%'
            """
        )
    )
    op.drop_index(
        "ix_calendar_import_identities_owner_series",
        table_name="calendar_import_identities",
    )
    op.drop_table("calendar_import_identities")
    op.drop_index(
        "ix_calendar_import_candidates_owner_batch",
        table_name="calendar_import_candidates",
    )
    op.drop_table("calendar_import_candidates")
    op.drop_index(
        "ix_calendar_import_batches_owner_updated",
        table_name="calendar_import_batches",
    )
    op.drop_table("calendar_import_batches")
    op.drop_constraint(
        "fk_calendar_series_source_owner",
        "calendar_series",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("ck_calendar_series_source_reference"),
        "calendar_series",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_calendar_series_source_type"),
        "calendar_series",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_calendar_series_source_type"),
        "calendar_series",
        "source_type = 'manual'",
    )
    op.drop_column("calendar_series", "source_id")
    op.drop_index("ix_calendar_sources_owner_created", table_name="calendar_sources")
    op.drop_table("calendar_sources")
