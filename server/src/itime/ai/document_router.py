import asyncio
from typing import Annotated

from fastapi import APIRouter, File, Request, UploadFile

from itime.ai.document_service import MAX_DOCUMENT_BYTES, extract_document
from itime.auth.session import authorized, check_ai_access

router = APIRouter(prefix="/api/v1/ai/documents", tags=["AI 文档"])


@router.post("/extract")
async def extract_document_endpoint(
    request: Request,
    document: Annotated[UploadFile, File()],
) -> dict:
    async with authorized(request) as identity:
        check_ai_access(identity.user)

    contents = await document.read(MAX_DOCUMENT_BYTES + 1)
    await document.close()
    filename, text, truncated = await asyncio.to_thread(
        extract_document,
        document.filename or "",
        contents,
    )
    return {
        "filename": filename,
        "text": text,
        "characters": len(text),
        "truncated": truncated,
    }
