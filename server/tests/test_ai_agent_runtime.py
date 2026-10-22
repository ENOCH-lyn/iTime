import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from cryptography.fernet import Fernet
from langgraph.types import Command
from sqlalchemy import select

from itime.ai.action_proposals import ActionProposal, apply_proposal
from itime.ai.agent_runtime import (
    AgentDependencies,
    _ensure_proposal,
    _run_graph,
)
from itime.ai.conversation_models import Conversation, ConversationMessage
from itime.ai.conversation_provider import AgentTurn
from itime.ai.provider import ProviderConfig
from itime.auth.models import User
from itime.auth.security import now
from itime.calendar.models import AcademicTerm, UserTimeSettings
from itime.config import Settings
from itime.database import Database
from itime.tasks.models import Task

pytestmark = pytest.mark.integration


async def test_agent_interrupts_for_write_tool_and_resumes_same_run(
    database_url,
    monkeypatch,
):
    settings = Settings(environment="test", database_url=database_url)
    database = Database(settings)
    owner_id = uuid4()
    conversation_id = uuid4()
    message_id = uuid4()
    run_id = uuid4()
    current = now()
    try:
        async with database.transaction() as session:
            session.add(
                User(
                    id=owner_id,
                    email=f"agent-{owner_id.hex}@example.com",
                    password_hash="test",
                )
            )
            await session.flush()
            session.add(
                UserTimeSettings(
                    owner_id=owner_id,
                    time_zone="Asia/Shanghai",
                )
            )
            session.add(
                Conversation(
                    id=conversation_id,
                    owner_id=owner_id,
                    title="Agent test",
                )
            )
            await session.flush()
            session.add(
                ConversationMessage(
                    id=message_id,
                    owner_id=owner_id,
                    conversation_id=conversation_id,
                    sequence=2,
                    role="assistant",
                    status="complete",
                    content="需要确认后继续。",
                    completed_at=current,
                )
            )

        calls: list[list[dict]] = []

        async def fake_turn(config, settings, cipher, **kwargs):
            calls.append(kwargs["messages"])
            if len(calls) == 1:
                return AgentTurn(
                    content="",
                    tool_calls=[
                        {
                            "id": "call_term",
                            "type": "function",
                            "function": {
                                "name": "propose_itime_term_create",
                                "arguments": json.dumps(
                                    {
                                        "assistant_message": "确认后创建学期。",
                                        "name": "2028 春季学期",
                                        "start_date": "2028-02-28",
                                        "week_count": 20,
                                    },
                                    ensure_ascii=False,
                                ),
                            },
                        }
                    ],
                    provider_request_id="req-1",
                    http_status=200,
                    usage=None,
                )
            return AgentTurn(
                content="学期已创建。",
                tool_calls=[],
                provider_request_id="req-2",
                http_status=200,
                usage=None,
            )

        monkeypatch.setattr(
            "itime.ai.agent_runtime.generate_agent_turn",
            fake_turn,
        )
        deps = AgentDependencies(
            database=database,
            settings=settings,
            cipher=Fernet(Fernet.generate_key()),
            config=ProviderConfig(
                endpoint_url="https://example.invalid",
                model="test-model",
                api_key_ciphertext=b"",
                timeout_seconds=10,
                max_output_tokens=1024,
            ),
            owner_id=owner_id,
            conversation_id=conversation_id,
            response_message_id=message_id,
            run_id=run_id,
            thread_id=str(run_id),
            context={"terms": [], "weather_location": None},
            image_media_id=None,
            deadline_at=datetime.now(UTC) + timedelta(seconds=60),
            on_request_started=lambda: _always_true(),
        )
        initial = {
            "messages": [{"role": "user", "content": "新建一个学期"}],
            "context": deps.context,
            "image_media_id": None,
            "tool_calls": [],
            "tool_index": 0,
            "turn": 0,
            "final_content": "",
            "memory_writes": [],
            "usage": None,
            "provider_request_id": None,
            "http_status": 200,
        }
        first = await _run_graph(deps, initial=initial, command=None)
        assert first["__interrupt__"]
        interrupt_value = first["__interrupt__"][0].value
        proposal_id = interrupt_value["proposal_id"]
        assert interrupt_value["tool_name"] == "create_term"
        assert len(calls) == 1

        async with database.transaction() as session:
            proposal = await session.get(
                ActionProposal,
                UUID(proposal_id),
            )
            assert proposal is not None
            await apply_proposal(
                session,
                owner_id,
                proposal.id,
                expected_version=proposal.version,
                current_time=now(),
            )

        second = await _run_graph(
            deps,
            initial=None,
            command=Command(resume={"decision": "approved"}),
        )
        assert not second.get("__interrupt__")
        assert second["final_content"] == "学期已创建。"
        assert len(calls) == 2
        assert any(
            message.get("role") == "tool"
            and "term_id" in str(message.get("content"))
            for message in second["messages"]
        )
        async with database.transaction() as session:
            term = await session.scalar(
                select(AcademicTerm).where(AcademicTerm.owner_id == owner_id)
            )
            assert term is not None
            assert term.name == "2028 春季学期"
    finally:
        await database.close()


async def _always_true() -> bool:
    return True


async def test_agent_task_update_proposal_carries_task_title(database_url):
    settings = Settings(environment="test", database_url=database_url)
    database = Database(settings)
    owner_id = uuid4()
    conversation_id = uuid4()
    message_id = uuid4()
    task_id = uuid4()
    current = now()
    try:
        async with database.transaction() as session:
            session.add(
                User(
                    id=owner_id,
                    email=f"agent-task-{owner_id.hex}@example.com",
                    password_hash="test",
                )
            )
            await session.flush()
            session.add(
                UserTimeSettings(
                    owner_id=owner_id,
                    time_zone="Asia/Shanghai",
                )
            )
            session.add(
                Conversation(
                    id=conversation_id,
                    owner_id=owner_id,
                    title="Task tool test",
                )
            )
            await session.flush()
            session.add(
                ConversationMessage(
                    id=message_id,
                    owner_id=owner_id,
                    conversation_id=conversation_id,
                    sequence=2,
                    role="assistant",
                    status="complete",
                    content="需要确认后继续。",
                    completed_at=current,
                )
            )
            session.add(
                Task(
                    id=task_id,
                    owner_id=owner_id,
                    title="写高数作业",
                    estimated_minutes=60,
                    estimate_source="user",
                    priority=1,
                )
            )
        deps = AgentDependencies(
            database=database,
            settings=settings,
            cipher=Fernet(Fernet.generate_key()),
            config=ProviderConfig(
                endpoint_url="https://example.invalid",
                model="test-model",
                api_key_ciphertext=b"",
                timeout_seconds=10,
                max_output_tokens=1024,
            ),
            owner_id=owner_id,
            conversation_id=conversation_id,
            response_message_id=message_id,
            run_id=uuid4(),
            thread_id=str(uuid4()),
            context={"tasks": []},
            image_media_id=None,
            deadline_at=datetime.now(UTC) + timedelta(seconds=60),
            on_request_started=_always_true,
        )
        proposal = await _ensure_proposal(
            deps,
            {"turn": 1, "tool_index": 0},
            {
                "name": "propose_itime_task_update",
                "arguments": {
                    "assistant_message": "把写高数作业的预计用时改为 30 分钟。",
                    "task_id": str(task_id),
                    "estimated_minutes": 30,
                },
            },
            "propose_itime_task_update",
        )
        assert proposal.tool_name == "update_task"
        assert proposal.preview["details"]["task_title"] == "写高数作业"
        assert proposal.payload["estimated_minutes"] == 30
        async with database.transaction() as session:
            await apply_proposal(
                session,
                owner_id,
                proposal.id,
                expected_version=proposal.version,
                current_time=now(),
            )
        async with database.transaction() as session:
            task = await session.get(Task, task_id)
            assert task is not None
            assert task.estimated_minutes == 30
    finally:
        await database.close()

