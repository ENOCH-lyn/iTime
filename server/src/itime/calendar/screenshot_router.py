import asyncio
from datetime import timedelta
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, Query, Request
from sqlalchemy import func, select

from itime.ai.image_validation import MAX_IMAGE_BYTES, validate_image
from itime.ai.job_service import job_result
from itime.ai.models import AIJob, TemporaryMedia
from itime.ai.routing_service import (
    load_active_config_route,
    provider_config,
    replace_job_config_route,
)
from itime.ai.screenshot_provider import screenshot_log_request_body
from itime.auth.security import now
from itime.auth.session import authorized, check_ai_access
from itime.calendar.import_schemas import ImportFilename
from itime.calendar.screenshot_schemas import ScreenshotUploadOperation
from itime.calendar.screenshot_service import recognition_context
from itime.errors import AppError
from itime.quota.service import (
    estimate_reservation_budget,
    get_or_create_daily_allowance,
    maximum_reservation_estimate,
    reserve_job_budget,
)
from itime.tasks.service import replay_operation, save_operation

router = APIRouter(
    prefix="/api/v1/calendar/imports/screenshots",
    tags=["日历截图识别"],
)


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
        route = await load_active_config_route(
            identity.session,
            "calendar_screenshot",
            unavailable_message=(
                "截图识别暂未配置，请稍后重试或使用 Excel、ICS 文件导入。"
            ),
        )
        config = route.primary

        current = now()
        daily = await get_or_create_daily_allowance(
            identity.session,
            identity.user.id,
            accepted_at=current,
        )
        context = await recognition_context(
            identity.session,
            identity.user.id,
            daily.time_zone,
        )
        input_summary = {
            "filename": filename,
            "content_type": image.content_type,
            "byte_size": image.byte_size,
            "width": image.width,
            "height": image.height,
            "content_sha256": image.sha256,
            "recognition_context": context,
        }
        estimate = maximum_reservation_estimate(
            [
                estimate_reservation_budget(
                    route_config,
                    request_body=screenshot_log_request_body(
                        provider_config(route_config),
                        content_type=image.content_type,
                        byte_size=image.byte_size,
                        content_sha256=image.sha256,
                        context=context,
                    ),
                    input_summary=input_summary,
                    capability="calendar_screenshot",
                )
                for route_config in route.configs
            ]
        )
        media = TemporaryMedia(
            id=uuid4(),
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
            id=uuid4(),
            owner_id=identity.user.id,
            capability="calendar_screenshot",
            config_version_id=config.id,
            input_media_id=media.id,
            quota_mode="user",
            input_summary=input_summary,
            available_at=current,
            deadline_at=current
            + timedelta(seconds=request.app.state.settings.ai_job_timeout_seconds),
        )
        identity.session.add(job)
        await identity.session.flush()
        await replace_job_config_route(identity.session, job, route)
        await reserve_job_budget(
            identity.session,
            owner_id=identity.user.id,
            job=job,
            accepted_at=current,
            estimate=estimate,
            daily=daily,
        )
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
