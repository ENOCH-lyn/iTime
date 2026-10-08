from collections.abc import Awaitable, Callable

from cryptography.fernet import Fernet

from itime.ai.provider import ProviderConfig
from itime.calendar.ai_import_provider import (
    AIImportGenerationSuperseded,
    AIImportProviderError,
    AIImportProviderResult,
    ai_import_log_request_body,
    recognize_ai_import,
)
from itime.config import Settings

ScreenshotProviderResult = AIImportProviderResult
ScreenshotProviderError = AIImportProviderError
ScreenshotGenerationSuperseded = AIImportGenerationSuperseded


def screenshot_log_request_body(
    config: ProviderConfig,
    *,
    content_type: str,
    byte_size: int,
    content_sha256: str,
    context: dict,
) -> dict:
    return ai_import_log_request_body(
        config,
        source_type="image",
        content_type=content_type,
        byte_size=byte_size,
        content_sha256=content_sha256,
        context=context,
    )


async def recognize_screenshot(
    config: ProviderConfig,
    settings: Settings,
    cipher: Fernet,
    *,
    image: bytes,
    content_type: str,
    context: dict,
    timeout_seconds: float,
    on_request_started: Callable[[], Awaitable[bool]],
) -> ScreenshotProviderResult:
    return await recognize_ai_import(
        config,
        settings,
        cipher,
        source_type="image",
        image=image,
        content_type=content_type,
        context=context,
        timeout_seconds=timeout_seconds,
        on_request_started=on_request_started,
    )
