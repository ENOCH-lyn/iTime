"""AI function tool package.

Exposes the whitelist registry, per-call budgets, and the system prompt
builder used by the conversation orchestrator in D3.
"""
from .registry import (
    BASELINE_SYSTEM_PROMPT,
    DEFAULT_TOKEN_BUDGET,
    DEFAULT_TOOL_CALL_BUDGET,
    READONLY_TIMEOUT_SECONDS,
    READONLY_TOOLS,
    TOOL_REGISTRY,
    WRITE_PREVIEW_TIMEOUT_SECONDS,
    WRITE_PREVIEW_TOOLS,
    ToolSpec,
    build_system_prompt,
)

__all__ = [
    "BASELINE_SYSTEM_PROMPT",
    "DEFAULT_TOKEN_BUDGET",
    "DEFAULT_TOOL_CALL_BUDGET",
    "READONLY_TIMEOUT_SECONDS",
    "READONLY_TOOLS",
    "TOOL_REGISTRY",
    "ToolSpec",
    "WRITE_PREVIEW_TIMEOUT_SECONDS",
    "WRITE_PREVIEW_TOOLS",
    "build_system_prompt",
]
