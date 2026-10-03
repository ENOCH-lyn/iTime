from datetime import datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from itime.ai.governance_models import AIBodyLog
from itime.ai.models import (
    AIConfigVersion,
    AIJob,
    AIJobAttempt,
    AIProviderCost,
)
from itime.ai.pricing import token_cost_breakdown
from itime.ai.usage import ESTIMATOR_VERSION, estimate_usage, normalize_provider_usage
from itime.auth.security import now
from itime.database import Database


class JobClaim(Protocol):
    id: UUID
    owner_id: UUID
    lease_id: UUID
    attempt_number: int
    deadline_at: datetime


async def mark_provider_request_started(
    database: Database,
    claim: JobClaim,
) -> bool:
    current = now()
    async with database.transaction() as session:
        job = await session.scalar(
            select(AIJob)
            .where(
                AIJob.id == claim.id,
                AIJob.owner_id == claim.owner_id,
                AIJob.status == "running",
                AIJob.lease_id == claim.lease_id,
                AIJob.lease_until > current,
                AIJob.deadline_at > current,
            )
            .with_for_update()
        )
        if job is None:
            return False
        attempt = await session.scalar(
            select(AIJobAttempt)
            .where(
                AIJobAttempt.job_id == job.id,
                AIJobAttempt.lease_id == claim.lease_id,
                AIJobAttempt.status == "running",
            )
            .with_for_update()
        )
        if attempt is None:
            return False
        if attempt.provider_started_at is None:
            attempt.provider_started_at = current
        return True


async def add_provider_cost(
    session: AsyncSession,
    job: AIJob,
    attempt: AIJobAttempt,
    *,
    provider_usage: object | None,
) -> None:
    existing = await session.scalar(
        select(AIProviderCost.id).where(
            AIProviderCost.job_id == job.id,
            AIProviderCost.attempt_number == attempt.attempt_number,
        )
    )
    if existing is not None:
        return
    body_log = await session.scalar(
        select(AIBodyLog).where(
            AIBodyLog.job_id == job.id,
            AIBodyLog.attempt_number == attempt.attempt_number,
        )
    )
    config = await session.get(AIConfigVersion, job.config_version_id)

    if attempt.provider_started_at is None:
        usage_source = "not_sent"
        estimation_reason = None
        estimator_version = None
        input_tokens = 0
        cached_input_tokens = 0
        output_tokens = 0
        total_tokens = 0
        usage_details: dict = {}
    else:
        usage = normalize_provider_usage(provider_usage)
        if usage is not None:
            usage_source = "provider"
            estimation_reason = None
            estimator_version = None
        else:
            usage_source = "estimated"
            estimation_reason = (
                "missing_usage" if provider_usage is None else "invalid_usage"
            )
            estimator_version = ESTIMATOR_VERSION
            usage = estimate_usage(
                body_log.request_body if body_log is not None else {},
                body_log.response_body if body_log is not None else "",
                capability=job.capability,
                input_summary=job.input_summary,
            )
        input_tokens = usage.input_tokens
        cached_input_tokens = usage.cached_input_tokens
        output_tokens = usage.output_tokens
        total_tokens = usage.total_tokens
        usage_details = usage.details

    prices = (
        config.input_price_nanos_per_million_tokens if config is not None else None,
        (
            config.cached_input_price_nanos_per_million_tokens
            if config is not None
            else None
        ),
        config.output_price_nanos_per_million_tokens if config is not None else None,
    )
    if (
        config is not None
        and config.price_currency is not None
        and all(price is not None for price in prices)
    ):
        input_price, cached_input_price, output_price = prices
        assert input_price is not None
        assert cached_input_price is not None
        assert output_price is not None
        (
            input_cost,
            cached_input_cost,
            output_cost,
            total_cost,
        ) = token_cost_breakdown(
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
            input_price=input_price,
            cached_input_price=cached_input_price,
            output_price=output_price,
        )
        price_currency = config.price_currency
    else:
        input_price = None
        cached_input_price = None
        output_price = None
        input_cost = None
        cached_input_cost = None
        output_cost = None
        total_cost = None
        price_currency = None

    session.add(
        AIProviderCost(
            job_id=job.id,
            owner_id=job.owner_id,
            attempt_number=attempt.attempt_number,
            attempt_status=attempt.status,
            provider_started_at=attempt.provider_started_at,
            usage_source=usage_source,
            estimation_reason=estimation_reason,
            estimator_version=estimator_version,
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            usage_details=usage_details,
            price_currency=price_currency,
            input_price_nanos_per_million_tokens=input_price,
            cached_input_price_nanos_per_million_tokens=cached_input_price,
            output_price_nanos_per_million_tokens=output_price,
            input_cost_nanos=input_cost,
            cached_input_cost_nanos=cached_input_cost,
            output_cost_nanos=output_cost,
            total_cost_nanos=total_cost,
            created_at=now(),
        )
    )
