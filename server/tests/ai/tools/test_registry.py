from datetime import UTC

from itime.ai.tools import (
    DEFAULT_TOKEN_BUDGET,
    DEFAULT_TOOL_CALL_BUDGET,
    READONLY_TIMEOUT_SECONDS,
    READONLY_TOOLS,
    TOOL_REGISTRY,
    WRITE_PREVIEW_TIMEOUT_SECONDS,
    WRITE_PREVIEW_TOOLS,
    build_system_prompt,
)
from itime.ai.tools.schemas import ToolInputBase

EXPECTED_READONLY = {
    "get_today",
    "get_time_settings",
    "get_calendar_events",
    "get_event",
    "list_tasks",
    "get_task",
    "search_tasks",
    "list_plans",
    "get_plan",
    "list_reviews",
    "get_review",
    "list_difficulties",
    "search_memory",
    "list_memories",
}

EXPECTED_WRITE = {
    "create_task",
    "update_task",
    "complete_task",
    "start_review",
    "write_review_report",
    "save_memory",
    "update_memory",
    "trash_memory",
    "create_difficulty",
    "propose_plan",
    "confirm_plan",
    "update_plan_block",
}


def test_registry_covers_all_expected_tools_once():
    names = {spec.name for spec in READONLY_TOOLS}
    assert names == EXPECTED_READONLY
    write_names = {spec.name for spec in WRITE_PREVIEW_TOOLS}
    assert write_names == EXPECTED_WRITE
    assert EXPECTED_READONLY.isdisjoint(EXPECTED_WRITE)
    assert set(TOOL_REGISTRY) == EXPECTED_READONLY | EXPECTED_WRITE


def test_registry_entries_are_well_typed():
    for spec in TOOL_REGISTRY.values():
        assert spec.kind in {"readonly", "write_preview"}
        assert callable(spec.handler)
        assert issubclass(spec.schema_cls, ToolInputBase)
        assert spec.description
        assert spec.max_per_conversation >= 1


def test_budget_constants_match_spec():
    assert DEFAULT_TOOL_CALL_BUDGET == 20
    assert DEFAULT_TOKEN_BUDGET == 16000
    assert READONLY_TIMEOUT_SECONDS == 10
    assert WRITE_PREVIEW_TIMEOUT_SECONDS == 15


def test_system_prompt_includes_time_and_memory_sections():
    from datetime import datetime

    prompt = build_system_prompt(
        current_time=datetime(2026, 10, 10, 9, 0, tzinfo=UTC),
        activity_date="2026-10-10",
        iana_tz="Asia/Shanghai",
        memory_snippets=[
            {"kind": "preference", "title": "专注时段", "content": "上午 9-11 禁打断"},
        ],
    )
    assert "合理追问原则" in prompt
    assert "2026-10-10" in prompt
    assert "Asia/Shanghai" in prompt
    assert "专注时段" in prompt
    assert "上午 9-11 禁打断" in prompt


def test_system_prompt_without_memory_section():
    from datetime import datetime

    prompt = build_system_prompt(
        current_time=datetime(2026, 10, 10, 9, 0, tzinfo=UTC),
        activity_date=None,
        iana_tz=None,
        memory_snippets=None,
    )
    assert "自动召回的长期记忆" not in prompt
    assert "合理追问原则" in prompt
