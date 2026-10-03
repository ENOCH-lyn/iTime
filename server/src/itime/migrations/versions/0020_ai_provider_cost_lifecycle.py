"""AI provider cost request lifecycle constraint"""

from alembic import op

revision = "0020_ai_provider_cost_lifecycle"
down_revision = "0019_ai_provider_costs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_ai_provider_costs_tokens"),
        "ai_provider_costs",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_ai_provider_costs_tokens"),
        "ai_provider_costs",
        "input_tokens BETWEEN 0 AND 1000000000 "
        "AND cached_input_tokens BETWEEN 0 AND 1000000000 "
        "AND output_tokens BETWEEN 0 AND 1000000000 "
        "AND total_tokens BETWEEN 0 AND 2000000000 "
        "AND cached_input_tokens <= input_tokens",
    )
    op.create_check_constraint(
        op.f("ck_ai_provider_costs_provider_started_state"),
        "ai_provider_costs",
        "(usage_source = 'not_sent') = (provider_started_at IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_ai_provider_costs_provider_started_state"),
        "ai_provider_costs",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_ai_provider_costs_tokens"),
        "ai_provider_costs",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_ai_provider_costs_tokens"),
        "ai_provider_costs",
        "input_tokens >= 0 AND cached_input_tokens >= 0 "
        "AND output_tokens >= 0 AND total_tokens >= 0 "
        "AND cached_input_tokens <= input_tokens",
    )
