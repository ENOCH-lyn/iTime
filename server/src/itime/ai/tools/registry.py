"""Function tool registry for the AI orchestrator.

- `TOOL_REGISTRY`: whitelist mapping tool name → metadata (kind, handler,
  schema class, max_per_conversation, description). The model only sees names
  in this map.
- Budget constants pin single-turn resource ceilings; see
  `doc/需求/iTime-AI工具链与长期记忆.md` §2.3.
- `build_system_prompt` composes the three-part system prompt: baseline
  追问原则 + current time + auto-recalled memory snippets.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from . import readonly, write_previews
from .schemas import (
    CalendarRangeInput,
    CompleteTaskArgs,
    ConfirmPlanArgs,
    CreateDifficultyArgs,
    CreateTaskArgs,
    DifficultyRangeInput,
    EmptyInput,
    EventIdInput,
    ListMemoriesInput,
    ListPlansInput,
    ListTasksInput,
    PlanIdInput,
    ProposePlanArgs,
    ReviewIdInput,
    ReviewRangeInput,
    SaveMemoryArgs,
    SearchMemoryInput,
    SearchTasksInput,
    StartReviewArgs,
    TaskIdInput,
    ToolInputBase,
    TrashMemoryArgs,
    UpdateMemoryArgs,
    UpdatePlanBlockArgs,
    UpdateTaskArgs,
    WriteReviewReportArgs,
)

DEFAULT_TOOL_CALL_BUDGET = 20
DEFAULT_TOKEN_BUDGET = 16000
READONLY_TIMEOUT_SECONDS = 10
WRITE_PREVIEW_TIMEOUT_SECONDS = 15


@dataclass(frozen=True)
class ToolSpec:
    name: str
    kind: str  # "readonly" or "write_preview"
    handler: Callable
    schema_cls: type[ToolInputBase]
    description: str
    max_per_conversation: int


READONLY_TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "get_today",
        "readonly",
        readonly.handle_get_today,
        EmptyInput,
        "获取用户当前的活动日、IANA 时区与服务器当前时间。",
        10,
    ),
    ToolSpec(
        "get_time_settings",
        "readonly",
        readonly.handle_get_time_settings,
        EmptyInput,
        "读取用户时间设置：时区、每日起始分钟、每周起始日。",
        5,
    ),
    ToolSpec(
        "get_calendar_events",
        "readonly",
        readonly.handle_get_calendar_events,
        CalendarRangeInput,
        "查询日期范围内的日历事件（跨度 ≤ 93 天，重复事件已展开）。",
        30,
    ),
    ToolSpec(
        "get_event",
        "readonly",
        readonly.handle_get_event,
        EventIdInput,
        "按 ID 获取单个日历事件详情。",
        20,
    ),
    ToolSpec(
        "list_tasks",
        "readonly",
        readonly.handle_list_tasks,
        ListTasksInput,
        "按条件列出任务（status/due 范围/关键词）。",
        30,
    ),
    ToolSpec(
        "get_task",
        "readonly",
        readonly.handle_get_task,
        TaskIdInput,
        "按 ID 获取任务详情（含步骤与链接）。",
        20,
    ),
    ToolSpec(
        "search_tasks",
        "readonly",
        readonly.handle_search_tasks,
        SearchTasksInput,
        "在未软删任务里按关键词模糊搜索。",
        20,
    ),
    ToolSpec(
        "list_plans",
        "readonly",
        readonly.handle_list_plans,
        ListPlansInput,
        "按周期范围列出规划（period_from/period_to 必填才返回数据）。",
        20,
    ),
    ToolSpec(
        "get_plan",
        "readonly",
        readonly.handle_get_plan,
        PlanIdInput,
        "按 ID 获取规划详情，含当前版本的 blocks。",
        20,
    ),
    ToolSpec(
        "list_reviews",
        "readonly",
        readonly.handle_list_reviews,
        ReviewRangeInput,
        "按活动日范围列出复盘摘要。",
        20,
    ),
    ToolSpec(
        "get_review",
        "readonly",
        readonly.handle_get_review,
        ReviewIdInput,
        "按 ID 获取复盘详情（含事实、消息、报告）。",
        20,
    ),
    ToolSpec(
        "list_difficulties",
        "readonly",
        readonly.handle_list_difficulties,
        DifficultyRangeInput,
        "按日期范围列出困难记录。",
        20,
    ),
    ToolSpec(
        "search_memory",
        "readonly",
        readonly.handle_search_memory,
        SearchMemoryInput,
        "搜索长期记忆；命中后更新 last_used_at。记忆设置关闭时返回空。",
        20,
    ),
    ToolSpec(
        "list_memories",
        "readonly",
        readonly.handle_list_memories,
        ListMemoriesInput,
        "分页列出长期记忆（可按 kind/tags/lifecycle 过滤）。",
        10,
    ),
)


WRITE_PREVIEW_TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "create_task",
        "write_preview",
        write_previews.handle_create_task,
        CreateTaskArgs,
        "创建任务（生成预览，待用户确认）。",
        10,
    ),
    ToolSpec(
        "update_task",
        "write_preview",
        write_previews.handle_update_task,
        UpdateTaskArgs,
        "更新任务字段（生成预览，待用户确认）。",
        10,
    ),
    ToolSpec(
        "complete_task",
        "write_preview",
        write_previews.handle_complete_task,
        CompleteTaskArgs,
        "标记任务完成（生成预览，待用户确认）。",
        10,
    ),
    ToolSpec(
        "start_review",
        "write_preview",
        write_previews.handle_start_review,
        StartReviewArgs,
        "开启或拉起复盘会话（生成预览，待用户确认）。",
        5,
    ),
    ToolSpec(
        "write_review_report",
        "write_preview",
        write_previews.handle_write_review_report,
        WriteReviewReportArgs,
        "撰写复盘报告（生成预览，待用户确认）。",
        5,
    ),
    ToolSpec(
        "save_memory",
        "write_preview",
        write_previews.handle_save_memory,
        SaveMemoryArgs,
        "保存一条长期记忆（生成预览，待用户确认）。",
        10,
    ),
    ToolSpec(
        "update_memory",
        "write_preview",
        write_previews.handle_update_memory,
        UpdateMemoryArgs,
        "更新长期记忆（生成预览，待用户确认）。",
        10,
    ),
    ToolSpec(
        "trash_memory",
        "write_preview",
        write_previews.handle_trash_memory,
        TrashMemoryArgs,
        "移除长期记忆到回收站（生成预览，待用户确认）。",
        10,
    ),
    ToolSpec(
        "create_difficulty",
        "write_preview",
        write_previews.handle_create_difficulty,
        CreateDifficultyArgs,
        "记录一条困难（生成预览，待用户确认）。",
        10,
    ),
    ToolSpec(
        "propose_plan",
        "write_preview",
        write_previews.handle_propose_plan,
        ProposePlanArgs,
        "提出一份新的规划（生成预览，待用户确认）。",
        5,
    ),
    ToolSpec(
        "confirm_plan",
        "write_preview",
        write_previews.handle_confirm_plan,
        ConfirmPlanArgs,
        "确认既有规划提案（生成预览，待用户确认）。",
        5,
    ),
    ToolSpec(
        "update_plan_block",
        "write_preview",
        write_previews.handle_update_plan_block,
        UpdatePlanBlockArgs,
        "更新规划内单个 block（生成预览，待用户确认）。",
        10,
    ),
)


TOOL_REGISTRY: dict[str, ToolSpec] = {
    spec.name: spec for spec in (*READONLY_TOOLS, *WRITE_PREVIEW_TOOLS)
}


BASELINE_SYSTEM_PROMPT = (
    "你是 iTime 的 AI 协助者，服务于用户的日历、任务、规划、复盘、困难与长期记忆管理。\n"
    "合理追问原则：\n"
    "1) 需要事实之前先调用相应只读工具，禁止凭空想象用户数据；\n"
    "2) 工具返回 `missing` 字段时，向用户追问缺失信息，不要编造；\n"
    "3) 产生写操作时必须通过 `*_preview` 工具生成预览，并明确告知用户将在确认后生效；\n"
    "4) 复盘不走固定问题序列，按当日事实组织追问，避免问「今天完成了什么」这类可查询信息；\n"
    "5) 工具预算有限，若 `tool_budget_exceeded` 则总结现状并询问是否继续；\n"
    "6) 禁止询问或输出用户账号、session、API Key 等凭据。"
)


def build_system_prompt(
    *,
    current_time: datetime,
    activity_date: str | None,
    iana_tz: str | None,
    memory_snippets: list[dict] | None = None,
) -> str:
    parts = [BASELINE_SYSTEM_PROMPT]
    time_block = (
        f"[当前时间]\n服务器时间：{current_time.isoformat()}\n"
        f"活动日：{activity_date or '未知'}\n时区：{iana_tz or '未知'}"
    )
    parts.append(time_block)
    if memory_snippets:
        lines = ["[自动召回的长期记忆]"]
        for item in memory_snippets[:5]:
            kind = item.get("kind") or "memory"
            title = item.get("title") or ""
            content = (item.get("content") or "").strip().replace("\n", " ")
            if len(content) > 240:
                content = content[:240] + "…"
            lines.append(f"- ({kind}) {title}：{content}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)
