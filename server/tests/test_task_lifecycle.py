import asyncio
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest

from itime.database import Database
from tests.conftest import psycopg_url
from tests.test_task_schedules import initialize_settings, schedule_payload
from tests.test_tasks import (
    browser,
    create_account,
    create_task,
    project_payload,
)

pytestmark = pytest.mark.integration


def archive_payload(
    expected_version: int,
    *,
    archived: bool = True,
    operation_id=None,
) -> dict:
    return {
        "operation_id": str(operation_id or uuid4()),
        "expected_version": expected_version,
        "archived": archived,
    }


def lifecycle_payload(expected_version: int, *, operation_id=None) -> dict:
    return {
        "operation_id": str(operation_id or uuid4()),
        "expected_version": expected_version,
    }


def archive_task(
    client,
    task_id: str,
    expected_version: int,
    *,
    archived: bool = True,
    operation_id=None,
):
    return client.put(
        f"/api/v1/tasks/{task_id}/archive",
        json=archive_payload(
            expected_version,
            archived=archived,
            operation_id=operation_id,
        ),
    )


def trash_task(client, task_id: str, expected_version: int, *, operation_id=None):
    return client.request(
        "DELETE",
        f"/api/v1/tasks/{task_id}",
        json=lifecycle_payload(expected_version, operation_id=operation_id),
    )


def restore_task(client, task_id: str, expected_version: int, *, operation_id=None):
    return client.post(
        f"/api/v1/tasks/{task_id}/restore",
        json=lifecycle_payload(expected_version, operation_id=operation_id),
    )


def task_ids(client, lifecycle: str | None = None, **filters) -> set[str]:
    params = dict(filters)
    if lifecycle is not None:
        params["lifecycle"] = lifecycle
    response = client.get("/api/v1/tasks", params=params)
    assert response.status_code == 200, response.text
    return {item["id"] for item in response.json()["items"]}


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def assert_lifecycle(task: dict, expected: str) -> None:
    assert task["lifecycle"] == expected
    assert task["purged_at"] is None
    if expected == "active":
        assert task["archived_at"] is None
        assert task["deleted_at"] is None
        assert task["purge_after"] is None
        assert task["trash_remaining_days"] is None
    elif expected == "archived":
        assert task["archived_at"] is not None
        assert task["deleted_at"] is None
        assert task["purge_after"] is None
        assert task["trash_remaining_days"] is None
    else:
        assert expected == "trashed"
        assert task["deleted_at"] is not None
        assert task["purge_after"] is not None
        assert task["trash_remaining_days"] == 30
        assert (
            parse_timestamp(task["purge_after"])
            - parse_timestamp(task["deleted_at"])
            == timedelta(days=30)
        )


def assert_write_blocked(response, *, saved: str = "not_saved") -> None:
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["saved"] == saved
    assert error["request_id"]


def daily_recurrence(count: int) -> dict:
    return {
        "frequency": "daily",
        "interval": 1,
        "weekdays": [],
        "month": None,
        "day_of_month": None,
        "until": None,
        "count": count,
        "week_numbers": [],
    }


def test_archive_is_orthogonal_to_completion_and_filters_counts(
    client,
    database_url,
):
    create_account(client, database_url)
    project_response = client.post(
        "/api/v1/projects",
        json=project_payload(name="生命周期项目"),
    )
    assert project_response.status_code == 201, project_response.text
    project_id = project_response.json()["project"]["id"]
    common = {
        "project_id": project_id,
        "labels": ["生命周期"],
        "steps": [],
        "links": [],
    }
    active = create_task(client, title="活动任务", **common)
    archived_open = create_task(client, title="未完成归档任务", **common)
    archived_completed = create_task(client, title="已完成归档任务", **common)
    trashed = create_task(client, title="回收站任务", **common)

    open_archive_response = archive_task(
        client,
        archived_open["id"],
        archived_open["version"],
    )
    assert open_archive_response.status_code == 200, open_archive_response.text
    open_archive = open_archive_response.json()["task"]
    assert_lifecycle(open_archive, "archived")
    assert open_archive["status"] == "open"

    completion = client.put(
        f"/api/v1/tasks/{archived_completed['id']}/completion",
        json={
            "operation_id": str(uuid4()),
            "expected_version": archived_completed["version"],
            "completed": True,
        },
    )
    assert completion.status_code == 200, completion.text
    completed_task = completion.json()["task"]
    completed_at = completed_task["completed_at"]
    completed_archive_response = archive_task(
        client,
        completed_task["id"],
        completed_task["version"],
    )
    assert completed_archive_response.status_code == 200, completed_archive_response.text
    completed_archive = completed_archive_response.json()["task"]
    assert_lifecycle(completed_archive, "archived")
    assert completed_archive["status"] == "completed"
    assert completed_archive["completed_at"] == completed_at

    trashed_response = trash_task(client, trashed["id"], trashed["version"])
    assert trashed_response.status_code == 200, trashed_response.text
    assert_lifecycle(trashed_response.json()["task"], "trashed")

    assert task_ids(client) == {active["id"]}
    assert task_ids(client, "active", label="生命周期") == {active["id"]}
    assert task_ids(client, "archived", label="生命周期") == {
        archived_open["id"],
        archived_completed["id"],
    }
    assert task_ids(
        client,
        "archived",
        label="生命周期",
        status="completed",
    ) == {archived_completed["id"]}
    assert task_ids(client, "trashed", label="生命周期") == {trashed["id"]}

    project = client.get(f"/api/v1/projects/{project_id}")
    assert project.status_code == 200, project.text
    assert project.json()["project"]["task_count"] == 1
    assert project.json()["project"]["open_task_count"] == 1
    labels = client.get("/api/v1/labels")
    assert labels.status_code == 200, labels.text
    lifecycle_label = next(
        item for item in labels.json()["items"] if item["name"] == "生命周期"
    )
    assert lifecycle_label["task_count"] == 1


def test_delete_preserves_archive_scope_and_restore_is_idempotent(
    client,
    database_url,
):
    create_account(client, database_url)
    task = create_task(client, steps=[], links=[], labels=[])
    archived_response = archive_task(client, task["id"], task["version"])
    assert archived_response.status_code == 200, archived_response.text
    archived = archived_response.json()["task"]
    archived_at = archived["archived_at"]

    delete_response = trash_task(client, task["id"], archived["version"])
    assert delete_response.status_code == 200, delete_response.text
    deleted = delete_response.json()["task"]
    assert_lifecycle(deleted, "trashed")
    assert deleted["archived_at"] == archived_at
    assert task_ids(client, "archived") == set()
    assert task_ids(client, "trashed") == {task["id"]}

    stale = restore_task(client, task["id"], archived["version"])
    assert stale.status_code == 409
    assert stale.json()["error"]["details"]["current_version"] == deleted["version"]

    restore_operation = uuid4()
    restored_response = restore_task(
        client,
        task["id"],
        deleted["version"],
        operation_id=restore_operation,
    )
    assert restored_response.status_code == 200, restored_response.text
    restored = restored_response.json()["task"]
    assert_lifecycle(restored, "archived")
    assert restored["archived_at"] == archived_at
    assert restored["status"] == task["status"]
    assert (
        restore_task(
            client,
            task["id"],
            deleted["version"],
            operation_id=restore_operation,
        ).json()
        == restored_response.json()
    )

    active_response = archive_task(
        client,
        task["id"],
        restored["version"],
        archived=False,
    )
    assert active_response.status_code == 200, active_response.text
    active = active_response.json()["task"]
    assert_lifecycle(active, "active")
    assert task_ids(client) == {task["id"]}


def test_lifecycle_ownership_idempotency_and_version_conflicts(
    client,
    database_url,
    live_server,
):
    create_account(client, database_url)
    task = create_task(client, steps=[], links=[], labels=[])
    archive_operation = uuid4()
    archive_request = archive_payload(
        task["version"],
        operation_id=archive_operation,
    )
    archived_response = client.put(
        f"/api/v1/tasks/{task['id']}/archive",
        json=archive_request,
    )
    assert archived_response.status_code == 200, archived_response.text
    archived = archived_response.json()["task"]
    assert archived["version"] == task["version"] + 1
    assert (
        client.put(
            f"/api/v1/tasks/{task['id']}/archive",
            json=archive_request,
        ).json()
        == archived_response.json()
    )

    reused = client.put(
        f"/api/v1/tasks/{task['id']}/archive",
        json={**archive_request, "archived": False},
    )
    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "operation_id_reused"
    no_op = archive_task(client, task["id"], archived["version"])
    assert no_op.status_code == 200, no_op.text
    assert no_op.json()["task"]["version"] == archived["version"]
    assert no_op.json()["task"]["archived_at"] == archived["archived_at"]

    stale_delete = trash_task(client, task["id"], task["version"])
    assert stale_delete.status_code == 409
    assert (
        stale_delete.json()["error"]["details"]["current_version"]
        == archived["version"]
    )

    with browser(live_server) as other:
        create_account(other, database_url)
        assert (
            archive_task(other, task["id"], archived["version"]).status_code
            == 404
        )
        assert trash_task(other, task["id"], archived["version"]).status_code == 404
        assert (
            restore_task(other, task["id"], archived["version"]).status_code
            == 404
        )

        delete_operation = uuid4()
        delete_request = lifecycle_payload(
            archived["version"],
            operation_id=delete_operation,
        )
        deleted_response = client.request(
            "DELETE",
            f"/api/v1/tasks/{task['id']}",
            json=delete_request,
        )
        assert deleted_response.status_code == 200, deleted_response.text
        deleted = deleted_response.json()["task"]
        assert deleted["version"] == archived["version"] + 1
        assert (
            client.request(
                "DELETE",
                f"/api/v1/tasks/{task['id']}",
                json=delete_request,
            ).json()
            == deleted_response.json()
        )
        assert restore_task(other, task["id"], deleted["version"]).status_code == 404


def test_lifecycle_changes_block_replay_of_older_task_operations(
    client,
    database_url,
):
    _, user_id = create_account(client, database_url)
    task = create_task(client, steps=[], links=[], labels=["旧操作"])
    edit_operation = uuid4()
    edit_payload = {
        "operation_id": str(edit_operation),
        "expected_version": task["version"],
        "title": "旧操作修改后的标题",
        "description": task["description"],
        "project_id": None,
        "estimated_minutes": task["estimated_minutes"],
        "priority": task["priority"],
        "labels": ["旧操作"],
        "links": [],
    }
    edited_response = client.put(
        f"/api/v1/tasks/{task['id']}",
        json=edit_payload,
    )
    assert edited_response.status_code == 200, edited_response.text
    edited = edited_response.json()["task"]

    archive_operation = uuid4()
    archive_request = archive_payload(
        edited["version"],
        operation_id=archive_operation,
    )
    archived_response = client.put(
        f"/api/v1/tasks/{task['id']}/archive",
        json=archive_request,
    )
    assert archived_response.status_code == 200, archived_response.text
    replayed_edit = client.put(
        f"/api/v1/tasks/{task['id']}",
        json=edit_payload,
    )
    assert_write_blocked(replayed_edit, saved="saved")

    archived = archived_response.json()["task"]
    deleted_response = trash_task(client, task["id"], archived["version"])
    assert deleted_response.status_code == 200, deleted_response.text
    replayed_archive = client.put(
        f"/api/v1/tasks/{task['id']}/archive",
        json=archive_request,
    )
    assert_write_blocked(replayed_archive, saved="saved")

    with psycopg.connect(psycopg_url(database_url)) as connection:
        responses = connection.execute(
            """
            SELECT operation_id, response
            FROM user_operations
            WHERE owner_id=%s AND operation_id = ANY(%s)
            """,
            (user_id, [edit_operation, archive_operation]),
        ).fetchall()
    assert {row[0] for row in responses} == {edit_operation, archive_operation}
    assert all(row[1] is None for row in responses)


@pytest.mark.parametrize("lifecycle", ["archived", "trashed"])
def test_non_active_tasks_reject_edits_progress_schedule_and_occurrence_writes(
    lifecycle,
    client,
    database_url,
):
    create_account(client, database_url)
    initialize_settings(client)
    task = create_task(client, labels=["只读"], links=[])
    scheduled_response = client.put(
        f"/api/v1/tasks/{task['id']}/schedule",
        json=schedule_payload(
            expected_task_version=task["version"],
            due_date="2035-01-01",
            recurrence=daily_recurrence(3),
        ),
    )
    assert scheduled_response.status_code == 200, scheduled_response.text
    scheduled = scheduled_response.json()["task"]
    if lifecycle == "archived":
        transition = archive_task(
            client,
            task["id"],
            scheduled["version"],
        )
    else:
        transition = trash_task(
            client,
            task["id"],
            scheduled["version"],
        )
    assert transition.status_code == 200, transition.text
    readonly = transition.json()["task"]
    assert_lifecycle(readonly, lifecycle)
    version = readonly["version"]
    first_step = task["steps"][0]
    step_ids = [step["id"] for step in task["steps"]]

    replacement = schedule_payload(
        due_date="2035-01-02",
        recurrence=daily_recurrence(2),
    )
    replacement.pop("operation_id")
    replacement.pop("expected_task_version")
    blocked_responses = [
        client.put(
            f"/api/v1/tasks/{task['id']}",
            json={
                "operation_id": str(uuid4()),
                "expected_version": version,
                "title": "不应保存的标题",
                "description": task["description"],
                "project_id": None,
                "estimated_minutes": task["estimated_minutes"],
                "priority": 3,
                "labels": ["不应保存"],
                "links": [],
            },
        ),
        client.put(
            f"/api/v1/tasks/{task['id']}/completion",
            json={
                "operation_id": str(uuid4()),
                "expected_version": version,
                "completed": True,
            },
        ),
        client.post(
            f"/api/v1/tasks/{task['id']}/steps",
            json={
                "operation_id": str(uuid4()),
                "expected_task_version": version,
                "title": "不应新增",
            },
        ),
        client.put(
            f"/api/v1/tasks/{task['id']}/steps/{first_step['id']}",
            json={
                "operation_id": str(uuid4()),
                "expected_task_version": version,
                "title": "不应更新",
                "completed": True,
            },
        ),
        client.put(
            f"/api/v1/tasks/{task['id']}/steps",
            json={
                "operation_id": str(uuid4()),
                "expected_task_version": version,
                "step_ids": list(reversed(step_ids)),
            },
        ),
        client.request(
            "DELETE",
            f"/api/v1/tasks/{task['id']}/steps/{first_step['id']}",
            json={
                "operation_id": str(uuid4()),
                "expected_task_version": version,
            },
        ),
        client.put(
            f"/api/v1/tasks/{task['id']}/schedule",
            json=schedule_payload(
                expected_task_version=version,
                due_date="2035-02-01",
                recurrence=daily_recurrence(3),
            ),
        ),
        client.put(
            f"/api/v1/tasks/{task['id']}/occurrences/status",
            json={
                "operation_id": str(uuid4()),
                "expected_task_version": version,
                "expected_occurrence_version": 0,
                "occurrence_key": "2035-01-01",
                "status": "completed",
                "completed_step_ids": [],
            },
        ),
        client.put(
            f"/api/v1/tasks/{task['id']}/occurrences/schedule",
            json={
                "operation_id": str(uuid4()),
                "expected_task_version": version,
                "expected_occurrence_version": 0,
                "occurrence_key": "2035-01-01",
                "due_date": "2035-02-01",
                "due_time": None,
                "ambiguous_utc_offset_minutes": None,
            },
        ),
        client.post(
            f"/api/v1/tasks/{task['id']}/occurrences/makeup",
            json={
                "operation_id": str(uuid4()),
                "expected_task_version": version,
                "original_occurrence_key": "2035-01-01",
                "due_date": "2035-02-02",
                "due_time": None,
                "ambiguous_utc_offset_minutes": None,
            },
        ),
        client.post(
            f"/api/v1/tasks/{task['id']}/schedule/split",
            json={
                "operation_id": str(uuid4()),
                "expected_task_version": version,
                "occurrence_key": "2035-01-02",
                "replacement": replacement,
            },
        ),
        client.post(
            "/api/v1/tasks/actions/bulk",
            json={
                "operation_id": str(uuid4()),
                "action": "set_priority",
                "priority": 3,
                "items": [{"id": task["id"], "expected_version": version}],
            },
        ),
    ]
    for response in blocked_responses:
        assert_write_blocked(response)

    unchanged_response = client.get(f"/api/v1/tasks/{task['id']}")
    assert unchanged_response.status_code == 200, unchanged_response.text
    unchanged = unchanged_response.json()["task"]
    assert_lifecycle(unchanged, lifecycle)
    assert unchanged["version"] == version
    assert unchanged["title"] == task["title"]
    assert unchanged["status"] == "open"
    assert [step["title"] for step in unchanged["steps"]] == [
        step["title"] for step in task["steps"]
    ]
    assert unchanged["schedule"]["due_date"] == "2035-01-01"
    with psycopg.connect(psycopg_url(database_url)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM task_occurrence_states WHERE task_id=%s",
            (task["id"],),
        ).fetchone()[0] == 0


def test_task_occurrences_are_visible_only_while_task_is_active(
    client,
    database_url,
):
    create_account(client, database_url)
    initialize_settings(client)
    task = create_task(client, steps=[], links=[], labels=[])
    scheduled_response = client.put(
        f"/api/v1/tasks/{task['id']}/schedule",
        json=schedule_payload(
            expected_task_version=task["version"],
            due_date="2035-03-01",
            recurrence=daily_recurrence(2),
        ),
    )
    assert scheduled_response.status_code == 200, scheduled_response.text
    scheduled = scheduled_response.json()["task"]

    def occurrence_task_ids() -> set[str]:
        response = client.get(
            "/api/v1/task-occurrences",
            params={"start": "2035-03-01", "end": "2035-03-03"},
        )
        assert response.status_code == 200, response.text
        return {item["task_id"] for item in response.json()["items"]}

    assert occurrence_task_ids() == {task["id"]}
    archived_response = archive_task(
        client,
        task["id"],
        scheduled["version"],
    )
    assert archived_response.status_code == 200, archived_response.text
    archived = archived_response.json()["task"]
    assert occurrence_task_ids() == set()

    active_response = archive_task(
        client,
        task["id"],
        archived["version"],
        archived=False,
    )
    assert active_response.status_code == 200, active_response.text
    active = active_response.json()["task"]
    assert occurrence_task_ids() == {task["id"]}

    deleted_response = trash_task(client, task["id"], active["version"])
    assert deleted_response.status_code == 200, deleted_response.text
    deleted = deleted_response.json()["task"]
    assert occurrence_task_ids() == set()

    restored_response = restore_task(client, task["id"], deleted["version"])
    assert restored_response.status_code == 200, restored_response.text
    assert_lifecycle(restored_response.json()["task"], "active")
    assert occurrence_task_ids() == {task["id"]}


def test_cleanup_redacts_expired_task_and_preserves_successor_foreign_key(
    client,
    database_url,
    settings,
):
    _, user_id = create_account(client, database_url)
    initialize_settings(client)
    create_operation = uuid4()
    secret_title = "到期后不可见标题 7fa38d"
    secret_description = "到期后不可见正文 3b12e9"
    secret_step = "到期后不可见步骤 c40d61"
    secret_url = "https://example.com/private-lifecycle-93bd"
    task = create_task(
        client,
        operation_id=create_operation,
        title=secret_title,
        description=secret_description,
        labels=["待清理"],
        steps=[{"title": secret_step}],
        links=[{"title": "私密链接", "url": secret_url}],
    )

    schedule_operation = uuid4()
    scheduled_response = client.put(
        f"/api/v1/tasks/{task['id']}/schedule",
        json=schedule_payload(
            operation_id=schedule_operation,
            expected_task_version=task["version"],
            due_date="2035-04-01",
            recurrence=daily_recurrence(4),
        ),
    )
    assert scheduled_response.status_code == 200, scheduled_response.text
    scheduled = scheduled_response.json()["task"]

    replacement = schedule_payload(
        due_date="2035-04-03",
        recurrence=daily_recurrence(2),
    )
    replacement.pop("operation_id")
    replacement.pop("expected_task_version")
    split_operation = uuid4()
    split_payload = {
        "operation_id": str(split_operation),
        "expected_task_version": scheduled["version"],
        "occurrence_key": "2035-04-03",
        "replacement": replacement,
    }
    split_response = client.post(
        f"/api/v1/tasks/{task['id']}/schedule/split",
        json=split_payload,
    )
    assert split_response.status_code == 201, split_response.text
    predecessor = split_response.json()["predecessor"]
    successor = split_response.json()["successor"]
    assert successor["schedule"]["predecessor_task_id"] == task["id"]

    delete_operation = uuid4()
    delete_request = lifecycle_payload(
        predecessor["version"],
        operation_id=delete_operation,
    )
    deleted_response = client.request(
        "DELETE",
        f"/api/v1/tasks/{task['id']}",
        json=delete_request,
    )
    assert deleted_response.status_code == 200, deleted_response.text
    deleted = deleted_response.json()["task"]
    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            """
            UPDATE tasks
            SET deleted_at=now() - interval '30 days 1 second',
                purge_after=now() - interval '1 second'
            WHERE id=%s
            """,
            (task["id"],),
        )

    async def run_cleanup() -> None:
        from itime.tasks.lifecycle_service import cleanup_expired_tasks

        database = Database(settings)
        try:
            assert await cleanup_expired_tasks(database, limit=100) == 1
            assert await cleanup_expired_tasks(database, limit=100) == 0
        finally:
            await database.close()

    asyncio.run(run_cleanup())

    owner_id = UUID(user_id)
    operation_ids = {
        create_operation,
        schedule_operation,
        split_operation,
        delete_operation,
    }
    with psycopg.connect(psycopg_url(database_url)) as connection:
        tombstone = connection.execute(
            """
            SELECT owner_id, title, description, project_id,
                   estimated_minutes, estimate_source, priority, status,
                   completed_at, archived_at, deleted_at, purge_after,
                   purged_at, version
            FROM tasks
            WHERE id=%s
            """,
            (task["id"],),
        ).fetchone()
        assert tombstone[0] == owner_id
        assert tombstone[1] != secret_title
        assert tombstone[2] != secret_description
        assert tombstone[3:6] == (None, None, None)
        assert tombstone[8] is None
        assert tombstone[9] is None
        assert tombstone[10] is not None
        assert tombstone[11] is not None
        assert tombstone[12] is not None
        assert tombstone[13] == deleted["version"] + 1

        dependent_counts = connection.execute(
            """
            SELECT
                (SELECT count(*) FROM task_steps WHERE task_id=%s),
                (SELECT count(*) FROM task_links WHERE task_id=%s),
                (SELECT count(*) FROM task_labels WHERE task_id=%s),
                (SELECT count(*) FROM task_schedules WHERE task_id=%s),
                (SELECT count(*) FROM task_occurrence_states WHERE task_id=%s)
            """,
            (task["id"],) * 5,
        ).fetchone()
        assert dependent_counts == (0, 0, 0, 0, 0)
        assert connection.execute(
            """
            SELECT predecessor_task_id
            FROM task_schedules
            WHERE task_id=%s
            """,
            (successor["id"],),
        ).fetchone()[0] == UUID(task["id"])

        operations = connection.execute(
            """
            SELECT operation_id, request_hash, response
            FROM user_operations
            WHERE owner_id=%s AND operation_id = ANY(%s)
            """,
            (owner_id, list(operation_ids)),
        ).fetchall()
        assert {row[0] for row in operations} == operation_ids
        assert all(len(row[1]) == 64 for row in operations)
        assert all(row[2] is None for row in operations)
        latest_change = connection.execute(
            """
            SELECT action, entity_version
            FROM change_events
            WHERE owner_id=%s AND entity_type='task' AND entity_id=%s
            ORDER BY sequence DESC
            LIMIT 1
            """,
            (owner_id, task["id"]),
        ).fetchone()
        assert latest_change == ("purged", deleted["version"] + 1)

    assert task["id"] not in task_ids(client, "active")
    assert task["id"] not in task_ids(client, "archived")
    assert task["id"] not in task_ids(client, "trashed")
    expired_detail = client.get(f"/api/v1/tasks/{task['id']}")
    assert expired_detail.status_code == 410, expired_detail.text
    expired_restore = restore_task(
        client,
        task["id"],
        deleted["version"] + 1,
    )
    assert expired_restore.status_code == 410, expired_restore.text
    for secret in (secret_title, secret_description, secret_step, secret_url):
        assert secret not in expired_detail.text
        assert secret not in expired_restore.text

    replayed_delete = client.request(
        "DELETE",
        f"/api/v1/tasks/{task['id']}",
        json=delete_request,
    )
    assert_write_blocked(replayed_delete, saved="saved")
    reused_delete = client.request(
        "DELETE",
        f"/api/v1/tasks/{task['id']}",
        json={
            **delete_request,
            "expected_version": deleted["version"] + 1,
        },
    )
    assert reused_delete.status_code == 409
    assert reused_delete.json()["error"]["code"] == "operation_id_reused"

    successor_detail = client.get(f"/api/v1/tasks/{successor['id']}")
    assert successor_detail.status_code == 200, successor_detail.text
    assert_lifecycle(successor_detail.json()["task"], "active")
    successor_occurrences = client.get(
        "/api/v1/task-occurrences",
        params={"start": "2035-04-03", "end": "2035-04-05"},
    )
    assert successor_occurrences.status_code == 200, successor_occurrences.text
    assert {
        item["task_id"] for item in successor_occurrences.json()["items"]
    } == {successor["id"]}


def test_cleanup_batch_limit_and_two_workers_do_not_process_twice(
    client,
    database_url,
    settings,
):
    create_account(client, database_url)
    tasks = [
        create_task(
            client,
            title=f"并发清理任务 {number}",
            steps=[],
            links=[],
            labels=[],
        )
        for number in range(3)
    ]
    for task in tasks:
        response = trash_task(client, task["id"], task["version"])
        assert response.status_code == 200, response.text
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        connection.execute(
            """
            UPDATE tasks
            SET deleted_at=now() - interval '30 days',
                purge_after=now() - interval '1 second'
            WHERE id = ANY(%s)
            """,
            ([UUID(task["id"]) for task in tasks],),
        )

    async def run_workers() -> list[int]:
        from itime.tasks.lifecycle_service import cleanup_expired_tasks

        databases = [Database(settings), Database(settings)]
        try:
            return list(
                await asyncio.gather(
                    *(cleanup_expired_tasks(database, limit=2) for database in databases)
                )
            )
        finally:
            await asyncio.gather(*(database.close() for database in databases))

    assert sorted(asyncio.run(run_workers())) == [1, 2]
    for task in tasks:
        response = client.get(f"/api/v1/tasks/{task['id']}")
        assert response.status_code == 410, response.text


def test_cleanup_worker_once_runs_in_a_separate_process(
    client,
    database_url,
):
    create_account(client, database_url)
    secret = "独立进程必须清理的正文 d5f17a"
    task = create_task(
        client,
        title=secret,
        description=secret,
        steps=[],
        links=[],
        labels=[],
    )
    deleted = trash_task(client, task["id"], task["version"])
    assert deleted.status_code == 200, deleted.text
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        connection.execute(
            "UPDATE tasks SET purge_after=now() WHERE id=%s",
            (task["id"],),
        )

    server = Path(__file__).resolve().parents[1]
    process = subprocess.run(
        [sys.executable, "-m", "itime.tasks.cleanup_worker", "--once"],
        cwd=server,
        env={
            **os.environ,
            "ITIME_ENVIRONMENT": "test",
            "ITIME_DATABASE_URL": database_url,
        },
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    assert secret not in process.stdout
    assert secret not in process.stderr
    expired = client.get(f"/api/v1/tasks/{task['id']}")
    assert expired.status_code == 410, expired.text
    assert secret not in expired.text
