import hashlib
from datetime import timedelta
from uuid import UUID, uuid4

from cryptography.fernet import Fernet
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from itime.ai.models import AIJob
from itime.ai.routing_service import (
    load_active_config_route,
    provider_config,
    replace_job_config_route,
)
from itime.auth.security import now
from itime.calendar.ai_import_provider import ai_import_log_request_body
from itime.calendar.models import CalendarImportBatch, CalendarSource
from itime.calendar.screenshot_service import recognition_context
from itime.config import Settings
from itime.errors import AppError
from itime.network.restricted_http import RestrictedHttpError, safe_url_display
from itime.quota.service import (
    estimate_reservation_budget,
    get_or_create_daily_allowance,
    maximum_reservation_estimate,
    reserve_job_budget,
)


async def enqueue_text_calendar_import(
    session: AsyncSession,
    *,
    owner_id: UUID,
    settings: Settings,
    cipher: Fernet,
    source_type: str,
    content: str,
    conversation_id: UUID | None = None,
    message_id: UUID | None = None,
) -> tuple[CalendarImportBatch, CalendarSource, AIJob]:
    if source_type not in {"public_url", "text"}:
        raise ValueError("text calendar import source is invalid")
    active_count = await session.scalar(
        select(func.count())
        .select_from(AIJob)
        .where(
            AIJob.owner_id == owner_id,
            AIJob.capability == "calendar_screenshot",
            AIJob.status.in_(["queued", "running"]),
        )
    )
    if active_count >= 3:
        raise AppError(
            "ai_job_limit_reached",
            "已有 3 个课表导入正在处理，请完成后再提交。",
            status=429,
            retryable=True,
        )
    route = await load_active_config_route(
        session,
        "calendar_screenshot",
        unavailable_message="课表解析暂未配置，请稍后重试。",
    )
    current = now()
    daily = await get_or_create_daily_allowance(
        session,
        owner_id,
        accepted_at=current,
    )
    context = await recognition_context(
        session,
        owner_id,
        daily.time_zone,
    )
    if source_type == "public_url":
        try:
            source_display, display_host = safe_url_display(content, settings)
        except RestrictedHttpError as exc:
            raise AppError(
                f"calendar_import_{exc.reason}",
                "公开网页地址不符合安全读取规则。",
            ) from exc
        source_name = display_host[:255]
    else:
        source_display = "文字描述"
        source_name = "文字描述"
    content_bytes = content.encode()
    content_hash = hashlib.sha256(content_bytes).hexdigest()
    input_summary = {
        "source_type": source_type,
        "content_sha256": content_hash,
        "character_count": len(content),
    }
    estimate_text = "x" * 24000 if source_type == "public_url" else content
    estimate = maximum_reservation_estimate(
        [
            estimate_reservation_budget(
                route_config,
                request_body=ai_import_log_request_body(
                    provider_config(route_config),
                    source_type=source_type,
                    context=context,
                    source_text=estimate_text,
                ),
                input_summary=input_summary,
                capability="calendar_screenshot",
            )
            for route_config in route.configs
        ]
    )
    source = CalendarSource(
        owner_id=owner_id,
        kind=source_type,
        name=source_name,
    )
    session.add(source)
    await session.flush()
    batch = CalendarImportBatch(
        owner_id=owner_id,
        source_id=source.id,
        format=source_type,
        source_type=source_type,
        filename="公开网页" if source_type == "public_url" else "文字描述",
        content_sha256=content_hash,
        state="queued",
        candidate_count=0,
        source_display=source_display,
        input_summary=input_summary,
        input_body_ciphertext=cipher.encrypt(content_bytes),
        input_body_expires_at=current
        + timedelta(days=settings.ai_body_log_retention_days),
        conversation_id=conversation_id,
        message_id=message_id,
        expires_at=current + timedelta(days=7),
    )
    session.add(batch)
    await session.flush()
    job = AIJob(
        id=uuid4(),
        owner_id=owner_id,
        capability="calendar_screenshot",
        config_version_id=route.primary.id,
        input_media_id=None,
        quota_mode="user",
        input_summary={
            **input_summary,
            "recognition_context": context,
            "batch_id": str(batch.id),
        },
        available_at=current,
        deadline_at=current
        + timedelta(seconds=settings.ai_job_timeout_seconds),
    )
    session.add(job)
    await session.flush()
    batch.origin_job_id = job.id
    await replace_job_config_route(session, job, route)
    await reserve_job_budget(
        session,
        owner_id=owner_id,
        job=job,
        accepted_at=current,
        estimate=estimate,
        daily=daily,
    )
    return batch, source, job
