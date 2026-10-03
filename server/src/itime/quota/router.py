from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request
from sqlalchemy import and_, or_, select

from itime.ai.models import AIJob
from itime.ai.pricing import format_currency_nanos
from itime.auth.security import now
from itime.auth.session import authorized
from itime.calendar.models import UserTimeSettings
from itime.errors import AppError
from itime.quota.models import (
    AIDailyAllowance,
    AIQuotaLedgerEntry,
    AIQuotaPolicyState,
    AIQuotaPolicyVersion,
    AIQuotaReservation,
)
from itime.quota.service import activity_window, quota_snapshot
from itime.tasks.service import validate_cursor

router = APIRouter(prefix="/api/v1/quota", tags=["AI 额度"])
LedgerKind = Literal["usage_charge", "admin_credit", "admin_debit"]


def ledger_result(
    entry: AIQuotaLedgerEntry,
    daily: AIDailyAllowance,
    job: AIJob | None,
) -> dict:
    return {
        "id": entry.id,
        "activity_date": daily.activity_date,
        "kind": entry.kind,
        "delta": format_currency_nanos(entry.delta_nanos),
        "currency": entry.currency,
        "usage_source": entry.usage_source,
        "job": (
            {
                "id": job.id,
                "capability": job.capability,
            }
            if job is not None
            else None
        ),
        "created_at": entry.created_at,
    }


@router.get("")
async def get_quota(request: Request) -> dict:
    current = now()
    async with authorized(request) as identity:
        settings = await identity.session.get(
            UserTimeSettings,
            identity.user.id,
        )
        if settings is None:
            raise AppError(
                "time_settings_required",
                "请先保存当前使用的时区，再查看 AI 额度。",
                status=409,
            )
        daily = await identity.session.scalar(
            select(AIDailyAllowance)
            .where(
                AIDailyAllowance.owner_id == identity.user.id,
                AIDailyAllowance.window_start_at <= current,
                AIDailyAllowance.window_end_at > current,
            )
            .order_by(
                AIDailyAllowance.window_end_at.desc(),
                AIDailyAllowance.id.desc(),
            )
            .limit(1)
        )
        if daily is None:
            state = await identity.session.get(AIQuotaPolicyState, 1)
            policy = (
                await identity.session.get(
                    AIQuotaPolicyVersion,
                    state.current_version_id,
                )
                if state is not None and state.current_version_id is not None
                else None
            )
            if policy is None or policy.status != "published":
                return {
                    "configured": False,
                    "current": None,
                    "reservations": [],
                }
            window = activity_window(
                current,
                settings.time_zone,
                settings.activity_day_start_minute,
            )
            return {
                "configured": True,
                "current": {
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
                    "available": format_currency_nanos(
                        policy.daily_allowance_nanos
                    ),
                    "version": 0,
                },
                "reservations": [],
            }

        snapshot = await quota_snapshot(identity.session, daily)
        policy_version = await identity.session.scalar(
            select(AIQuotaPolicyVersion.version).where(
                AIQuotaPolicyVersion.id == daily.policy_version_id
            )
        )
        reservation_rows = list(
            (
                await identity.session.execute(
                    select(AIQuotaReservation, AIJob)
                    .join(AIJob, AIJob.id == AIQuotaReservation.job_id)
                    .where(
                        AIQuotaReservation.owner_id == identity.user.id,
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
            "configured": True,
            "current": {
                "id": daily.id,
                "activity_date": daily.activity_date,
                "time_zone": daily.time_zone,
                "activity_day_start_minute": daily.activity_day_start_minute,
                "window_start_at": daily.window_start_at,
                "window_end_at": daily.window_end_at,
                "policy_version": policy_version,
                "currency": daily.currency,
                "base": format_currency_nanos(snapshot.base_amount_nanos),
                "adjustment": format_currency_nanos(
                    snapshot.adjustment_nanos
                ),
                "charged": format_currency_nanos(snapshot.charged_nanos),
                "reserved": format_currency_nanos(snapshot.reserved_nanos),
                "available": format_currency_nanos(snapshot.available_nanos),
                "version": daily.version,
            },
            "reservations": [
                {
                    "id": reservation.id,
                    "job_id": reservation.job_id,
                    "capability": job.capability,
                    "reserved": format_currency_nanos(
                        reservation.reserved_nanos
                    ),
                    "accepted_at": reservation.accepted_at,
                }
                for reservation, job in reservation_rows
            ],
        }


@router.get("/ledger")
async def list_quota_ledger(
    request: Request,
    activity_date: date | None = None,
    kind: LedgerKind | None = None,
    before_time: datetime | None = None,
    before_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> dict:
    validate_cursor(before_time, before_id)
    async with authorized(request) as identity:
        statement = (
            select(AIQuotaLedgerEntry, AIDailyAllowance, AIJob)
            .join(
                AIDailyAllowance,
                AIDailyAllowance.id == AIQuotaLedgerEntry.daily_allowance_id,
            )
            .outerjoin(AIJob, AIJob.id == AIQuotaLedgerEntry.job_id)
            .where(AIQuotaLedgerEntry.owner_id == identity.user.id)
        )
        if activity_date is not None:
            statement = statement.where(
                AIDailyAllowance.activity_date == activity_date
            )
        if kind is not None:
            statement = statement.where(AIQuotaLedgerEntry.kind == kind)
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
            "items": [
                ledger_result(entry, daily, job)
                for entry, daily, job in page
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
