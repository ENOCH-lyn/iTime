from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from itime.ai.models import AIConfigVersion, AIJob, AIProviderCost
from itime.ai.pricing import (
    audio_duration_cost,
    format_currency_nanos,
    token_cost_breakdown,
)
from itime.ai.usage import estimate_usage
from itime.auth.models import User
from itime.auth.security import now
from itime.calendar.models import UserTimeSettings
from itime.calendar.service import activity_day_at
from itime.calendar.time_math import earliest_valid_local_datetime
from itime.errors import AppError
from itime.quota.models import (
    AIDailyAllowance,
    AIQuotaLedgerEntry,
    AIQuotaPolicyState,
    AIQuotaPolicyVersion,
    AIQuotaReservation,
)

RESERVATION_ESTIMATOR_VERSION = "reservation-utf8-vision-v1"
AUDIO_RESERVATION_ESTIMATOR_VERSION = "reservation-audio-duration-v1"


class PricedConfig(Protocol):
    max_output_tokens: int | None
    pricing_basis: str
    price_currency: str | None
    input_price_nanos_per_million_tokens: int | None
    cached_input_price_nanos_per_million_tokens: int | None
    output_price_nanos_per_million_tokens: int | None
    audio_price_nanos_per_minute: int | None


@dataclass(frozen=True)
class ActivityWindow:
    activity_date: date
    time_zone: str
    start_minute: int
    window_start_at: datetime
    window_end_at: datetime


@dataclass(frozen=True)
class BudgetEstimate:
    currency: str
    input_tokens: int
    output_tokens: int
    reserved_nanos: int
    estimator_version: str


@dataclass(frozen=True)
class QuotaSnapshot:
    base_amount_nanos: int
    adjustment_nanos: int
    charged_nanos: int
    reserved_nanos: int
    available_nanos: int


def _activity_boundary(
    activity_date: date,
    time_zone: str,
    start_minute: int,
) -> datetime:
    local_value = datetime.combine(activity_date, time()) + timedelta(
        minutes=start_minute
    )
    for minute_offset in range(24 * 60 + 1):
        candidate = earliest_valid_local_datetime(
            local_value + timedelta(minutes=minute_offset),
            time_zone,
        )
        if candidate is not None:
            return candidate
    raise AppError(
        "activity_day_unavailable",
        "当前时区无法确定额度刷新边界，请检查时区设置。",
        status=409,
    )


def activity_window(
    instant: datetime,
    time_zone: str,
    start_minute: int = 240,
) -> ActivityWindow:
    if instant.tzinfo is None:
        raise ValueError("instant must include a UTC offset")
    activity_date = activity_day_at(
        instant,
        time_zone,
        start_minute=start_minute,
    )
    return ActivityWindow(
        activity_date=activity_date,
        time_zone=time_zone,
        start_minute=start_minute,
        window_start_at=_activity_boundary(
            activity_date,
            time_zone,
            start_minute,
        ),
        window_end_at=_activity_boundary(
            activity_date + timedelta(days=1),
            time_zone,
            start_minute,
        ),
    )


def estimate_reservation_budget(
    config: PricedConfig,
    *,
    request_body: dict,
    input_summary: dict,
    capability: str,
) -> BudgetEstimate:
    prices = (
        config.input_price_nanos_per_million_tokens,
        config.cached_input_price_nanos_per_million_tokens,
        config.output_price_nanos_per_million_tokens,
    )
    if config.price_currency is None or any(price is None for price in prices):
        raise AppError(
            "ai_quota_pricing_unavailable",
            "当前 AI 配置缺少价格，暂时不能接收新请求。",
            status=503,
        )
    input_price, cached_input_price, output_price = prices
    assert input_price is not None
    assert cached_input_price is not None
    assert output_price is not None
    estimated = estimate_usage(
        request_body,
        "",
        capability=capability,
        input_summary=input_summary,
    )
    output_tokens = config.max_output_tokens
    if output_tokens is None:
        raise AppError(
            "ai_quota_pricing_unavailable",
            "当前 AI 配置缺少最大输出限制，暂时不能接收新请求。",
            status=503,
        )
    _, _, _, reserved_nanos = token_cost_breakdown(
        input_tokens=estimated.input_tokens,
        cached_input_tokens=0,
        output_tokens=output_tokens,
        input_price=input_price,
        cached_input_price=cached_input_price,
        output_price=output_price,
    )
    return BudgetEstimate(
        currency=config.price_currency,
        input_tokens=estimated.input_tokens,
        output_tokens=output_tokens,
        reserved_nanos=reserved_nanos,
        estimator_version=RESERVATION_ESTIMATOR_VERSION,
    )


def estimate_audio_reservation_budget(
    config: PricedConfig,
    *,
    duration_ms: int,
) -> BudgetEstimate:
    if (
        config.pricing_basis != "audio_minute"
        or config.price_currency is None
        or config.audio_price_nanos_per_minute is None
    ):
        raise AppError(
            "ai_quota_pricing_unavailable",
            "当前语音转写配置缺少音频价格，暂时不能接收新请求。",
            status=503,
        )
    return BudgetEstimate(
        currency=config.price_currency,
        input_tokens=0,
        output_tokens=0,
        reserved_nanos=audio_duration_cost(
            duration_ms,
            config.audio_price_nanos_per_minute,
        ),
        estimator_version=AUDIO_RESERVATION_ESTIMATOR_VERSION,
    )


def maximum_reservation_estimate(
    estimates: list[BudgetEstimate],
) -> BudgetEstimate:
    if not estimates:
        raise ValueError("at least one quota estimate is required")
    currencies = {estimate.currency for estimate in estimates}
    if len(currencies) != 1:
        raise AppError(
            "ai_quota_pricing_unavailable",
            "AI 主备配置价格币种不一致，暂时不能接收新请求。",
            status=503,
        )
    return max(estimates, key=lambda estimate: estimate.reserved_nanos)


async def _current_policy(session: AsyncSession) -> AIQuotaPolicyVersion:
    state = await session.scalar(
        select(AIQuotaPolicyState)
        .where(AIQuotaPolicyState.id == 1)
        .with_for_update(read=True)
    )
    policy = (
        await session.get(AIQuotaPolicyVersion, state.current_version_id)
        if state is not None and state.current_version_id is not None
        else None
    )
    if policy is None or policy.status != "published":
        raise AppError(
            "ai_quota_policy_unavailable",
            "每日 AI 额度尚未配置，请稍后重试。",
            status=503,
        )
    return policy


async def get_or_create_daily_allowance(
    session: AsyncSession,
    owner_id: UUID,
    *,
    accepted_at: datetime,
) -> AIDailyAllowance:
    if accepted_at.tzinfo is None:
        raise ValueError("accepted_at must include a UTC offset")
    user = await session.scalar(
        select(User).where(User.id == owner_id).with_for_update()
    )
    if user is None:
        raise AppError("user_not_found", "未找到此账号。", status=404)

    covering = await session.scalar(
        select(AIDailyAllowance)
        .where(
            AIDailyAllowance.owner_id == owner_id,
            AIDailyAllowance.window_start_at <= accepted_at,
            AIDailyAllowance.window_end_at > accepted_at,
        )
        .order_by(
            AIDailyAllowance.window_end_at.desc(),
            AIDailyAllowance.id.desc(),
        )
        .limit(1)
        .with_for_update()
    )
    if covering is not None:
        return covering

    settings = await session.scalar(
        select(UserTimeSettings)
        .where(UserTimeSettings.owner_id == owner_id)
        .with_for_update(read=True)
    )
    if settings is None:
        raise AppError(
            "time_settings_required",
            "请先保存当前使用的时区，再使用 AI 功能。",
            status=409,
        )
    policy = await _current_policy(session)
    window = activity_window(
        accepted_at,
        settings.time_zone,
        settings.activity_day_start_minute,
    )
    daily = AIDailyAllowance(
        owner_id=owner_id,
        activity_date=window.activity_date,
        window_start_at=window.window_start_at,
        window_end_at=window.window_end_at,
        time_zone=window.time_zone,
        activity_day_start_minute=window.start_minute,
        policy_version_id=policy.id,
        currency=policy.currency,
        base_amount_nanos=policy.daily_allowance_nanos,
        created_at=now(),
        updated_at=now(),
    )
    session.add(daily)
    await session.flush()
    return daily


async def quota_snapshot(
    session: AsyncSession,
    daily: AIDailyAllowance,
) -> QuotaSnapshot:
    adjustment_nanos = (
        select(
            func.coalesce(
                func.sum(AIQuotaLedgerEntry.delta_nanos),
                0,
            )
        )
        .where(
            AIQuotaLedgerEntry.daily_allowance_id == daily.id,
            AIQuotaLedgerEntry.kind.in_(["admin_credit", "admin_debit"]),
        )
        .scalar_subquery()
    )
    usage_delta = (
        select(
            func.coalesce(
                func.sum(AIQuotaLedgerEntry.delta_nanos),
                0,
            )
        )
        .where(
            AIQuotaLedgerEntry.daily_allowance_id == daily.id,
            AIQuotaLedgerEntry.kind == "usage_charge",
        )
        .scalar_subquery()
    )
    reserved_nanos = (
        select(func.coalesce(func.sum(AIQuotaReservation.reserved_nanos), 0))
        .where(
            AIQuotaReservation.daily_allowance_id == daily.id,
            AIQuotaReservation.status == "reserved",
        )
        .scalar_subquery()
    )
    adjustment_nanos, usage_delta, reserved_nanos = (
        await session.execute(
            select(adjustment_nanos, usage_delta, reserved_nanos)
        )
    ).one()
    adjustment_nanos = int(adjustment_nanos)
    charged_nanos = -int(usage_delta)
    reserved_nanos = int(reserved_nanos or 0)
    available_nanos = (
        daily.base_amount_nanos
        + adjustment_nanos
        - charged_nanos
        - reserved_nanos
    )
    if available_nanos < 0:
        raise AppError(
            "ai_quota_state_invalid",
            "额度账本状态不一致，请保留请求编号并联系管理员。",
            status=503,
            saved="unknown",
        )
    return QuotaSnapshot(
        base_amount_nanos=daily.base_amount_nanos,
        adjustment_nanos=adjustment_nanos,
        charged_nanos=charged_nanos,
        reserved_nanos=reserved_nanos,
        available_nanos=available_nanos,
    )


async def reserve_job_budget(
    session: AsyncSession,
    *,
    owner_id: UUID,
    job: AIJob,
    accepted_at: datetime,
    estimate: BudgetEstimate,
    daily: AIDailyAllowance | None = None,
) -> AIQuotaReservation:
    if job.owner_id != owner_id or job.quota_mode != "user":
        raise AppError(
            "ai_quota_state_invalid",
            "AI 作业额度状态不一致，请保留请求编号并联系管理员。",
            status=503,
            saved="unknown",
        )
    if daily is None:
        daily = await get_or_create_daily_allowance(
            session,
            owner_id,
            accepted_at=accepted_at,
        )
    elif (
        daily.owner_id != owner_id
        or not daily.window_start_at <= accepted_at < daily.window_end_at
    ):
        raise AppError(
            "ai_quota_state_invalid",
            "额度活动日状态不一致，请保留请求编号并联系管理员。",
            status=503,
            saved="unknown",
        )
    config = await session.get(AIConfigVersion, job.config_version_id)
    if (
        config is None
        or config.price_currency is None
        or config.price_currency != daily.currency
        or estimate.currency != daily.currency
    ):
        raise AppError(
            "ai_quota_pricing_unavailable",
            "AI 配置价格与每日额度币种不一致，暂时不能接收新请求。",
            status=503,
        )
    snapshot = await quota_snapshot(session, daily)
    if estimate.reserved_nanos > snapshot.available_nanos:
        raise AppError(
            "ai_quota_exhausted",
            "今日 AI 额度不足，本次请求未接收。",
            status=429,
            details={
                "activity_date": daily.activity_date.isoformat(),
                "currency": daily.currency,
                "required": format_currency_nanos(estimate.reserved_nanos),
                "available": format_currency_nanos(snapshot.available_nanos),
                "refresh_at": daily.window_end_at.isoformat(),
            },
        )
    reservation = AIQuotaReservation(
        job_id=job.id,
        owner_id=owner_id,
        daily_allowance_id=daily.id,
        reserved_nanos=estimate.reserved_nanos,
        estimator_version=estimate.estimator_version,
        accepted_at=accepted_at.astimezone(UTC),
        created_at=now(),
        updated_at=now(),
    )
    daily.version += 1
    daily.updated_at = now()
    session.add(reservation)
    await session.flush()
    return reservation


async def _locked_job_reservation(
    session: AsyncSession,
    job: AIJob,
) -> AIQuotaReservation | None:
    reservation = await session.scalar(
        select(AIQuotaReservation)
        .where(
            AIQuotaReservation.job_id == job.id,
            AIQuotaReservation.owner_id == job.owner_id,
        )
        .with_for_update()
    )
    if job.quota_mode == "platform":
        if reservation is not None:
            raise AppError(
                "ai_quota_state_invalid",
                "平台承担作业不应关联用户额度预留。",
                status=503,
                saved="unknown",
            )
        return None
    if reservation is None:
        raise AppError(
            "ai_quota_state_invalid",
            "AI 作业缺少额度预留，请保留请求编号并联系管理员。",
            status=503,
            saved="unknown",
        )
    return reservation


async def settle_success(
    session: AsyncSession,
    *,
    job: AIJob,
    provider_cost: AIProviderCost,
) -> AIQuotaLedgerEntry | None:
    reservation = await _locked_job_reservation(session, job)
    if reservation is None:
        return None
    if reservation.status == "settled":
        ledger = await session.scalar(
            select(AIQuotaLedgerEntry).where(
                AIQuotaLedgerEntry.reservation_id == reservation.id,
                AIQuotaLedgerEntry.kind == "usage_charge",
            )
        )
        if ledger is None:
            raise AppError(
                "ai_quota_state_invalid",
                "额度预留已结算但缺少账本记录。",
                status=503,
                saved="unknown",
            )
        return ledger
    if reservation.status != "reserved":
        raise AppError(
            "ai_quota_state_invalid",
            "额度预留已经释放，不能再次结算。",
            status=503,
            saved="unknown",
        )
    if (
        provider_cost.job_id != job.id
        or provider_cost.owner_id != job.owner_id
        or provider_cost.attempt_status != "succeeded"
        or provider_cost.usage_source not in {
            "provider",
            "estimated",
            "measured",
            "not_sent",
        }
        or provider_cost.price_currency is None
        or provider_cost.total_cost_nanos is None
    ):
        raise AppError(
            "ai_quota_state_invalid",
            "最终成功尝试缺少可结算的成本事实。",
            status=503,
            saved="unknown",
        )
    daily = await session.scalar(
        select(AIDailyAllowance)
        .where(
            AIDailyAllowance.id == reservation.daily_allowance_id,
            AIDailyAllowance.owner_id == job.owner_id,
        )
        .with_for_update()
    )
    if daily is None or daily.currency != provider_cost.price_currency:
        raise AppError(
            "ai_quota_state_invalid",
            "额度币种或活动日状态不一致。",
            status=503,
            saved="unknown",
        )
    charged_nanos = min(
        provider_cost.total_cost_nanos,
        reservation.reserved_nanos,
    )
    reservation.status = "settled"
    reservation.actual_cost_nanos = provider_cost.total_cost_nanos
    reservation.charged_nanos = charged_nanos
    reservation.platform_overage_nanos = (
        provider_cost.total_cost_nanos - charged_nanos
    )
    reservation.usage_source = provider_cost.usage_source
    reservation.provider_cost_id = provider_cost.id
    reservation.terminal_at = now()
    reservation.updated_at = reservation.terminal_at
    daily.version += 1
    daily.updated_at = reservation.terminal_at
    ledger = AIQuotaLedgerEntry(
        id=uuid4(),
        owner_id=job.owner_id,
        daily_allowance_id=daily.id,
        reservation_id=reservation.id,
        adjustment_id=None,
        job_id=job.id,
        provider_cost_id=provider_cost.id,
        kind="usage_charge",
        delta_nanos=-charged_nanos,
        currency=daily.currency,
        usage_source=provider_cost.usage_source,
        created_at=reservation.terminal_at,
    )
    session.add(ledger)
    await session.flush()
    return ledger


async def release_reservation(
    session: AsyncSession,
    *,
    job: AIJob,
    reason: str,
) -> AIQuotaReservation | None:
    if reason not in {"failed", "timed_out", "cancelled"}:
        raise ValueError("unsupported quota release reason")
    reservation = await _locked_job_reservation(session, job)
    if reservation is None:
        return None
    if reservation.status == "released":
        return reservation
    if reservation.status != "reserved":
        raise AppError(
            "ai_quota_state_invalid",
            "已结算的额度预留不能释放。",
            status=503,
            saved="unknown",
        )
    daily = await session.scalar(
        select(AIDailyAllowance)
        .where(
            AIDailyAllowance.id == reservation.daily_allowance_id,
            AIDailyAllowance.owner_id == job.owner_id,
        )
        .with_for_update()
    )
    if daily is None:
        raise AppError(
            "ai_quota_state_invalid",
            "额度活动日记录不存在。",
            status=503,
            saved="unknown",
        )
    current = now()
    reservation.status = "released"
    reservation.release_reason = reason
    reservation.terminal_at = current
    reservation.updated_at = current
    daily.version += 1
    daily.updated_at = current
    return reservation
