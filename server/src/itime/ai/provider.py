import asyncio
import base64
import ipaddress
import json
import socket
from dataclasses import dataclass
from importlib import resources
from urllib.parse import urlsplit

import httpx
from cryptography.fernet import Fernet, InvalidToken

from itime.config import Settings

MAX_TEST_RESPONSE_BYTES = 1024 * 1024
PLANNING_TEST_TOOL = "submit_itime_planning_test"
REVIEW_TEST_TOOL = "submit_itime_review_test"
SYNTHETIC_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAASsAAABACAIAAADNgsbOAAABR0lEQVR42u3cwQ2DMAxA"
    "0czADlzZfztYgUiO41jvq9emisnjUCHGK2lfwwgkAiUCJREoESiJQIlASQRKBEoiUCJQ"
    "EoESgZIyBF738/8TuFTaD00tNTXftOGsOyI1L0qbLRBIIIEEEkgggQQSSCCBBBJIYOip"
    "arDy1JR3UU8TGHiLWbfUEVsgkEACCSSQQAIJJJBAAgkkkEACo4dO4NLj22ALBBJIIIEE"
    "EkgggQQSSCCBBBJIYA2Bux5D67dBAgkkkEACCSSQQAIJJJBAAgkksIvAwO+2/y9016wI"
    "JJBAAgkkkEACCSSQQAIJJJBAAgmscfUJJJBAAgkkkEACCSSQQAIJJLCYwLSHidYt5Z3Z"
    "BSfpndkEEkgggQQSSCCBBBJIIIEEEigpMQIlAiUCJREoESiJQIlASQRKBEoiUCJQEoES"
    "gZIIlE7uA2qfPFsoP349AAAAAElFTkSuQmCC"
)


@dataclass(frozen=True)
class ProviderConfig:
    endpoint_url: str
    model: str
    api_key_ciphertext: bytes
    timeout_seconds: int
    max_output_tokens: int | None


@dataclass(frozen=True)
class ProviderTestResult:
    succeeded: bool
    error_code: str | None = None
    error_stage: str | None = None
    message: str | None = None
    status: int = 200
    retryable: bool = False


class EndpointPolicyError(ValueError):
    pass


def _loopback_host(hostname: str) -> bool:
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def enforce_endpoint_policy(endpoint_url: str, settings: Settings) -> None:
    parsed = urlsplit(endpoint_url)
    if parsed.scheme == "http":
        if settings.environment != "test" or not _loopback_host(parsed.hostname or ""):
            raise EndpointPolicyError(
                "非测试环境只允许 HTTPS；测试 HTTP 端点必须使用回环地址。"
            )


async def resolved_endpoint_allowed(
    endpoint_url: str, settings: Settings
) -> bool | None:
    parsed = urlsplit(endpoint_url)
    hostname = parsed.hostname or ""
    if hostname.lower() in settings.ai_private_endpoint_hosts:
        return True
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        results = await asyncio.to_thread(
            socket.getaddrinfo,
            hostname,
            port,
            type=socket.SOCK_STREAM,
        )
    except OSError:
        return None
    addresses = {ipaddress.ip_address(item[4][0]) for item in results}
    if settings.environment == "test" and parsed.scheme == "http":
        return bool(addresses) and all(address.is_loopback for address in addresses)
    return bool(addresses) and all(address.is_global for address in addresses)


def _failure(
    code: str,
    stage: str,
    message: str,
    *,
    status: int,
    retryable: bool = False,
) -> ProviderTestResult:
    return ProviderTestResult(
        succeeded=False,
        error_code=code,
        error_stage=stage,
        message=message,
        status=status,
        retryable=retryable,
    )


def _valid_chat_response(body: bytes, capability: str) -> bool:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return False
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return False
    if capability in {"planning", "review"}:
        expected_tool = (
            PLANNING_TEST_TOOL
            if capability == "planning"
            else REVIEW_TEST_TOOL
        )
        tool_calls = message.get("tool_calls")
        if (
            not isinstance(tool_calls, list)
            or len(tool_calls) != 1
            or not isinstance(tool_calls[0], dict)
            or tool_calls[0].get("type") != "function"
        ):
            return False
        function = tool_calls[0].get("function")
        if (
            not isinstance(function, dict)
            or function.get("name") != expected_tool
            or not isinstance(function.get("arguments"), str)
        ):
            return False
        try:
            arguments = json.loads(function["arguments"])
        except json.JSONDecodeError:
            return False
        return arguments == {"marker": "09:00"}
    content = message.get("content")
    if not isinstance(content, str):
        return False
    if capability == "conversation":
        return content.strip() == "ITIME_CHAT_OK"
    try:
        recognized = json.loads(content)
    except json.JSONDecodeError:
        return False
    return recognized == {"marker": "09:00"}


def _valid_transcription_response(body: bytes) -> bool:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    text = payload.get("text")
    return (
        isinstance(text, str)
        and 1 <= len(text.strip()) <= 4000
        and "\x00" not in text
    )


async def _network_test(
    config: ProviderConfig,
    settings: Settings,
    api_key: str,
    capability: str,
) -> ProviderTestResult:
    endpoint_allowed = await resolved_endpoint_allowed(config.endpoint_url, settings)
    if endpoint_allowed is None:
        return _failure(
            "ai_config_test_unreachable",
            "network",
            "无法解析上游端点，请检查地址和网络。",
            status=502,
            retryable=True,
        )
    if not endpoint_allowed:
        return _failure(
            "ai_config_test_endpoint_blocked",
            "endpoint_policy",
            "端点解析到未明确放行的内部网络地址。",
            status=422,
        )

    if capability != "transcription" and config.max_output_tokens is None:
        return _failure(
            "ai_config_test_invalid",
            "当前能力缺少最大输出 Token 配置。",
            "configuration",
            status=422,
        )

    if capability == "transcription":
        request_body = None
        request_data = {
            "model": config.model,
            "response_format": "json",
        }
        request_files = {
            "file": (
                "itime-voice-check.wav",
                resources.files("itime.ai")
                .joinpath("assets/transcription_probe.wav")
                .read_bytes(),
                "audio/wav",
            )
        }
        endpoint = config.endpoint_url + "/audio/transcriptions"
    elif capability == "conversation":
        request_body = {
            "model": config.model,
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "This is a synthetic text endpoint check. "
                        "Return exactly ITIME_CHAT_OK and nothing else. "
                        "No user data is included."
                    ),
                }
            ],
            "max_tokens": min(config.max_output_tokens, 32),
            "temperature": 0,
        }
        request_data = None
        request_files = None
        endpoint = config.endpoint_url + "/chat/completions"
    elif capability in {"planning", "review"}:
        test_tool = (
            PLANNING_TEST_TOOL
            if capability == "planning"
            else REVIEW_TEST_TOOL
        )
        request_body = {
            "model": config.model,
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "This is a synthetic planning tool check with no user data. "
                        f"Call {test_tool} exactly once with marker 09:00. "
                        "Do not call any other tool."
                    ),
                }
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": test_tool,
                        "description": "Return the synthetic iTime capability marker.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "marker": {
                                    "type": "string",
                                    "const": "09:00",
                                }
                            },
                            "required": ["marker"],
                            "additionalProperties": False,
                        },
                    },
                }
            ],
            "tool_choice": {
                "type": "function",
                "function": {"name": test_tool},
            },
            "parallel_tool_calls": False,
            "max_tokens": min(config.max_output_tokens, 64),
            "temperature": 0,
        }
        request_data = None
        request_files = None
        endpoint = config.endpoint_url + "/chat/completions"
    else:
        request_body = {
            "model": config.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "This is a synthetic vision endpoint check. Read the time "
                                "shown in the image. Return only a JSON object with exactly "
                                'one string field named "marker". The expected visible '
                                "value is not provided in this text. Do not infer or include "
                                "user data."
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": (
                                    "data:image/png;base64,"
                                    + base64.b64encode(SYNTHETIC_PNG).decode()
                                )
                            },
                        },
                    ],
                }
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": config.max_output_tokens,
        }
        request_data = None
        request_files = None
        endpoint = config.endpoint_url + "/chat/completions"
    timeout = httpx.Timeout(float(config.timeout_seconds))
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            trust_env=False,
            follow_redirects=False,
        ) as client:
            async with client.stream(
                "POST",
                endpoint,
                headers={
                    "Authorization": f"Bearer {api_key}",
                },
                json=request_body,
                data=request_data,
                files=request_files,
            ) as response:
                if response.status_code in {401, 403}:
                    return _failure(
                        "ai_config_test_authentication_failed",
                        "authentication",
                        "上游拒绝了配置凭据，请检查 Key。",
                        status=422,
                    )
                if response.status_code == 429:
                    return _failure(
                        "ai_config_test_rate_limited",
                        "upstream",
                        "上游暂时限流，请稍后重新测试。",
                        status=502,
                        retryable=True,
                    )
                if response.status_code >= 500:
                    return _failure(
                        "ai_config_test_upstream_unavailable",
                        "upstream",
                        "上游服务暂时不可用，请稍后重新测试。",
                        status=502,
                        retryable=True,
                    )
                if not 200 <= response.status_code < 300:
                    return _failure(
                        "ai_config_test_rejected",
                        "upstream",
                        "上游拒绝了测试请求，请检查端点、模型和参数。",
                        status=422,
                    )
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_TEST_RESPONSE_BYTES:
                        return _failure(
                            "ai_config_test_invalid_response",
                            "response",
                            "上游响应格式不符合 OpenAI 兼容协议。",
                            status=502,
                        )
                    chunks.append(chunk)
    except httpx.TimeoutException:
        return _failure(
            "ai_config_test_timeout",
            "network",
            "上游测试请求超时，请检查端点或调整时限。",
            status=504,
            retryable=True,
        )
    except httpx.HTTPError:
        return _failure(
            "ai_config_test_unreachable",
            "network",
            "无法连接上游端点，请检查地址和网络。",
            status=502,
            retryable=True,
        )

    valid_response = (
        _valid_transcription_response(b"".join(chunks))
        if capability == "transcription"
        else _valid_chat_response(b"".join(chunks), capability)
    )
    if not valid_response:
        return _failure(
            "ai_config_test_invalid_response",
            "response",
            "上游响应格式不符合 OpenAI 兼容协议。",
            status=502,
        )
    return ProviderTestResult(succeeded=True)


async def test_openai_compatible(
    config: ProviderConfig,
    settings: Settings,
    cipher: Fernet,
    *,
    capability: str,
) -> ProviderTestResult:
    try:
        enforce_endpoint_policy(config.endpoint_url, settings)
    except EndpointPolicyError:
        return _failure(
            "ai_config_test_endpoint_blocked",
            "endpoint_policy",
            "端点不符合当前环境的网络安全策略。",
            status=422,
        )
    try:
        api_key = cipher.decrypt(config.api_key_ciphertext).decode()
    except (InvalidToken, UnicodeDecodeError):
        return _failure(
            "ai_config_test_secret_unavailable",
            "configuration",
            "配置密钥暂时无法读取，请检查服务端加密密钥。",
            status=503,
            retryable=True,
        )

    try:
        async with asyncio.timeout(float(config.timeout_seconds)):
            return await _network_test(config, settings, api_key, capability)
    except TimeoutError:
        return _failure(
            "ai_config_test_timeout",
            "network",
            "上游测试请求超时，请检查端点或调整时限。",
            status=504,
            retryable=True,
        )
    finally:
        api_key = ""
