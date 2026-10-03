import json
import socket
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from cryptography.fernet import Fernet

from itime.ai.provider import ProviderConfig
from itime.config import Settings
from itime.planning.provider import (
    MAX_PLANNING_RESPONSE_BYTES,
    PLANNING_CONTENT_FALLBACK,
    PLANNING_TOOL_NAME,
    PlanningGenerationSuperseded,
    PlanningProviderError,
    planning_request_body,
    stream_planning_reply,
)


def valid_arguments(*, summary: str = "已生成计划") -> dict:
    return {
        "summary": summary,
        "assumptions": [],
        "estimates": [],
        "blocks": [],
        "unscheduled_items": [],
    }


def completion(
    *,
    content: str | None = "先处理最重要的任务。",
    tool_calls: list[dict] | None = None,
    usage: object | None = None,
    extra: dict | None = None,
) -> bytes:
    if tool_calls is None:
        tool_calls = [
            {
                "id": "call-plan",
                "type": "function",
                "function": {
                    "name": PLANNING_TOOL_NAME,
                    "arguments": json.dumps(valid_arguments(), ensure_ascii=False),
                },
            }
        ]
    payload = {
        "choices": [{"message": {"content": content, "tool_calls": tool_calls}}],
        "usage": usage,
        **(extra or {}),
    }
    return json.dumps(payload, ensure_ascii=False).encode()


def sse_event(payload: object) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


@dataclass
class UpstreamState:
    body: bytes = field(default_factory=completion)
    content_type: str = "application/json"
    status: int = 200
    delay_seconds: float = 0
    requests: list[dict] = field(default_factory=list)


def upstream_handler(state: UpstreamState):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            request_body = self.rfile.read(length)
            state.requests.append(
                {
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "body": json.loads(request_body),
                }
            )
            if state.delay_seconds:
                time.sleep(state.delay_seconds)
            try:
                self.send_response(state.status)
                self.send_header("Content-Type", state.content_type)
                self.send_header("x-request-id", "planning-request-1")
                self.send_header("Content-Length", str(len(state.body)))
                self.end_headers()
                self.wfile.write(state.body)
            except OSError:
                pass

        def log_message(self, format: str, *args) -> None:
            pass

    return Handler


@pytest.fixture
def upstream():
    state = UpstreamState()
    server = ThreadingHTTPServer(("127.0.0.1", 0), upstream_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state, f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def provider_context(upstream):
    state, endpoint = upstream
    cipher = Fernet(Fernet.generate_key())
    config = ProviderConfig(
        endpoint_url=endpoint,
        model="planning-model",
        api_key_ciphertext=cipher.encrypt(b"sk-planning-secret"),
        timeout_seconds=2,
        max_output_tokens=2048,
    )
    settings = Settings(
        environment="test",
        database_url="postgresql+psycopg://itime:itime@127.0.0.1/itime_test",
        encryption_key=Fernet.generate_key().decode(),
    )
    return state, config, settings, cipher


def test_planning_request_body_fixes_tool_choice_and_uses_model_schema(
    provider_context,
) -> None:
    _, config, _, _ = provider_context
    messages = [{"role": "user", "content": "请规划今天"}]

    body = planning_request_body(config, messages)

    assert body["model"] == "planning-model"
    assert body["messages"][-1] == messages[0]
    assert body["max_tokens"] == 2048
    assert body["temperature"] == 0
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}
    assert body["parallel_tool_calls"] is False
    assert body["tool_choice"] == {
        "type": "function",
        "function": {"name": PLANNING_TOOL_NAME},
    }
    assert len(body["tools"]) == 1
    function = body["tools"][0]["function"]
    assert function["name"] == PLANNING_TOOL_NAME
    assert function["parameters"]["title"] == "PlanningToolOutput"
    assert set(function["parameters"]["required"]) == {"summary"}
    assert function["parameters"]["additionalProperties"] is False


@pytest.mark.asyncio
async def test_streaming_reply_separates_content_and_fragmented_arguments(
    provider_context,
) -> None:
    state, config, settings, cipher = provider_context
    arguments = json.dumps(valid_arguments(summary="流式计划"), ensure_ascii=False)
    midpoint = len(arguments) // 2
    usage = {"prompt_tokens": 31, "completion_tokens": 17, "total_tokens": 48}
    state.content_type = "text/event-stream; charset=utf-8"
    state.body = b"".join(
        [
            sse_event(
                {
                    "choices": [
                        {
                            "delta": {
                                "role": "assistant",
                                "content": "我已",
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call-plan",
                                        "type": "function",
                                        "function": {
                                            "name": PLANNING_TOOL_NAME,
                                            "arguments": arguments[:midpoint],
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                }
            ),
            sse_event(
                {
                    "choices": [
                        {
                            "delta": {
                                "content": "整理完成。",
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "function": {
                                            "arguments": arguments[midpoint:],
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                }
            ),
            sse_event({"choices": [], "usage": usage}),
            b"data: [DONE]\n\n",
        ]
    )
    deltas: list[str] = []
    started = 0

    async def on_request_started() -> bool:
        nonlocal started
        started += 1
        return True

    async def on_delta(delta: str) -> bool:
        deltas.append(delta)
        return True

    result = await stream_planning_reply(
        config,
        settings,
        cipher,
        messages=[{"role": "user", "content": "安排今天"}],
        timeout_seconds=2,
        on_delta=on_delta,
        on_request_started=on_request_started,
    )

    assert started == 1
    assert deltas == ["我已", "整理完成。"]
    assert result.content == "我已整理完成。"
    assert result.output.summary == "流式计划"
    assert result.provider_request_id == "planning-request-1"
    assert result.http_status == 200
    assert result.usage == usage
    logged = json.loads(result.response_body)
    assert logged == {
        "content": "我已整理完成。",
        "tool_call": {
            "name": PLANNING_TOOL_NAME,
            "arguments": valid_arguments(summary="流式计划"),
        },
    }
    assert "call-plan" not in result.response_body
    assert state.requests[0]["authorization"] == "Bearer sk-planning-secret"
    assert state.requests[0]["path"] == "/v1/chat/completions"


@pytest.mark.asyncio
async def test_json_reply_uses_explicit_fallback_for_empty_content(
    provider_context,
) -> None:
    state, config, settings, cipher = provider_context
    usage = {"input_tokens": 8, "output_tokens": 9}
    state.body = completion(content=None, usage=usage, extra={"private": "drop-me"})
    deltas: list[str] = []

    async def on_delta(delta: str) -> bool:
        deltas.append(delta)
        return True

    result = await stream_planning_reply(
        config,
        settings,
        cipher,
        messages=[],
        timeout_seconds=2,
        on_delta=on_delta,
        on_request_started=_allow,
    )

    assert result.content == PLANNING_CONTENT_FALLBACK
    assert deltas == [PLANNING_CONTENT_FALLBACK]
    assert result.usage == usage
    assert "drop-me" not in result.response_body
    assert len(result.response_body.encode()) <= MAX_PLANNING_RESPONSE_BYTES


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_calls",
    [
        [],
        [
            {
                "type": "function",
                "function": {
                    "name": "unknown_tool",
                    "arguments": json.dumps(valid_arguments()),
                },
            }
        ],
        [
            {
                "type": "function",
                "function": {
                    "name": PLANNING_TOOL_NAME,
                    "arguments": json.dumps(valid_arguments()),
                },
            },
            {
                "type": "function",
                "function": {
                    "name": PLANNING_TOOL_NAME,
                    "arguments": json.dumps(valid_arguments()),
                },
            },
        ],
    ],
    ids=["missing", "unknown", "multiple"],
)
async def test_json_reply_requires_exactly_one_named_tool(
    provider_context,
    tool_calls: list[dict],
) -> None:
    state, config, settings, cipher = provider_context
    usage = {"total_tokens": 23}
    state.body = completion(tool_calls=tool_calls, usage=usage)

    with pytest.raises(PlanningProviderError) as caught:
        await _request(config, settings, cipher)

    assert caught.value.stage == "validation"
    assert caught.value.provider_request_id == "planning-request-1"
    assert caught.value.http_status == 200
    assert caught.value.usage == usage


@pytest.mark.asyncio
async def test_streaming_reply_rejects_second_tool_index(provider_context) -> None:
    state, config, settings, cipher = provider_context
    arguments = json.dumps(valid_arguments())
    state.content_type = "text/event-stream"
    state.body = b"".join(
        [
            sse_event(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "function": {
                                            "name": PLANNING_TOOL_NAME,
                                            "arguments": arguments,
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                }
            ),
            sse_event(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 1,
                                        "function": {
                                            "name": PLANNING_TOOL_NAME,
                                            "arguments": arguments,
                                        },
                                    }
                                ]
                            }
                        }
                    ],
                    "usage": {"total_tokens": 44},
                }
            ),
        ]
    )

    with pytest.raises(PlanningProviderError) as caught:
        await _request(config, settings, cipher)

    assert caught.value.stage == "validation"
    assert caught.value.usage == {"total_tokens": 44}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        "{",
        json.dumps({**valid_arguments(), "unexpected": True}),
        json.dumps({**valid_arguments(), "summary": " "}),
    ],
    ids=["invalid-json", "extra-field", "blank-summary"],
)
async def test_invalid_tool_arguments_preserve_usage_and_metadata(
    provider_context,
    arguments: str,
) -> None:
    state, config, settings, cipher = provider_context
    usage = {"prompt_tokens": 12, "completion_tokens": 4}
    state.body = completion(
        tool_calls=[
            {
                "type": "function",
                "function": {
                    "name": PLANNING_TOOL_NAME,
                    "arguments": arguments,
                },
            }
        ],
        usage=usage,
    )

    with pytest.raises(PlanningProviderError) as caught:
        await _request(config, settings, cipher)

    assert caught.value.code == "planning_output_invalid"
    assert caught.value.provider_request_id == "planning-request-1"
    assert caught.value.http_status == 200
    assert caught.value.usage == usage


@pytest.mark.asyncio
async def test_response_is_bounded_to_one_mib(provider_context) -> None:
    state, config, settings, cipher = provider_context
    state.body = b"x" * (MAX_PLANNING_RESPONSE_BYTES + 1)

    with pytest.raises(PlanningProviderError) as caught:
        await _request(config, settings, cipher)

    assert caught.value.code == "planning_output_too_large"
    assert caught.value.provider_request_id == "planning-request-1"
    assert caught.value.http_status == 200


@pytest.mark.asyncio
async def test_request_started_cancellation_happens_before_http_request(
    provider_context,
) -> None:
    state, config, settings, cipher = provider_context

    with pytest.raises(PlanningGenerationSuperseded):
        await stream_planning_reply(
            config,
            settings,
            cipher,
            messages=[],
            timeout_seconds=2,
            on_delta=_allow_delta,
            on_request_started=_deny,
        )

    assert state.requests == []


@pytest.mark.asyncio
async def test_delta_cancellation_stops_success_result(provider_context) -> None:
    state, config, settings, cipher = provider_context
    state.body = completion(content="不能继续保存")

    with pytest.raises(PlanningGenerationSuperseded):
        await stream_planning_reply(
            config,
            settings,
            cipher,
            messages=[],
            timeout_seconds=2,
            on_delta=_deny_delta,
            on_request_started=_allow,
        )

    assert len(state.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "retryable", "stage"),
    [
        (401, False, "authentication"),
        (429, True, "upstream"),
        (503, True, "upstream"),
        (422, False, "upstream"),
    ],
)
async def test_http_errors_keep_request_metadata(
    provider_context,
    status: int,
    retryable: bool,
    stage: str,
) -> None:
    state, config, settings, cipher = provider_context
    state.status = status
    state.body = b'{"private":"must-not-be-exposed"}'

    with pytest.raises(PlanningProviderError) as caught:
        await _request(config, settings, cipher)

    assert caught.value.retryable is retryable
    assert caught.value.stage == stage
    assert caught.value.provider_request_id == "planning-request-1"
    assert caught.value.http_status == status
    assert "must-not-be-exposed" not in caught.value.message


@pytest.mark.asyncio
async def test_endpoint_policy_secret_timeout_and_network_errors(
    provider_context,
    monkeypatch,
) -> None:
    state, config, settings, cipher = provider_context
    blocked_settings = settings.model_copy(update={"environment": "development"})
    with pytest.raises(PlanningProviderError) as blocked:
        await _request(config, blocked_settings, cipher)
    assert blocked.value.code == "ai_endpoint_blocked"
    assert blocked.value.stage == "endpoint_policy"
    assert state.requests == []

    invalid_config = ProviderConfig(
        endpoint_url=config.endpoint_url,
        model=config.model,
        api_key_ciphertext=b"not-a-valid-token",
        timeout_seconds=config.timeout_seconds,
        max_output_tokens=config.max_output_tokens,
    )
    with pytest.raises(PlanningProviderError) as secret:
        await _request(invalid_config, settings, cipher)
    assert secret.value.code == "ai_secret_unavailable"
    assert secret.value.stage == "configuration"
    assert state.requests == []

    state.delay_seconds = 0.2
    with pytest.raises(PlanningProviderError) as timeout:
        await stream_planning_reply(
            config,
            settings,
            cipher,
            messages=[],
            timeout_seconds=0.05,
            on_delta=_allow_delta,
            on_request_started=_allow,
        )
    assert timeout.value.code == "ai_upstream_timeout"
    assert timeout.value.retryable is True

    async def unresolved(_endpoint_url: str, _settings: Settings) -> None:
        return None

    monkeypatch.setattr(
        "itime.planning.provider.resolved_endpoint_allowed",
        unresolved,
    )
    with pytest.raises(PlanningProviderError) as network:
        await _request(config, settings, cipher)
    assert network.value.code == "ai_upstream_unreachable"
    assert network.value.retryable is True
    assert network.value.stage == "network"


@pytest.mark.asyncio
async def test_connection_failure_is_retryable(provider_context) -> None:
    _, config, settings, cipher = provider_context
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        unavailable_port = sock.getsockname()[1]
    unavailable = ProviderConfig(
        endpoint_url=f"http://127.0.0.1:{unavailable_port}/v1",
        model=config.model,
        api_key_ciphertext=config.api_key_ciphertext,
        timeout_seconds=config.timeout_seconds,
        max_output_tokens=config.max_output_tokens,
    )

    with pytest.raises(PlanningProviderError) as caught:
        await _request(unavailable, settings, cipher)

    assert caught.value.code == "ai_upstream_unreachable"
    assert caught.value.retryable is True
    assert caught.value.stage == "network"


async def _request(config, settings, cipher):
    return await stream_planning_reply(
        config,
        settings,
        cipher,
        messages=[{"role": "user", "content": "安排计划"}],
        timeout_seconds=2,
        on_delta=_allow_delta,
        on_request_started=_allow,
    )


async def _allow() -> bool:
    return True


async def _deny() -> bool:
    return False


async def _allow_delta(_delta: str) -> bool:
    return True


async def _deny_delta(_delta: str) -> bool:
    return False
