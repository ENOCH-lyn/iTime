import asyncio
from datetime import timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request
from sqlalchemy import func, select

from itime.ai.image_validation import MAX_IMAGE_BYTES, validate_image
from itime.ai.job_service import job_result
from itime.ai.models import AIConfigCapability, AIConfigVersion, AIJob, TemporaryMedia
from itime.auth.security import now
from itime.auth.session import authorized, check_ai_access
from itime.calendar.import_schemas import ImportFilename
from itime.calendar.import_service import default_import_time_zone
from itime.calendar.models import AcademicTerm, TermPeriod
from itime.calendar.screenshot_schemas import ScreenshotUploadOperation
from itime.errors import AppError
from itime.tasks.service import replay_operation, save_operation

router = APIRouter(
    prefix="/api/v1/calendar/imports/screenshots",
    tags=["日历截图识别"],
)


async def _recognition_context(session, owner_id, time_zone: str) -> dict:
    terms = list(
        await session.scalars(
            select(AcademicTerm)
            .where(AcademicTerm.owner_id == owner_id)
            .order_by(AcademicTerm.updated_at.desc(), AcademicTerm.id.desc())
            .limit(50)
        )
    )
    term_ids = [term.id for term in terms]
    periods_by_term: dict[UUID, list[dict]] = {term.id: [] for term in terms}
    if term_ids:
        periods = await session.scalars(
            select(TermPeriod)
            .where(
                TermPeriod.owner_id == owner_id,
                TermPeriod.term_id.in_(term_ids),
            )
            .order_by(TermPeriod.term_id, TermPeriod.period_number)
        )
        for period in periods:
            periods_by_term[period.term_id].append(
                {
                    "number": period.period_number,
                    "name": period.name,
                    "starts_at": period.starts_at.isoformat(),
                    "ends_at": period.ends_at.isoformat(),
                }
            )
    return {
        "time_zone": time_zone,
        "terms": [
            {
                "id": str(term.id),
                "name": term.name,
                "start_date": term.start_date.isoformat(),
                "end_date": term.end_date.isoformat(),
                "first_week_monday": term.first_week_monday.isoformat(),
                "time_zone": term.time_zone,
                "periods": periods_by_term[term.id],
            }
            for term in terms
        ],
    }


@router.post("", status_code=202)
async def upload_calendar_screenshot(
    request: Request,
    filename: Annotated[str, Query(min_length=1, max_length=255)],
    operation_id: Annotated[UUID, Header(alias="X-Operation-ID")],
) -> dict:
    filename = ImportFilename(filename=filename).filename
    contents = await request.body()
    if len(contents) > MAX_IMAGE_BYTES:
        raise AppError(
            "image_too_large",
            "图片不能超过 5 MiB。",
            status=413,
        )
    async with authorized(request) as identity:
        check_ai_access(identity.user)

    content_type = request.headers.get("content-type", "")
    image = await asyncio.to_thread(
        validate_image,
        contents,
        filename,
        content_type,
    )
    encrypted_content = await asyncio.to_thread(
        request.app.state.security.ai_cipher.encrypt,
        contents,
    )
    operation = ScreenshotUploadOperation(
        operation_id=operation_id,
        filename=filename,
        content_type=image.content_type,
        content_sha256=image.sha256,
    )

    async with authorized(request, write=True) as identity:
        check_ai_access(identity.user)
        replay = await replay_operation(
            identity.session,
            identity.user.id,
            operation_id,
            "calendar_screenshot.upload",
            operation,
        )
        if replay is not None:
            return replay
        active_count = await identity.session.scalar(
            select(func.count())
            .select_from(AIJob)
            .where(
                AIJob.owner_id == identity.user.id,
                AIJob.capability == "calendar_screenshot",
                AIJob.status.in_(["queued", "running"]),
            )
        )
        if active_count >= 3:
            raise AppError(
                "ai_job_limit_reached",
                "已有 3 个截图识别作业正在处理，请完成后再上传。",
                status=429,
                retryable=True,
            )
        state = await identity.session.get(
            AIConfigCapability,
            "calendar_screenshot",
        )
        config = (
            await identity.session.get(AIConfigVersion, state.current_version_id)
            if state is not None and state.current_version_id is not None
            else None
        )
        if config is None or config.status != "published":
            raise AppError(
                "ai_capability_unavailable",
                "截图识别暂未配置，请稍后重试或使用 Excel、ICS 文件导入。",
                status=503,
                retryable=True,
            )

        time_zone = await default_import_time_zone(
            identity.session,
            identity.user.id,
        )
        context = await _recognition_context(
            identity.session,
            identity.user.id,
            time_zone,
        )
        current = now()
        media = TemporaryMedia(
            owner_id=identity.user.id,
            purpose="calendar_screenshot",
            original_filename=filename,
            content_type=image.content_type,
            content_sha256=image.sha256,
            byte_size=image.byte_size,
            width=image.width,
            height=image.height,
            encrypted_content=encrypted_content,
            expires_at=current + timedelta(hours=24),
        )
        identity.session.add(media)
        await identity.session.flush()
        job = AIJob(
            owner_id=identity.user.id,
            capability="calendar_screenshot",
            config_version_id=config.id,
            input_media_id=media.id,
            input_summary={
                "filename": filename,
                "content_type": image.content_type,
                "byte_size": image.byte_size,
                "width": image.width,
                "height": image.height,
                "content_sha256": image.sha256,
                "recognition_context": context,
            },
            available_at=current,
            deadline_at=current
            + timedelta(seconds=request.app.state.settings.ai_job_timeout_seconds),
        )
        identity.session.add(job)
        await identity.session.flush()
        result = {"operation_id": operation_id, "job": job_result(job)}
        save_operation(
            identity.session,
            identity.user.id,
            operation_id,
            "calendar_screenshot.upload",
            operation,
            result,
        )
        return result
