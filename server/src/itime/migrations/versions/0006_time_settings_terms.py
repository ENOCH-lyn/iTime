"""user time settings, academic terms, and term periods"""

import sqlalchemy as sa
from alembic import op

revision = "0006_time_terms"
down_revision = "0005_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_time_settings",
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("time_zone", sa.String(length=64), nullable=False),
        sa.Column("week_starts_on", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "activity_day_start_minute",
            sa.Integer(),
            server_default="240",
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
            "length(btrim(time_zone)) > 0",
            name=op.f("ck_user_time_settings_nonempty_time_zone"),
        ),
        sa.CheckConstraint(
            "week_starts_on = 1",
            name=op.f("ck_user_time_settings_week_starts_on"),
        ),
        sa.CheckConstraint(
            "activity_day_start_minute = 240",
            name=op.f("ck_user_time_settings_activity_day_start_minute"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_user_time_settings_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("owner_id", name=op.f("pk_user_time_settings")),
    )

    op.create_table(
        "academic_terms",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("first_week_monday", sa.Date(), nullable=False),
        sa.Column("time_zone", sa.String(length=64), nullable=False),
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
            "length(btrim(name)) > 0",
            name=op.f("ck_academic_terms_nonempty_name"),
        ),
        sa.CheckConstraint(
            "length(btrim(time_zone)) > 0",
            name=op.f("ck_academic_terms_nonempty_time_zone"),
        ),
        sa.CheckConstraint(
            "end_date - start_date BETWEEN 0 AND 399",
            name=op.f("ck_academic_terms_date_range"),
        ),
        sa.CheckConstraint(
            "start_date - first_week_monday BETWEEN 0 AND 6",
            name=op.f("ck_academic_terms_first_week_range"),
        ),
        sa.CheckConstraint(
            "EXTRACT(ISODOW FROM first_week_monday) = 1",
            name=op.f("ck_academic_terms_first_week_monday"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_academic_terms_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_academic_terms")),
        sa.UniqueConstraint("id", "owner_id", name="uq_academic_terms_id_owner"),
    )
    op.create_index(
        "ix_academic_terms_owner_updated",
        "academic_terms",
        ["owner_id", "updated_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_academic_terms_owner_dates",
        "academic_terms",
        ["owner_id", "start_date", "end_date", "id"],
        unique=False,
    )

    op.create_table(
        "term_periods",
        sa.Column("term_id", sa.Uuid(), nullable=False),
        sa.Column("period_number", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("normalized_name", sa.String(length=80), nullable=False),
        sa.Column("starts_at", sa.Time(), nullable=False),
        sa.Column("ends_at", sa.Time(), nullable=False),
        sa.CheckConstraint(
            "period_number BETWEEN 1 AND 99",
            name=op.f("ck_term_periods_period_number"),
        ),
        sa.CheckConstraint(
            "length(btrim(name)) > 0",
            name=op.f("ck_term_periods_nonempty_name"),
        ),
        sa.CheckConstraint(
            "normalized_name = lower(normalized_name)",
            name=op.f("ck_term_periods_normalized_name"),
        ),
        sa.CheckConstraint(
            "starts_at < ends_at",
            name=op.f("ck_term_periods_time_range"),
        ),
        sa.ForeignKeyConstraint(
            ["term_id", "owner_id"],
            ["academic_terms.id", "academic_terms.owner_id"],
            name="fk_term_periods_term_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "term_id",
            "period_number",
            name=op.f("pk_term_periods"),
        ),
        sa.UniqueConstraint(
            "term_id",
            "normalized_name",
            name="uq_term_periods_term_normalized_name",
        ),
    )
    op.create_index(
        "ix_term_periods_owner_term_number",
        "term_periods",
        ["owner_id", "term_id", "period_number"],
        unique=False,
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
        "('project', 'task', 'step', 'label', 'time_settings', 'term')",
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            DELETE FROM user_operations
            WHERE scope = 'time_settings.update'
               OR scope = 'terms.create'
               OR scope LIKE 'terms.update:%'
               OR scope LIKE 'terms.periods.replace:%'
            """
        )
    )
    op.execute(
        sa.text(
            """
            DELETE FROM change_events
            WHERE entity_type IN ('time_settings', 'term')
            """
        )
    )
    op.drop_constraint(
        op.f("ck_change_events_entity_type"),
        "change_events",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_change_events_entity_type"),
        "change_events",
        "entity_type IN ('project', 'task', 'step', 'label')",
    )
    op.drop_index(
        "ix_term_periods_owner_term_number",
        table_name="term_periods",
    )
    op.drop_table("term_periods")
    op.drop_index("ix_academic_terms_owner_dates", table_name="academic_terms")
    op.drop_index("ix_academic_terms_owner_updated", table_name="academic_terms")
    op.drop_table("academic_terms")
    op.drop_table("user_time_settings")
