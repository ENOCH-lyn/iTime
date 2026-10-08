import asyncio
import hashlib
from datetime import timedelta
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy import func, select
from starlette.datastructures import UploadFile

from itime.ai.conversation_models import Conversation, ConversationMessage
from itime.ai.image_validation import MAX_IMAGE_BYTES, validate_image
from itime.ai.job_service import job_result
from itime.ai.models import AIJob, TemporaryMedia
from itime.ai.routing_service import (
    load_active_config_route,
    provider_config,
    replace_job_config_route,
)
from itime.auth.security import now
from itime.auth.session import authorized, check_ai_access
from itime.calendar.ai_import_provider import ai_import_log_request_body
from itime.calendar.import_schemas import (
    AIImportCreateInput,
    AIImportOperation,
    ImportFilename,
)
from itime.calendar.import_service import (
    batch_result,
    new_batch_expiry,
)
from itime.calendar.models import CalendarImportBatch, CalendarSource
from itime.calendar.screenshot_service import recognition_context
from itime.errors import AppError
from itime.network.restricted_http import RestrictedHttpError, safe_url_display
from itime.quota.service import (
    estimate_reservation_budget,
    get_or_create_daily_allowance,
    maximum_reservation_estimate,
    reserve_job_budget,
)
from itime.tasks.service import replay_operation, save_operation

router = APIRouter(
    prefix="/api/v1/calendar/imports/ai",
    tags=["AI 课表导入"],
)


def _validation_error(error: ValidationError) -> RequestValidationError:
    return RequestValidationError(error.errors())


def _optional_uuid(value: object, field_name: str) -> UUID | None:
    if value in {None, ""}:
        return None
    try:
        return UUID(str(value))
    except ValueError as exc:
        raise AppError(
            "invalid_import_association",
            f"{field_name} 格式不正确。",
        ) from exc


async def _parse_request(
    request: Request,
) -> tuple[
    str,
    bytes,
    str,
    str | None,
    UUID | None,
    UUID | None,
]:
    content_type = request.headers.get("content-type", "")
    media_type = content_type.split(";", 1)[0].strip().casefold()
    if media_type == "application/json":
        try:
            data = AIImportCreateInput.model_validate(await request.json())
        except (ValueError, ValidationError) as exc:
            if isinstance(exc, ValidationError):
                raise _validation_error(exc) from exc
            raise AppError(
                "invalid_json",
                "请求正文不是有效的 JSON。",
            ) from exc
        return (
            data.source_type,
            data.content.encode(),
            "公开网页" if data.source_type == "public_url" else "文字描述",
            None,
            data.conversation_id,
            data.message_id,
        )
    if media_type != "multipart/form-data":
        raise AppError(
            "calendar_import_media_type_invalid",
            "请提交 JSON 文字或网址，或上传课表图片。",
            status=415,
        )
    form = await request.form()
    if form.get("source_type") != "image":
        raise AppError(
            "calendar_import_source_invalid",
            "图片上传必须使用 image 来源类型。",
        )
    upload = form.get("file")
    if not isinstance(upload, UploadFile):
        raise AppError(
            "calendar_import_file_required",
            "请选择要识别的课表图片。",
        )
    filename = ImportFilename(filename=upload.filename or "课表图片").filename
    contents = await upload.read(MAX_IMAGE_BYTES + 1)
    await upload.close()
    if len(contents) > MAX_IMAGE_BYTES:
        raise AppError("image_too_large", "图片不能超过 5 MiB。", status=413)
    return (
        "image",
        contents,
        filename,
        upload.content_type,
        _optional_uuid(form.get("conversation_id"), "会话 ID"),
        _optional_uuid(form.get("message_id"), "消息 ID"),
    )


async def _validate_association(
    session,
    owner_id: UUID,
    conversation_id: UUID | None,
    message_id: UUID | None,
) -> None:
    if message_id is not None and conversation_id is None:
        raise AppError(
            "invalid_import_association",
            "关联消息时必须同时提供会话。",
        )
    if conversation_id is None:
        return
    conversation = await session.scalar(
        select(Conversation.id).where(
            Conversation.id == conversation_id,
            Conversation.owner_id == owner_id,
        )
    )
    if conversation is None:
        raise AppError(
            "ai_conversation_not_found",
            "未找到关联会话。",
            status=404,
        )
    if message_id is None:
        return
    message = await session.scalar(
        select(ConversationMessage.id).where(
            ConversationMessage.id == message_id,
            ConversationMessage.owner_id == owner_id,
            ConversationMessage.conversation_id == conversation_id,
        )
    )
    if message is None:
        raise AppError(
            "ai_message_not_found",
            "未找到关联消息。",
            status=404,
        )


@router.post("", status_code=202)
async def create_ai_calendar_import(
    request: Request,
    operation_id: Annotated[UUID, Header(alias="X-Operation-ID")],
) -> dict:
    (
        source_type,
        contents,
        filename,
        upload_content_type,
        conversation_id,
        message_id,
    ) = await _parse_request(request)
    async with authorized(request) as identity:
        check_ai_access(identity.user)

    image = None
    if source_type == "image":
        image = await asyncio.to_thread(
            validate_image,
            contents,
            filename,
            upload_content_type or "",
        )
        content_type = image.content_type
    else:
        content_type = "text/plain"
    content_hash = hashlib.sha256(contents).hexdigest()
    operation = AIImportOperation(
        operation_id=operation_id,
        source_type=source_type,
        content_sha256=content_hash,
        filename=filename if source_type == "image" else None,
        conversation_id=conversation_id,
        message_id=message_id,
    )

    async with authorized(request, write=True) as identity:
        check_ai_access(identity.user)
        replay = await replay_operation(
            identity.session,
            identity.user.id,
            operation_id,
            "calendar_ai_import.create",
            operation,
        )
        if replay is not None:
            return replay
        await _validate_association(
            identity.session,
            identity.user.id,
            conversation_id,
            message_id,
        )
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
                "已有 3 个课表导入正在处理，请完成后再提交。",
                status=429,
                retryable=True,
            )
        route = await load_active_config_route(
            identity.session,
            "calendar_screenshot",
            unavailable_message="课表解析暂未配置，请稍后重试。",
        )
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

        source_text = contents.decode() if source_type != "image" else None
        if source_type == "public_url":
            try:
                source_display, display_host = safe_url_display(
                    source_text or "",
                    request.app.state.settings,
                )
            except RestrictedHttpError as exc:
                raise AppError(
                    f"calendar_import_{exc.reason}",
                    "公开网页地址不符合安全读取规则。",
                ) from exc
            source_name = display_host[:255]
        elif source_type == "text":
            source_display = "文字描述"
            source_name = "文字描述"
        else:
            source_display = filename
            source_name = filename

        estimate_text = (
            "x" * 24000 if source_type == "public_url" else source_text
        )
        estimate = maximum_reservation_estimate(
            [
                estimate_reservation_budget(
                    route_config,
                    request_body=ai_import_log_request_body(
                        provider_config(route_config),
                        source_type=source_type,
                        context=context,
                        source_text=estimate_text,
                        content_type=content_type if image is not None else None,
                        byte_size=image.byte_size if image is not None else None,
                        content_sha256=(
                            image.sha256 if image is not None else None
                        ),
                    ),
                    input_summary={
                        "source_type": source_type,
                        "content_sha256": content_hash,
                        **(
                            {
                                "width": image.width,
                                "height": image.height,
                                "byte_size": image.byte_size,
                            }
                            if image is not None
                            else {"character_count": len(source_text or "")}
                        ),
                    },
                    capability="calendar_screenshot",
                )
                for route_config in route.configs
            ]
        )

        encrypted_input = None
        media = None
        if image is not None:
            encrypted_image = await asyncio.to_thread(
                request.app.state.security.ai_cipher.encrypt,
                contents,
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
                encrypted_content=encrypted_image,
                expires_at=current + timedelta(hours=24),
            )
            identity.session.add(media)
        else:
            encrypted_input = await asyncio.to_thread(
                request.app.state.security.ai_cipher.encrypt,
                contents,
            )

        source = CalendarSource(
            owner_id=identity.user.id,
            kind=source_type,
            name=source_name,
        )
        identity.session.add(source)
        await identity.session.flush()
        input_summary = {
            "source_type": source_type,
            "content_sha256": content_hash,
            **(
                {
                    "filename": filename,
                    "content_type": image.content_type,
                    "byte_size": image.byte_size,
                    "width": image.width,
                    "height": image.height,
                }
                if image is not None
                else {"character_count": len(source_text or "")}
            ),
        }
        batch = CalendarImportBatch(
            owner_id=identity.user.id,
            source_id=source.id,
            format="screenshot" if source_type == "image" else source_type,
            source_type=source_type,
            filename=filename,
            content_sha256=content_hash,
            state="queued",
            candidate_count=0,
            source_display=source_display,
            input_summary=input_summary,
            input_body_ciphertext=encrypted_input,
            input_body_expires_at=(
                current
                + timedelta(
                    days=request.app.state.settings.ai_body_log_retention_days
                )
                if encrypted_input is not None
                else None
            ),
            conversation_id=conversation_id,
            message_id=message_id,
            expires_at=new_batch_expiry(),
        )
        identity.session.add(batch)
        await identity.session.flush()
        job = AIJob(
            id=uuid4(),
            owner_id=identity.user.id,
            capability="calendar_screenshot",
            config_version_id=route.primary.id,
            input_media_id=media.id if media is not None else None,
            quota_mode="user",
            input_summary={
                **input_summary,
                "recognition_context": context,
                "batch_id": str(batch.id),
            },
            available_at=current,
            deadline_at=current
            + timedelta(
                seconds=request.app.state.settings.ai_job_timeout_seconds
            ),
        )
        identity.session.add(job)
        await identity.session.flush()
        batch.origin_job_id = job.id
        await replace_job_config_route(identity.session, job, route)
        await reserve_job_budget(
            identity.session,
            owner_id=identity.user.id,
            job=job,
            accepted_at=current,
            estimate=estimate,
            daily=daily,
        )
        result = {
            "operation_id": operation_id,
            "batch": batch_result(batch, source, []),
            "job": job_result(job),
        }
        save_operation(
            identity.session,
            identity.user.id,
            operation_id,
            "calendar_ai_import.create",
            operation,
            result,
        )
        return result
