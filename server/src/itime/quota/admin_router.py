from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request
from sqlalchemy import and_, or_, select

from itime.admin.security import authorized_admin
from itime.ai.models import AIConfigCapability, AIConfigVersion
from itime.ai.pricing import format_currency_nanos, parse_currency_amount
from itime.auth.security import now
from itime.auth.session import audit
from itime.errors import AppError
from itime.quota.models import AIQuotaPolicyState, AIQuotaPolicyVersion
from itime.quota.schemas import (
    QuotaPolicyCreateInput,
    QuotaPolicyListOutput,
    QuotaPolicyOutput,
    QuotaPolicyPublishInput,
)
from itime.quota.service import RESERVATION_ESTIMATOR_VERSION
from itime.tasks.service import (
    replay_operation,
    save_operation,
    validate_cursor,
)

router = APIRouter(prefix="/api/v1/admin/quota", tags=["后台 AI 额度"])
REQUIRED_AI_CAPABILITIES = ("calendar_screenshot", "conversation")


def policy_result(
    policy: AIQuotaPolicyVersion,
    *,
    current_version_id: UUID | None,
) -> dict:
    return {
        "id": policy.id,
        "version": policy.version,
        "currency": policy.currency,
        "daily_allowance": format_currency_nanos(policy.daily_allowance_nanos),
        "reservation_estimator_version": policy.reservation_estimator_version,
        "status": policy.status,
        "current": policy.id == current_version_id and policy.status == "published",
        "created_by_user_id": policy.created_by_user_id,
        "published_by_user_id": policy.published_by_user_id,
        "created_at": policy.created_at,
        "updated_at": policy.updated_at,
        "published_at": policy.published_at,
        "retired_at": policy.retired_at,
    }


async def locked_policy_state(session) -> AIQuotaPolicyState:
    state = await session.scalar(
        select(AIQuotaPolicyState)
        .where(AIQuotaPolicyState.id == 1)
        .with_for_update()
    )
    if state is None:
        raise AppError(
            "ai_quota_state_unavailable",
            "额度策略状态暂时不可用，请检查数据库迁移。",
            status=503,
            retryable=True,
        )
    return state


def policy_not_found() -> AppError:
    return AppError("ai_quota_policy_not_found", "未找到此额度策略版本。", status=404)


async def validate_pricing_currency(session, currency: str) -> None:
    invalid_capabilities: list[str] = []
    for capability in REQUIRED_AI_CAPABILITIES:
        state = await session.scalar(
            select(AIConfigCapability)
            .where(AIConfigCapability.capability == capability)
            .with_for_update(read=True)
        )
        config = (
            await session.get(AIConfigVersion, state.current_version_id)
            if state is not None and state.current_version_id is not None
            else None
        )
        if (
            config is None
            or config.status != "published"
            or config.price_currency != currency
            or config.input_price_nanos_per_million_tokens is None
            or config.cached_input_price_nanos_per_million_tokens is None
            or config.output_price_nanos_per_million_tokens is None
        ):
            invalid_capabilities.append(capability)
    if invalid_capabilities:
        raise AppError(
            "ai_quota_pricing_unavailable",
            "发布额度策略前，请先发布币种一致且价格完整的 AI 配置。",
            status=409,
            details={"capabilities": invalid_capabilities, "currency": currency},
        )


@router.get("/policies", response_model=QuotaPolicyListOutput)
async def list_policies(
    request: Request,
    before_time: datetime | None = None,
    before_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> dict:
    validate_cursor(before_time, before_id)
    async with authorized_admin(request) as identity:
        state = await identity.session.get(AIQuotaPolicyState, 1)
        statement = select(AIQuotaPolicyVersion)
        if before_time is not None:
            statement = statement.where(
                or_(
                    AIQuotaPolicyVersion.created_at < before_time,
                    and_(
                        AIQuotaPolicyVersion.created_at == before_time,
                        AIQuotaPolicyVersion.id < before_id,
                    ),
                )
            )
        rows = list(
            await identity.session.scalars(
                statement.order_by(
                    AIQuotaPolicyVersion.created_at.desc(),
                    AIQuotaPolicyVersion.id.desc(),
                ).limit(limit + 1)
            )
        )
        page = rows[:limit]
        current_id = state.current_version_id if state is not None else None
        return {
            "items": [
                policy_result(policy, current_version_id=current_id)
                for policy in page
            ],
            "next_cursor": (
                {
                    "before_time": page[-1].created_at,
                    "before_id": page[-1].id,
                }
                if len(rows) > limit
                else None
            ),
        }


@router.post("/policies", status_code=201, response_model=QuotaPolicyOutput)
async def create_policy(data: QuotaPolicyCreateInput, request: Request) -> dict:
    scope = "admin_quota_policy:create"
    async with authorized_admin(request, write=True) as identity:
        replay = await replay_operation(
            identity.session,
            identity.user.id,
            data.operation_id,
            scope,
            data,
        )
        if replay is not None:
            return replay
        state = await locked_policy_state(identity.session)
        existing_currency = await identity.session.scalar(
            select(AIQuotaPolicyVersion.currency)
            .order_by(AIQuotaPolicyVersion.version)
            .limit(1)
        )
        if existing_currency is not None and existing_currency != data.currency:
            raise AppError(
                "ai_quota_currency_locked",
                "额度币种已经确定；如需换币种必须先完成数据迁移。",
                status=409,
                details={"currency": existing_currency},
            )
        current = now()
        policy = AIQuotaPolicyVersion(
            version=state.next_version,
            currency=data.currency,
            daily_allowance_nanos=parse_currency_amount(data.daily_allowance),
            reservation_estimator_version=RESERVATION_ESTIMATOR_VERSION,
            created_by_user_id=identity.user.id,
            created_at=current,
            updated_at=current,
        )
        state.next_version += 1
        state.updated_at = current
        identity.session.add(policy)
        await identity.session.flush()
        result = policy_result(
            policy,
            current_version_id=state.current_version_id,
        )
        save_operation(
            identity.session,
            identity.user.id,
            data.operation_id,
            scope,
            data,
            result,
        )
        audit(
            request,
            identity.session,
            identity.user,
            "ai_quota_policy_created",
            policy.id,
        )
        return result


@router.post(
    "/policies/{policy_id}/publish",
    response_model=QuotaPolicyOutput,
)
async def publish_policy(
    policy_id: UUID,
    data: QuotaPolicyPublishInput,
    request: Request,
) -> dict:
    scope = f"admin_quota_policy:publish:{policy_id}"
    async with authorized_admin(request, write=True) as identity:
        replay = await replay_operation(
            identity.session,
            identity.user.id,
            data.operation_id,
            scope,
            data,
        )
        if replay is not None:
            return replay
        state = await locked_policy_state(identity.session)
        policy = await identity.session.scalar(
            select(AIQuotaPolicyVersion)
            .where(AIQuotaPolicyVersion.id == policy_id)
            .with_for_update()
        )
        if policy is None:
            raise policy_not_found()
        await validate_pricing_currency(identity.session, policy.currency)
        current = now()
        target_was_retired = policy.status == "retired"
        if state.current_version_id != policy.id or policy.status != "published":
            if policy.status not in {"draft", "retired"}:
                raise AppError(
                    "ai_quota_policy_state_invalid",
                    "额度策略状态不允许发布，请刷新后重试。",
                    status=409,
                )
            if state.current_version_id is not None:
                previous = await identity.session.scalar(
                    select(AIQuotaPolicyVersion)
                    .where(AIQuotaPolicyVersion.id == state.current_version_id)
                    .with_for_update()
                )
                if previous is None or previous.status != "published":
                    raise AppError(
                        "ai_quota_state_unavailable",
                        "额度策略指针状态不一致，请保留请求编号并检查数据。",
                        status=503,
                        saved="unknown",
                    )
                previous.status = "retired"
                previous.retired_at = current
                previous.updated_at = current
                await identity.session.flush()
            policy.status = "published"
            policy.published_at = policy.published_at or current
            policy.retired_at = None
            policy.published_by_user_id = identity.user.id
            policy.updated_at = current
            state.current_version_id = policy.id
            state.updated_at = current
        result = policy_result(policy, current_version_id=policy.id)
        save_operation(
            identity.session,
            identity.user.id,
            data.operation_id,
            scope,
            data,
            result,
        )
        audit(
            request,
            identity.session,
            identity.user,
            (
                "ai_quota_policy_rolled_back"
                if target_was_retired
                else "ai_quota_policy_published"
            ),
            policy.id,
        )
        return result
