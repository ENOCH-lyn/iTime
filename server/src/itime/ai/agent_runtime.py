"""LangGraph Agent runtime: model turns, tools, approval interrupts and resume."""

import base64
import json
import logging
import operator
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Annotated, Any, TypedDict
from uuid import UUID, uuid4

from cryptography.fernet import Fernet, InvalidToken
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy import select

from itime.ai.action_proposals import ActionProposal, create_proposal, get_proposal
from itime.ai.agent_models import AgentRun
from itime.ai.conversation_provider import (
    ConversationProviderError,
    _live_weather,
    _live_web_search,
    generate_agent_turn,
)
from itime.ai.models import TemporaryMedia
from itime.ai.provider import ProviderConfig
from itime.auth.security import now
from itime.calendar.ai_import_enqueue import enqueue_conversation_image_import
from itime.calendar.models import AcademicTerm
from itime.config import Settings
from itime.database import Database
from itime.errors import AppError

logger = logging.getLogger(__name__)

WRITE_TOOLS = {
    "propose_itime_term_create": "create_term",
    "propose_itime_term_update": "update_term",
}


class AgentState(TypedDict):
    messages: Annotated[list[dict], operator.add]
    context: dict
    image_media_id: str | None
    tool_calls: list[dict]
    tool_index: int
    turn: int
    final_content: str
    memory_writes: list[dict]
    usage: object | None
    provider_request_id: str | None
    http_status: int


@dataclass
class AgentDependencies:
    database: Database
    settings: Settings
    cipher: Fernet
    config: ProviderConfig
    owner_id: UUID
    conversation_id: UUID
    response_message_id: UUID
    run_id: UUID
    thread_id: str
    context: dict
    image_media_id: UUID | None
    deadline_at: datetime
    on_request_started: Callable[[], Awaitable[bool]]
    _image_cache: dict[str, str | None] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentOutcome:
    run_id: UUID
    content: str
    final: bool
    proposal_id: UUID | None
    proposal_tool_name: str | None
    usage: object | None
    provider_request_id: str | None
    http_status: int
    memory_writes: list[dict] = field(default_factory=list)


def _database_url(settings: Settings) -> str:
    return settings.database_url.get_secret_value().replace(
        "postgresql+psycopg", "postgresql"
    )


def _last_assistant_content(state: dict) -> str:
    for message in reversed(state.get("messages") or []):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content
    return ""


async def _image_data_url(deps: AgentDependencies) -> str | None:
    if deps.image_media_id is None:
        return None
    key = str(deps.image_media_id)
    if key in deps._image_cache:
        return deps._image_cache[key]
    async with deps.database.transaction() as session:
        media = await session.get(TemporaryMedia, deps.image_media_id)
    if (
        media is None
        or media.deleted_at is not None
        or media.encrypted_content is None
        or media.expires_at <= now()
    ):
        deps._image_cache[key] = None
        return None
    try:
        raw = deps.cipher.decrypt(media.encrypted_content)
    except InvalidToken:
        deps._image_cache[key] = None
        return None
    url = f"data:{media.content_type};base64," + base64.b64encode(raw).decode()
    deps._image_cache[key] = url
    return url


def _normalize_calls(raw_calls: list[dict], turn: int) -> list[dict]:
    calls: list[dict] = []
    for index, call in enumerate(raw_calls):
        function = call.get("function") if isinstance(call, dict) else None
        if not isinstance(function, dict):
            continue
        name = str(function.get("name") or "")
        if not name:
            continue
        arguments: dict[str, Any] = {}
        raw_arguments = function.get("arguments")
        if isinstance(raw_arguments, dict):
            arguments = raw_arguments
        elif isinstance(raw_arguments, str) and raw_arguments.strip():
            try:
                parsed = json.loads(raw_arguments)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                arguments = parsed
        calls.append(
            {
                "id": str(call.get("id") or f"call_{turn}_{index}"),
                "name": name,
                "arguments": arguments,
                "raw": call,
            }
        )
    return calls


async def _agent_node(state: AgentState, *, deps: AgentDependencies) -> dict:
    remaining = (deps.deadline_at - now()).total_seconds()
    if remaining <= 0:
        raise ConversationProviderError(
            "ai_job_deadline_exceeded",
            "AI 作业已超过总等待时间，请重新发送。",
            "deadline",
        )
    turn = await generate_agent_turn(
        deps.config,
        deps.settings,
        deps.cipher,
        messages=state["messages"],
        context=state["context"],
        timeout_seconds=remaining,
        on_request_started=deps.on_request_started,
        image_data_url=await _image_data_url(deps),
    )
    calls = _normalize_calls(turn.tool_calls, state["turn"])
    assistant: dict[str, Any] = {
        "role": "assistant",
        "content": turn.content,
    }
    if calls:
        assistant["tool_calls"] = [call["raw"] for call in calls]
    return {
        "messages": [assistant],
        "tool_calls": calls,
        "tool_index": 0,
        "turn": state["turn"] + 1,
        "final_content": "" if calls else turn.content,
        "usage": turn.usage,
        "provider_request_id": turn.provider_request_id,
        "http_status": turn.http_status,
    }


def _route_agent(state: AgentState) -> str:
    return "tool" if state.get("tool_calls") else END


def _route_tool(state: AgentState) -> str:
    return "tool" if state["tool_index"] < len(state["tool_calls"]) else "agent"


async def _list_terms(deps: AgentDependencies) -> dict:
    async with deps.database.transaction() as session:
        terms = list(
            await session.scalars(
                select(AcademicTerm)
                .where(AcademicTerm.owner_id == deps.owner_id)
                .order_by(AcademicTerm.start_date.desc(), AcademicTerm.id.desc())
                .limit(50)
            )
        )
    return {
        "terms": [
            {
                "id": str(term.id),
                "name": term.name,
                "start_date": term.start_date.isoformat(),
                "end_date": term.end_date.isoformat(),
                "week_count": ((term.end_date - term.start_date).days // 7) + 1,
            }
            for term in terms
        ]
    }


def _term_payload(action: str, arguments: dict) -> dict:
    if action == "create_term":
        name = str(arguments.get("name") or "").strip()[:120]
        if not name:
            raise ValueError("name is required")
        start_raw = str(arguments.get("start_date") or "")
        try:
            start = date.fromisoformat(start_raw)
        except ValueError as exc:
            raise ValueError("start_date must be YYYY-MM-DD") from exc
        try:
            week_count = int(arguments.get("week_count") or 20)
        except (TypeError, ValueError) as exc:
            raise ValueError("week_count must be an integer") from exc
        if week_count < 1 or week_count > 30:
            raise ValueError("week_count must be between 1 and 30")
        return {
            "assistant_message": str(arguments.get("assistant_message") or "")[:4000],
            "name": name,
            "start_date": start.isoformat(),
            "week_count": week_count,
        }
    try:
        term_id = UUID(str(arguments.get("term_id") or ""))
    except ValueError as exc:
        raise ValueError("term_id must be a UUID") from exc
    payload: dict[str, Any] = {
        "assistant_message": str(arguments.get("assistant_message") or "")[:4000],
        "term_id": str(term_id),
    }
    if arguments.get("name"):
        payload["name"] = str(arguments["name"]).strip()[:120]
    if arguments.get("start_date"):
        try:
            payload["start_date"] = date.fromisoformat(
                str(arguments["start_date"])
            ).isoformat()
        except ValueError as exc:
            raise ValueError("start_date must be YYYY-MM-DD") from exc
    if arguments.get("week_count") is not None:
        try:
            week_count = int(arguments["week_count"])
        except (TypeError, ValueError) as exc:
            raise ValueError("week_count must be an integer") from exc
        if week_count < 1 or week_count > 30:
            raise ValueError("week_count must be between 1 and 30")
        payload["week_count"] = week_count
    if len(payload) <= 2:
        raise ValueError("at least one of name, start_date or week_count is required")
    return payload


async def _ensure_proposal(
    deps: AgentDependencies,
    state: AgentState,
    call: dict,
    action: str,
) -> ActionProposal:
    payload = _term_payload(action, call["arguments"])
    operation_id = f"a:{deps.run_id}:{state['turn']}:{state['tool_index']}"
    async with deps.database.transaction() as session:
        existing = await session.scalar(
            select(ActionProposal).where(
                ActionProposal.owner_id == deps.owner_id,
                ActionProposal.conversation_id == deps.conversation_id,
                ActionProposal.tool_name == action,
                ActionProposal.operation_id == operation_id,
            )
        )
        if existing is not None:
            return existing
        details: dict[str, Any] = {
            "kind": action,
            **{key: value for key, value in payload.items() if key != "assistant_message"},
        }
        if action == "update_term":
            term = await session.scalar(
                select(AcademicTerm).where(
                    AcademicTerm.id == UUID(payload["term_id"]),
                    AcademicTerm.owner_id == deps.owner_id,
                )
            )
            if term is not None:
                details["name"] = payload.get("name") or term.name
        return await create_proposal(
            session,
            deps.owner_id,
            conversation_id=deps.conversation_id,
            source_message_id=deps.response_message_id,
            tool_name=action,
            operation_id=operation_id,
            preview={
                "summary": payload.get("assistant_message") or "",
                "details": details,
            },
            payload=payload,
            expected_version=None,
            current_time=now(),
        )


async def _decide_proposal(
    deps: AgentDependencies,
    state: AgentState,
    call: dict,
    action: str,
) -> dict:
    try:
        proposal = await _ensure_proposal(deps, state, call, action)
    except AppError as exc:
        return {"error": exc.message}
    except ValueError as exc:
        return {"error": str(exc)}
    if proposal.status == "pending":
        interrupt(
            {
                "proposal_id": str(proposal.id),
                "tool_name": proposal.tool_name,
                "arguments": call["arguments"],
                "assistant_message": proposal.payload.get("assistant_message") or "",
            }
        )
        async with deps.database.transaction() as session:
            proposal = await get_proposal(
                session,
                deps.owner_id,
                proposal.id,
            )
    if proposal.status == "applied":
        return {
            "ok": True,
            "proposal_id": str(proposal.id),
            "term_id": (
                str(proposal.applied_resource_id)
                if proposal.applied_resource_id is not None
                else None
            ),
            "message": "用户已批准，操作已完成。",
        }
    if proposal.status == "rejected":
        return {
            "ok": False,
            "rejected": True,
            "proposal_id": str(proposal.id),
            "message": "用户拒绝了这次工具调用。不要重复同一个调用；先说明或改为其他做法。",
        }
    return {
        "ok": False,
        "proposal_id": str(proposal.id),
        "status": proposal.status,
        "message": "这次工具调用没有完成，请重新判断下一步。",
    }


async def _execute_tool(
    deps: AgentDependencies,
    state: AgentState,
    call: dict,
) -> tuple[dict, list[dict]]:
    name = call["name"]
    arguments = call["arguments"]
    if name == "get_weather":
        return (
            await _live_weather(
                arguments.get("location") or deps.context.get("weather_location")
            ),
            [],
        )
    if name == "web_search":
        return (
            await _live_web_search(
                str(arguments.get("query") or ""),
                deps.settings,
            ),
            [],
        )
    if name == "list_terms":
        return await _list_terms(deps), []
    if name == "save_memory":
        content = str(arguments.get("content") or "").strip()[:2000]
        title = str(arguments.get("title") or "").strip()[:120] or content[:120]
        kind = arguments.get("kind")
        if len(content) < 4 or kind not in {"context", "constraint"}:
            return {"error": "invalid memory"}, []
        return {"saved": True, "title": title}, [
            {
                "kind": kind,
                "title": title,
                "content": content,
            }
        ]
    if name == "start_itime_timetable_import":
        if deps.image_media_id is None:
            return {
                "error": "当前会话没有可导入的课表图片。请让用户上传课表截图。"
            }, []
        try:
            term_id = UUID(str(arguments.get("term_id") or ""))
        except ValueError:
            return {"error": "term_id must be a UUID from list_terms"}, []
        try:
            async with deps.database.transaction() as session:
                job = await enqueue_conversation_image_import(
                    session,
                    owner_id=deps.owner_id,
                    settings=deps.settings,
                    cipher=deps.cipher,
                    media_id=deps.image_media_id,
                    conversation_id=deps.conversation_id,
                    message_id=deps.response_message_id,
                    term_id=term_id,
                )
                job_id = str(job.id)
        except AppError as exc:
            return {"error": exc.message}, []
        return {
            "started": True,
            "job_id": job_id,
            "message": "已开始从图片识别并导入课表，完成后需要在课表页确认候选。",
        }, []
    if name in WRITE_TOOLS:
        return await _decide_proposal(deps, state, call, WRITE_TOOLS[name]), []
    return {"error": f"unknown tool: {name}"}, []


async def _tool_node(state: AgentState, *, deps: AgentDependencies) -> dict:
    index = state["tool_index"]
    calls = state["tool_calls"]
    if index >= len(calls):
        return {}
    call = calls[index]
    result, memory_writes = await _execute_tool(deps, state, call)
    message = {
        "role": "tool",
        "tool_call_id": call["id"],
        "name": call["name"],
        "content": json.dumps(result, ensure_ascii=False),
    }
    update: dict[str, Any] = {
        "messages": [message],
        "tool_index": index + 1,
    }
    if memory_writes:
        update["memory_writes"] = state["memory_writes"] + memory_writes
    return update


def _build_graph(checkpointer: AsyncPostgresSaver, deps: AgentDependencies):
    builder = StateGraph(AgentState)

    async def agent_node(state: AgentState) -> dict:
        return await _agent_node(state, deps=deps)

    async def tool_node(state: AgentState) -> dict:
        return await _tool_node(state, deps=deps)

    builder.add_node("agent", agent_node)
    builder.add_node("tool", tool_node)
    builder.add_edge(START, "agent")
    builder.add_conditional_edges(
        "agent",
        _route_agent,
        {"tool": "tool", END: END},
    )
    builder.add_conditional_edges(
        "tool",
        _route_tool,
        {"tool": "tool", "agent": "agent"},
    )
    return builder.compile(checkpointer=checkpointer)


def _outcome(result: dict, run_id: UUID) -> AgentOutcome:
    interrupts = result.get("__interrupt__") or []
    usage = result.get("usage")
    provider_request_id = result.get("provider_request_id")
    http_status = result.get("http_status") or 200
    memory_writes = [
        item
        for item in (result.get("memory_writes") or [])
        if isinstance(item, dict)
    ]
    if interrupts:
        value = interrupts[0].value if hasattr(interrupts[0], "value") else {}
        if not isinstance(value, dict):
            value = {}
        content = (
            _last_assistant_content(result)
            or str(value.get("assistant_message") or "").strip()
            or "需要你确认后继续。"
        )
        proposal_id = value.get("proposal_id")
        return AgentOutcome(
            run_id=run_id,
            content=content,
            final=False,
            proposal_id=UUID(str(proposal_id)) if proposal_id else None,
            proposal_tool_name=(
                str(value.get("tool_name")) if value.get("tool_name") else None
            ),
            usage=usage,
            provider_request_id=provider_request_id,
            http_status=http_status,
            memory_writes=memory_writes,
        )
    content = (
        result.get("final_content")
        or _last_assistant_content(result)
        or "已完成。"
    )
    return AgentOutcome(
        run_id=run_id,
        content=content,
        final=True,
        proposal_id=None,
        proposal_tool_name=None,
        usage=usage,
        provider_request_id=provider_request_id,
        http_status=http_status,
        memory_writes=memory_writes,
    )


async def _run_graph(
    deps: AgentDependencies,
    *,
    initial: dict | None,
    command: Command | None,
) -> dict:
    async with AsyncPostgresSaver.from_conn_string(_database_url(deps.settings)) as saver:
        await saver.setup()
        graph = _build_graph(saver, deps)
        config = {
            "configurable": {"thread_id": deps.thread_id},
            "recursion_limit": 60,
        }
        if command is not None:
            return await graph.ainvoke(command, config)
        if initial is None:
            raise ValueError("initial state or resume command is required")
        return await graph.ainvoke(initial, config)


async def start_agent_run(
    database: Database,
    settings: Settings,
    cipher: Fernet,
    *,
    claim,
    config: ProviderConfig,
    messages: list[dict],
    context: dict,
    image_media_id: UUID | None,
    response_message_id: UUID,
    on_request_started: Callable[[], Awaitable[bool]],
) -> AgentOutcome:
    run_id = uuid4()
    thread_id = str(run_id)
    current = now()
    async with database.transaction() as session:
        session.add(
            AgentRun(
                id=run_id,
                owner_id=claim.owner_id,
                conversation_id=claim.conversation_id,
                source_message_id=response_message_id,
                last_job_id=claim.id,
                thread_id=thread_id,
                status="running",
                step=0,
                context=context,
                image_media_id=image_media_id,
                last_content="",
                created_at=current,
                updated_at=current,
            )
        )
    deps = AgentDependencies(
        database=database,
        settings=settings,
        cipher=cipher,
        config=config,
        owner_id=claim.owner_id,
        conversation_id=claim.conversation_id,
        response_message_id=response_message_id,
        run_id=run_id,
        thread_id=thread_id,
        context=context,
        image_media_id=image_media_id,
        deadline_at=claim.deadline_at,
        on_request_started=on_request_started,
    )
    initial = {
        "messages": messages,
        "context": context,
        "image_media_id": str(image_media_id) if image_media_id else None,
        "tool_calls": [],
        "tool_index": 0,
        "turn": 0,
        "final_content": "",
        "memory_writes": [],
        "usage": None,
        "provider_request_id": None,
        "http_status": 200,
    }
    try:
        result = await _run_graph(deps, initial=initial, command=None)
    except Exception:
        await fail_agent_run(database, run_id, "ai_agent_failed", "Agent 执行失败。")
        raise
    return _outcome(result, run_id)


async def resume_agent_run(
    database: Database,
    settings: Settings,
    cipher: Fernet,
    *,
    claim,
    run: AgentRun,
    config: ProviderConfig,
    response_message_id: UUID,
    decision: str,
    on_request_started: Callable[[], Awaitable[bool]],
) -> AgentOutcome:
    async with database.transaction() as session:
        locked = await session.scalar(
            select(AgentRun)
            .where(
                AgentRun.id == run.id,
                AgentRun.owner_id == claim.owner_id,
            )
            .with_for_update()
        )
        if locked is None:
            raise AppError("ai_agent_run_missing", "Agent 会话已不存在。", status=404)
        locked.status = "running"
        locked.pending_proposal_id = None
        locked.last_job_id = claim.id
        locked.source_message_id = response_message_id
        locked.updated_at = now()
        locked.version += 1
        run = locked
    deps = AgentDependencies(
        database=database,
        settings=settings,
        cipher=cipher,
        config=config,
        owner_id=claim.owner_id,
        conversation_id=claim.conversation_id,
        response_message_id=response_message_id,
        run_id=run.id,
        thread_id=run.thread_id,
        context=run.context or {},
        image_media_id=run.image_media_id,
        deadline_at=claim.deadline_at,
        on_request_started=on_request_started,
    )
    try:
        result = await _run_graph(
            deps,
            initial=None,
            command=Command(resume={"decision": decision}),
        )
    except Exception:
        await fail_agent_run(database, run.id, "ai_agent_failed", "Agent 执行失败。")
        raise
    return _outcome(result, run.id)


async def fail_agent_run(
    database: Database,
    run_id: UUID,
    code: str,
    message: str,
) -> None:
    current = now()
    async with database.transaction() as session:
        run = await session.scalar(
            select(AgentRun).where(AgentRun.id == run_id).with_for_update()
        )
        if run is None or run.status in {"completed", "cancelled"}:
            return
        run.status = "failed"
        run.error_code = code[:80]
        run.error_message = message[:2000]
        run.updated_at = current
        run.version += 1


async def get_agent_run(
    database: Database,
    owner_id: UUID,
    run_id: UUID,
) -> AgentRun | None:
    async with database.transaction() as session:
        return await session.scalar(
            select(AgentRun).where(
                AgentRun.id == run_id,
                AgentRun.owner_id == owner_id,
            )
        )
