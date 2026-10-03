import pytest

from itime.ai.pricing import (
    format_price_per_million,
    parse_price_per_million,
    token_cost_breakdown,
)
from itime.ai.usage import estimate_usage, normalize_provider_usage


def test_price_conversion_and_aggregate_rounding_are_exact():
    assert parse_price_per_million("1.000000001") == 1_000_000_001
    assert format_price_per_million(1_000_000_001) == "1.000000001"
    assert token_cost_breakdown(
        input_tokens=1000,
        cached_input_tokens=100,
        output_tokens=500,
        input_price=1_000_000_000,
        cached_input_price=500_000_000,
        output_price=2_000_000_000,
    ) == (900_000, 50_000, 1_000_000, 1_950_000)
    assert token_cost_breakdown(
        input_tokens=1,
        cached_input_tokens=0,
        output_tokens=1,
        input_price=1,
        cached_input_price=1,
        output_price=1,
    ) == (1, 0, 0, 1)
    with pytest.raises(ValueError, match="可存储范围"):
        token_cost_breakdown(
            input_tokens=10**12,
            cached_input_tokens=0,
            output_tokens=10**12,
            input_price=10**15,
            cached_input_price=10**15,
            output_price=10**15,
        )


def test_provider_usage_aliases_and_invalid_values():
    usage = normalize_provider_usage(
        {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 125,
            "prompt_tokens_details": {
                "cached_tokens": 10,
                "ignored_string": "not retained",
                "user@example.com": 1,
            },
        }
    )
    assert usage is not None
    assert (
        usage.input_tokens,
        usage.cached_input_tokens,
        usage.output_tokens,
        usage.total_tokens,
    ) == (100, 10, 20, 125)
    assert usage.details["prompt_tokens_details"] == {"cached_tokens": 10}

    zero_cached = normalize_provider_usage(
        {
            "prompt_tokens": 10,
            "completion_tokens": 1,
            "prompt_tokens_details": {"cached_tokens": 0},
            "input_tokens_details": {"cached_tokens": 5},
        }
    )
    assert zero_cached is not None
    assert zero_cached.cached_input_tokens == 0

    aliased = normalize_provider_usage(
        {"input_tokens": 8, "output_tokens": 3}
    )
    assert aliased is not None
    assert aliased.total_tokens == 11
    assert normalize_provider_usage("invalid") is None
    assert normalize_provider_usage(
        {
            "prompt_tokens": 10,
            "completion_tokens": 1,
            "prompt_tokens_details": {"cached_tokens": 11},
        }
    ) is None


def test_estimator_marks_text_and_screenshot_inputs():
    request = {
        "messages": [
            {"role": "system", "content": "系统说明"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "识别这张课表"},
                    {
                        "type": "image_metadata",
                        "content_type": "image/png",
                    },
                ],
            },
        ]
    }
    screenshot = estimate_usage(
        request,
        '{"items":[]}',
        capability="calendar_screenshot",
        input_summary={"width": 640, "height": 360},
    )
    conversation = estimate_usage(
        request,
        "普通回复",
        capability="conversation",
        input_summary={},
    )
    assert screenshot.input_tokens > conversation.input_tokens
    assert screenshot.details["vision_tokens"] > 0
    assert screenshot.output_tokens > 0


def test_planning_estimator_counts_tool_schema_and_arguments():
    messages = [{"role": "user", "content": "安排任务"}]
    plain = estimate_usage(
        {"messages": messages},
        "计划完成",
        capability="conversation",
        input_summary={},
    )
    planning = estimate_usage(
        {
            "messages": messages,
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "submit_itime_plan",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "summary": {"type": "string"},
                                "blocks": {"type": "array"},
                            },
                            "required": ["summary", "blocks"],
                        },
                    },
                }
            ],
            "tool_choice": {
                "type": "function",
                "function": {"name": "submit_itime_plan"},
            },
            "parallel_tool_calls": False,
        },
        (
            '{"content":"计划完成","tool_call":{"name":"submit_itime_plan",'
            '"arguments":{"summary":"完成","blocks":[]}}}'
        ),
        capability="planning",
        input_summary={},
    )
    assert planning.input_tokens > plain.input_tokens
    assert planning.output_tokens > plain.output_tokens
    assert planning.details["tool_schema_utf8_bytes"] > 0
    assert planning.details["tool_arguments_utf8_bytes"] > 0
