from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from tests.test_calendar_series import recurrence
from tests.test_focus_sessions import start_payload, transition_payload
from tests.test_task_schedules import (
    get_occurrences,
    initialize_settings,
    schedule_payload,
)
from tests.test_tasks import create_account, create_task

pytestmark = pytest.mark.integration


def progress_payload(focus: dict, task: dict, completed_step_ids: list[str], **changes):
    payload = {
        "operation_id": str(uuid4()),
        "expected_version": focus["version"],
        "expected_task_version": task["version"],
        "expected_occurrence_version": focus["occurrence_version"],
        "completed_step_ids": completed_step_ids,
    }
    payload.update(changes)
    return payload


def completion_payload(focus: dict, task_version: int, **changes):
    payload = {
        "operation_id": str(uuid4()),
        "expected_version": focus["version"],
        "expected_task_version": task_version,
        "expected_occurrence_version": focus["occurrence_version"],
    }
    payload.update(changes)
    return payload


def test_focus_progress_never_implicitly_completes_regular_task(client, database_url):
    create_account(client, database_url)
    initialize_settings(client)
    task = create_task(client, title="专注步骤任务")
    focus_response = client.post(
        "/api/v1/focus-sessions",
        json=start_payload(title=None, task_id=task["id"]),
    )
    assert focus_response.status_code == 201, focus_response.text
    focus = focus_response.json()["focus"]
    step_ids = [step["id"] for step in focus["steps"]]

    first = client.put(
        f"/api/v1/focus-sessions/{focus['id']}/progress",
        json=progress_payload(focus, task, step_ids[:1]),
    )
    assert first.status_code == 200, first.text
    focus = first.json()["focus"]
    task = first.json()["task"]
    assert [step["completed"] for step in focus["steps"]] == [True, False]
    assert task["status"] == "open"

    operation_id = uuid4()
    all_body = progress_payload(
        focus,
        task,
        step_ids,
        operation_id=str(operation_id),
    )
    all_done = client.put(
        f"/api/v1/focus-sessions/{focus['id']}/progress",
        json=all_body,
    )
    assert all_done.status_code == 200, all_done.text
    focus = all_done.json()["focus"]
    task = all_done.json()["task"]
    assert focus["task_status"] == "open"
    assert task["step_progress"] == {"total": 2, "completed": 2}
    assert task["status"] == "open"
    assert (
        client.put(
            f"/api/v1/focus-sessions/{focus['id']}/progress",
            json=all_body,
        ).json()
        == all_done.json()
    )

    finished_response = client.post(
        f"/api/v1/focus-sessions/{focus['id']}/transitions",
        json=transition_payload(focus["version"], "finish"),
    )
    assert finished_response.status_code == 200
    focus = finished_response.json()["focus"]
    assert focus["task_status"] == "open"

    completed_response = client.post(
        f"/api/v1/focus-sessions/{focus['id']}/complete-task",
        json=completion_payload(focus, task["version"]),
    )
    assert completed_response.status_code == 200, completed_response.text
    completed = completed_response.json()
    assert completed["focus"]["status"] == "finished"
    assert completed["focus"]["task_status"] == "completed"
    assert completed["task"]["status"] == "completed"
    assert completed["focus"]["effective_duration_seconds"] == (
        focus["effective_duration_seconds"]
    )


def test_focus_progress_updates_only_repeating_occurrence(client, database_url):
    create_account(client, database_url)
    initialize_settings(client)
    task = create_task(client, title="每日复习")
    target_date = (datetime.now(UTC) + timedelta(days=1)).date()
    schedule_response = client.put(
        f"/api/v1/tasks/{task['id']}/schedule",
        json=schedule_payload(
            due_date=target_date.isoformat(),
            recurrence=recurrence("daily", count=3),
        ),
    )
    assert schedule_response.status_code == 200, schedule_response.text
    task = schedule_response.json()["task"]
    occurrence = get_occurrences(
        client,
        target_date.isoformat(),
        (target_date + timedelta(days=1)).isoformat(),
    )[0]

    focus_response = client.post(
        "/api/v1/focus-sessions",
        json=start_payload(
            title=None,
            task_id=task["id"],
            occurrence_key=occurrence["occurrence_key"],
        ),
    )
    assert focus_response.status_code == 201, focus_response.text
    focus = focus_response.json()["focus"]
    step_ids = [step["id"] for step in focus["steps"]]

    progress = client.put(
        f"/api/v1/focus-sessions/{focus['id']}/progress",
        json=progress_payload(focus, task, step_ids),
    )
    assert progress.status_code == 200, progress.text
    task = progress.json()["task"]
    focus = progress.json()["focus"]
    assert focus["task_status"] == "open"
    assert focus["occurrence_version"] == 1
    assert all(step["completed"] for step in focus["steps"])
    assert all(
        not step["completed"]
        for step in client.get(f"/api/v1/tasks/{task['id']}").json()["task"]["steps"]
    )

    finished = client.post(
        f"/api/v1/focus-sessions/{focus['id']}/transitions",
        json=transition_payload(focus["version"], "finish"),
    ).json()["focus"]
    completed = client.post(
        f"/api/v1/focus-sessions/{focus['id']}/complete-task",
        json=completion_payload(
            finished,
            task["version"],
            expected_occurrence_version=focus["occurrence_version"],
        ),
    )
    assert completed.status_code == 200, completed.text
    result = completed.json()
    assert result["occurrence"]["status"] == "completed"
    assert result["task"]["status"] == "open"
    assert result["focus"]["task_status"] == "completed"


def test_focus_progress_validates_versions_state_and_task_lifecycle(
    client,
    database_url,
):
    create_account(client, database_url)
    initialize_settings(client)
    task = create_task(client, title="并发步骤任务")
    focus = client.post(
        "/api/v1/focus-sessions",
        json=start_payload(title=None, task_id=task["id"]),
    ).json()["focus"]
    step_id = focus["steps"][0]["id"]

    stale_focus = client.put(
        f"/api/v1/focus-sessions/{focus['id']}/progress",
        json=progress_payload(
            focus,
            task,
            [step_id],
            expected_version=focus["version"] + 1,
        ),
    )
    assert stale_focus.status_code == 409
    assert stale_focus.json()["error"]["code"] == "focus_version_conflict"

    stale_task = client.put(
        f"/api/v1/focus-sessions/{focus['id']}/progress",
        json=progress_payload(
            focus,
            task,
            [step_id],
            expected_task_version=task["version"] + 1,
        ),
    )
    assert stale_task.status_code == 409
    assert stale_task.json()["error"]["code"] == "task_version_conflict"

    archived = client.put(
        f"/api/v1/tasks/{task['id']}/archive",
        json={
            "operation_id": str(uuid4()),
            "expected_version": task["version"],
            "archived": True,
        },
    )
    assert archived.status_code == 200
    blocked = client.put(
        f"/api/v1/focus-sessions/{focus['id']}/progress",
        json=progress_payload(
            focus,
            archived.json()["task"],
            [step_id],
        ),
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "task_archived"


def test_task_completion_requires_terminal_focus(client, database_url):
    create_account(client, database_url)
    initialize_settings(client)
    task = create_task(client, title="结束后确认")
    focus = client.post(
        "/api/v1/focus-sessions",
        json=start_payload(title=None, task_id=task["id"]),
    ).json()["focus"]
    blocked = client.post(
        f"/api/v1/focus-sessions/{focus['id']}/complete-task",
        json=completion_payload(focus, task["version"]),
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "focus_not_terminal"
