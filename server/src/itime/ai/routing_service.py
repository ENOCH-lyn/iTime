from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from itime.ai.models import (
    AIConfigCapability,
    AIConfigFallback,
    AIConfigVersion,
    AIJob,
    AIJobConfigRoute,
)
from itime.auth.security import now
from itime.errors import AppError


@dataclass(frozen=True)
class ActiveConfigRoute:
    capability: str
    routing_version: int
    configs: tuple[AIConfigVersion, ...]

    @property
    def primary(self) -> AIConfigVersion:
        return self.configs[0]

    def attempts(self, max_attempts: int) -> tuple[AIConfigVersion, ...]:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        return tuple(
            self.configs[min(index, len(self.configs) - 1)]
            for index in range(max_attempts)
        )


async def read_config_route(
    session: AsyncSession,
    capability: str,
    *,
    lock: bool = False,
) -> tuple[
    AIConfigCapability,
    AIConfigVersion | None,
    list[tuple[AIConfigFallback, AIConfigVersion]],
]:
    state_statement = select(AIConfigCapability).where(
        AIConfigCapability.capability == capability
    )
    if lock:
        state_statement = state_statement.with_for_update()
    state = await session.scalar(state_statement)
    if state is None:
        raise AppError(
            "ai_config_state_unavailable",
            "AI 配置状态暂时不可用，请检查数据库迁移。",
            status=503,
            retryable=True,
        )
    primary = (
        await session.get(AIConfigVersion, state.current_version_id)
        if state.current_version_id is not None
        else None
    )
    fallback_statement = (
        select(AIConfigFallback, AIConfigVersion)
        .join(
            AIConfigVersion,
            AIConfigVersion.id == AIConfigFallback.config_version_id,
        )
        .where(AIConfigFallback.capability == capability)
        .order_by(AIConfigFallback.position)
    )
    if lock:
        fallback_statement = fallback_statement.with_for_update(
            of=AIConfigFallback
        )
    fallback_rows = list((await session.execute(fallback_statement)).all())
    return state, primary, fallback_rows


def _pricing_complete(config: AIConfigVersion) -> bool:
    return (
        config.price_currency is not None
        and config.input_price_nanos_per_million_tokens is not None
        and config.cached_input_price_nanos_per_million_tokens is not None
        and config.output_price_nanos_per_million_tokens is not None
    )


async def load_active_config_route(
    session: AsyncSession,
    capability: str,
) -> ActiveConfigRoute:
    state, primary, fallback_rows = await read_config_route(
        session,
        capability,
    )
    if primary is None or primary.status != "published":
        raise AppError(
            "ai_capability_unavailable",
            "此 AI 能力暂未配置，请稍后重试。",
            status=503,
            retryable=True,
        )
    configs = (primary, *(config for _row, config in fallback_rows))
    currency = primary.price_currency
    if not _pricing_complete(primary):
        raise AppError(
            "ai_quota_pricing_unavailable",
            "当前 AI 配置缺少价格，暂时不能接收新请求。",
            status=503,
        )
    for config in configs[1:]:
        if (
            config.status not in {"tested", "retired"}
            or config.last_test_completed_at is None
            or config.last_test_error_code is not None
        ):
            raise AppError(
                "ai_config_route_invalid",
                "AI 备用路由包含不可用配置，请管理员重新设置。",
                status=503,
                retryable=True,
            )
        if not _pricing_complete(config) or config.price_currency != currency:
            raise AppError(
                "ai_quota_pricing_unavailable",
                "AI 主备配置价格币种不一致，暂时不能接收新请求。",
                status=503,
            )
    return ActiveConfigRoute(
        capability=capability,
        routing_version=state.routing_version,
        configs=configs,
    )


async def replace_fallbacks(
    session: AsyncSession,
    capability: str,
    *,
    expected_routing_version: int,
    config_ids: list[UUID],
) -> tuple[
    AIConfigCapability,
    AIConfigVersion,
    list[tuple[AIConfigFallback, AIConfigVersion]],
    list[UUID],
]:
    state, primary, current_rows = await read_config_route(
        session,
        capability,
        lock=True,
    )
    if state.routing_version != expected_routing_version:
        raise AppError(
            "ai_config_route_version_conflict",
            "AI 调用顺序已被其他管理员修改，请刷新后重试。",
            status=409,
            details={"current_version": state.routing_version},
        )
    if len(config_ids) != len(set(config_ids)):
        raise AppError(
            "ai_config_route_duplicate",
            "备用配置不能重复。",
            status=422,
        )
    if primary is None or primary.status != "published":
        raise AppError(
            "ai_capability_unavailable",
            "请先发布此能力的主配置。",
            status=409,
        )
    if primary.id in config_ids:
        raise AppError(
            "ai_config_route_primary_reused",
            "当前主配置不能同时作为备用配置。",
            status=409,
        )
    configs = list(
        await session.scalars(
            select(AIConfigVersion).where(
                AIConfigVersion.id.in_(config_ids),
                AIConfigVersion.capability == capability,
            )
        )
    ) if config_ids else []
    by_id = {config.id: config for config in configs}
    if len(by_id) != len(config_ids):
        raise AppError(
            "ai_config_route_config_not_found",
            "备用配置不存在或不属于此能力。",
            status=404,
        )
    ordered_configs = [by_id[config_id] for config_id in config_ids]
    for config in ordered_configs:
        if (
            config.status not in {"tested", "retired"}
            or config.last_test_completed_at is None
            or config.last_test_error_code is not None
        ):
            raise AppError(
                "ai_config_route_config_not_tested",
                "备用配置必须先通过最近一次合成测试。",
                status=409,
            )
        if not _pricing_complete(config):
            raise AppError(
                "ai_config_route_pricing_unavailable",
                "备用配置必须提供完整价格。",
                status=409,
            )
        if config.price_currency != primary.price_currency:
            raise AppError(
                "ai_config_route_currency_mismatch",
                "主配置与备用配置必须使用相同币种。",
                status=409,
                details={"currency": primary.price_currency},
            )
    current_ids = [row.config_version_id for row, _config in current_rows]
    if current_ids == config_ids:
        return state, primary, current_rows, current_ids

    await session.execute(
        delete(AIConfigFallback).where(
            AIConfigFallback.capability == capability
        )
    )
    current = now()
    rows = [
        AIConfigFallback(
            capability=capability,
            position=index,
            config_version_id=config.id,
            created_at=current,
            updated_at=current,
        )
        for index, config in enumerate(ordered_configs, start=1)
    ]
    session.add_all(rows)
    state.routing_version += 1
    state.updated_at = current
    await session.flush()
    return (
        state,
        primary,
        list(zip(rows, ordered_configs, strict=True)),
        current_ids,
    )


async def remove_fallback_config(
    session: AsyncSession,
    state: AIConfigCapability,
    config_id: UUID,
) -> bool:
    rows = list(
        await session.scalars(
            select(AIConfigFallback)
            .where(AIConfigFallback.capability == state.capability)
            .order_by(AIConfigFallback.position)
            .with_for_update()
        )
    )
    remaining = [
        row for row in rows if row.config_version_id != config_id
    ]
    if len(remaining) == len(rows):
        return False
    await session.execute(
        delete(AIConfigFallback).where(
            AIConfigFallback.capability == state.capability
        )
    )
    current = now()
    session.add_all(
        [
            AIConfigFallback(
                capability=state.capability,
                position=index,
                config_version_id=row.config_version_id,
                created_at=row.created_at,
                updated_at=current,
            )
            for index, row in enumerate(remaining, start=1)
        ]
    )
    return True


async def fallback_config_in_use(
    session: AsyncSession,
    config_id: UUID,
) -> bool:
    return (
        await session.scalar(
            select(AIConfigFallback.config_version_id).where(
                AIConfigFallback.config_version_id == config_id
            )
        )
        is not None
    )


async def replace_job_config_route(
    session: AsyncSession,
    job: AIJob,
    route: ActiveConfigRoute,
) -> None:
    configs = route.attempts(job.max_attempts)
    rows = list(
        await session.scalars(
            select(AIJobConfigRoute)
            .where(AIJobConfigRoute.job_id == job.id)
            .order_by(AIJobConfigRoute.attempt_number)
            .with_for_update()
        )
    )
    if len(rows) != job.max_attempts:
        raise AppError(
            "ai_job_route_invalid",
            "AI 作业调用路由不完整，请保留请求编号并联系管理员。",
            status=503,
            saved="unknown",
        )
    for row, config in zip(rows, configs, strict=True):
        row.config_version_id = config.id
    await session.flush()
