import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
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

MAX_CONVERSATION_RESPONSE_BYTES = 1024 * 1024
MAX_CONVERSATION_OUTPUT_CHARS = 100_000
DELTA_BATCH_BYTES = 1024
DELTA_FLUSH_SECONDS = 0.15
SYSTEM_PROMPT = (
    "You are the iTime assistant. Reply in Simplified Chinese unless the user asks "
    "for another language. This capability can only provide text. Never claim that "
    "you created, changed, completed, or deleted tasks, calendar events, plans, "
    "preferences, or memories. When a user requests such a change, explain the "
    "suggestion clearly; a separate confirmed proposal is required before iTime "
    "changes business data."
)


@dataclass(frozen=True)
class ConversationProviderResult:
    content: str
    provider_request_id: str | None
    http_status: int
    usage: dict | None


class ConversationProviderError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        stage: str,
        *,
        retryable: bool = False,
        provider_request_id: str | None = None,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.stage = stage
        self.retryable = retryable
        self.provider_request_id = provider_request_id
        self.http_status = http_status


class ConversationGenerationSuperseded(Exception):
    pass


def _provider_error(
    response: httpx.Response,
    request_id: str | None,
) -> ConversationProviderError | None:
    if response.status_code in {401, 403}:
        return ConversationProviderError(
            "ai_authentication_failed",
            "AI 服务配置无效，请联系管理员。",
            "authentication",
            provider_request_id=request_id,
            http_status=response.status_code,
        )
    if response.status_code == 429:
        return ConversationProviderError(
            "ai_rate_limited",
            "AI 服务暂时繁忙，系统会自动重试。",
            "upstream",
            retryable=True,
            provider_request_id=request_id,
            http_status=response.status_code,
        )
    if response.status_code >= 500:
        return ConversationProviderError(
            "ai_upstream_unavailable",
            "AI 服务暂时不可用，系统会自动重试。",
            "upstream",
            retryable=True,
            provider_request_id=request_id,
            http_status=response.status_code,
        )
    if not 200 <= response.status_code < 300:
        return ConversationProviderError(
            "ai_request_rejected",
            "AI 服务拒绝了请求，请联系管理员检查配置。",
            "upstream",
            provider_request_id=request_id,
            http_status=response.status_code,
        )
    return None


def _invalid_stream(
    request_id: str | None,
    http_status: int,
) -> ConversationProviderError:
    return ConversationProviderError(
        "conversation_stream_invalid",
        "AI 服务返回的文本流无法读取，请重新尝试。",
        "validation",
        provider_request_id=request_id,
        http_status=http_status,
    )


async def _iter_sse_data(response: httpx.Response) -> AsyncIterator[str]:
    data_lines: list[str] = []
    byte_count = 0
    async for line in response.aiter_lines():
        byte_count += len(line.encode("utf-8")) + 1
        if byte_count > MAX_CONVERSATION_RESPONSE_BYTES:
            raise ConversationProviderError(
                "conversation_output_too_large",
                "AI 回复超过可处理大小，请缩小问题范围后重试。",
                "validation",
                provider_request_id=response.headers.get("x-request-id"),
                http_status=response.status_code,
            )
        if line == "":
            if data_lines:
                yield "\n".join(data_lines)
                data_lines.clear()
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        yield "\n".join(data_lines)


def _event_delta(
    data: str,
    request_id: str | None,
    http_status: int,
) -> tuple[str | None, dict | None, bool]:
    if data.strip() == "[DONE]":
        return None, None, True
    try:
        payload = json.loads(data)
        choices = payload.get("choices")
        usage = payload.get("usage")
    except (TypeError, json.JSONDecodeError) as exc:
        raise _invalid_stream(request_id, http_status) from exc
    if usage is not None and not isinstance(usage, dict):
        raise _invalid_stream(request_id, http_status)
    if not choices:
        return None, usage, False
    if not isinstance(choices, list) or not isinstance(choices[0], dict):
        raise _invalid_stream(request_id, http_status)
    delta = choices[0].get("delta")
    if not isinstance(delta, dict):
        raise _invalid_stream(request_id, http_status)
    if delta.get("tool_calls") or delta.get("function_call"):
        raise ConversationProviderError(
            "conversation_tools_not_supported",
            "当前对话返回了不支持的工具调用，请重新描述需求。",
            "validation",
            provider_request_id=request_id,
            http_status=http_status,
        )
    content = delta.get("content")
    if content is None:
        return None, usage, False
    if not isinstance(content, str):
        raise _invalid_stream(request_id, http_status)
    return content, usage, False


def _json_content(
    body: bytes,
    request_id: str | None,
    http_status: int,
) -> tuple[str, dict | None]:
    try:
        payload = json.loads(body)
        choices = payload["choices"]
        content = choices[0]["message"]["content"]
        usage = payload.get("usage")
    except (
        KeyError,
        IndexError,
        TypeError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise _invalid_stream(request_id, http_status) from exc
    if not isinstance(content, str) or (
        usage is not None and not isinstance(usage, dict)
    ):
        raise _invalid_stream(request_id, http_status)
    return content, usage


async def _consume_sse(
    response: httpx.Response,
    *,
    on_delta: Callable[[str], Awaitable[bool]],
) -> tuple[str, dict | None]:
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    request_id = response.headers.get("x-request-id")
    usage: dict | None = None

    async def read_events() -> None:
        nonlocal usage
        try:
            async for data in _iter_sse_data(response):
                delta, event_usage, done = _event_delta(
                    data,
                    request_id,
                    response.status_code,
                )
                if event_usage is not None:
                    usage = event_usage
                if delta:
                    await queue.put(delta)
                if done:
                    break
        finally:
            await queue.put(None)

    reader = asyncio.create_task(read_events())
    content = ""
    pending = ""

    async def flush() -> None:
        nonlocal pending
        if not pending:
            return
        if not await on_delta(pending):
            raise ConversationGenerationSuperseded
        pending = ""

    try:
        while True:
            try:
                item = await asyncio.wait_for(
                    queue.get(),
                    timeout=DELTA_FLUSH_SECONDS,
                )
            except TimeoutError:
                await flush()
                continue
            if item is None:
                await reader
                await flush()
                break
            content += item
            pending += item
            if len(content) > MAX_CONVERSATION_OUTPUT_CHARS:
                raise ConversationProviderError(
                    "conversation_output_too_large",
                    "AI 回复超过可处理大小，请缩小问题范围后重试。",
                    "validation",
                    provider_request_id=request_id,
                    http_status=response.status_code,
                )
            if len(pending.encode("utf-8")) >= DELTA_BATCH_BYTES:
                await flush()
    finally:
        if not reader.done():
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
    return content, usage


async def stream_conversation_reply(
    config: ProviderConfig,
    settings: Settings,
    cipher: Fernet,
    *,
    messages: list[dict[str, str]],
    timeout_seconds: float,
    on_delta: Callable[[str], Awaitable[bool]],
) -> ConversationProviderResult:
    try:
        enforce_endpoint_policy(config.endpoint_url, settings)
    except EndpointPolicyError as exc:
        raise ConversationProviderError(
            "ai_endpoint_blocked",
            "AI 端点不符合当前网络安全策略。",
            "endpoint_policy",
        ) from exc
    endpoint_allowed = await resolved_endpoint_allowed(config.endpoint_url, settings)
    if endpoint_allowed is None:
        raise ConversationProviderError(
            "ai_upstream_unreachable",
            "暂时无法解析 AI 服务地址，请稍后重试。",
            "network",
            retryable=True,
        )
    if not endpoint_allowed:
        raise ConversationProviderError(
            "ai_endpoint_blocked",
            "AI 端点解析到未明确放行的内部网络地址。",
            "endpoint_policy",
        )
    try:
        api_key = cipher.decrypt(config.api_key_ciphertext).decode()
    except (InvalidToken, UnicodeDecodeError) as exc:
        raise ConversationProviderError(
            "ai_secret_unavailable",
            "AI 配置暂时无法读取，请联系管理员。",
            "configuration",
        ) from exc

    request_body = {
        "model": config.model,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}, *messages],
        "max_tokens": config.max_output_tokens,
        "temperature": 0,
        "stream": True,
    }
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
                async with client.stream(
                    "POST",
                    config.endpoint_url + "/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=request_body,
                ) as response:
                    request_id = (
                        response.headers.get("x-request-id")
                        or response.headers.get("request-id")
                    )
                    http_status = response.status_code
                    error = _provider_error(response, request_id)
                    if error is not None:
                        raise error
                    if "text/event-stream" in response.headers.get(
                        "content-type",
                        "",
                    ).lower():
                        content, usage = await _consume_sse(
                            response,
                            on_delta=on_delta,
                        )
                    else:
                        body = await response.aread()
                        if len(body) > MAX_CONVERSATION_RESPONSE_BYTES:
                            raise ConversationProviderError(
                                "conversation_output_too_large",
                                "AI 回复超过可处理大小，请缩小问题范围后重试。",
                                "validation",
                                provider_request_id=request_id,
                                http_status=http_status,
                            )
                        content, usage = _json_content(
                            body,
                            request_id,
                            response.status_code,
                        )
                        if len(content) > MAX_CONVERSATION_OUTPUT_CHARS:
                            raise ConversationProviderError(
                                "conversation_output_too_large",
                                "AI 回复超过可处理大小，请缩小问题范围后重试。",
                                "validation",
                                provider_request_id=request_id,
                                http_status=http_status,
                            )
                        if content and not await on_delta(content):
                            raise ConversationGenerationSuperseded
    except (ConversationProviderError, ConversationGenerationSuperseded):
        raise
    except (TimeoutError, httpx.TimeoutException) as exc:
        raise ConversationProviderError(
            "ai_upstream_timeout",
            "AI 服务响应超时，系统会自动重试。",
            "network",
            retryable=True,
            provider_request_id=request_id,
            http_status=http_status,
        ) from exc
    except httpx.HTTPError as exc:
        raise ConversationProviderError(
            "ai_upstream_unreachable",
            "暂时无法连接 AI 服务，系统会自动重试。",
            "network",
            retryable=True,
            provider_request_id=request_id,
            http_status=http_status,
        ) from exc
    finally:
        api_key = ""

    if not content.strip():
        raise ConversationProviderError(
            "conversation_output_empty",
            "AI 服务没有返回可显示内容，请重新尝试。",
            "validation",
            provider_request_id=request_id,
            http_status=http_status,
        )
    return ConversationProviderResult(
        content=content,
        provider_request_id=request_id,
        http_status=http_status or 200,
        usage=usage,
    )
