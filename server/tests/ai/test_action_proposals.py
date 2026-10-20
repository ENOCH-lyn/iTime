from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest

from itime.ai.action_proposals import (
    ActionProposal,
    create_proposal,
    expire_proposals,
    get_proposal,
)
from itime.config import Settings
from itime.database import Database
from itime.errors import AppError
from tests.conftest import psycopg_url
from tests.test_tasks import create_account, create_task

pytestmark = pytest.mark.integration


def _seed_conversation(database_url: str, owner_id: UUID) -> UUID:
    conversation_id = uuid4()
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        connection.execute(
            """
            INSERT INTO ai_conversations (id, owner_id, title)
            VALUES (%s, %s, '工具链测试')
            """,
            (conversation_id, owner_id),
        )
    return conversation_id


def test_create_proposal_is_idempotent_and_can_expire(client, database_url):
    _, owner_id_value = create_account(client, database_url)
    owner_id = UUID(owner_id_value)
    conversation_id = _seed_conversation(database_url, owner_id)
    database = Database(Settings(database_url=database_url, environment="test"))
    now = datetime(2026, 10, 10, 9, 0, tzinfo=UTC)

    async def run() -> tuple[ActionProposal, ActionProposal, int, int]:
        async with database.transaction() as session:
            first = await create_proposal(
                session,
                owner_id,
                conversation_id=conversation_id,
                source_message_id=None,
                tool_name="create_task",
                operation_id="op-1",
                preview={"summary": "创建任务 A"},
                payload={"title": "任务 A"},
                expected_version=None,
                current_time=now,
            )
            second = await create_proposal(
                session,
                owner_id,
                conversation_id=conversation_id,
                source_message_id=None,
                tool_name="create_task",
                operation_id="op-1",
                preview={"summary": "重复提交忽略"},
                payload={"title": "任务 A"},
                expected_version=None,
                current_time=now,
            )
            # 新建一条待过期的 proposal，模拟 7 天后到期。
            await create_proposal(
                session,
                owner_id,
                conversation_id=conversation_id,
                source_message_id=None,
                tool_name="save_memory",
                operation_id="op-2",
                preview={"summary": "待过期"},
                payload={"title": "过期示例"},
                expected_version=None,
                current_time=now - timedelta(days=8),
            )
            # 构造另一条有效 proposal 不应被过期。
            await create_proposal(
                session,
                owner_id,
                conversation_id=conversation_id,
                source_message_id=None,
                tool_name="save_memory",
                operation_id="op-3",
                preview={"summary": "有效"},
                payload={"title": "新鲜示例"},
                expected_version=None,
                current_time=now,
            )
            expired = await expire_proposals(session, current_time=now)
            still_pending = 0
            fetched = await get_proposal(session, owner_id, first.id)
            if fetched.status == "pending":
                still_pending += 1
            return first, second, expired, still_pending

    import asyncio

    first, second, expired, still_pending = asyncio.run(run())
    assert first.id == second.id
    assert first.preview == {"summary": "创建任务 A"}
    # op-2 预设于 8 天前已到期，op-1 和 op-3 仍为 pending。
    assert expired == 1
    assert still_pending == 1


def test_get_proposal_raises_app_error_when_missing(client, database_url):
    _, owner_id_value = create_account(client, database_url)
    owner_id = UUID(owner_id_value)
    database = Database(Settings(database_url=database_url, environment="test"))

    async def run() -> None:
        async with database.transaction() as session:
            with pytest.raises(AppError) as excinfo:
                await get_proposal(session, owner_id, uuid4())
            assert excinfo.value.code == "ai_action_proposal_not_found"

    import asyncio

    asyncio.run(run())


def test_apply_updates_task_completes_and_reject_blocks(client, database_url):
    _, owner_id_value = create_account(client, database_url)
    owner_id = UUID(owner_id_value)
    task = create_task(client, title="AI 待更新任务", estimated_minutes=30)
    conversation_id = _seed_conversation(database_url, owner_id)
    database = Database(Settings(database_url=database_url, environment="test"))
    now = datetime(2026, 10, 10, 9, 0, tzinfo=UTC)

    async def run() -> None:
        from itime.ai.action_proposals import apply_proposal, reject_proposal

        async with database.transaction() as session:
            update = await create_proposal(
                session,
                owner_id,
                conversation_id=conversation_id,
                source_message_id=None,
                tool_name="update_task",
                operation_id="apply-update",
                preview={"summary": "更新任务"},
                payload={
                    "task_id": task["id"],
                    "title": "AI 已更新任务",
                    "estimated_minutes": 90,
                },
                expected_version=None,
                current_time=now,
            )
            applied = await apply_proposal(
                session,
                owner_id,
                update.id,
                expected_version=update.version,
                current_time=now,
            )
            assert applied["task"]["title"] == "AI 已更新任务"
            assert applied["task"]["estimated_minutes"] == 90
            assert applied["proposal"]["status"] == "applied"

            rejected = await create_proposal(
                session,
                owner_id,
                conversation_id=conversation_id,
                source_message_id=None,
                tool_name="complete_task",
                operation_id="reject-complete",
                preview={"summary": "完成任务"},
                payload={"task_id": task["id"]},
                expected_version=None,
                current_time=now,
            )
            rejected_result = await reject_proposal(
                session,
                owner_id,
                rejected.id,
                expected_version=rejected.version,
                current_time=now,
            )
            assert rejected_result["proposal"]["status"] == "rejected"

            complete = await create_proposal(
                session,
                owner_id,
                conversation_id=conversation_id,
                source_message_id=None,
                tool_name="complete_task",
                operation_id="apply-complete",
                preview={"summary": "完成任务"},
                payload={"task_id": task["id"]},
                expected_version=None,
                current_time=now,
            )
            completed = await apply_proposal(
                session,
                owner_id,
                complete.id,
                expected_version=complete.version,
                current_time=now,
            )
            assert completed["task"]["status"] == "completed"

    import asyncio

    asyncio.run(run())


def test_invalid_tool_name_is_rejected(client, database_url):
    _, owner_id_value = create_account(client, database_url)
    owner_id = UUID(owner_id_value)
    conversation_id = _seed_conversation(database_url, owner_id)
    database = Database(Settings(database_url=database_url, environment="test"))
    now = datetime(2026, 10, 10, 9, 0, tzinfo=UTC)

    async def run() -> None:
        async with database.transaction() as session:
            with pytest.raises(AppError) as excinfo:
                await create_proposal(
                    session,
                    owner_id,
                    conversation_id=conversation_id,
                    source_message_id=None,
                    tool_name="not_a_tool",
                    operation_id="op",
                    preview={},
                    payload={},
                    expected_version=None,
                    current_time=now,
                )
            assert excinfo.value.code == "ai_tool_name_invalid"

    import asyncio

    asyncio.run(run())
