"""Account deletion lifecycle and deletion receipts."""

import sqlalchemy as sa
from alembic import op

revision = "0029_account_deletions"
down_revision = "0028_sync_feed"
branch_labels = None
depends_on = None


def _replace_user_status_constraint(*, account_deletion: bool) -> None:
    op.drop_constraint(
        op.f("ck_users_status"),
        "users",
        type_="check",
    )
    statuses = (
        "('active', 'suspended', 'pending_deletion')"
        if account_deletion
        else "('active', 'suspended')"
    )
    op.create_check_constraint(
        op.f("ck_users_status"),
        "users",
        f"status IN {statuses}",
    )


def _replace_media_deletion_reason_constraint(*, account_deletion: bool) -> None:
    op.drop_constraint(
        op.f("ck_temporary_media_deletion_reason"),
        "temporary_media",
        type_="check",
    )
    reasons = (
        "('confirmed', 'expired', 'account_deletion')"
        if account_deletion
        else "('confirmed', 'expired')"
    )
    op.create_check_constraint(
        op.f("ck_temporary_media_deletion_reason"),
        "temporary_media",
        f"deletion_reason IS NULL OR deletion_reason IN {reasons}",
    )


def upgrade() -> None:
    _replace_user_status_constraint(account_deletion=True)
    _replace_media_deletion_reason_constraint(account_deletion=True)
    op.create_table(
        "account_deletions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("subject_digest", sa.String(length=64), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("purge_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_id", sa.Uuid(), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=80), nullable=True),
        sa.Column("last_error_message", sa.String(length=300), nullable=True),
        sa.Column("request_id", sa.String(length=36), nullable=False),
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
            "status IN ('pending', 'processing', 'cancelled', 'completed')",
            name=op.f("ck_account_deletions_status"),
        ),
        sa.CheckConstraint(
            "subject_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_account_deletions_subject_digest"),
        ),
        sa.CheckConstraint(
            "purge_after > requested_at",
            name=op.f("ck_account_deletions_purge_after"),
        ),
        sa.CheckConstraint(
            "available_at >= requested_at",
            name=op.f("ck_account_deletions_available_at"),
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name=op.f("ck_account_deletions_attempt_count"),
        ),
        sa.CheckConstraint(
            "(last_error_code IS NULL) = (last_error_message IS NULL)",
            name=op.f("ck_account_deletions_error_pair"),
        ),
        sa.CheckConstraint(
            "(cancelled_at IS NULL OR "
            "(cancelled_at >= requested_at AND cancelled_at < purge_after)) "
            "AND (started_at IS NULL OR started_at >= requested_at) "
            "AND (completed_at IS NULL OR completed_at >= purge_after)",
            name=op.f("ck_account_deletions_event_times"),
        ),
        sa.CheckConstraint(
            "(status = 'pending' "
            "AND user_id IS NOT NULL "
            "AND lease_id IS NULL AND lease_until IS NULL "
            "AND cancelled_at IS NULL AND completed_at IS NULL) "
            "OR (status = 'processing' "
            "AND user_id IS NOT NULL "
            "AND lease_id IS NOT NULL AND lease_until IS NOT NULL "
            "AND started_at IS NOT NULL "
            "AND cancelled_at IS NULL AND completed_at IS NULL) "
            "OR (status = 'cancelled' "
            "AND lease_id IS NULL AND lease_until IS NULL "
            "AND cancelled_at IS NOT NULL AND completed_at IS NULL) "
            "OR (status = 'completed' "
            "AND user_id IS NULL "
            "AND lease_id IS NULL AND lease_until IS NULL "
            "AND cancelled_at IS NULL AND completed_at IS NOT NULL)",
            name=op.f("ck_account_deletions_state"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_account_deletions_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_account_deletions")),
        sa.UniqueConstraint(
            "operation_id",
            name=op.f("uq_account_deletions_operation_id"),
        ),
    )
    op.create_index(
        "uq_account_deletions_user_active",
        "account_deletions",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'processing')"),
    )
    op.create_index(
        "ix_account_deletions_due",
        "account_deletions",
        ["available_at", "purge_after", "lease_until", "id"],
        unique=False,
        postgresql_where=sa.text("status IN ('pending', 'processing')"),
    )
    op.create_index(
        "ix_account_deletions_subject_digest",
        "account_deletions",
        ["subject_digest"],
        unique=False,
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM account_deletions) THEN
                RAISE EXCEPTION
                    'account_deletions must be empty before downgrade';
            END IF;
        END
        $$
        """
    )
    op.drop_table("account_deletions")
    _replace_media_deletion_reason_constraint(account_deletion=False)
    _replace_user_status_constraint(account_deletion=False)
