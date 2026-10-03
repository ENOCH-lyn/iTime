from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request
from sqlalchemy import and_, or_, select

from itime.admin.security import authorized_admin
from itime.ai.governance_models import AIBodyLog, AIQualitySample
from itime.ai.governance_schemas import (
    BodyLogAccessInput,
    QualitySampleAccessInput,
    QualitySampleReviewInput,
)
from itime.ai.schemas import Capability
from itime.auth.models import SecurityEvent, User
from itime.auth.security import now
from itime.auth.session import audit
from itime.errors import AppError
from itime.tasks.service import validate_cursor

router = APIRouter(prefix="/api/v1/admin", tags=["后台 AI 治理"])

BodyLogStatus = Literal[
    "running",
    "succeeded",
    "retryable_failed",
    "failed",
    "timed_out",
    "cancelled",
    "abandoned",
]
QualityStatus = Literal["pending", "reviewed"]
QualityOutcome = Literal["acceptable", "needs_improvement"]


def body_log_metadata(body_log: AIBodyLog, owner_email: str) -> dict:
    return {
        "id": body_log.id,
        "job_id": body_log.job_id,
        "owner": {"id": body_log.owner_id, "email": owner_email},
        "attempt_number": body_log.attempt_number,
        "capability": body_log.capability,
        "status": body_log.status,
        "request_prepared": body_log.request_prepared_at is not None,
        "error_code": body_log.error_code,
        "error_stage": body_log.error_stage,
        "provider_request_id": body_log.provider_request_id,
        "http_status": body_log.http_status,
        "started_at": body_log.started_at,
        "finished_at": body_log.finished_at,
        "expires_at": body_log.expires_at,
    }


def quality_sample_metadata(
    sample: AIQualitySample,
    owner_email: str,
    *,
    include_review_notes: bool = False,
) -> dict:
    result = {
        "id": sample.id,
        "source_job_id": sample.source_job_id,
        "source_body_log_id": sample.source_body_log_id,
        "owner": {"id": sample.owner_id, "email": owner_email},
        "capability": sample.capability,
        "status": sample.status,
        "outcome": sample.outcome,
        "reviewed_by_user_id": sample.reviewed_by_user_id,
        "reviewed_at": sample.reviewed_at,
        "version": sample.version,
        "sampled_at": sample.sampled_at,
        "expires_at": sample.expires_at,
    }
    if include_review_notes:
        result["review_notes"] = sample.review_notes
    return result


def access_details(identity, resource_type: str, purpose: str, result: str, owner_id=None):
    return {
        "admin_user_id": str(identity.user.id),
        "admin_session_id": str(identity.device.id),
        "resource_type": resource_type,
        "purpose": purpose,
        "result": result,
        "target_owner_id": str(owner_id) if owner_id is not None else None,
    }


@router.get("/ai-body-logs")
async def list_body_logs(
    request: Request,
    capability: Capability | None = None,
    status: BodyLogStatus | None = None,
    search: Annotated[str | None, Query(max_length=254)] = None,
    before_time: datetime | None = None,
    before_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> dict:
    validate_cursor(before_time, before_id)
    async with authorized_admin(request) as identity:
        current = now()
        statement = (
            select(AIBodyLog, User.email)
            .join(User, User.id == AIBodyLog.owner_id)
            .where(AIBodyLog.expires_at > current)
        )
        if capability is not None:
            statement = statement.where(AIBodyLog.capability == capability)
        if status is not None:
            statement = statement.where(AIBodyLog.status == status)
        if search and (normalized := search.strip().lower()):
            statement = statement.where(
                User.email.contains(normalized, autoescape=True)
            )
        if before_time is not None:
            statement = statement.where(
                or_(
                    AIBodyLog.started_at < before_time,
                    and_(
                        AIBodyLog.started_at == before_time,
                        AIBodyLog.id < before_id,
                    ),
                )
            )
        rows = list(
            (
                await identity.session.execute(
                    statement.order_by(
                        AIBodyLog.started_at.desc(),
                        AIBodyLog.id.desc(),
                    ).limit(limit + 1)
                )
            ).all()
        )
        page = rows[:limit]
        return {
            "items": [
                body_log_metadata(body_log, owner_email)
                for body_log, owner_email in page
            ],
            "next_cursor": (
                {
                    "before_time": page[-1][0].started_at,
                    "before_id": page[-1][0].id,
                }
                if len(rows) > limit
                else None
            ),
        }


@router.post("/ai-body-logs/{body_log_id}/access")
async def access_body_log(
    body_log_id: UUID,
    data: BodyLogAccessInput,
    request: Request,
) -> dict:
    result = None
    pending_error = None
    async with authorized_admin(request, write=True) as identity:
        row = (
            await identity.session.execute(
                select(AIBodyLog, User.email)
                .join(User, User.id == AIBodyLog.owner_id)
                .where(AIBodyLog.id == body_log_id)
                .with_for_update(of=AIBodyLog)
            )
        ).first()
        if row is None or row[0].expires_at <= now():
            audit(
                request,
                identity.session,
                identity.user,
                "ai_body_log_access_denied",
                body_log_id,
                details=access_details(
                    identity,
                    "ai_body_log",
                    data.purpose,
                    "not_found",
                ),
            )
            pending_error = AppError(
                "ai_body_log_not_found",
                "未找到此 AI 技术日志，或日志已到期。",
                status=404,
            )
        else:
            body_log, owner_email = row
            audit(
                request,
                identity.session,
                identity.user,
                "ai_body_log_accessed",
                body_log.id,
                details=access_details(
                    identity,
                    "ai_body_log",
                    data.purpose,
                    "succeeded",
                    body_log.owner_id,
                ),
            )
            result = {
                **body_log_metadata(body_log, owner_email),
                "request_body": body_log.request_body,
                "response_body": body_log.response_body,
            }
    if pending_error is not None:
        raise pending_error
    return result


@router.get("/ai-quality-samples")
async def list_quality_samples(
    request: Request,
    capability: Capability | None = None,
    status: QualityStatus | None = None,
    outcome: QualityOutcome | None = None,
    search: Annotated[str | None, Query(max_length=254)] = None,
    before_time: datetime | None = None,
    before_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> dict:
    validate_cursor(before_time, before_id)
    async with authorized_admin(request) as identity:
        current = now()
        statement = (
            select(AIQualitySample, User.email)
            .join(User, User.id == AIQualitySample.owner_id)
            .where(AIQualitySample.expires_at > current)
        )
        if capability is not None:
            statement = statement.where(
                AIQualitySample.capability == capability
            )
        if status is not None:
            statement = statement.where(AIQualitySample.status == status)
        if outcome is not None:
            statement = statement.where(AIQualitySample.outcome == outcome)
        if search and (normalized := search.strip().lower()):
            statement = statement.where(
                User.email.contains(normalized, autoescape=True)
            )
        if before_time is not None:
            statement = statement.where(
                or_(
                    AIQualitySample.sampled_at < before_time,
                    and_(
                        AIQualitySample.sampled_at == before_time,
                        AIQualitySample.id < before_id,
                    ),
                )
            )
        rows = list(
            (
                await identity.session.execute(
                    statement.order_by(
                        AIQualitySample.sampled_at.desc(),
                        AIQualitySample.id.desc(),
                    ).limit(limit + 1)
                )
            ).all()
        )
        page = rows[:limit]
        return {
            "items": [
                quality_sample_metadata(sample, owner_email)
                for sample, owner_email in page
            ],
            "next_cursor": (
                {
                    "before_time": page[-1][0].sampled_at,
                    "before_id": page[-1][0].id,
                }
                if len(rows) > limit
                else None
            ),
        }


@router.post("/ai-quality-samples/{sample_id}/access")
async def access_quality_sample(
    sample_id: UUID,
    data: QualitySampleAccessInput,
    request: Request,
) -> dict:
    result = None
    pending_error = None
    async with authorized_admin(request, write=True) as identity:
        row = (
            await identity.session.execute(
                select(AIQualitySample, User.email)
                .join(User, User.id == AIQualitySample.owner_id)
                .where(AIQualitySample.id == sample_id)
                .with_for_update(of=AIQualitySample)
            )
        ).first()
        if row is None or row[0].expires_at <= now():
            audit(
                request,
                identity.session,
                identity.user,
                "ai_quality_sample_access_denied",
                sample_id,
                details=access_details(
                    identity,
                    "ai_quality_sample",
                    data.purpose,
                    "not_found",
                ),
            )
            pending_error = AppError(
                "ai_quality_sample_not_found",
                "未找到此质量样本，或样本已到期。",
                status=404,
            )
        else:
            sample, owner_email = row
            audit(
                request,
                identity.session,
                identity.user,
                "ai_quality_sample_accessed",
                sample.id,
                details=access_details(
                    identity,
                    "ai_quality_sample",
                    data.purpose,
                    "succeeded",
                    sample.owner_id,
                ),
            )
            result = {
                **quality_sample_metadata(
                    sample,
                    owner_email,
                    include_review_notes=True,
                ),
                "request_body": sample.request_body,
                "response_body": sample.response_body,
            }
    if pending_error is not None:
        raise pending_error
    return result


@router.put("/ai-quality-samples/{sample_id}/review")
async def review_quality_sample(
    sample_id: UUID,
    data: QualitySampleReviewInput,
    request: Request,
) -> dict:
    async with authorized_admin(request, write=True) as identity:
        row = (
            await identity.session.execute(
                select(AIQualitySample, User.email)
                .join(User, User.id == AIQualitySample.owner_id)
                .where(AIQualitySample.id == sample_id)
                .with_for_update(of=AIQualitySample)
            )
        ).first()
        current = now()
        if row is None or row[0].expires_at <= current:
            raise AppError(
                "ai_quality_sample_not_found",
                "未找到此质量样本，或样本已到期。",
                status=404,
            )
        sample, owner_email = row
        if sample.version != data.expected_version:
            raise AppError(
                "ai_quality_sample_version_conflict",
                "此质量样本已被其他管理员更新，请刷新后重试。",
                status=409,
                details={"current_version": sample.version},
            )
        if (
            sample.status != "reviewed"
            or sample.outcome != data.outcome
            or sample.review_notes != data.review_notes
        ):
            sample.status = "reviewed"
            sample.outcome = data.outcome
            sample.review_notes = data.review_notes
            sample.reviewed_by_user_id = identity.user.id
            sample.reviewed_at = current
            sample.version += 1
            audit(
                request,
                identity.session,
                identity.user,
                "ai_quality_sample_reviewed",
                sample.id,
                details={
                    **access_details(
                        identity,
                        "ai_quality_sample",
                        "manual_quality_review",
                        "succeeded",
                        sample.owner_id,
                    ),
                    "outcome": data.outcome,
                    "sample_version": sample.version,
                },
            )
        return quality_sample_metadata(
            sample,
            owner_email,
            include_review_notes=True,
        )


@router.get("/audit-events")
async def list_audit_events(
    request: Request,
    action: Annotated[str | None, Query(max_length=50)] = None,
    actor_id: UUID | None = None,
    before_time: datetime | None = None,
    before_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict:
    validate_cursor(before_time, before_id)
    async with authorized_admin(request) as identity:
        statement = select(SecurityEvent, User.email).outerjoin(
            User,
            User.id == SecurityEvent.user_id,
        )
        if action is not None:
            statement = statement.where(SecurityEvent.action == action)
        if actor_id is not None:
            statement = statement.where(SecurityEvent.user_id == actor_id)
        if before_time is not None:
            statement = statement.where(
                or_(
                    SecurityEvent.created_at < before_time,
                    and_(
                        SecurityEvent.created_at == before_time,
                        SecurityEvent.id < before_id,
                    ),
                )
            )
        rows = list(
            (
                await identity.session.execute(
                    statement.order_by(
                        SecurityEvent.created_at.desc(),
                        SecurityEvent.id.desc(),
                    ).limit(limit + 1)
                )
            ).all()
        )
        page = rows[:limit]
        return {
            "items": [
                {
                    "id": event.id,
                    "actor": (
                        {"id": event.user_id, "email": actor_email}
                        if event.user_id is not None
                        else None
                    ),
                    "action": event.action,
                    "request_id": event.request_id,
                    "target_id": event.target_id,
                    "details": event.details,
                    "created_at": event.created_at,
                }
                for event, actor_email in page
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
