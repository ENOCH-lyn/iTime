import json
import math
from dataclasses import dataclass

MAX_USAGE_TOKENS = 1_000_000_000
MAX_TOTAL_USAGE_TOKENS = MAX_USAGE_TOKENS * 2
ESTIMATOR_VERSION = "utf8-vision-tools-v2"
ALLOWED_DETAIL_KEYS = {
    "accepted_prediction_tokens",
    "audio_tokens",
    "cached_tokens",
    "reasoning_tokens",
    "rejected_prediction_tokens",
}


@dataclass(frozen=True)
class NormalizedUsage:
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    total_tokens: int
    details: dict


def _token_count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if not 0 <= value <= MAX_USAGE_TOKENS:
        return None
    return value


def _first_count(payload: dict, *keys: str) -> int | None:
    for key in keys:
        if key in payload:
            return _token_count(payload[key])
    return None


def _nested_count(payload: dict, parent: str, child: str) -> int | None:
    details = payload.get(parent)
    if not isinstance(details, dict):
        return None
    return _token_count(details.get(child))


def normalize_provider_usage(value: object) -> NormalizedUsage | None:
    if not isinstance(value, dict):
        return None
    input_tokens = _first_count(value, "prompt_tokens", "input_tokens")
    output_tokens = _first_count(value, "completion_tokens", "output_tokens")
    if input_tokens is None or output_tokens is None:
        return None
    total_tokens = _first_count(value, "total_tokens")
    if total_tokens is None:
        total_tokens = input_tokens + output_tokens
    if total_tokens > MAX_TOTAL_USAGE_TOKENS:
        return None
    cached_input_tokens = _nested_count(
        value,
        "prompt_tokens_details",
        "cached_tokens",
    )
    if cached_input_tokens is None:
        cached_input_tokens = _nested_count(
            value,
            "input_tokens_details",
            "cached_tokens",
        )
    if cached_input_tokens is None:
        cached_input_tokens = 0
    if cached_input_tokens > input_tokens:
        return None

    details: dict[str, int | dict[str, int]] = {}
    for key in (
        "prompt_tokens",
        "input_tokens",
        "completion_tokens",
        "output_tokens",
        "total_tokens",
    ):
        count = _token_count(value.get(key))
        if count is not None:
            details[key] = count
    for key in (
        "prompt_tokens_details",
        "input_tokens_details",
        "completion_tokens_details",
        "output_tokens_details",
    ):
        source = value.get(key)
        if not isinstance(source, dict):
            continue
        numeric = {
            detail_key: count
            for detail_key, raw_count in source.items()
            if detail_key in ALLOWED_DETAIL_KEYS
            and (count := _token_count(raw_count)) is not None
        }
        if numeric:
            details[key] = numeric
    return NormalizedUsage(
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        details=details,
    )


def _estimated_text_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, math.ceil(len(text.encode("utf-8")) / 3))


def _message_text(request_body: dict) -> tuple[str, int]:
    messages = request_body.get("messages")
    if not isinstance(messages, list):
        return "", 0
    parts: list[str] = []
    message_count = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        message_count += 1
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                if isinstance(item.get("text"), str):
                    parts.append(item["text"])
                if item.get("type") == "image_metadata":
                    parts.append(str(item.get("content_type") or ""))
    return "\n".join(parts), message_count


def _tool_schema_text(request_body: dict) -> str:
    tool_fields = {
        key: request_body[key]
        for key in ("tools", "tool_choice", "parallel_tool_calls")
        if key in request_body
    }
    if not tool_fields:
        return ""
    return json.dumps(
        tool_fields,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _tool_argument_bytes(response_body: str) -> int:
    try:
        payload = json.loads(response_body)
    except (TypeError, json.JSONDecodeError):
        return 0
    total = 0

    def visit(value: object) -> None:
        nonlocal total
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "arguments":
                    serialized = (
                        item
                        if isinstance(item, str)
                        else json.dumps(
                            item,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        )
                    )
                    total += len(serialized.encode("utf-8"))
                else:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(payload)
    return total


def _vision_tokens(input_summary: dict) -> int:
    width = input_summary.get("width")
    height = input_summary.get("height")
    if (
        isinstance(width, bool)
        or isinstance(height, bool)
        or not isinstance(width, int)
        or not isinstance(height, int)
        or width <= 0
        or height <= 0
    ):
        return 0
    scale = min(1.0, 2048 / max(width, height))
    width = max(1, math.ceil(width * scale))
    height = max(1, math.ceil(height * scale))
    detail_scale = min(1.0, 768 / min(width, height))
    width = max(1, math.ceil(width * detail_scale))
    height = max(1, math.ceil(height * detail_scale))
    tiles = math.ceil(width / 512) * math.ceil(height / 512)
    return 85 + 170 * tiles


def estimate_usage(
    request_body: dict,
    response_body: str,
    *,
    capability: str,
    input_summary: dict,
) -> NormalizedUsage:
    request_text, message_count = _message_text(request_body)
    tool_schema_text = _tool_schema_text(request_body)
    input_tokens = (
        _estimated_text_tokens(request_text)
        + _estimated_text_tokens(tool_schema_text)
        + message_count * 4
        + 2
    )
    if capability == "calendar_screenshot":
        input_tokens += _vision_tokens(input_summary)
    output_tokens = _estimated_text_tokens(response_body)
    input_tokens = min(input_tokens, MAX_USAGE_TOKENS)
    output_tokens = min(output_tokens, MAX_USAGE_TOKENS)
    return NormalizedUsage(
        input_tokens=input_tokens,
        cached_input_tokens=0,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        details={
            "estimator": ESTIMATOR_VERSION,
            "request_utf8_bytes": len(request_text.encode("utf-8")),
            "response_utf8_bytes": len(response_body.encode("utf-8")),
            "message_count": message_count,
            "tool_schema_utf8_bytes": len(tool_schema_text.encode("utf-8")),
            "tool_arguments_utf8_bytes": _tool_argument_bytes(response_body),
            "vision_tokens": (
                _vision_tokens(input_summary)
                if capability == "calendar_screenshot"
                else 0
            ),
        },
    )
