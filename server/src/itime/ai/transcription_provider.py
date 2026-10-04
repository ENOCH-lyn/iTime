import asyncio
import json
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx
from cryptography.fernet import Fernet, InvalidToken

from itime.ai.provider import (
    EndpointPolicyError,
    ProviderConfig,
    enforce_endpoint_policy,
    resolved_endpoint_allowed,
)
from itime.config import Settings

MAX_TRANSCRIPTION_RESPONSE_BYTES = 256 * 1024
MAX_TRANSCRIPT_CHARACTERS = 4000


@dataclass(frozen=True)
class TranscriptionProviderResult:
    transcript: str
    language: str | None
    response_body: str
    provider_request_id: str | None
    http_status: int
    usage: object | None


class TranscriptionProviderError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        stage: str,
        *,
        retryable: bool = False,
        provider_request_id: str | None = None,
        http_status: int | None = None,
        usage: object | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.stage = stage
        self.retryable = retryable
        self.provider_request_id = provider_request_id
        self.http_status = http_status
        self.usage = usage


class TranscriptionGenerationSuperseded(Exception):
    pass


def transcription_log_request_body(
    config: ProviderConfig,
    *,
    filename: str,
    content_type: str,
    byte_size: int,
    duration_ms: int,
    content_sha256: str,
    purpose: str,
) -> dict:
    return {
        "model": config.model,
        "response_format": "json",
        "purpose": purpose,
        "file": {
            "filename": filename,
            "content_type": content_type,
            "byte_size": byte_size,
            "duration_ms": duration_ms,
            "content_sha256": content_sha256,
            "content": "omitted_by_retention_policy",
        },
    }


def _contains_control_characters(value: str) -> bool:
    return any(unicodedata.category(character).startswith("C") for character in value)


def _parse_response(
    body: bytes,
) -> tuple[str, str | None, object | None]:
    usage: object | None = None
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise TypeError
        usage = {
            key: payload[key]
            for key in ("duration", "usage")
            if key in payload
        } or None
        transcript = payload["text"]
        if not isinstance(transcript, str):
            raise TypeError
        transcript = transcript.strip()
        if (
            not 1 <= len(transcript) <= MAX_TRANSCRIPT_CHARACTERS
            or _contains_control_characters(transcript)
        ):
            raise ValueError
        raw_language = payload.get("language")
        if raw_language is None:
            language = None
        elif isinstance(raw_language, str):
            language = raw_language.strip()
            if (
                not 1 <= len(language) <= 32
                or _contains_control_characters(language)
            ):
                raise ValueError
        else:
            raise TypeError
    except (
        KeyError,
        TypeError,
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise TranscriptionProviderError(
            "transcription_output_invalid",
            "转写服务返回的文字无法校验，请重新录制或手工输入。",
            "validation",
            usage=usage,
        ) from exc
    return transcript, language, usage


async def transcribe_audio(
    config: ProviderConfig,
    settings: Settings,
    cipher: Fernet,
    *,
    audio: bytes,
    filename: str,
    timeout_seconds: float,
    on_request_started: Callable[[], Awaitable[bool]],
) -> TranscriptionProviderResult:
    try:
        enforce_endpoint_policy(config.endpoint_url, settings)
    except EndpointPolicyError as exc:
        raise TranscriptionProviderError(
            "ai_endpoint_blocked",
            "转写端点不符合当前网络安全策略。",
            "endpoint_policy",
        ) from exc
    endpoint_allowed = await resolved_endpoint_allowed(
        config.endpoint_url,
        settings,
    )
    if endpoint_allowed is None:
        raise TranscriptionProviderError(
            "ai_upstream_unreachable",
            "暂时无法解析转写服务地址，系统会自动重试。",
            "network",
            retryable=True,
        )
    if not endpoint_allowed:
        raise TranscriptionProviderError(
            "ai_endpoint_blocked",
            "转写端点解析到未明确放行的内部网络地址。",
            "endpoint_policy",
        )
    try:
        api_key = cipher.decrypt(config.api_key_ciphertext).decode()
    except (InvalidToken, UnicodeDecodeError) as exc:
        raise TranscriptionProviderError(
            "ai_secret_unavailable",
            "转写配置暂时无法读取，请联系管理员。",
            "configuration",
        ) from exc

    endpoint = config.endpoint_url.rstrip("/") + "/audio/transcriptions"
    timeout = httpx.Timeout(
        max(0.1, min(float(config.timeout_seconds), timeout_seconds))
    )
    request_id: str | None = None
    http_status: int | None = None
    try:
        async with asyncio.timeout(timeout_seconds):
            async with httpx.AsyncClient(
                timeout=timeout,
                trust_env=False,
                follow_redirects=False,
            ) as client:
                if not await on_request_started():
                    raise TranscriptionGenerationSuperseded
                async with client.stream(
                    "POST",
                    endpoint,
                    headers={"Authorization": f"Bearer {api_key}"},
                    data={
                        "model": config.model,
                        "response_format": "json",
                    },
                    files={"file": (filename, audio, "audio/wav")},
                ) as response:
                    request_id = (
                        response.headers.get("x-request-id")
                        or response.headers.get("request-id")
                    )
                    http_status = response.status_code
                    if response.status_code in {401, 403}:
                        raise TranscriptionProviderError(
                            "ai_authentication_failed",
                            "转写服务配置无效，请联系管理员。",
                            "authentication",
                            provider_request_id=request_id,
                            http_status=response.status_code,
                        )
                    if response.status_code == 429:
                        raise TranscriptionProviderError(
                            "ai_rate_limited",
                            "转写服务暂时繁忙，系统会自动重试。",
                            "upstream",
                            retryable=True,
                            provider_request_id=request_id,
                            http_status=response.status_code,
                        )
                    if response.status_code >= 500:
                        raise TranscriptionProviderError(
                            "ai_upstream_unavailable",
                            "转写服务暂时不可用，系统会自动重试。",
                            "upstream",
                            retryable=True,
                            provider_request_id=request_id,
                            http_status=response.status_code,
                        )
                    if not 200 <= response.status_code < 300:
                        raise TranscriptionProviderError(
                            "ai_request_rejected",
                            "转写服务拒绝了请求，请联系管理员检查配置。",
                            "upstream",
                            provider_request_id=request_id,
                            http_status=response.status_code,
                        )
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > MAX_TRANSCRIPTION_RESPONSE_BYTES:
                            raise TranscriptionProviderError(
                                "transcription_output_too_large",
                                "转写结果超过可处理大小，请缩短录音后重试。",
                                "validation",
                                provider_request_id=request_id,
                                http_status=response.status_code,
                            )
                        chunks.append(chunk)
    except (
        TranscriptionProviderError,
        TranscriptionGenerationSuperseded,
    ):
        raise
    except (TimeoutError, httpx.TimeoutException) as exc:
        raise TranscriptionProviderError(
            "ai_upstream_timeout",
            "转写服务响应超时，系统会自动重试。",
            "network",
            retryable=True,
            provider_request_id=request_id,
            http_status=http_status,
        ) from exc
    except httpx.HTTPError as exc:
        raise TranscriptionProviderError(
            "ai_upstream_unreachable",
            "暂时无法连接转写服务，系统会自动重试。",
            "network",
            retryable=True,
            provider_request_id=request_id,
            http_status=http_status,
        ) from exc
    finally:
        api_key = ""

    try:
        transcript, language, usage = _parse_response(b"".join(chunks))
    except TranscriptionProviderError as exc:
        exc.provider_request_id = request_id
        exc.http_status = http_status
        raise
    return TranscriptionProviderResult(
        transcript=transcript,
        language=language,
        response_body=transcript,
        provider_request_id=request_id,
        http_status=http_status or 200,
        usage=usage,
    )
