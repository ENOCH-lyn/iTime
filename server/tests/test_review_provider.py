import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from cryptography.fernet import Fernet

from itime.ai.provider import ProviderConfig
from itime.config import Settings
from itime.review.provider import (
    REVIEW_QUESTION_TOOL,
    REVIEW_REPORT_TOOL,
    ReviewGenerationSuperseded,
    ReviewProviderError,
    review_request_body,
    stream_review_reply,
)


def report_arguments() -> dict:
    return {
        "summary": "今天完成了核心任务。",
        "facts": [
            {
                "source_key": "focus:00000000-0000-0000-0000-000000000001",
                "text": "完成了 30 分钟专注。",
            }
        ],
        "user_statements": ["下午补充整理了资料。"],
        "insights": [
            {
                "text": "较完整的时间段可能更适合这类任务。",
                "basis": "ai_inference",
            }
        ],
        "next_actions": ["明早继续整理资料。"],
        "uncertainties": [],
        "memory_candidates": [
            {
                "kind": "preference",
                "title": "深度工作时段",
                "content": "上午更适合持续思考。",
                "rationale": "用户在复盘中明确说明。",
                "source_keys": ["user:supplemental_facts"],
            }
        ],
    }


def completion(tool_name: str, arguments: dict, *, usage=None) -> bytes:
    return json.dumps(
        {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "review-call",
                                "type": "function",
                                "function": {
                                    "name": tool_name,
                                    "arguments": json.dumps(
                                        arguments,
                                        ensure_ascii=False,
                                    ),
                                },
                            }
                        ],
                    }
                }
            ],
            "usage": usage,
        },
        ensure_ascii=False,
    ).encode()


def sse_event(payload: object) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


@dataclass
class UpstreamState:
    body: bytes = field(
        default_factory=lambda: completion(
            REVIEW_QUESTION_TOOL,
            {"question": "今天最顺利的是哪一段？"},
        )
    )
    content_type: str = "application/json"
    status: int = 200
    requests: list[dict] = field(default_factory=list)


def upstream_handler(state: UpstreamState):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            state.requests.append(json.loads(self.rfile.read(length)))
            self.send_response(state.status)
            self.send_header("Content-Type", state.content_type)
            self.send_header("x-request-id", "review-request-1")
            self.send_header("Content-Length", str(len(state.body)))
            self.end_headers()
            self.wfile.write(state.body)

        def log_message(self, format: str, *args) -> None:
            pass

    return Handler


@pytest.fixture
def provider_context():
    state = UpstreamState()
    server = ThreadingHTTPServer(("127.0.0.1", 0), upstream_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    cipher = Fernet(Fernet.generate_key())
    config = ProviderConfig(
        endpoint_url=f"http://127.0.0.1:{server.server_port}/v1",
        model="review-model",
        api_key_ciphertext=cipher.encrypt(b"review-secret"),
        timeout_seconds=2,
        max_output_tokens=2048,
    )
    settings = Settings(
        environment="test",
        database_url="postgresql+psycopg://itime:itime@127.0.0.1/itime_test",
        encryption_key=Fernet.generate_key().decode(),
    )
    try:
        yield state, config, settings, cipher
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


async def allow() -> bool:
    return True


async def accept_delta(_: str) -> bool:
    return True


def test_review_request_body_uses_purpose_specific_single_tool(provider_context):
    _, config, _, _ = provider_context
    question = review_request_body(config, [], "question")
    report = review_request_body(config, [], "report")

    assert question["tool_choice"]["function"]["name"] == REVIEW_QUESTION_TOOL
    assert report["tool_choice"]["function"]["name"] == REVIEW_REPORT_TOOL
    assert question["parallel_tool_calls"] is False
    assert report["stream"] is True
    assert question["tools"][0]["function"]["parameters"]["additionalProperties"] is False


@pytest.mark.asyncio
async def test_json_question_response_is_validated(provider_context):
    state, config, settings, cipher = provider_context
    usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    state.body = completion(
        REVIEW_QUESTION_TOOL,
        {"question": "哪项安排最需要调整？"},
        usage=usage,
    )
    result = await stream_review_reply(
        config,
        settings,
        cipher,
        messages=[{"role": "user", "content": "{}"}],
        purpose="question",
        timeout_seconds=2,
        on_delta=accept_delta,
        on_request_started=allow,
    )

    assert result.output.question == "哪项安排最需要调整？"
    assert result.usage == usage
    assert result.provider_request_id == "review-request-1"
    assert json.loads(result.response_body)["tool_call"]["name"] == REVIEW_QUESTION_TOOL


@pytest.mark.asyncio
async def test_streamed_report_joins_fragmented_tool_arguments(provider_context):
    state, config, settings, cipher = provider_context
    arguments = json.dumps(report_arguments(), ensure_ascii=False)
    midpoint = len(arguments) // 2
    usage = {"prompt_tokens": 40, "completion_tokens": 30, "total_tokens": 70}
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
                                        "id": "review-call",
                                        "type": "function",
                                        "function": {
                                            "name": REVIEW_REPORT_TOOL,
                                            "arguments": arguments[:midpoint],
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
                                        "index": 0,
                                        "function": {
                                            "arguments": arguments[midpoint:],
                                        },
                                    }
                                ]
                            }
                        }
                    ],
                    "usage": usage,
                }
            ),
            b"data: [DONE]\n\n",
        ]
    )
    result = await stream_review_reply(
        config,
        settings,
        cipher,
        messages=[],
        purpose="report",
        timeout_seconds=2,
        on_delta=accept_delta,
        on_request_started=allow,
    )

    assert result.output.summary == "今天完成了核心任务。"
    assert result.output.memory_candidates[0].kind == "preference"
    assert result.usage == usage


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("unknown", {"question": "错误工具"}),
        (REVIEW_QUESTION_TOOL, {"question": "   "}),
        (REVIEW_REPORT_TOOL, {"summary": "缺少必需数组"}),
    ],
)
async def test_invalid_review_tool_output_is_rejected(
    provider_context,
    tool_name,
    arguments,
):
    state, config, settings, cipher = provider_context
    state.body = completion(tool_name, arguments)

    with pytest.raises(ReviewProviderError) as caught:
        await stream_review_reply(
            config,
            settings,
            cipher,
            messages=[],
            purpose="question" if tool_name != REVIEW_REPORT_TOOL else "report",
            timeout_seconds=2,
            on_delta=accept_delta,
            on_request_started=allow,
        )

    assert caught.value.stage == "validation"
    assert caught.value.provider_request_id == "review-request-1"


@pytest.mark.asyncio
async def test_review_request_can_be_superseded_before_network(provider_context):
    state, config, settings, cipher = provider_context

    async def reject_start() -> bool:
        return False

    with pytest.raises(ReviewGenerationSuperseded):
        await stream_review_reply(
            config,
            settings,
            cipher,
            messages=[],
            purpose="question",
            timeout_seconds=2,
            on_delta=accept_delta,
            on_request_started=reject_start,
        )

    assert state.requests == []


@pytest.mark.asyncio
async def test_review_upstream_failure_preserves_retry_metadata(provider_context):
    state, config, settings, cipher = provider_context
    state.status = 503
    state.body = b'{"private":"not exposed"}'

    with pytest.raises(ReviewProviderError) as caught:
        await stream_review_reply(
            config,
            settings,
            cipher,
            messages=[],
            purpose="report",
            timeout_seconds=2,
            on_delta=accept_delta,
            on_request_started=allow,
        )

    assert caught.value.code == "ai_upstream_unavailable"
    assert caught.value.retryable is True
    assert caught.value.provider_request_id == "review-request-1"
    assert "private" not in caught.value.message
