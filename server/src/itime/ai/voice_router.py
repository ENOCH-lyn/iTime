from typing import Annotated

from fastapi import APIRouter, File, Request, UploadFile

from itime.ai.voice_service import (
    MAX_AUDIO_BYTES,
    LocalVoiceUnavailable,
    transcribe_audio_async,
)
from itime.auth.session import authorized, check_ai_access
from itime.errors import AppError

router = APIRouter(prefix="/api/v1/ai/voice", tags=["语音输入"])

_SUFFIXES = {
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mp4": ".m4a",
    "audio/mpeg": ".mp3",
}


@router.post("/transcribe")
async def transcribe_voice(
    request: Request,
    audio: Annotated[UploadFile, File()],
) -> dict:
    async with authorized(request, write=True) as identity:
        check_ai_access(identity.user)
        contents = await audio.read(MAX_AUDIO_BYTES + 1)
        if len(contents) > MAX_AUDIO_BYTES:
            raise AppError(
                "voice_too_large",
                "语音片段不能超过 10 MiB。",
                status=413,
            )
        content_type = (audio.content_type or "").split(";", 1)[0].strip().lower()
        suffix = _SUFFIXES.get(content_type, ".webm")
        try:
            text = await transcribe_audio_async(contents, suffix)
        except LocalVoiceUnavailable as exc:
            raise AppError(
                "local_voice_unavailable",
                str(exc),
                status=501,
            ) from exc
        return {"text": text}