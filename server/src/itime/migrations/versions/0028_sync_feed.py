"""Incremental sync cursor state and conversation change events."""

import sqlalchemy as sa
from alembic import op

revision = "0028_sync_feed"
down_revision = "0027_data_exports"
branch_labels = None
depends_on = None

OLD_ENTITY_TYPES = (
    "('project', 'task', 'step', 'label', 'time_settings', 'term', "
    "'calendar_series', 'calendar_exception', 'task_occurrence', "
    "'calendar_addition', 'calendar_source', 'privacy_settings', 'plan', "
    "'focus_session', 'difficulty', 'review_session', 'review_report', "
    "'review_annotation', 'memory', 'memory_settings')"
)
NEW_ENTITY_TYPES = OLD_ENTITY_TYPES[:-1] + ", 'conversation')"


def _replace_change_entity_types(*, expanded: bool) -> None:
    op.drop_constraint(
        op.f("ck_change_events_entity_type"),
        "change_events",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_change_events_entity_type"),
        "change_events",
        f"entity_type IN {NEW_ENTITY_TYPES if expanded else OLD_ENTITY_TYPES}",
    )


def upgrade() -> None:
    op.create_table(
        "sync_states",
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column(
            "discarded_through_sequence",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("last_cleanup_at", sa.DateTime(timezone=True), nullable=True),
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
            "discarded_through_sequence >= 0",
            name=op.f("ck_sync_states_discarded_through_sequence"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_sync_states_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("owner_id", name=op.f("pk_sync_states")),
    )
    op.execute(
        """
        INSERT INTO sync_states (owner_id)
        SELECT id FROM users
        ON CONFLICT (owner_id) DO NOTHING
        """
    )
    op.execute(
        """
        CREATE FUNCTION itime_create_sync_state()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            INSERT INTO sync_states (owner_id) VALUES (NEW.id)
            ON CONFLICT (owner_id) DO NOTHING;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER users_sync_state_after_insert
        AFTER INSERT ON users
        FOR EACH ROW EXECUTE FUNCTION itime_create_sync_state()
        """
    )
    _replace_change_entity_types(expanded=True)


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM change_events
                WHERE entity_type = 'conversation'
            ) THEN
                RAISE EXCEPTION
                    'cannot downgrade with conversation synchronization events';
            END IF;
        END
        $$
        """
    )
    _replace_change_entity_types(expanded=False)
    op.execute("DROP TRIGGER users_sync_state_after_insert ON users")
    op.execute("DROP FUNCTION itime_create_sync_state()")
    op.drop_table("sync_states")
