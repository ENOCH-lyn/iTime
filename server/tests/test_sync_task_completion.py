from contextlib import contextmanager
from uuid import uuid4

import httpx
import pytest

from tests.test_calendar_series import create_account, recurrence
from tests.test_task_schedules import (
    get_occurrences,
    occurrence_status_payload,
    schedule_payload,
)
from tests.test_tasks import create_task

pytestmark = pytest.mark.integration


@contextmanager
def browser(live_server):
    with httpx.Client(
        base_url=live_server.origin,
        timeout=10,
        trust_env=False,
    ) as client:
        yield client


def update_task_title(client, task: dict, title: str) -> dict:
    response = client.put(
        f"/api/v1/tasks/{task['id']}",
        json={
            "operation_id": str(uuid4()),
            "expected_version": task["version"],
            "title": title,
            "description": task["description"],
            "project_id": task["project"]["id"] if task["project"] else None,
            "estimated_minutes": task["estimated_minutes"],
            "priority": task["priority"],
            "labels": [label["name"] for label in task["labels"]],
            "links": [
                {"title": link["title"], "url": link["url"]}
                for link in task["links"]
            ],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["task"]


def test_template_step_completion_rebases_without_overwriting_and_replays(
    client,
    database_url,
    live_server,
):
    _, owner_id = create_account(client, database_url)
    task = create_task(
        client,
        title="同步任务",
        steps=[{"title": "第一步"}, {"title": "第二步"}],
        labels=[],
        links=[],
    )
    first_step = task["steps"][0]
    second_step = task["steps"][1]
    renamed = update_task_title(client, task, "另一设备修改后的标题")

    operation_id = str(uuid4())
    body = {
        "operation_id": operation_id,
        "expected_task_version": task["version"],
        "base_completed": False,
        "completed": True,
    }
    merged = client.put(
        f"/api/v1/sync/tasks/{task['id']}/steps/{first_step['id']}/completion",
        json=body,
    )
    assert merged.status_code == 200, merged.text
    result = merged.json()
    assert result["merge"] == "rebased"
    assert result["task"]["title"] == "另一设备修改后的标题"
    assert result["task"]["version"] == renamed["version"] + 1
    assert result["task"]["steps"][0]["completed"] is True
    assert client.put(
        f"/api/v1/sync/tasks/{task['id']}/steps/{first_step['id']}/completion",
        json=body,
    ).json() == result

    converged = client.put(
        f"/api/v1/sync/tasks/{task['id']}/steps/{first_step['id']}/completion",
        json={
            **body,
            "operation_id": str(uuid4()),
        },
    )
    assert converged.status_code == 200
    assert converged.json()["merge"] == "converged"
    assert converged.json()["task"]["version"] == result["task"]["version"]

    with browser(live_server) as other:
        create_account(other, database_url)
        hidden = other.put(
            f"/api/v1/sync/tasks/{task['id']}/steps/{first_step['id']}/completion",
            json={**body, "operation_id": str(uuid4())},
        )
        assert hidden.status_code == 404
        assert hidden.json()["error"]["code"] == "task_not_found"

    deleted_step = client.request(
        "DELETE",
        f"/api/v1/tasks/{task['id']}/steps/{second_step['id']}",
        json={
            "operation_id": str(uuid4()),
            "expected_task_version": result["task"]["version"],
        },
    )
    assert deleted_step.status_code == 200, deleted_step.text
    missing_step = client.put(
        f"/api/v1/sync/tasks/{task['id']}/steps/{second_step['id']}/completion",
        json={
            "operation_id": str(uuid4()),
            "expected_task_version": result["task"]["version"],
            "base_completed": False,
            "completed": True,
        },
    )
    assert missing_step.status_code == 409
    assert missing_step.json()["error"]["code"] == "task_steps_changed"

    archived = client.put(
        f"/api/v1/tasks/{task['id']}/archive",
        json={
            "operation_id": str(uuid4()),
            "expected_version": deleted_step.json()["task"]["version"],
            "archived": True,
        },
    )
    assert archived.status_code == 200
    blocked = client.put(
        f"/api/v1/sync/tasks/{task['id']}/steps/{first_step['id']}/completion",
        json={
            "operation_id": str(uuid4()),
            "expected_task_version": archived.json()["task"]["version"],
            "base_completed": True,
            "completed": False,
        },
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "task_archived"
    assert owner_id


def test_occurrence_step_completion_is_scoped_and_terminal_conflicts(
    client,
    database_url,
):
    create_account(client, database_url)
    task = create_task(
        client,
        title="重复同步任务",
        steps=[{"title": "准备"}, {"title": "完成"}],
        labels=[],
        links=[],
    )
    scheduled = client.put(
        f"/api/v1/tasks/{task['id']}/schedule",
        json=schedule_payload(
            expected_task_version=task["version"],
            recurrence=recurrence("daily", count=2),
        ),
    )
    assert scheduled.status_code == 200, scheduled.text
    task = scheduled.json()["task"]
    occurrences = get_occurrences(client, "2026-10-06", "2026-10-08")
    first = occurrences[0]
    second = occurrences[1]
    first_step_id = task["steps"][0]["id"]
    second_step_id = task["steps"][1]["id"]

    first_result = client.put(
        "/api/v1/sync/task-occurrences/steps/completion",
        json={
            "operation_id": str(uuid4()),
            "task_id": task["id"],
            "occurrence_key": first["occurrence_key"],
            "step_id": first_step_id,
            "expected_task_version": task["version"],
            "expected_occurrence_version": first["occurrence_version"],
            "base_completed": False,
            "completed": True,
        },
    )
    assert first_result.status_code == 200, first_result.text
    first_body = first_result.json()
    assert first_body["merge"] == "applied"
    assert first_body["occurrence"]["status"] == "open"
    assert first_body["occurrence"]["completed_step_ids"] == [first_step_id]

    update_task_title(client, first_body["task"], "重复任务新标题")
    rebased = client.put(
        "/api/v1/sync/task-occurrences/steps/completion",
        json={
            "operation_id": str(uuid4()),
            "task_id": task["id"],
            "occurrence_key": first["occurrence_key"],
            "step_id": second_step_id,
            "expected_task_version": first_body["task"]["version"],
            "expected_occurrence_version": first_body["occurrence"][
                "occurrence_version"
            ],
            "base_completed": False,
            "completed": True,
        },
    )
    assert rebased.status_code == 200, rebased.text
    rebased_body = rebased.json()
    assert rebased_body["merge"] == "rebased"
    assert rebased_body["task"]["title"] == "重复任务新标题"
    assert set(rebased_body["occurrence"]["completed_step_ids"]) == {
        first_step_id,
        second_step_id,
    }
    untouched = get_occurrences(client, "2026-10-06", "2026-10-08")
    assert next(
        item for item in untouched
        if item["occurrence_key"] == second["occurrence_key"]
    )["completed_step_ids"] == []

    completed = client.put(
        f"/api/v1/tasks/{task['id']}/occurrences/status",
        json=occurrence_status_payload(
            first["occurrence_key"],
            expected_task_version=rebased_body["task"]["version"],
            expected_occurrence_version=rebased_body["occurrence"][
                "occurrence_version"
            ],
            completed_step_ids=[first_step_id, second_step_id],
        ),
    )
    assert completed.status_code == 200, completed.text
    blocked = client.put(
        "/api/v1/sync/task-occurrences/steps/completion",
        json={
            "operation_id": str(uuid4()),
            "task_id": task["id"],
            "occurrence_key": first["occurrence_key"],
            "step_id": first_step_id,
            "expected_task_version": completed.json()["task"]["version"],
            "expected_occurrence_version": completed.json()["occurrence"][
                "occurrence_version"
            ],
            "base_completed": True,
            "completed": False,
        },
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "task_occurrence_not_open"
