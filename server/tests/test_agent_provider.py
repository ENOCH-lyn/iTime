import json

import pytest

from itime.ai.conversation_provider import (
    AgentTaskPlan,
    ConversationProviderError,
    _agent_json_result,
    agent_request_body,
)
from itime.ai.provider import ProviderConfig


def provider() -> ProviderConfig:
    return ProviderConfig(
        endpoint_url="https://ai.example.com/v1",
        model="agent-model",
        api_key_ciphertext=b"encrypted",
        timeout_seconds=30,
        max_output_tokens=2048,
    )


def test_agent_request_exposes_only_confirmed_task_plan_tool() -> None:
    body = agent_request_body(
        provider(),
        [{"role": "user", "content": "明天上午学习两小时线性代数"}],
        {
            "current_date": "2026-10-08",
            "time_zone": "Asia/Shanghai",
            "tasks": [],
            "busy_calendar": [],
        },
    )

    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}
    assert body["tool_choice"] == "auto"
    assert [item["function"]["name"] for item in body["tools"]] == [
        "propose_itime_task_plan"
    ]
    assert "2026-10-08" in body["messages"][1]["content"]


def test_agent_response_accepts_text_or_task_plan() -> None:
    content, task_plan, usage = _agent_json_result(
        json.dumps(
            {
                "choices": [
                    {"message": {"role": "assistant", "content": "可以，先确认目标。"}}
                ],
                "usage": {"total_tokens": 12},
            }
        ).encode(),
        "request-1",
        200,
    )
    assert content == "可以，先确认目标。"
    assert task_plan is None
    assert usage == {"total_tokens": 12}

    arguments = {
        "assistant_message": "建议明天上午学习线性代数。",
        "task_title": "学习线性代数",
        "task_description": "",
        "estimated_minutes": 120,
        "local_date": "2026-10-09",
        "starts_at": "09:00:00",
        "ends_at": "11:00:00",
        "assumptions": ["上午按 09:00 开始。"],
    }
    content, task_plan, _ = _agent_json_result(
        json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "type": "function",
                                    "function": {
                                        "name": "propose_itime_task_plan",
                                        "arguments": json.dumps(arguments),
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        ).encode(),
        "request-2",
        200,
    )
    assert content == arguments["assistant_message"]
    assert task_plan == AgentTaskPlan.model_validate(arguments)


def test_agent_response_rejects_unaligned_or_unknown_tool() -> None:
    invalid = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "delete_everything",
                                "arguments": "{}",
                            }
                        }
                    ]
                }
            }
        ]
    }
    with pytest.raises(ConversationProviderError) as captured:
        _agent_json_result(json.dumps(invalid).encode(), "request-3", 200)
    assert captured.value.code == "conversation_tool_invalid"
