"""Persist AI fallback routes and per-attempt job routing."""

import sqlalchemy as sa
from alembic import op

revision = "0033_ai_fallback_routes"
down_revision = "0032_subscription_exceptions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_config_capabilities",
        sa.Column(
            "routing_version",
            sa.Integer(),
            server_default="1",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_ai_config_capabilities_routing_version"),
        "ai_config_capabilities",
        "routing_version >= 1",
    )
    op.create_table(
        "ai_config_fallbacks",
        sa.Column("capability", sa.String(length=50), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("config_version_id", sa.Uuid(), nullable=False),
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
            "position BETWEEN 1 AND 2",
            name=op.f("ck_ai_config_fallbacks_position"),
        ),
        sa.ForeignKeyConstraint(
            ["capability"],
            ["ai_config_capabilities.capability"],
            name="fk_ai_config_fallbacks_capability",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["config_version_id", "capability"],
            ["ai_config_versions.id", "ai_config_versions.capability"],
            name="fk_ai_config_fallbacks_config",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "capability",
            "position",
            name=op.f("pk_ai_config_fallbacks"),
        ),
        sa.UniqueConstraint(
            "capability",
            "config_version_id",
            name="uq_ai_config_fallbacks_config",
        ),
    )
    op.create_unique_constraint(
        "uq_ai_jobs_id_capability_owner",
        "ai_jobs",
        ["id", "capability", "owner_id"],
    )
    op.create_table(
        "ai_job_config_routes",
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("capability", sa.String(length=50), nullable=False),
        sa.Column("config_version_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "attempt_number BETWEEN 1 AND 5",
            name=op.f("ck_ai_job_config_routes_attempt_number"),
        ),
        sa.ForeignKeyConstraint(
            ["job_id", "capability", "owner_id"],
            ["ai_jobs.id", "ai_jobs.capability", "ai_jobs.owner_id"],
            name="fk_ai_job_config_routes_job",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["config_version_id", "capability"],
            ["ai_config_versions.id", "ai_config_versions.capability"],
            name="fk_ai_job_config_routes_config",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "job_id",
            "attempt_number",
            name=op.f("pk_ai_job_config_routes"),
        ),
        sa.UniqueConstraint(
            "job_id",
            "attempt_number",
            "config_version_id",
            name="uq_ai_job_config_routes_attempt_config",
        ),
    )
    op.create_index(
        "ix_ai_job_config_routes_owner_job",
        "ai_job_config_routes",
        ["owner_id", "job_id", "attempt_number"],
    )
    op.execute(
        """
        INSERT INTO ai_job_config_routes (
            job_id, attempt_number, owner_id, capability, config_version_id
        )
        SELECT
            job.id,
            attempt.attempt_number,
            job.owner_id,
            job.capability,
            job.config_version_id
        FROM ai_jobs AS job
        CROSS JOIN LATERAL generate_series(
            1,
            job.max_attempts
        ) AS attempt(attempt_number)
        """
    )
    op.execute(
        """
        CREATE FUNCTION create_default_ai_job_config_route()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            INSERT INTO ai_job_config_routes (
                job_id,
                attempt_number,
                owner_id,
                capability,
                config_version_id
            )
            SELECT
                NEW.id,
                attempt_number,
                NEW.owner_id,
                NEW.capability,
                NEW.config_version_id
            FROM generate_series(1, NEW.max_attempts) AS attempt_number;
            RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_ai_jobs_default_config_route
        AFTER INSERT ON ai_jobs
        FOR EACH ROW EXECUTE FUNCTION create_default_ai_job_config_route()
        """
    )
    op.execute(
        """
        CREATE FUNCTION guard_ai_job_config_route_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            parent_status text;
            parent_attempt_count integer;
        BEGIN
            SELECT status, attempt_count
            INTO parent_status, parent_attempt_count
            FROM ai_jobs
            WHERE id = OLD.job_id;

            IF NOT FOUND THEN
                RETURN OLD;
            END IF;
            IF parent_status = 'queued' AND parent_attempt_count = 0 THEN
                RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
            END IF;
            RAISE EXCEPTION 'AI job config routes are immutable after execution starts'
                USING ERRCODE = '23514';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_ai_job_config_routes_guard
        BEFORE UPDATE OR DELETE ON ai_job_config_routes
        FOR EACH ROW EXECUTE FUNCTION guard_ai_job_config_route_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM ai_config_fallbacks)
               OR EXISTS (
                   SELECT 1
                   FROM ai_job_config_routes AS route
                   JOIN ai_jobs AS job ON job.id = route.job_id
                   WHERE route.config_version_id <> job.config_version_id
               ) THEN
                RAISE EXCEPTION
                    'cannot downgrade 0033_ai_fallback_routes '
                    'while fallback routing data exists';
            END IF;
        END
        $$;
        """
    )
    op.execute(
        "DROP TRIGGER trg_ai_job_config_routes_guard ON ai_job_config_routes"
    )
    op.execute("DROP FUNCTION guard_ai_job_config_route_mutation()")
    op.execute("DROP TRIGGER trg_ai_jobs_default_config_route ON ai_jobs")
    op.execute("DROP FUNCTION create_default_ai_job_config_route()")
    op.drop_index(
        "ix_ai_job_config_routes_owner_job",
        table_name="ai_job_config_routes",
    )
    op.drop_table("ai_job_config_routes")
    op.drop_constraint(
        "uq_ai_jobs_id_capability_owner",
        "ai_jobs",
        type_="unique",
    )
    op.drop_table("ai_config_fallbacks")
    op.drop_constraint(
        op.f("ck_ai_config_capabilities_routing_version"),
        "ai_config_capabilities",
        type_="check",
    )
    op.drop_column("ai_config_capabilities", "routing_version")
