from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest

from itime.config import Settings
from itime.database import Database
from itime.tasks.lifecycle_service import cleanup_expired_tasks
from tests.conftest import psycopg_url
from tests.test_focus_sessions import start_payload
from tests.test_task_schedules import initialize_settings
from tests.test_tasks import browser, create_account, create_task

pytestmark = pytest.mark.integration


def difficulty_payload(operation_id=None, **changes) -> dict:
    payload = {
        "operation_id": str(operation_id or uuid4()),
        "content": "资料太多，不知道先整理哪一部分。",
        "category": None,
        "emotion": None,
        "task_id": None,
        "focus_session_id": None,
        "occurrence_key": None,
        "occurred_at": None,
    }
    payload.update(changes)
    return payload


def lifecycle_payload(version: int, operation_id=None) -> dict:
    return {
        "operation_id": str(operation_id or uuid4()),
        "expected_version": version,
    }


def test_difficulty_crud_filters_replay_and_optional_fields(client, database_url):
    create_account(client, database_url)
    initialize_settings(client)
    operation_id = uuid4()
    request = difficulty_payload(operation_id)
    created = client.post("/api/v1/difficulties", json=request)
    assert created.status_code == 201, created.text
    record = created.json()["difficulty"]
    assert record["content"] == request["content"]
    assert record["category"] is None
    assert record["emotion"] is None
    assert record["lifecycle"] == "active"
    assert client.post("/api/v1/difficulties", json=request).json() == created.json()

    reused = client.post(
        "/api/v1/difficulties",
        json={**request, "content": "另一个困难"},
    )
    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "operation_id_reused"

    update_id = uuid4()
    updated_body = {
        **difficulty_payload(
            update_id,
            content="需要先找到两篇核心资料。",
            category="缺少资料",
            emotion="有点焦虑",
        ),
        "expected_version": record["version"],
    }
    updated = client.put(
        f"/api/v1/difficulties/{record['id']}",
        json=updated_body,
    )
    assert updated.status_code == 200, updated.text
    record = updated.json()["difficulty"]
    assert record["version"] == 2
    assert record["category"] == "缺少资料"
    assert record["emotion"] == "有点焦虑"
    assert client.put(
        f"/api/v1/difficulties/{record['id']}",
        json=updated_body,
    ).json() == updated.json()

    listed = client.get(
        "/api/v1/difficulties",
        params={
            "scope": "active",
            "category": "缺少资料",
            "start": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
            "end": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    )
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [record["id"]]

    trashed = client.request(
        "DELETE",
        f"/api/v1/difficulties/{record['id']}",
        json=lifecycle_payload(record["version"]),
    )
    assert trashed.status_code == 200, trashed.text
    record = trashed.json()["difficulty"]
    assert record["lifecycle"] == "trashed"
    assert record["purge_after"] is not None
    assert client.get(
        "/api/v1/difficulties",
        params={"scope": "active"},
    ).json()["items"] == []
    assert [item["id"] for item in client.get(
        "/api/v1/difficulties",
        params={"scope": "trashed"},
    ).json()["items"]] == [record["id"]]

    restored = client.post(
        f"/api/v1/difficulties/{record['id']}/restore",
        json=lifecycle_payload(record["version"]),
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["difficulty"]["lifecycle"] == "active"


def test_difficulty_focus_task_relationship_and_owner_isolation(
    client,
    database_url,
    live_server,
):
    create_account(client, database_url)
    initialize_settings(client)
    task = create_task(client, title="关联任务")
    other_task = create_task(client, title="另一任务")
    focus = client.post(
        "/api/v1/focus-sessions",
        json=start_payload(title=None, task_id=task["id"]),
    ).json()["focus"]

    associated = client.post(
        "/api/v1/difficulties",
        json=difficulty_payload(
            task_id=task["id"],
            focus_session_id=focus["id"],
            content="第一步的范围不明确。",
        ),
    )
    assert associated.status_code == 201, associated.text
    record = associated.json()["difficulty"]
    assert record["task_id"] == task["id"]
    assert record["focus_session_id"] == focus["id"]
    assert record["task_title"] == task["title"]

    mismatch = client.post(
        "/api/v1/difficulties",
        json=difficulty_payload(
            task_id=other_task["id"],
            focus_session_id=focus["id"],
        ),
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "difficulty_focus_task_mismatch"

    with browser(live_server) as other:
        create_account(other, database_url)
        initialize_settings(other)
        hidden = other.get(f"/api/v1/difficulties/{record['id']}")
        assert hidden.status_code == 404
        cross_task = other.post(
            "/api/v1/difficulties",
            json=difficulty_payload(task_id=task["id"]),
        )
        assert cross_task.status_code == 404


def test_difficulty_version_conflict_and_task_purge_preserves_snapshot(
    client,
    database_url,
):
    create_account(client, database_url)
    initialize_settings(client)
    task = create_task(client, title="稍后清理的任务")
    created = client.post(
        "/api/v1/difficulties",
        json=difficulty_payload(task_id=task["id"]),
    ).json()["difficulty"]

    stale = client.put(
        f"/api/v1/difficulties/{created['id']}",
        json={
            **difficulty_payload(content="陈旧修改"),
            "expected_version": created["version"] + 1,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "difficulty_version_conflict"

    trashed_task = client.request(
        "DELETE",
        f"/api/v1/tasks/{task['id']}",
        json=lifecycle_payload(task["version"]),
    )
    assert trashed_task.status_code == 200
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        connection.execute(
            """
            UPDATE tasks
            SET deleted_at=now() - interval '31 days',
                purge_after=now() - interval '1 second'
            WHERE id=%s
            """,
            (task["id"],),
        )

    database = Database(Settings(database_url=database_url, environment="test"))

    async def cleanup() -> None:
        try:
            assert await cleanup_expired_tasks(database) == 1
        finally:
            await database.close()

    import asyncio

    asyncio.run(cleanup())
    detail = client.get(f"/api/v1/difficulties/{created['id']}")
    assert detail.status_code == 200
    assert detail.json()["difficulty"]["task_id"] is None
    assert detail.json()["difficulty"]["task_title"] == "稍后清理的任务"
