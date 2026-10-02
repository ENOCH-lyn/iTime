"""projects, tasks, steps, labels, links, and idempotent change records"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_tasks"
down_revision = "0004_user_management"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
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
            "kind IN ('project', 'goal')",
            name=op.f("ck_projects_kind"),
        ),
        sa.CheckConstraint(
            "length(btrim(name)) > 0",
            name=op.f("ck_projects_nonempty_name"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_projects_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_projects")),
        sa.UniqueConstraint("id", "owner_id", name="uq_projects_id_owner"),
    )
    op.create_index(
        "ix_projects_owner_updated",
        "projects",
        ["owner_id", "updated_at", "id"],
        unique=False,
    )

    op.create_table(
        "labels",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("normalized_name", sa.String(length=40), nullable=False),
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
            name=op.f("ck_labels_nonempty_name"),
        ),
        sa.CheckConstraint(
            "normalized_name = lower(normalized_name)",
            name=op.f("ck_labels_normalized_name"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_labels_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_labels")),
        sa.UniqueConstraint("id", "owner_id", name="uq_labels_id_owner"),
        sa.UniqueConstraint(
            "owner_id",
            "normalized_name",
            name="uq_labels_owner_normalized",
        ),
    )
    op.create_index(
        "ix_labels_owner_name",
        "labels",
        ["owner_id", "normalized_name", "id"],
        unique=False,
    )

    op.create_table(
        "tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("estimated_minutes", sa.Integer(), nullable=True),
        sa.Column("estimate_source", sa.String(length=10), nullable=True),
        sa.Column("priority", sa.Integer(), server_default="0", nullable=False),
        sa.Column("status", sa.String(length=12), server_default="open", nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
            "length(btrim(title)) > 0",
            name=op.f("ck_tasks_nonempty_title"),
        ),
        sa.CheckConstraint(
            "priority BETWEEN 0 AND 3",
            name=op.f("ck_tasks_priority"),
        ),
        sa.CheckConstraint(
            "status IN ('open', 'completed')",
            name=op.f("ck_tasks_status"),
        ),
        sa.CheckConstraint(
            "estimated_minutes IS NULL OR estimated_minutes BETWEEN 1 AND 525600",
            name=op.f("ck_tasks_estimated_minutes"),
        ),
        sa.CheckConstraint(
            "estimate_source IS NULL OR estimate_source IN ('user', 'ai')",
            name=op.f("ck_tasks_estimate_source"),
        ),
        sa.CheckConstraint(
            "(estimated_minutes IS NULL) = (estimate_source IS NULL)",
            name=op.f("ck_tasks_estimate_source_consistency"),
        ),
        sa.CheckConstraint(
            "(status = 'completed') = (completed_at IS NOT NULL)",
            name=op.f("ck_tasks_completion_consistency"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_tasks_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "owner_id"],
            ["projects.id", "projects.owner_id"],
            name="fk_tasks_project_owner",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tasks")),
        sa.UniqueConstraint("id", "owner_id", name="uq_tasks_id_owner"),
    )
    op.create_index(
        "ix_tasks_owner_priority_updated",
        "tasks",
        ["owner_id", "priority", "updated_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_tasks_owner_project_updated",
        "tasks",
        ["owner_id", "project_id", "updated_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_tasks_owner_status_updated",
        "tasks",
        ["owner_id", "status", "updated_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_tasks_owner_updated",
        "tasks",
        ["owner_id", "updated_at", "id"],
        unique=False,
    )

    op.create_table(
        "task_labels",
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("label_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["label_id", "owner_id"],
            ["labels.id", "labels.owner_id"],
            name="fk_task_labels_label_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id", "owner_id"],
            ["tasks.id", "tasks.owner_id"],
            name="fk_task_labels_task_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("task_id", "label_id", name=op.f("pk_task_labels")),
    )
    op.create_index(
        "ix_task_labels_owner_label",
        "task_labels",
        ["owner_id", "label_id", "task_id"],
        unique=False,
    )

    op.create_table(
        "task_steps",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("completed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
            "length(btrim(title)) > 0",
            name=op.f("ck_task_steps_nonempty_title"),
        ),
        sa.CheckConstraint("position >= 0", name=op.f("ck_task_steps_position")),
        sa.ForeignKeyConstraint(
            ["task_id", "owner_id"],
            ["tasks.id", "tasks.owner_id"],
            name="fk_task_steps_task_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_steps")),
        sa.UniqueConstraint(
            "task_id",
            "position",
            name="uq_task_steps_task_position",
            deferrable=True,
            initially="DEFERRED",
        ),
    )
    op.create_index(
        "ix_task_steps_owner_task",
        "task_steps",
        ["owner_id", "task_id", "position"],
        unique=False,
    )

    op.create_table(
        "task_links",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=120), server_default="", nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("position >= 0", name=op.f("ck_task_links_position")),
        sa.CheckConstraint("url ~ '^https?://'", name=op.f("ck_task_links_http_url")),
        sa.ForeignKeyConstraint(
            ["task_id", "owner_id"],
            ["tasks.id", "tasks.owner_id"],
            name="fk_task_links_task_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_links")),
        sa.UniqueConstraint(
            "task_id",
            "position",
            name="uq_task_links_task_position",
            deferrable=True,
            initially="DEFERRED",
        ),
    )
    op.create_index(
        "ix_task_links_owner_task",
        "task_links",
        ["owner_id", "task_id", "position"],
        unique=False,
    )

    op.create_table(
        "user_operations",
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("scope", sa.String(length=120), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_user_operations_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "owner_id",
            "operation_id",
            name=op.f("pk_user_operations"),
        ),
    )
    op.create_index(
        "ix_user_operations_owner_created",
        "user_operations",
        ["owner_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "change_events",
        sa.Column("sequence", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("entity_type", sa.String(length=20), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("entity_version", sa.Integer(), nullable=False),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "entity_type IN ('project', 'task', 'step', 'label')",
            name=op.f("ck_change_events_entity_type"),
        ),
        sa.CheckConstraint(
            "action IN ('created', 'updated', 'completed', 'reopened', 'deleted')",
            name=op.f("ck_change_events_action"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_change_events_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("sequence", name=op.f("pk_change_events")),
    )
    op.create_index(
        "ix_change_events_owner_sequence",
        "change_events",
        ["owner_id", "sequence"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_change_events_owner_sequence", table_name="change_events")
    op.drop_table("change_events")
    op.drop_index("ix_user_operations_owner_created", table_name="user_operations")
    op.drop_table("user_operations")
    op.drop_index("ix_task_links_owner_task", table_name="task_links")
    op.drop_table("task_links")
    op.drop_index("ix_task_steps_owner_task", table_name="task_steps")
    op.drop_table("task_steps")
    op.drop_index("ix_task_labels_owner_label", table_name="task_labels")
    op.drop_table("task_labels")
    op.drop_index("ix_tasks_owner_updated", table_name="tasks")
    op.drop_index("ix_tasks_owner_status_updated", table_name="tasks")
    op.drop_index("ix_tasks_owner_project_updated", table_name="tasks")
    op.drop_index("ix_tasks_owner_priority_updated", table_name="tasks")
    op.drop_table("tasks")
    op.drop_index("ix_labels_owner_name", table_name="labels")
    op.drop_table("labels")
    op.drop_index("ix_projects_owner_updated", table_name="projects")
    op.drop_table("projects")
