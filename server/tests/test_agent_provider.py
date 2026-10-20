import json
from uuid import uuid4

import pytest

from itime.ai.conversation_provider import (
    AgentCourseDelete,
    AgentTaskComplete,
    AgentTaskPlan,
    AgentTaskUpdate,
    AgentTimetableImport,
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
        "propose_itime_task_plan",
        "propose_itime_timetable_import",
        "propose_itime_task_update",
        "propose_itime_task_complete",
        "propose_itime_task_delete",
        "propose_itime_course_delete",
        "propose_itime_course_update",
        "propose_itime_occurrence_exception",
    ]
    assert "2026-10-08" in body["messages"][1]["content"]


def test_agent_response_accepts_text_or_task_plan() -> None:
    (
        content,
        task_plan,
        timetable_import,
        task_update,
        task_complete,
        task_delete,
        course_update,
        course_delete,
        occurrence_exception,
        usage,
    ) = _agent_json_result(
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
    assert timetable_import is None
    assert task_update is None
    assert task_complete is None
    assert task_delete is None
    assert course_delete is None
    assert course_update is None
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
    (
        content,
        task_plan,
        timetable_import,
        task_update,
        task_complete,
        task_delete,
        course_update,
        course_delete,
        occurrence_exception,
        _,
    ) = _agent_json_result(
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
    assert timetable_import is None
    assert task_update is None
    assert task_complete is None
    assert task_delete is None
    assert course_delete is None
    assert course_update is None

    import_arguments = {
        "assistant_message": "我已整理为待确认的课表导入。",
        "source_type": "text",
        "content": "每周一、三 08:00 高等数学，持续到 2027-01-10",
    }
    (
        content,
        task_plan,
        timetable_import,
        task_update,
        task_complete,
        task_delete,
        course_update,
        course_delete,
        occurrence_exception,
        _,
    ) = _agent_json_result(
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
                                        "name": (
                                            "propose_itime_timetable_import"
                                        ),
                                        "arguments": json.dumps(
                                            import_arguments,
                                            ensure_ascii=False,
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
            ensure_ascii=False,
        ).encode(),
        "request-3",
        200,
    )
    assert content == import_arguments["assistant_message"]
    assert task_plan is None
    assert timetable_import == AgentTimetableImport.model_validate(
        import_arguments
    )
    assert task_update is None
    assert task_complete is None
    assert task_delete is None
    assert course_delete is None
    assert course_update is None

    task_id = uuid4()
    update_arguments = {
        "assistant_message": "把预计用时改为 90 分钟。",
        "task_id": str(task_id),
        "estimated_minutes": 90,
    }
    (
        content,
        task_plan,
        timetable_import,
        task_update,
        task_complete,
        task_delete,
        course_update,
        course_delete,
        occurrence_exception,
        _,
    ) = _agent_json_result(
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
                                        "name": "propose_itime_task_update",
                                        "arguments": json.dumps(update_arguments),
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        ).encode(),
        "request-4",
        200,
    )
    assert content == update_arguments["assistant_message"]
    assert task_update == AgentTaskUpdate.model_validate(update_arguments)
    assert task_plan is None
    assert timetable_import is None
    assert task_complete is None
    assert task_delete is None
    assert course_delete is None
    assert course_update is None

    complete_arguments = {
        "assistant_message": "完成后告诉我。",
        "task_id": str(task_id),
    }
    (
        content,
        task_plan,
        timetable_import,
        task_update,
        task_complete,
        task_delete,
        course_update,
        course_delete,
        occurrence_exception,
        _,
    ) = _agent_json_result(
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
                                        "name": "propose_itime_task_complete",
                                        "arguments": json.dumps(complete_arguments),
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        ).encode(),
        "request-5",
        200,
    )
    assert content == complete_arguments["assistant_message"]
    assert task_complete == AgentTaskComplete.model_validate(
        complete_arguments
    )
    assert task_plan is None
    assert timetable_import is None
    assert task_update is None

    course_arguments = {
        "assistant_message": "确认后删除这门课程。",
        "series_id": str(uuid4()),
    }
    (
        content,
        task_plan,
        timetable_import,
        task_update,
        task_complete,
        task_delete,
        course_update,
        course_delete,
        occurrence_exception,
        _,
    ) = _agent_json_result(
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
                                        "name": "propose_itime_course_delete",
                                        "arguments": json.dumps(course_arguments),
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        ).encode(),
        "request-6",
        200,
    )
    assert content == course_arguments["assistant_message"]
    assert course_delete == AgentCourseDelete.model_validate(course_arguments)
    assert task_plan is None
    assert timetable_import is None
    assert task_update is None
    assert task_complete is None
    assert task_delete is None


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
        _agent_json_result(json.dumps(invalid).encode(), "request-4", 200)
    assert captured.value.code == "conversation_tool_invalid"
