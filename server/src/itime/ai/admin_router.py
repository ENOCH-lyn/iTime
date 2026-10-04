from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Query, Request
from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert

from itime.admin.security import authorized_admin
from itime.ai.models import (
    AIConfigCapability,
    AIConfigFallback,
    AIConfigVersion,
)
from itime.ai.pricing import format_price_per_million, parse_price_per_million
from itime.ai.provider import (
    EndpointPolicyError,
    ProviderConfig,
    enforce_endpoint_policy,
    test_openai_compatible,
)
from itime.ai.routing_service import (
    fallback_config_in_use,
    read_config_route,
    remove_fallback_config,
    replace_fallbacks,
)
from itime.ai.schemas import (
    AIConfigCreateInput,
    AIConfigRouteOutput,
    AIConfigRouteUpdate,
    AIConfigVersionListOutput,
    AIConfigVersionOutput,
    Capability,
)
from itime.auth.security import now
from itime.auth.session import audit
from itime.errors import AppError
from itime.quota.models import AIQuotaPolicyState, AIQuotaPolicyVersion

router = APIRouter(prefix="/api/v1/admin/ai-configs", tags=["后台 AI 配置"])


def config_result(config: AIConfigVersion) -> dict:
    prices = (
        config.input_price_nanos_per_million_tokens,
        config.cached_input_price_nanos_per_million_tokens,
        config.output_price_nanos_per_million_tokens,
    )
    pricing_configured = config.price_currency is not None and all(
        price is not None for price in prices
    )
    pricing = None
    if pricing_configured:
        input_price, cached_input_price, output_price = prices
        assert input_price is not None
        assert cached_input_price is not None
        assert output_price is not None
        pricing = {
            "currency": config.price_currency,
            "input_per_million_tokens": format_price_per_million(input_price),
            "cached_input_per_million_tokens": format_price_per_million(
                cached_input_price
            ),
            "output_per_million_tokens": format_price_per_million(output_price),
        }
    return {
        "id": config.id,
        "capability": config.capability,
        "version": config.version,
        "endpoint_url": config.endpoint_url,
        "model": config.model,
        "timeout_seconds": config.timeout_seconds,
        "max_output_tokens": config.max_output_tokens,
        "pricing": pricing,
        "pricing_configured": pricing_configured,
        "key_configured": True,
        "status": config.status,
        "current": config.status == "published",
        "test_in_progress": (
            config.last_test_id is not None and config.last_test_completed_at is None
        ),
        "last_test_started_at": config.last_test_started_at,
        "last_test_completed_at": config.last_test_completed_at,
        "last_test_error_code": config.last_test_error_code,
        "last_test_error_stage": config.last_test_error_stage,
        "created_by_user_id": config.created_by_user_id,
        "last_tested_by_user_id": config.last_tested_by_user_id,
        "published_by_user_id": config.published_by_user_id,
        "created_at": config.created_at,
        "updated_at": config.updated_at,
        "published_at": config.published_at,
        "retired_at": config.retired_at,
    }


def config_not_found() -> AppError:
    return AppError("ai_config_not_found", "未找到此 AI 配置版本。", status=404)


async def locked_capability(session, capability: str) -> AIConfigCapability:
    await session.execute(
        insert(AIConfigCapability)
        .values(capability=capability, next_version=1)
        .on_conflict_do_nothing(index_elements=[AIConfigCapability.capability])
    )
    state = await session.scalar(
        select(AIConfigCapability)
        .where(AIConfigCapability.capability == capability)
        .with_for_update()
    )
    if state is None:
        raise AppError(
            "ai_config_state_unavailable",
            "AI 配置状态暂时不可用，请检查数据库迁移。",
            status=503,
            retryable=True,
        )
    return state


@router.get("", response_model=AIConfigVersionListOutput)
async def list_configs(
    request: Request,
    capability: Capability | None = None,
    status: Literal["draft", "tested", "published", "retired"] | None = None,
    before_time: datetime | None = None,
    before_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> dict:
    if (before_time is None) != (before_id is None) or (
        before_time is not None and before_time.tzinfo is None
    ):
        raise AppError("invalid_cursor", "列表游标无效，请重新加载。")
    async with authorized_admin(request) as identity:
        statement = select(AIConfigVersion)
        if capability is not None:
            statement = statement.where(AIConfigVersion.capability == capability)
        if status is not None:
            statement = statement.where(AIConfigVersion.status == status)
        if before_time is not None:
            statement = statement.where(
                or_(
                    AIConfigVersion.created_at < before_time,
                    and_(
                        AIConfigVersion.created_at == before_time,
                        AIConfigVersion.id < before_id,
                    ),
                )
            )
        configs = list(
            await identity.session.scalars(
                statement.order_by(
                    AIConfigVersion.created_at.desc(),
                    AIConfigVersion.id.desc(),
                ).limit(limit + 1)
            )
        )
        page = configs[:limit]
        return {
            "items": [config_result(config) for config in page],
            "next_cursor": (
                {"before_time": page[-1].created_at, "before_id": page[-1].id}
                if len(configs) > limit
                else None
            ),
        }


def config_route_result(
    state: AIConfigCapability,
    primary: AIConfigVersion | None,
    fallbacks: list[tuple[AIConfigFallback, AIConfigVersion]],
) -> dict:
    return {
        "capability": state.capability,
        "routing_version": state.routing_version,
        "primary": config_result(primary) if primary is not None else None,
        "fallbacks": [
            {
                "position": row.position,
                "config": config_result(config),
            }
            for row, config in fallbacks
        ],
    }


@router.get(
    "/routing/{capability}",
    response_model=AIConfigRouteOutput,
)
async def get_config_route(
    capability: Capability,
    request: Request,
) -> dict:
    async with authorized_admin(request) as identity:
        state, primary, fallbacks = await read_config_route(
            identity.session,
            capability,
        )
        return config_route_result(state, primary, fallbacks)


@router.put(
    "/routing/{capability}",
    response_model=AIConfigRouteOutput,
)
async def update_config_route(
    capability: Capability,
    data: AIConfigRouteUpdate,
    request: Request,
) -> dict:
    async with authorized_admin(request, write=True) as identity:
        state, primary, fallbacks, previous_ids = await replace_fallbacks(
            identity.session,
            capability,
            expected_routing_version=data.expected_routing_version,
            config_ids=data.fallback_config_ids,
        )
        current_ids = [row.config_version_id for row, _config in fallbacks]
        if current_ids != previous_ids:
            audit(
                request,
                identity.session,
                identity.user,
                "ai_config_fallbacks_updated",
                primary.id,
                details={
                    "capability": capability,
                    "routing_version": state.routing_version,
                    "before": [str(config_id) for config_id in previous_ids],
                    "after": [str(config_id) for config_id in current_ids],
                },
            )
        return config_route_result(state, primary, fallbacks)


@router.get("/{config_id}", response_model=AIConfigVersionOutput)
async def get_config(config_id: UUID, request: Request) -> dict:
    async with authorized_admin(request) as identity:
        config = await identity.session.get(AIConfigVersion, config_id)
        if config is None:
            raise config_not_found()
        return config_result(config)


@router.post("", status_code=201, response_model=AIConfigVersionOutput)
async def create_config(data: AIConfigCreateInput, request: Request) -> dict:
    try:
        enforce_endpoint_policy(data.endpoint_url, request.app.state.settings)
    except EndpointPolicyError as exc:
        raise AppError("ai_endpoint_insecure", str(exc), status=422) from exc
    ciphertext = request.app.state.security.ai_cipher.encrypt(
        data.api_key.get_secret_value().encode()
    )
    input_price = parse_price_per_million(data.pricing.input_per_million_tokens)
    cached_input_price = parse_price_per_million(
        data.pricing.cached_input_per_million_tokens
        or data.pricing.input_per_million_tokens
    )
    output_price = parse_price_per_million(data.pricing.output_per_million_tokens)
    async with authorized_admin(request, write=True) as identity:
        state = await locked_capability(identity.session, data.capability)
        config = AIConfigVersion(
            capability=data.capability,
            version=state.next_version,
            endpoint_url=data.endpoint_url,
            model=data.model,
            api_key_ciphertext=ciphertext,
            timeout_seconds=data.timeout_seconds,
            max_output_tokens=data.max_output_tokens,
            price_currency=data.pricing.currency,
            input_price_nanos_per_million_tokens=input_price,
            cached_input_price_nanos_per_million_tokens=cached_input_price,
            output_price_nanos_per_million_tokens=output_price,
            created_by_user_id=identity.user.id,
        )
        state.next_version += 1
        state.updated_at = now()
        identity.session.add(config)
        await identity.session.flush()
        audit(
            request,
            identity.session,
            identity.user,
            "ai_config_version_created",
            config.id,
        )
        return config_result(config)


@router.post("/{config_id}/test", response_model=AIConfigVersionOutput)
async def test_config(config_id: UUID, request: Request) -> dict:
    test_id = uuid4()
    started_at = now()
    async with authorized_admin(request, write=True) as identity:
        config = await identity.session.scalar(
            select(AIConfigVersion)
            .where(AIConfigVersion.id == config_id)
            .with_for_update()
        )
        if config is None:
            raise config_not_found()
        if await fallback_config_in_use(identity.session, config.id):
            raise AppError(
                "ai_config_fallback_in_use",
                "此配置正在备用路由中，请先从调用顺序移除。",
                status=409,
            )
        if config.status in {"published", "retired"}:
            raise AppError(
                "ai_config_test_not_allowed",
                "已发布或已退役版本不可重新测试，请创建新版本。",
                status=409,
            )
        config.status = "draft"
        config.last_test_id = test_id
        config.last_test_started_at = started_at
        config.last_test_completed_at = None
        config.last_test_error_code = None
        config.last_test_error_stage = None
        config.last_tested_by_user_id = identity.user.id
        config.updated_at = started_at
        snapshot = ProviderConfig(
            endpoint_url=config.endpoint_url,
            model=config.model,
            api_key_ciphertext=config.api_key_ciphertext,
            timeout_seconds=config.timeout_seconds,
            max_output_tokens=config.max_output_tokens,
        )
        audit(
            request,
            identity.session,
            identity.user,
            "ai_config_test_started",
            config.id,
        )

    result = await test_openai_compatible(
        snapshot,
        request.app.state.settings,
        request.app.state.security.ai_cipher,
        capability=config.capability,
    )
    completed_at = now()
    async with authorized_admin(request, write=True) as identity:
        config = await identity.session.scalar(
            select(AIConfigVersion)
            .where(AIConfigVersion.id == config_id)
            .with_for_update()
        )
        if config is None:
            raise config_not_found()
        if config.last_test_id != test_id:
            raise AppError(
                "ai_config_test_superseded",
                "此测试已被较新的测试替代，请刷新配置状态。",
                status=409,
            )
        config.last_test_completed_at = completed_at
        config.updated_at = completed_at
        if result.succeeded:
            config.status = "tested"
            config.last_test_error_code = None
            config.last_test_error_stage = None
            action = "ai_config_test_succeeded"
        else:
            config.status = "draft"
            config.last_test_error_code = result.error_code
            config.last_test_error_stage = result.error_stage
            action = "ai_config_test_failed"
        audit(request, identity.session, identity.user, action, config.id)
        response = config_result(config)

    if not result.succeeded:
        raise AppError(
            result.error_code or "ai_config_test_failed",
            result.message or "AI 配置测试失败。",
            status=result.status,
            retryable=result.retryable,
            saved="saved",
            details={"stage": result.error_stage},
        )
    return response


@router.post("/{config_id}/publish", response_model=AIConfigVersionOutput)
async def publish_config(config_id: UUID, request: Request) -> dict:
    current = now()
    async with authorized_admin(request, write=True) as identity:
        quota_state = await identity.session.scalar(
            select(AIQuotaPolicyState)
            .where(AIQuotaPolicyState.id == 1)
            .with_for_update(read=True)
        )
        quota_policy = (
            await identity.session.get(
                AIQuotaPolicyVersion,
                quota_state.current_version_id,
            )
            if quota_state is not None
            and quota_state.current_version_id is not None
            else None
        )
        capability = await identity.session.scalar(
            select(AIConfigVersion.capability).where(AIConfigVersion.id == config_id)
        )
        if capability is None:
            raise config_not_found()
        state = await locked_capability(identity.session, capability)
        config = await identity.session.scalar(
            select(AIConfigVersion)
            .where(AIConfigVersion.id == config_id)
            .with_for_update()
        )
        if config is None:
            raise config_not_found()
        if quota_policy is not None and (
            config.price_currency != quota_policy.currency
            or config.input_price_nanos_per_million_tokens is None
            or config.cached_input_price_nanos_per_million_tokens is None
            or config.output_price_nanos_per_million_tokens is None
        ):
            raise AppError(
                "ai_quota_pricing_unavailable",
                "AI 配置价格必须与当前每日额度币种一致。",
                status=409,
                details={"currency": quota_policy.currency},
            )
        if state.current_version_id == config.id and config.status == "published":
            return config_result(config)
        target_was_retired = config.status == "retired"
        if (
            config.status not in {"tested", "retired"}
            or config.last_test_completed_at is None
            or config.last_test_error_code is not None
        ):
            raise AppError(
                "ai_config_not_tested",
                "只有最近一次合成测试成功的版本可以发布。",
                status=409,
            )
        await remove_fallback_config(
            identity.session,
            state,
            config.id,
        )
        if state.current_version_id is not None:
            previous = await identity.session.scalar(
                select(AIConfigVersion)
                .where(AIConfigVersion.id == state.current_version_id)
                .with_for_update()
            )
            if previous is None or previous.status != "published":
                raise AppError(
                    "ai_config_state_invalid",
                    "当前发布配置状态不一致，请保留请求编号并检查数据。",
                    status=503,
                )
            previous.status = "retired"
            previous.retired_at = current
            previous.updated_at = current
            await identity.session.flush()
        config.status = "published"
        config.published_at = current
        config.retired_at = None
        config.published_by_user_id = identity.user.id
        config.updated_at = current
        state.current_version_id = config.id
        state.routing_version += 1
        state.updated_at = current
        audit(
            request,
            identity.session,
            identity.user,
            (
                "ai_config_version_rolled_back"
                if target_was_retired
                else "ai_config_version_published"
            ),
            config.id,
        )
        return config_result(config)
