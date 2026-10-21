import asyncio
from datetime import timedelta
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, Query, Request
from sqlalchemy import select

from itime.ai.image_validation import validate_image
from itime.ai.models import TemporaryMedia
from itime.auth.security import now
from itime.auth.session import authorized, check_ai_access
from itime.calendar.import_schemas import ImportFilename
from itime.errors import AppError

router = APIRouter(prefix="/api/v1/ai/images", tags=["AI 图片"])
MAX_CHAT_IMAGE_BYTES = 600 * 1024


@router.post("", status_code=201)
async def upload_image(
    request: Request,
    filename: Annotated[str, Query(min_length=1, max_length=255)],
    operation_id: Annotated[UUID, Header(alias="X-Operation-ID")],
) -> dict:
    filename = ImportFilename(filename=filename).filename
    contents = await request.body()
    if len(contents) > MAX_CHAT_IMAGE_BYTES:
        raise AppError(
            "image_too_large",
            "对话图片不能超过 600 KiB。",
            status=413,
        )
    async with authorized(request) as identity:
        check_ai_access(identity.user)
    image = await asyncio.to_thread(
        validate_image,
        contents,
        filename,
        request.headers.get("content-type", ""),
    )
    encrypted = await asyncio.to_thread(
        request.app.state.security.ai_cipher.encrypt,
        contents,
    )
    async with authorized(request, write=True) as identity:
        existing = await identity.session.scalar(
            select(TemporaryMedia).where(
                TemporaryMedia.owner_id == identity.user.id,
                TemporaryMedia.purpose == "calendar_screenshot",
                TemporaryMedia.content_sha256 == image.sha256,
                TemporaryMedia.deleted_at.is_(None),
                TemporaryMedia.expires_at > now(),
            )
        )
        if existing is not None:
            return {"media_id": str(existing.id)}
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
            encrypted_content=encrypted,
            expires_at=now() + timedelta(hours=24),
        )
        identity.session.add(media)
        await identity.session.flush()
        return {"media_id": str(media.id), "operation_id": str(operation_id)}
