from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from uuid import uuid4

import httpx
import psycopg
import pytest

from tests.conftest import psycopg_url

pytestmark = pytest.mark.integration
AUTH = "/api/v1/auth"
PASSWORD = "Valid-task-password-123"


def address(prefix: str = "task") -> str:
    return f"{prefix}-{uuid4().hex}@example.com"


def create_account(client: httpx.Client, database_url: str) -> tuple[str, str]:
    email = address()
    registered = client.post(
        f"{AUTH}/register",
        json={"email": email, "password": PASSWORD},
    )
    assert registered.status_code == 202, registered.text
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        user_id = connection.execute(
            "UPDATE users SET email_verified_at=now() WHERE email=%s RETURNING id",
            (email,),
        ).fetchone()[0]
    logged_in = client.post(
        f"{AUTH}/login",
        json={"email": email, "password": PASSWORD, "device_name": "任务测试"},
    )
    assert logged_in.status_code == 200, logged_in.text
    client.headers["x-csrf-token"] = logged_in.json()["csrf_token"]
    return email, str(user_id)


@contextmanager
def browser(live_server):
    with httpx.Client(base_url=live_server.origin, timeout=10, trust_env=False) as client:
        yield client


def project_payload(operation_id=None, **changes):
    payload = {
        "operation_id": str(operation_id or uuid4()),
        "kind": "project",
        "name": "毕业论文",
        "description": "集中管理论文相关任务",
    }
    payload.update(changes)
    return payload


def task_payload(operation_id=None, **changes):
    payload = {
        "operation_id": str(operation_id or uuid4()),
        "title": "完成论文提纲",
        "description": "先整理研究问题，再补参考文献。",
        "project_id": None,
        "estimated_minutes": 90,
        "priority": 2,
        "labels": ["学习", " 深度工作 ", "学习"],
        "links": [
            {"title": "资料", "url": "https://example.com/reference"},
        ],
        "steps": [
            {"title": "列出研究问题"},
            {"title": "整理章节结构"},
        ],
    }
    payload.update(changes)
    return payload


def create_task(client, **changes) -> dict:
    response = client.post("/api/v1/tasks", json=task_payload(**changes))
    assert response.status_code == 201, response.text
    return response.json()["task"]


def test_project_task_creation_idempotency_search_and_ownership(
    client, database_url, live_server
):
    assert client.get("/api/v1/tasks").status_code == 401
    _, user_id = create_account(client, database_url)

    csrf = client.headers.pop("x-csrf-token")
    denied = client.post("/api/v1/projects", json=project_payload())
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "invalid_csrf"
    client.headers["x-csrf-token"] = csrf
    invalid = client.post(
        "/api/v1/tasks",
        json=task_payload(
            title="   ",
            links=[{"title": "错误协议", "url": "ftp://example.com/file"}],
            labels=[f"标签 {number}" for number in range(11)],
        ),
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["saved"] == "not_saved"
    assert "ftp://example.com/file" not in invalid.text

    operation_id = uuid4()
    payload = project_payload(operation_id)
    created = client.post("/api/v1/projects", json=payload)
    assert created.status_code == 201, created.text
    project = created.json()["project"]
    replayed = client.post("/api/v1/projects", json=payload)
    assert replayed.status_code == 201
    assert replayed.json() == created.json()
    reused = client.post(
        "/api/v1/projects",
        json={**payload, "name": "另一个项目"},
    )
    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "operation_id_reused"

    task_operation = uuid4()
    task_request = task_payload(
        task_operation,
        project_id=project["id"],
        title="  完成论文提纲  ",
    )
    task_response = client.post("/api/v1/tasks", json=task_request)
    assert task_response.status_code == 201, task_response.text
    task = task_response.json()["task"]
    assert task["title"] == "完成论文提纲"
    assert task["project"]["id"] == project["id"]
    assert {label["name"] for label in task["labels"]} == {"深度工作", "学习"}
    assert task["step_progress"] == {"total": 2, "completed": 0}
    assert task["link_count"] == 1
    assert task["status"] == "open"
    assert client.post("/api/v1/tasks", json=task_request).json() == task_response.json()

    found = client.get(
        "/api/v1/tasks",
        params={
            "search": "论文",
            "label": "深度工作",
            "project_id": project["id"],
            "status": "open",
        },
    )
    assert found.status_code == 200
    assert [item["id"] for item in found.json()["items"]] == [task["id"]]
    assert client.get("/api/v1/labels").json()["items"][0]["task_count"] == 1
    updated_project = client.put(
        f"/api/v1/projects/{project['id']}",
        json={
            **project_payload(
                kind="goal",
                name="完成毕业论文",
                description=project["description"],
            ),
            "expected_version": 1,
        },
    )
    assert updated_project.status_code == 200, updated_project.text
    assert updated_project.json()["project"]["version"] == 2
    assert updated_project.json()["project"]["kind"] == "goal"
    stale_project = client.put(
        f"/api/v1/projects/{project['id']}",
        json={
            **project_payload(name="陈旧项目修改"),
            "expected_version": 1,
        },
    )
    assert stale_project.status_code == 409
    assert stale_project.json()["error"]["details"]["current_version"] == 2

    projects = client.get("/api/v1/projects", params={"kind": "goal"}).json()["items"]
    assert projects[0]["task_count"] == 1
    assert projects[0]["open_task_count"] == 1

    with browser(live_server) as other:
        create_account(other, database_url)
        assert other.get(f"/api/v1/tasks/{task['id']}").status_code == 404
        cross_project = other.post(
            "/api/v1/tasks",
            json=task_payload(project_id=project["id"]),
        )
        assert cross_project.status_code == 404
        assert cross_project.json()["error"]["code"] == "project_not_found"

    with psycopg.connect(psycopg_url(database_url)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM tasks WHERE owner_id=%s",
            (user_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM labels WHERE owner_id=%s",
            (user_id,),
        ).fetchone()[0] == 2
        assert (
            connection.execute(
            "SELECT count(*) FROM user_operations WHERE owner_id=%s",
            (user_id,),
            ).fetchone()[0]
            == 3
        )
        actions = connection.execute(
            "SELECT entity_type, action FROM change_events WHERE owner_id=%s",
            (user_id,),
        ).fetchall()
        assert actions.count(("project", "created")) == 1
        assert actions.count(("task", "created")) == 1
        assert actions.count(("label", "created")) == 2


def test_versioned_task_edit_steps_and_explicit_completion(client, database_url):
    create_account(client, database_url)
    task = create_task(client)
    task_id = task["id"]

    update_id = uuid4()
    update = {
        "operation_id": str(update_id),
        "expected_version": task["version"],
        "title": "完成并检查论文提纲",
        "description": task["description"],
        "project_id": None,
        "estimated_minutes": 120,
        "priority": 3,
        "labels": ["学习", "写作"],
        "links": [{"title": "规范", "url": "https://example.com/style"}],
    }
    edited_response = client.put(f"/api/v1/tasks/{task_id}", json=update)
    assert edited_response.status_code == 200, edited_response.text
    edited = edited_response.json()["task"]
    assert edited["version"] == 2
    assert edited["estimated_minutes"] == 120
    assert edited["estimate_source"] == "user"
    assert client.put(f"/api/v1/tasks/{task_id}", json=update).json() == edited_response.json()

    changed_retry = client.put(
        f"/api/v1/tasks/{task_id}",
        json={**update, "title": "复用编号的新内容"},
    )
    assert changed_retry.status_code == 409
    assert changed_retry.json()["error"]["code"] == "operation_id_reused"
    stale = client.put(
        f"/api/v1/tasks/{task_id}",
        json={**update, "operation_id": str(uuid4()), "title": "陈旧修改"},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["details"]["current_version"] == 2

    first_step = edited["steps"][0]
    checked = client.put(
        f"/api/v1/tasks/{task_id}/steps/{first_step['id']}",
        json={
            "operation_id": str(uuid4()),
            "expected_task_version": 2,
            "title": first_step["title"],
            "completed": True,
        },
    )
    assert checked.status_code == 200, checked.text
    after_step = checked.json()["task"]
    assert after_step["version"] == 3
    assert after_step["steps"][0]["completed"] is True
    assert after_step["status"] == "open"

    second_step = after_step["steps"][1]
    added = client.post(
        f"/api/v1/tasks/{task_id}/steps",
        json={
            "operation_id": str(uuid4()),
            "expected_task_version": 3,
            "title": "检查引用格式",
        },
    )
    assert added.status_code == 201, added.text
    after_add = added.json()["task"]
    assert [step["position"] for step in after_add["steps"]] == [0, 1, 2]

    reordered_ids = [
        after_add["steps"][2]["id"],
        after_add["steps"][0]["id"],
        after_add["steps"][1]["id"],
    ]
    reordered = client.put(
        f"/api/v1/tasks/{task_id}/steps",
        json={
            "operation_id": str(uuid4()),
            "expected_task_version": 4,
            "step_ids": reordered_ids,
        },
    )
    assert reordered.status_code == 200, reordered.text
    assert [step["id"] for step in reordered.json()["task"]["steps"]] == reordered_ids
    assert reordered.json()["task"]["status"] == "open"

    invalid_order = client.put(
        f"/api/v1/tasks/{task_id}/steps",
        json={
            "operation_id": str(uuid4()),
            "expected_task_version": 5,
            "step_ids": reordered_ids[:2],
        },
    )
    assert invalid_order.status_code == 409
    assert invalid_order.json()["error"]["code"] == "invalid_step_order"
    unchanged = client.get(f"/api/v1/tasks/{task_id}").json()["task"]
    assert unchanged["version"] == 5
    assert [step["id"] for step in unchanged["steps"]] == reordered_ids

    deleted = client.request(
        "DELETE",
        f"/api/v1/tasks/{task_id}/steps/{second_step['id']}",
        json={
            "operation_id": str(uuid4()),
            "expected_task_version": 5,
        },
    )
    assert deleted.status_code == 200, deleted.text
    after_delete = deleted.json()["task"]
    assert [step["position"] for step in after_delete["steps"]] == [0, 1]

    completed = client.put(
        f"/api/v1/tasks/{task_id}/completion",
        json={
            "operation_id": str(uuid4()),
            "expected_version": 6,
            "completed": True,
        },
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["task"]["status"] == "completed"
    assert completed.json()["task"]["completed_at"] is not None


def test_bulk_update_is_atomic_idempotent_and_paginated(client, database_url):
    create_account(client, database_url)
    tasks = [
        create_task(
            client,
            title=f"批量任务 {number}",
            description=f"检索内容 {number}",
            labels=["批量"],
            steps=[],
            links=[],
        )
        for number in range(3)
    ]
    page_one = client.get("/api/v1/tasks", params={"limit": 2}).json()
    assert len(page_one["items"]) == 2
    assert page_one["next_cursor"] is not None
    page_two = client.get(
        "/api/v1/tasks",
        params={**page_one["next_cursor"], "limit": 2},
    ).json()
    assert len(page_two["items"]) == 1
    assert {
        item["id"] for item in page_one["items"] + page_two["items"]
    } == {task["id"] for task in tasks}

    changed = client.put(
        f"/api/v1/tasks/{tasks[0]['id']}/completion",
        json={
            "operation_id": str(uuid4()),
            "expected_version": 1,
            "completed": True,
        },
    ).json()["task"]
    stale_bulk = client.post(
        "/api/v1/tasks/actions/bulk",
        json={
            "operation_id": str(uuid4()),
            "action": "complete",
            "items": [
                {"id": tasks[0]["id"], "expected_version": 1},
                {"id": tasks[1]["id"], "expected_version": 1},
            ],
        },
    )
    assert stale_bulk.status_code == 409
    assert stale_bulk.json()["error"]["saved"] == "not_saved"
    assert client.get(f"/api/v1/tasks/{tasks[1]['id']}").json()["task"]["status"] == "open"

    bulk_id = uuid4()
    bulk_request = {
        "operation_id": str(bulk_id),
        "action": "complete",
        "items": [
            {"id": tasks[0]["id"], "expected_version": changed["version"]},
            {"id": tasks[1]["id"], "expected_version": 1},
        ],
    }
    completed = client.post("/api/v1/tasks/actions/bulk", json=bulk_request)
    assert completed.status_code == 200, completed.text
    assert {item["status"] for item in completed.json()["items"]} == {"completed"}
    assert client.post("/api/v1/tasks/actions/bulk", json=bulk_request).json() == completed.json()

    labeled = client.post(
        "/api/v1/tasks/actions/bulk",
        json={
            "operation_id": str(uuid4()),
            "action": "add_labels",
            "labels": ["本周"],
            "items": [
                {"id": item["id"], "expected_version": item["version"]}
                for item in completed.json()["items"]
            ],
        },
    )
    assert labeled.status_code == 200, labeled.text
    assert all(
        "本周" in {label["name"] for label in item["labels"]}
        for item in labeled.json()["items"]
    )

    completed_filter = client.get(
        "/api/v1/tasks",
        params={"status": "completed", "label": "本周"},
    ).json()["items"]
    assert {item["id"] for item in completed_filter} == {
        tasks[0]["id"],
        tasks[1]["id"],
    }


def test_simultaneous_updates_allow_only_one_version_winner(
    client, database_url, live_server
):
    email, _ = create_account(client, database_url)
    task = create_task(client, steps=[], links=[], labels=[])
    with browser(live_server) as second:
        logged_in = second.post(
            f"{AUTH}/login",
            json={"email": email, "password": PASSWORD, "device_name": "并发设备"},
        )
        assert logged_in.status_code == 200
        second.headers["x-csrf-token"] = logged_in.json()["csrf_token"]

        def complete(active_client):
            return active_client.put(
                f"/api/v1/tasks/{task['id']}/completion",
                json={
                    "operation_id": str(uuid4()),
                    "expected_version": 1,
                    "completed": True,
                },
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(complete, (client, second)))
    assert sorted(response.status_code for response in responses) == [200, 409]
    winner = next(response for response in responses if response.status_code == 200)
    conflict = next(response for response in responses if response.status_code == 409)
    assert winner.json()["task"]["version"] == 2
    assert conflict.json()["error"]["code"] == "task_version_conflict"
    assert conflict.json()["error"]["details"]["current_version"] == 2
    current = client.get(f"/api/v1/tasks/{task['id']}").json()["task"]
    assert current["status"] == "completed"
    assert current["version"] == 2


def test_simultaneous_idempotent_create_returns_one_task(
    client, database_url, live_server
):
    email, user_id = create_account(client, database_url)
    operation_id = uuid4()
    payload = task_payload(
        operation_id,
        title="并发只创建一次",
        steps=[],
        links=[],
        labels=[],
    )
    with browser(live_server) as second:
        logged_in = second.post(
            f"{AUTH}/login",
            json={"email": email, "password": PASSWORD, "device_name": "重试设备"},
        )
        assert logged_in.status_code == 200
        second.headers["x-csrf-token"] = logged_in.json()["csrf_token"]

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(
                pool.map(
                    lambda active_client: active_client.post(
                        "/api/v1/tasks",
                        json=payload,
                    ),
                    (client, second),
                )
            )
    assert [response.status_code for response in responses] == [201, 201]
    assert responses[0].json() == responses[1].json()
    with psycopg.connect(psycopg_url(database_url)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM tasks WHERE owner_id=%s AND title=%s",
            (user_id, payload["title"]),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM user_operations"
            " WHERE owner_id=%s AND operation_id=%s",
            (user_id, operation_id),
        ).fetchone()[0] == 1
