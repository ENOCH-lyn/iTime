from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request
from sqlalchemy import and_, or_, select

from itime.admin.security import authorized_admin
from itime.ai.models import (
    AIConfigVersion,
    AIJob,
    AIJobAttempt,
    AIProviderCost,
)
from itime.ai.pricing import format_currency_nanos, format_price_per_million
from itime.ai.schemas import Capability
from itime.auth.models import User
from itime.tasks.service import validate_cursor

router = APIRouter(prefix="/api/v1/admin/provider-costs", tags=["后台 AI 成本"])

AttemptStatus = Literal[
    "succeeded",
    "retryable_failed",
    "failed",
    "timed_out",
    "cancelled",
    "abandoned",
]
UsageSource = Literal["provider", "estimated", "not_sent"]


def cost_result(
    cost: AIProviderCost,
    job: AIJob,
    attempt: AIJobAttempt,
    config: AIConfigVersion,
    owner_email: str,
) -> dict:
    pricing = (
        {
            "currency": cost.price_currency,
            "input_per_million_tokens": format_price_per_million(
                cost.input_price_nanos_per_million_tokens
            ),
            "cached_input_per_million_tokens": format_price_per_million(
                cost.cached_input_price_nanos_per_million_tokens
            ),
            "output_per_million_tokens": format_price_per_million(
                cost.output_price_nanos_per_million_tokens
            ),
        }
        if cost.price_currency is not None
        and cost.input_price_nanos_per_million_tokens is not None
        and cost.cached_input_price_nanos_per_million_tokens is not None
        and cost.output_price_nanos_per_million_tokens is not None
        else None
    )
    amounts = (
        {
            "input": format_currency_nanos(cost.input_cost_nanos),
            "cached_input": format_currency_nanos(cost.cached_input_cost_nanos),
            "output": format_currency_nanos(cost.output_cost_nanos),
            "total": format_currency_nanos(cost.total_cost_nanos),
        }
        if cost.input_cost_nanos is not None
        and cost.cached_input_cost_nanos is not None
        and cost.output_cost_nanos is not None
        and cost.total_cost_nanos is not None
        else None
    )
    return {
        "id": cost.id,
        "job_id": cost.job_id,
        "owner": {"id": cost.owner_id, "email": owner_email},
        "attempt_number": cost.attempt_number,
        "attempt_status": cost.attempt_status,
        "capability": job.capability,
        "config": {
            "id": config.id,
            "version": config.version,
            "model": config.model,
        },
        "provider_request_id": attempt.provider_request_id,
        "http_status": attempt.http_status,
        "error_code": attempt.error_code,
        "error_stage": attempt.error_stage,
        "provider_started_at": cost.provider_started_at,
        "usage_source": cost.usage_source,
        "estimation_reason": cost.estimation_reason,
        "estimator_version": cost.estimator_version,
        "usage": {
            "input_tokens": cost.input_tokens,
            "cached_input_tokens": cost.cached_input_tokens,
            "output_tokens": cost.output_tokens,
            "total_tokens": cost.total_tokens,
            "details": cost.usage_details,
        },
        "pricing": pricing,
        "amounts": amounts,
        "created_at": cost.created_at,
    }


@router.get("")
async def list_provider_costs(
    request: Request,
    capability: Capability | None = None,
    attempt_status: AttemptStatus | None = None,
    usage_source: UsageSource | None = None,
    priced: bool | None = None,
    search: Annotated[str | None, Query(max_length=254)] = None,
    before_time: datetime | None = None,
    before_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> dict:
    validate_cursor(before_time, before_id)
    async with authorized_admin(request) as identity:
        statement = (
            select(
                AIProviderCost,
                AIJob,
                AIJobAttempt,
                AIConfigVersion,
                User.email,
            )
            .join(
                AIJob,
                and_(
                    AIJob.id == AIProviderCost.job_id,
                    AIJob.owner_id == AIProviderCost.owner_id,
                ),
            )
            .join(
                AIJobAttempt,
                and_(
                    AIJobAttempt.job_id == AIProviderCost.job_id,
                    AIJobAttempt.attempt_number == AIProviderCost.attempt_number,
                ),
            )
            .join(AIConfigVersion, AIConfigVersion.id == AIJob.config_version_id)
            .join(User, User.id == AIProviderCost.owner_id)
        )
        if capability is not None:
            statement = statement.where(AIJob.capability == capability)
        if attempt_status is not None:
            statement = statement.where(
                AIProviderCost.attempt_status == attempt_status
            )
        if usage_source is not None:
            statement = statement.where(
                AIProviderCost.usage_source == usage_source
            )
        if priced is not None:
            statement = statement.where(
                AIProviderCost.total_cost_nanos.is_not(None)
                if priced
                else AIProviderCost.total_cost_nanos.is_(None)
            )
        if search and (normalized := search.strip().lower()):
            statement = statement.where(
                User.email.contains(normalized, autoescape=True)
            )
        if before_time is not None:
            statement = statement.where(
                or_(
                    AIProviderCost.created_at < before_time,
                    and_(
                        AIProviderCost.created_at == before_time,
                        AIProviderCost.id < before_id,
                    ),
                )
            )
        rows = list(
            (
                await identity.session.execute(
                    statement.order_by(
                        AIProviderCost.created_at.desc(),
                        AIProviderCost.id.desc(),
                    ).limit(limit + 1)
                )
            ).all()
        )
        page = rows[:limit]
        return {
            "items": [
                cost_result(cost, job, attempt, config, owner_email)
                for cost, job, attempt, config, owner_email in page
            ],
            "next_cursor": (
                {
                    "before_time": page[-1][0].created_at,
                    "before_id": page[-1][0].id,
                }
                if len(rows) > limit
                else None
            ),
        }
