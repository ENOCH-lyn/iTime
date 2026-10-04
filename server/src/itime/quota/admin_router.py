from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Query, Request
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import aliased

from itime.admin.security import authorized_admin
from itime.ai.models import AIConfigCapability, AIConfigVersion, AIJob
from itime.ai.pricing import format_currency_nanos, parse_currency_amount
from itime.ai.routing_service import pricing_complete
from itime.auth.models import User
from itime.auth.security import now
from itime.auth.session import audit
from itime.calendar.models import UserTimeSettings
from itime.errors import AppError
from itime.quota.models import (
    AIDailyAllowance,
    AIQuotaAdminAdjustment,
    AIQuotaLedgerEntry,
    AIQuotaPolicyState,
    AIQuotaPolicyVersion,
    AIQuotaReservation,
)
from itime.quota.schemas import (
    QuotaAdjustmentInput,
    QuotaPolicyCreateInput,
    QuotaPolicyListOutput,
    QuotaPolicyOutput,
    QuotaPolicyPublishInput,
)
from itime.quota.service import (
    RESERVATION_ESTIMATOR_VERSION,
    activity_window,
    get_or_create_daily_allowance,
    quota_snapshot,
)
from itime.tasks.service import (
    replay_operation,
    request_hash,
    save_operation,
    validate_cursor,
)

router = APIRouter(prefix="/api/v1/admin/quota", tags=["后台 AI 额度"])
REQUIRED_AI_CAPABILITIES = (
    "calendar_screenshot",
    "conversation",
    "planning",
    "review",
    "transcription",
)
BASELINE_AI_CAPABILITIES = {"calendar_screenshot", "conversation"}
LedgerKind = Literal["usage_charge", "admin_credit", "admin_debit"]


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
        select(AIQuotaPolicyState).where(AIQuotaPolicyState.id == 1).with_for_update()
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


def quota_user_result(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "status": user.status,
        "ai_access": user.ai_access,
    }


def quota_daily_result(
    daily: AIDailyAllowance,
    *,
    policy_version: int,
    snapshot,
) -> dict:
    return {
        "id": daily.id,
        "activity_date": daily.activity_date,
        "time_zone": daily.time_zone,
        "activity_day_start_minute": daily.activity_day_start_minute,
        "window_start_at": daily.window_start_at,
        "window_end_at": daily.window_end_at,
        "policy_version": policy_version,
        "currency": daily.currency,
        "base": format_currency_nanos(snapshot.base_amount_nanos),
        "adjustment": format_currency_nanos(snapshot.adjustment_nanos),
        "charged": format_currency_nanos(snapshot.charged_nanos),
        "reserved": format_currency_nanos(snapshot.reserved_nanos),
        "available": format_currency_nanos(snapshot.available_nanos),
        "version": daily.version,
    }


def quota_daily_preview(
    policy: AIQuotaPolicyVersion,
    settings: UserTimeSettings,
    current: datetime,
) -> dict:
    window = activity_window(
        current,
        settings.time_zone,
        settings.activity_day_start_minute,
    )
    return {
        "id": None,
        "activity_date": window.activity_date,
        "time_zone": window.time_zone,
        "activity_day_start_minute": window.start_minute,
        "window_start_at": window.window_start_at,
        "window_end_at": window.window_end_at,
        "policy_version": policy.version,
        "currency": policy.currency,
        "base": format_currency_nanos(policy.daily_allowance_nanos),
        "adjustment": "0",
        "charged": "0",
        "reserved": "0",
        "available": format_currency_nanos(policy.daily_allowance_nanos),
        "version": 0,
    }


async def current_policy(session) -> AIQuotaPolicyVersion | None:
    state = await session.get(AIQuotaPolicyState, 1)
    policy = (
        await session.get(AIQuotaPolicyVersion, state.current_version_id)
        if state is not None and state.current_version_id is not None
        else None
    )
    return policy if policy is not None and policy.status == "published" else None


async def covering_daily(
    session,
    owner_id: UUID,
    current: datetime,
    *,
    lock: bool = False,
) -> AIDailyAllowance | None:
    statement = (
        select(AIDailyAllowance)
        .where(
            AIDailyAllowance.owner_id == owner_id,
            AIDailyAllowance.window_start_at <= current,
            AIDailyAllowance.window_end_at > current,
        )
        .order_by(
            AIDailyAllowance.window_end_at.desc(),
            AIDailyAllowance.id.desc(),
        )
        .limit(1)
    )
    if lock:
        statement = statement.with_for_update()
    return await session.scalar(statement)


async def daily_for_activity_date(
    session,
    owner_id: UUID,
    activity_date: date,
    current: datetime,
    *,
    lock: bool = False,
) -> AIDailyAllowance | None:
    statement = (
        select(AIDailyAllowance)
        .where(
            AIDailyAllowance.owner_id == owner_id,
            AIDailyAllowance.activity_date == activity_date,
        )
        .order_by(
            AIDailyAllowance.window_end_at.desc(),
            AIDailyAllowance.id.desc(),
        )
        .limit(3)
    )
    if lock:
        statement = statement.with_for_update()
    rows = list(await session.scalars(statement))
    if len(rows) <= 1:
        return rows[0] if rows else None
    covering = [daily for daily in rows if daily.window_start_at <= current < daily.window_end_at]
    if len(covering) == 1:
        return covering[0]
    raise AppError(
        "ai_quota_daily_ambiguous",
        "此活动日存在多个冻结时区记录，请按账本记录核对后再调整。",
        status=409,
        details={"activity_date": activity_date.isoformat()},
    )


async def quota_user_summary(
    session,
    user: User,
    *,
    requested_date: date | None,
    current: datetime,
) -> dict:
    daily = (
        await daily_for_activity_date(
            session,
            user.id,
            requested_date,
            current,
        )
        if requested_date is not None
        else await covering_daily(session, user.id, current)
    )
    if daily is None:
        settings = await session.get(UserTimeSettings, user.id)
        if settings is None:
            raise AppError(
                "time_settings_required",
                "此用户尚未保存时区，不能确定额度活动日。",
                status=409,
            )
        policy = await current_policy(session)
        preview = quota_daily_preview(policy, settings, current) if policy is not None else None
        if requested_date is not None and (
            preview is None or preview["activity_date"] != requested_date
        ):
            preview = None
        return {
            "user": quota_user_result(user),
            "configured": policy is not None,
            "current": preview,
            "reservations": [],
        }

    snapshot = await quota_snapshot(session, daily)
    policy_version = await session.scalar(
        select(AIQuotaPolicyVersion.version).where(
            AIQuotaPolicyVersion.id == daily.policy_version_id
        )
    )
    reservation_rows = list(
        (
            await session.execute(
                select(AIQuotaReservation, AIJob)
                .join(AIJob, AIJob.id == AIQuotaReservation.job_id)
                .where(
                    AIQuotaReservation.owner_id == user.id,
                    AIQuotaReservation.daily_allowance_id == daily.id,
                    AIQuotaReservation.status == "reserved",
                )
                .order_by(
                    AIQuotaReservation.accepted_at.desc(),
                    AIQuotaReservation.id.desc(),
                )
            )
        ).all()
    )
    return {
        "user": quota_user_result(user),
        "configured": True,
        "current": quota_daily_result(
            daily,
            policy_version=policy_version,
            snapshot=snapshot,
        ),
        "reservations": [
            {
                "id": reservation.id,
                "job_id": reservation.job_id,
                "capability": job.capability,
                "reserved": format_currency_nanos(reservation.reserved_nanos),
                "accepted_at": reservation.accepted_at,
            }
            for reservation, job in reservation_rows
        ],
    }


def adjustment_result(
    adjustment: AIQuotaAdminAdjustment,
    *,
    daily: AIDailyAllowance,
) -> dict:
    return {
        "id": adjustment.id,
        "operation_id": adjustment.operation_id,
        "target_user_id": adjustment.target_owner_id,
        "daily_allowance_id": adjustment.daily_allowance_id,
        "activity_date": daily.activity_date,
        "mode": "credit" if adjustment.delta_nanos > 0 else "debit",
        "amount": format_currency_nanos(abs(adjustment.delta_nanos)),
        "delta": format_currency_nanos(adjustment.delta_nanos),
        "currency": daily.currency,
        "reason": adjustment.reason,
        "actor_admin_id": adjustment.actor_admin_id,
        "request_id": adjustment.request_id,
        "created_at": adjustment.created_at,
    }


async def stored_adjustment_result(
    session,
    adjustment: AIQuotaAdminAdjustment,
) -> dict:
    daily = await session.get(
        AIDailyAllowance,
        adjustment.daily_allowance_id,
    )
    if daily is None:
        raise AppError(
            "ai_quota_state_invalid",
            "额度调整缺少对应活动日记录，请保留请求编号并检查数据。",
            status=503,
            saved="unknown",
        )
    return {"adjustment": adjustment_result(adjustment, daily=daily)}


def admin_ledger_result(
    entry: AIQuotaLedgerEntry,
    daily: AIDailyAllowance,
    job: AIJob | None,
    adjustment: AIQuotaAdminAdjustment | None,
    owner_id: UUID,
    owner_email: str,
    actor_id: UUID | None,
    actor_email: str | None,
) -> dict:
    return {
        "id": entry.id,
        "user": {"id": owner_id, "email": owner_email},
        "activity_date": daily.activity_date,
        "kind": entry.kind,
        "delta": format_currency_nanos(entry.delta_nanos),
        "currency": entry.currency,
        "usage_source": entry.usage_source,
        "job": ({"id": job.id, "capability": job.capability} if job is not None else None),
        "adjustment": (
            {
                "id": adjustment.id,
                "operation_id": adjustment.operation_id,
                "mode": "credit" if adjustment.delta_nanos > 0 else "debit",
                "amount": format_currency_nanos(abs(adjustment.delta_nanos)),
                "reason": adjustment.reason,
                "actor": (
                    {"id": actor_id, "email": actor_email}
                    if actor_id is not None and actor_email is not None
                    else None
                ),
                "request_id": adjustment.request_id,
            }
            if adjustment is not None
            else None
        ),
        "created_at": entry.created_at,
    }


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
        if capability not in BASELINE_AI_CAPABILITIES and config is None:
            # A quota policy may predate a later capability rollout. Once the
            # capability is published, every later policy must price it.
            continue
        if (
            config is None
            or config.status != "published"
            or config.price_currency != currency
            or not pricing_complete(config)
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
            "items": [policy_result(policy, current_version_id=current_id) for policy in page],
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
            select(AIQuotaPolicyVersion.currency).order_by(AIQuotaPolicyVersion.version).limit(1)
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


@router.get("/users/{user_id}")
async def get_user_quota(
    user_id: UUID,
    request: Request,
    activity_date: date | None = None,
) -> dict:
    current = now()
    async with authorized_admin(request) as identity:
        user = await identity.session.get(User, user_id)
        if user is None:
            raise AppError("user_not_found", "未找到此用户。", status=404)
        return await quota_user_summary(
            identity.session,
            user,
            requested_date=activity_date,
            current=current,
        )


@router.post(
    "/users/{user_id}/adjustments",
    status_code=201,
)
async def create_adjustment(
    user_id: UUID,
    data: QuotaAdjustmentInput,
    request: Request,
) -> dict:
    scope = f"admin_quota_adjustment:{user_id}"
    current = now()
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
        request_digest = request_hash(scope, data)
        await identity.session.execute(
            select(
                func.pg_advisory_xact_lock(
                    func.hashtextextended(str(data.operation_id), 0)
                )
            )
        )
        existing_adjustment = await identity.session.scalar(
            select(AIQuotaAdminAdjustment).where(
                AIQuotaAdminAdjustment.operation_id == data.operation_id
            )
        )
        if existing_adjustment is not None:
            if existing_adjustment.request_hash != request_digest:
                raise AppError(
                    "operation_id_reused",
                    "此操作编号已经用于不同的内容，请刷新状态后重新提交。",
                    status=409,
                )
            return await stored_adjustment_result(
                identity.session,
                existing_adjustment,
            )

        user = await identity.session.scalar(
            select(User).where(User.id == user_id).with_for_update()
        )
        if user is None:
            raise AppError("user_not_found", "未找到此用户。", status=404)
        if user.is_admin:
            raise AppError(
                "admin_account_protected",
                "管理员账号不参与用户 AI 额度调整。",
                status=409,
            )
        daily = await daily_for_activity_date(
            identity.session,
            user.id,
            data.activity_date,
            current,
            lock=True,
        )
        if daily is None:
            if data.expected_daily_version != 0:
                raise AppError(
                    "ai_quota_daily_version_conflict",
                    "此活动日额度已变化，请刷新后重试。",
                    status=409,
                    details={"current_version": 0},
                )
            settings = await identity.session.scalar(
                select(UserTimeSettings)
                .where(UserTimeSettings.owner_id == user.id)
                .with_for_update(read=True)
            )
            if settings is None:
                raise AppError(
                    "time_settings_required",
                    "此用户尚未保存时区，不能确定额度活动日。",
                    status=409,
                )
            expected_window = activity_window(
                current,
                settings.time_zone,
                settings.activity_day_start_minute,
            )
            covering = await covering_daily(
                identity.session,
                user.id,
                current,
                lock=True,
            )
            if expected_window.activity_date != data.activity_date or (
                covering is not None and covering.activity_date != data.activity_date
            ):
                raise AppError(
                    "ai_quota_daily_not_found",
                    "此活动日尚无额度记录，不能创建历史或未来调整。",
                    status=404,
                    details={"activity_date": data.activity_date.isoformat()},
                )
            daily = await get_or_create_daily_allowance(
                identity.session,
                user.id,
                accepted_at=current,
            )
            if daily.activity_date != data.activity_date:
                raise AppError(
                    "ai_quota_daily_not_found",
                    "当前冻结活动日已变化，请刷新后重试。",
                    status=409,
                    details={"activity_date": daily.activity_date.isoformat()},
                )
        elif daily.version != data.expected_daily_version:
            raise AppError(
                "ai_quota_daily_version_conflict",
                "此活动日额度已变化，请刷新后重试。",
                status=409,
                details={"current_version": daily.version},
            )

        snapshot = await quota_snapshot(identity.session, daily)
        amount_nanos = parse_currency_amount(data.amount)
        if data.mode == "debit" and amount_nanos > snapshot.available_nanos:
            raise AppError(
                "ai_quota_adjustment_insufficient",
                "扣减超过当前可用额度，请刷新后重新输入。",
                status=409,
                details={
                    "available": format_currency_nanos(snapshot.available_nanos),
                    "reserved": format_currency_nanos(snapshot.reserved_nanos),
                    "charged": format_currency_nanos(snapshot.charged_nanos),
                    "currency": daily.currency,
                    "current_version": daily.version,
                },
            )
        delta_nanos = amount_nanos if data.mode == "credit" else -amount_nanos
        adjustment = AIQuotaAdminAdjustment(
            id=uuid4(),
            operation_id=data.operation_id,
            request_hash=request_digest,
            target_owner_id=user.id,
            daily_allowance_id=daily.id,
            actor_admin_id=identity.user.id,
            delta_nanos=delta_nanos,
            reason=data.reason,
            request_id=request.state.request_id,
            created_at=current,
        )
        daily.version += 1
        daily.updated_at = current
        identity.session.add(adjustment)
        await identity.session.flush()
        ledger = AIQuotaLedgerEntry(
            id=uuid4(),
            owner_id=user.id,
            daily_allowance_id=daily.id,
            reservation_id=None,
            adjustment_id=adjustment.id,
            job_id=None,
            provider_cost_id=None,
            kind=f"admin_{data.mode}",
            delta_nanos=delta_nanos,
            currency=daily.currency,
            usage_source=None,
            created_at=current,
        )
        identity.session.add(ledger)
        await identity.session.flush()
        result = {"adjustment": adjustment_result(adjustment, daily=daily)}
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
            f"ai_quota_admin_{data.mode}",
            user.id,
            details={
                "adjustment_id": str(adjustment.id),
                "daily_allowance_id": str(daily.id),
                "activity_date": daily.activity_date.isoformat(),
                "delta": format_currency_nanos(delta_nanos),
                "currency": daily.currency,
                "daily_version": daily.version,
            },
        )
        return result


@router.get("/ledger")
async def list_admin_quota_ledger(
    request: Request,
    user_id: UUID | None = None,
    activity_date: date | None = None,
    kind: LedgerKind | None = None,
    currency: Annotated[
        str | None,
        Query(min_length=3, max_length=3, pattern=r"^[A-Za-z]{3}$"),
    ] = None,
    operation_id: UUID | None = None,
    before_time: datetime | None = None,
    before_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> dict:
    validate_cursor(before_time, before_id)
    owner = aliased(User)
    actor = aliased(User)
    async with authorized_admin(request) as identity:
        statement = (
            select(
                AIQuotaLedgerEntry,
                AIDailyAllowance,
                AIJob,
                AIQuotaAdminAdjustment,
                owner.id,
                owner.email,
                actor.id,
                actor.email,
            )
            .join(
                AIDailyAllowance,
                AIDailyAllowance.id == AIQuotaLedgerEntry.daily_allowance_id,
            )
            .join(owner, owner.id == AIQuotaLedgerEntry.owner_id)
            .outerjoin(AIJob, AIJob.id == AIQuotaLedgerEntry.job_id)
            .outerjoin(
                AIQuotaAdminAdjustment,
                AIQuotaAdminAdjustment.id == AIQuotaLedgerEntry.adjustment_id,
            )
            .outerjoin(
                actor,
                actor.id == AIQuotaAdminAdjustment.actor_admin_id,
            )
        )
        if user_id is not None:
            statement = statement.where(AIQuotaLedgerEntry.owner_id == user_id)
        if activity_date is not None:
            statement = statement.where(AIDailyAllowance.activity_date == activity_date)
        if kind is not None:
            statement = statement.where(AIQuotaLedgerEntry.kind == kind)
        if currency is not None:
            statement = statement.where(AIQuotaLedgerEntry.currency == currency.upper())
        if operation_id is not None:
            statement = statement.where(AIQuotaAdminAdjustment.operation_id == operation_id)
        if before_time is not None:
            statement = statement.where(
                or_(
                    AIQuotaLedgerEntry.created_at < before_time,
                    and_(
                        AIQuotaLedgerEntry.created_at == before_time,
                        AIQuotaLedgerEntry.id < before_id,
                    ),
                )
            )
        rows = list(
            (
                await identity.session.execute(
                    statement.order_by(
                        AIQuotaLedgerEntry.created_at.desc(),
                        AIQuotaLedgerEntry.id.desc(),
                    ).limit(limit + 1)
                )
            ).all()
        )
        page = rows[:limit]
        return {
            "items": [admin_ledger_result(*row) for row in page],
            "next_cursor": (
                {
                    "before_time": page[-1][0].created_at,
                    "before_id": page[-1][0].id,
                }
                if len(rows) > limit
                else None
            ),
        }


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
            ("ai_quota_policy_rolled_back" if target_was_retired else "ai_quota_policy_published"),
            policy.id,
        )
        return result
