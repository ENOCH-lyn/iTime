import subprocess
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest

from tests.conftest import isolated_database, migrate, psycopg_url
from tests.test_calendar_series import browser, recurrence
from tests.test_tasks import PASSWORD, create_account, create_task

pytestmark = pytest.mark.integration
AUTH = "/api/v1/auth"


def address(prefix: str = "task-schedule") -> str:
    return f"{prefix}-{uuid4().hex}@example.com"


def login_account(client, email: str) -> None:
    response = client.post(
        f"{AUTH}/login",
        json={
            "email": email,
            "password": PASSWORD,
            "device_name": "任务日期测试",
        },
    )
    assert response.status_code == 200, response.text
    client.headers["x-csrf-token"] = response.json()["csrf_token"]


def initialize_settings(client, time_zone: str = "Asia/Shanghai") -> None:
    response = client.put(
        "/api/v1/time-settings",
        json={
            "operation_id": str(uuid4()),
            "expected_version": 0,
            "time_zone": time_zone,
            "week_starts_on": 1,
            "activity_day_start_minute": 240,
        },
    )
    assert response.status_code == 200, response.text


def schedule_payload(operation_id=None, **changes) -> dict:
    payload = {
        "operation_id": str(operation_id or uuid4()),
        "expected_task_version": 1,
        "due_kind": "date",
        "due_date": "2026-10-06",
        "due_time": None,
        "time_zone": None,
        "ambiguous_utc_offset_minutes": None,
        "splittable": True,
        "minimum_block_minutes": 15,
        "recurrence": recurrence("none"),
    }
    payload.update(changes)
    return payload


def occurrence_status_payload(
    occurrence_key: str,
    *,
    expected_task_version: int,
    expected_occurrence_version: int = 0,
    status: str = "completed",
    completed_step_ids=None,
    operation_id=None,
) -> dict:
    return {
        "operation_id": str(operation_id or uuid4()),
        "expected_task_version": expected_task_version,
        "expected_occurrence_version": expected_occurrence_version,
        "occurrence_key": occurrence_key,
        "status": status,
        "completed_step_ids": completed_step_ids or [],
    }


def get_occurrences(client, start: str, end: str) -> list[dict]:
    response = client.get(
        "/api/v1/task-occurrences",
        params={"start": start, "end": end},
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


def test_date_and_datetime_due_settings_are_versioned_and_time_zone_stable(
    client,
    database_url,
):
    create_account(client, database_url)
    initialize_settings(client)
    task = create_task(client, steps=[], links=[], labels=[])
    task_id = task["id"]
    assert task["schedule"]["configured"] is False
    assert task["schedule"]["minimum_block_minutes"] == 15

    operation_id = uuid4()
    payload = schedule_payload(operation_id)
    dated = client.put(f"/api/v1/tasks/{task_id}/schedule", json=payload)
    assert dated.status_code == 200, dated.text
    scheduled_task = dated.json()["task"]
    assert scheduled_task["version"] == 2
    assert scheduled_task["schedule"]["due_kind"] == "date"
    assert scheduled_task["schedule"]["time_zone"] == "Asia/Shanghai"
    assert client.put(
        f"/api/v1/tasks/{task_id}/schedule",
        json=payload,
    ).json() == dated.json()

    item = get_occurrences(client, "2026-10-06", "2026-10-07")[0]
    assert item["due_date"] == "2026-10-06"
    assert item["due_at"] is None
    assert item["overdue_after"] == "2026-10-06T16:00:00Z"
    assert item["splittable"] is True
    assert item["minimum_block_minutes"] == 15

    unchanged = client.put(
        f"/api/v1/tasks/{task_id}/schedule",
        json=schedule_payload(expected_task_version=2),
    )
    assert unchanged.status_code == 200
    assert unchanged.json()["task"]["version"] == 2

    timed = client.put(
        f"/api/v1/tasks/{task_id}/schedule",
        json=schedule_payload(
            expected_task_version=2,
            due_kind="datetime",
            due_time="18:30:00",
            splittable=False,
            minimum_block_minutes=30,
        ),
    )
    assert timed.status_code == 200, timed.text
    assert timed.json()["task"]["version"] == 3
    assert timed.json()["task"]["schedule"]["due_at"] == "2026-10-06T10:30:00Z"

    settings = client.get("/api/v1/time-settings").json()["settings"]
    changed_zone = client.put(
        "/api/v1/time-settings",
        json={
            "operation_id": str(uuid4()),
            "expected_version": settings["version"],
            "time_zone": "Asia/Tokyo",
            "week_starts_on": 1,
            "activity_day_start_minute": 240,
        },
    )
    assert changed_zone.status_code == 200
    unchanged_due = client.get(f"/api/v1/tasks/{task_id}").json()["task"]["schedule"]
    assert unchanged_due["time_zone"] == "Asia/Shanghai"
    assert unchanged_due["due_at"] == "2026-10-06T10:30:00Z"

    invalid = client.put(
        f"/api/v1/tasks/{task_id}/schedule",
        json=schedule_payload(
            expected_task_version=3,
            due_kind="datetime",
            due_time="18:30:00",
            time_zone="Invalid/Zone",
        ),
    )
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "invalid_time_zone"

    cleared = client.put(
        f"/api/v1/tasks/{task_id}/schedule",
        json=schedule_payload(
            expected_task_version=3,
            due_kind="none",
            due_date=None,
            recurrence=recurrence("none"),
        ),
    )
    assert cleared.status_code == 200
    assert cleared.json()["task"]["version"] == 4
    assert cleared.json()["task"]["schedule"]["configured"] is False
    assert cleared.json()["task"]["schedule"]["preferences_saved"] is False


def test_repeating_task_occurrences_have_independent_status_and_step_progress(
    client,
    database_url,
):
    create_account(client, database_url)
    initialize_settings(client)
    task = create_task(client)
    task_id = task["id"]
    step_ids = [step["id"] for step in task["steps"]]
    scheduled = client.put(
        f"/api/v1/tasks/{task_id}/schedule",
        json=schedule_payload(
            due_kind="datetime",
            due_date="2026-10-05",
            due_time="17:00:00",
            recurrence=recurrence("daily", count=3),
        ),
    )
    assert scheduled.status_code == 200, scheduled.text
    assert scheduled.json()["task"]["version"] == 2

    blocked_completion = client.put(
        f"/api/v1/tasks/{task_id}/completion",
        json={
            "operation_id": str(uuid4()),
            "expected_version": 2,
            "completed": True,
        },
    )
    assert blocked_completion.status_code == 409
    assert blocked_completion.json()["error"]["code"] == (
        "repeating_task_requires_occurrence"
    )
    blocked_step = client.put(
        f"/api/v1/tasks/{task_id}/steps/{step_ids[0]}",
        json={
            "operation_id": str(uuid4()),
            "expected_task_version": 2,
            "title": task["steps"][0]["title"],
            "completed": True,
        },
    )
    assert blocked_step.status_code == 409
    blocked_bulk = client.post(
        "/api/v1/tasks/actions/bulk",
        json={
            "operation_id": str(uuid4()),
            "action": "complete",
            "items": [{"id": task_id, "expected_version": 2}],
        },
    )
    assert blocked_bulk.status_code == 409

    instances = get_occurrences(client, "2026-10-05", "2026-10-08")
    assert len(instances) == 3
    assert {item["status"] for item in instances} == {"open"}
    assert {item["occurrence_version"] for item in instances} == {0}
    assert all(item["step_progress"] == {"total": 2, "completed": 0} for item in instances)

    first_key = instances[0]["occurrence_key"]
    operation_id = uuid4()
    completion_payload = occurrence_status_payload(
        first_key,
        operation_id=operation_id,
        expected_task_version=2,
        completed_step_ids=[step_ids[0]],
    )
    completed = client.put(
        f"/api/v1/tasks/{task_id}/occurrences/status",
        json=completion_payload,
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["task"]["version"] == 3
    assert completed.json()["task"]["status"] == "open"
    assert completed.json()["occurrence"]["status"] == "completed"
    assert completed.json()["occurrence"]["step_progress"] == {
        "total": 2,
        "completed": 1,
    }
    assert completed.json()["occurrence"]["due_at"] == "2026-10-05T09:00:00Z"
    completed_at = completed.json()["occurrence"]["completed_at"]
    assert client.put(
        f"/api/v1/tasks/{task_id}/occurrences/status",
        json=completion_payload,
    ).json() == completed.json()
    protected_step = client.request(
        "DELETE",
        f"/api/v1/tasks/{task_id}/steps/{step_ids[0]}",
        json={
            "operation_id": str(uuid4()),
            "expected_task_version": 3,
        },
    )
    assert protected_step.status_code == 409
    assert protected_step.json()["error"]["code"] == "task_step_in_use"
    protected_add = client.post(
        f"/api/v1/tasks/{task_id}/steps",
        json={
            "operation_id": str(uuid4()),
            "expected_task_version": 3,
            "title": "不应追溯加入",
        },
    )
    assert protected_add.status_code == 409
    assert protected_add.json()["error"]["code"] == (
        "task_steps_frozen_by_occurrences"
    )

    updated_progress = client.put(
        f"/api/v1/tasks/{task_id}/occurrences/status",
        json=occurrence_status_payload(
            first_key,
            expected_task_version=3,
            expected_occurrence_version=1,
            completed_step_ids=step_ids,
        ),
    )
    assert updated_progress.status_code == 200
    assert updated_progress.json()["occurrence"]["completed_at"] == completed_at
    assert updated_progress.json()["occurrence"]["step_progress"] == {
        "total": 2,
        "completed": 2,
    }

    skipped = client.put(
        f"/api/v1/tasks/{task_id}/occurrences/status",
        json=occurrence_status_payload(
            instances[1]["occurrence_key"],
            expected_task_version=4,
            status="skipped",
        ),
    )
    assert skipped.status_code == 200
    assert skipped.json()["task"]["version"] == 5
    refreshed = get_occurrences(client, "2026-10-05", "2026-10-08")
    assert [item["status"] for item in refreshed] == [
        "completed",
        "skipped",
        "open",
    ]
    shifted_series = client.put(
        f"/api/v1/tasks/{task_id}/schedule",
        json=schedule_payload(
            expected_task_version=5,
            due_kind="datetime",
            due_date="2026-10-05",
            due_time="18:00:00",
            recurrence=recurrence("daily", count=3),
        ),
    )
    assert shifted_series.status_code == 200, shifted_series.text
    shifted_instances = get_occurrences(client, "2026-10-05", "2026-10-08")
    assert {item["due_time"] for item in shifted_instances} == {"18:00:00"}
    assert [item["status"] for item in shifted_instances] == [
        "completed",
        "skipped",
        "open",
    ]
    third = client.put(
        f"/api/v1/tasks/{task_id}/occurrences/status",
        json=occurrence_status_payload(
            shifted_instances[2]["occurrence_key"],
            expected_task_version=6,
            status="skipped",
        ),
    )
    assert third.status_code == 200
    orphaning = client.put(
        f"/api/v1/tasks/{task_id}/schedule",
        json=schedule_payload(
            expected_task_version=7,
            due_kind="datetime",
            due_date="2026-10-05",
            due_time="18:00:00",
            recurrence=recurrence("daily", count=2),
        ),
    )
    assert orphaning.status_code == 409
    assert orphaning.json()["error"]["code"] == "task_occurrences_would_be_orphaned"

    second_task = create_task(client, steps=[], links=[], labels=[])
    enabled = client.put(
        f"/api/v1/tasks/{second_task['id']}/schedule",
        json=schedule_payload(
            due_date="2026-11-01",
            recurrence=recurrence("daily", count=2),
        ),
    )
    assert enabled.status_code == 200
    first_state = client.put(
        f"/api/v1/tasks/{second_task['id']}/occurrences/status",
        json=occurrence_status_payload(
            "2026-11-01",
            expected_task_version=2,
            status="skipped",
        ),
    )
    assert first_state.status_code == 200
    disable_recurrence = client.put(
        f"/api/v1/tasks/{second_task['id']}/schedule",
        json=schedule_payload(
            expected_task_version=3,
            due_date="2026-11-01",
            recurrence=recurrence("none"),
        ),
    )
    assert disable_recurrence.status_code == 409
    assert disable_recurrence.json()["error"]["code"] == (
        "task_occurrences_require_recurring_schedule"
    )


def test_reschedule_and_makeup_keep_original_occurrence_link(client, database_url):
    create_account(client, database_url)
    initialize_settings(client)
    task = create_task(client, steps=[], links=[], labels=[])
    task_id = task["id"]
    scheduled = client.put(
        f"/api/v1/tasks/{task_id}/schedule",
        json=schedule_payload(
            due_date="2026-10-05",
            recurrence=recurrence("daily", count=2),
        ),
    )
    assert scheduled.status_code == 200

    rescheduled = client.put(
        f"/api/v1/tasks/{task_id}/occurrences/schedule",
        json={
            "operation_id": str(uuid4()),
            "expected_task_version": 2,
            "expected_occurrence_version": 0,
            "occurrence_key": "2026-10-05",
            "due_date": "2026-10-10",
            "due_time": None,
            "ambiguous_utc_offset_minutes": None,
        },
    )
    assert rescheduled.status_code == 200, rescheduled.text
    assert rescheduled.json()["task"]["version"] == 3
    assert [item["due_date"] for item in get_occurrences(
        client,
        "2026-10-05",
        "2026-10-07",
    )] == ["2026-10-06"]
    moved_items = get_occurrences(client, "2026-10-10", "2026-10-11")
    assert moved_items[0]["occurrence_key"] == "2026-10-05"
    assert moved_items[0]["original_due_date"] == "2026-10-05"

    makeup_operation = uuid4()
    makeup_payload = {
        "operation_id": str(makeup_operation),
        "expected_task_version": 3,
        "original_occurrence_key": "2026-10-05",
        "due_date": "2026-10-11",
        "due_time": None,
        "ambiguous_utc_offset_minutes": None,
    }
    makeup = client.post(
        f"/api/v1/tasks/{task_id}/occurrences/makeup",
        json=makeup_payload,
    )
    assert makeup.status_code == 201, makeup.text
    makeup_item = makeup.json()["occurrence"]
    assert makeup_item["kind"] == "makeup"
    assert makeup_item["makeup_for_key"] == "2026-10-05"
    assert client.post(
        f"/api/v1/tasks/{task_id}/occurrences/makeup",
        json=makeup_payload,
    ).json() == makeup.json()

    completed = client.put(
        f"/api/v1/tasks/{task_id}/occurrences/status",
        json=occurrence_status_payload(
            makeup_item["occurrence_key"],
            expected_task_version=4,
            expected_occurrence_version=1,
        ),
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["occurrence"]["kind"] == "makeup"
    assert completed.json()["occurrence"]["status"] == "completed"
    assert get_occurrences(client, "2026-10-11", "2026-10-12")[0][
        "status"
    ] == "completed"


def test_task_due_dst_behavior_is_explicit(client, database_url):
    create_account(client, database_url)
    initialize_settings(client, "America/New_York")
    task = create_task(client, steps=[], links=[], labels=[])

    missing_first = client.put(
        f"/api/v1/tasks/{task['id']}/schedule",
        json=schedule_payload(
            due_kind="datetime",
            due_date="2026-03-08",
            due_time="02:30:00",
            recurrence=recurrence("none"),
        ),
    )
    assert missing_first.status_code == 400
    assert missing_first.json()["error"]["code"] == "invalid_local_time"

    recurring = client.put(
        f"/api/v1/tasks/{task['id']}/schedule",
        json=schedule_payload(
            due_kind="datetime",
            due_date="2026-03-07",
            due_time="02:30:00",
            recurrence=recurrence("daily", count=3),
        ),
    )
    assert recurring.status_code == 200, recurring.text
    assert recurring.json()["warnings"][0]["occurrence_key"] == (
        "2026-03-08T02:30:00"
    )
    invalid = next(
        item
        for item in get_occurrences(client, "2026-03-07", "2026-03-10")
        if item["original_due_date"] == "2026-03-08"
    )
    assert invalid["due_at"] is None
    assert invalid["warning_codes"] == ["invalid_local_time"]
    skipped = client.put(
        f"/api/v1/tasks/{task['id']}/occurrences/status",
        json=occurrence_status_payload(
            invalid["occurrence_key"],
            expected_task_version=2,
            status="skipped",
        ),
    )
    assert skipped.status_code == 200, skipped.text
    assert skipped.json()["occurrence"]["due_at"] is None
    assert skipped.json()["occurrence"]["warning_codes"] == [
        "invalid_local_time"
    ]

    second = create_task(client, steps=[], links=[], labels=[])
    ambiguous = client.put(
        f"/api/v1/tasks/{second['id']}/schedule",
        json=schedule_payload(
            due_kind="datetime",
            due_date="2026-11-01",
            due_time="01:30:00",
        ),
    )
    assert ambiguous.status_code == 400
    assert ambiguous.json()["error"]["code"] == "ambiguous_local_time"
    resolved = client.put(
        f"/api/v1/tasks/{second['id']}/schedule",
        json=schedule_payload(
            due_kind="datetime",
            due_date="2026-11-01",
            due_time="01:30:00",
            ambiguous_utc_offset_minutes=-300,
        ),
    )
    assert resolved.status_code == 200
    assert resolved.json()["task"]["schedule"]["due_at"] == "2026-11-01T06:30:00Z"

    dated = create_task(client, steps=[], links=[], labels=[])
    dated_schedule = client.put(
        f"/api/v1/tasks/{dated['id']}/schedule",
        json=schedule_payload(
            due_date="2020-10-31",
            time_zone="America/Havana",
        ),
    )
    assert dated_schedule.status_code == 200
    dated_occurrence = get_occurrences(
        client,
        "2020-10-31",
        "2020-11-01",
    )[0]
    assert dated_occurrence["overdue_after"] == "2020-11-01T04:00:00Z"

    future = create_task(client, steps=[], links=[], labels=[])
    future_schedule = client.put(
        f"/api/v1/tasks/{future['id']}/schedule",
        json=schedule_payload(
            due_kind="datetime",
            due_date="2027-03-13",
            due_time="02:30:00",
            recurrence=recurrence("daily", count=3),
        ),
    )
    assert future_schedule.status_code == 200
    skipped_future = client.put(
        f"/api/v1/tasks/{future['id']}/occurrences/status",
        json=occurrence_status_payload(
            "2027-03-14T02:30:00",
            expected_task_version=2,
            status="skipped",
        ),
    )
    assert skipped_future.status_code == 200
    replacement = schedule_payload(
        due_kind="datetime",
        due_date="2027-03-14",
        due_time="03:30:00",
        recurrence=recurrence("daily", count=2),
    )
    replacement.pop("operation_id")
    replacement.pop("expected_task_version")
    split_future = client.post(
        f"/api/v1/tasks/{future['id']}/schedule/split",
        json={
            "operation_id": str(uuid4()),
            "expected_task_version": 3,
            "occurrence_key": "2027-03-14T02:30:00",
            "replacement": replacement,
        },
    )
    assert split_future.status_code == 201, split_future.text


def test_schedule_ownership_and_concurrent_updates(client, database_url, live_server):
    email, _ = create_account(client, database_url)
    initialize_settings(client)
    task = create_task(client, steps=[], links=[], labels=[])

    with browser(live_server) as other:
        create_account(other, database_url)
        initialize_settings(other)
        assert other.get(f"/api/v1/tasks/{task['id']}/schedule").status_code == 404
        cross = other.put(
            f"/api/v1/tasks/{task['id']}/schedule",
            json=schedule_payload(),
        )
        assert cross.status_code == 404

    barrier = Barrier(2)

    def update_with(session, due_date):
        barrier.wait(timeout=5)
        return session.put(
            f"/api/v1/tasks/{task['id']}/schedule",
            json=schedule_payload(due_date=due_date),
        )

    with browser(live_server) as left, browser(live_server) as right:
        login_account(left, email)
        login_account(right, email)
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(
                executor.map(
                    lambda pair: update_with(*pair),
                    [
                        (left, "2026-10-06"),
                        (right, "2026-10-07"),
                    ],
                )
            )
    assert sorted(response.status_code for response in responses) == [200, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["error"]["code"] == "task_version_conflict"
    current = client.get(f"/api/v1/tasks/{task['id']}").json()["task"]
    assert current["version"] == 2
    assert current["schedule"]["due_date"] in {"2026-10-06", "2026-10-07"}


def test_task_schedule_split_clones_template_and_moves_future_progress(
    client,
    database_url,
):
    _, user_id = create_account(client, database_url)
    initialize_settings(client)
    task = create_task(client)
    task_id = task["id"]
    old_step_ids = [step["id"] for step in task["steps"]]
    scheduled = client.put(
        f"/api/v1/tasks/{task_id}/schedule",
        json=schedule_payload(
            due_date="2026-10-05",
            recurrence=recurrence("daily", count=5),
        ),
    )
    assert scheduled.status_code == 200

    past = client.put(
        f"/api/v1/tasks/{task_id}/occurrences/status",
        json=occurrence_status_payload(
            "2026-10-06",
            expected_task_version=2,
            completed_step_ids=[old_step_ids[0]],
        ),
    )
    assert past.status_code == 200
    future = client.put(
        f"/api/v1/tasks/{task_id}/occurrences/status",
        json=occurrence_status_payload(
            "2026-10-08",
            expected_task_version=3,
            completed_step_ids=[old_step_ids[1]],
        ),
    )
    assert future.status_code == 200
    makeup = client.post(
        f"/api/v1/tasks/{task_id}/occurrences/makeup",
        json={
            "operation_id": str(uuid4()),
            "expected_task_version": 4,
            "original_occurrence_key": "2026-10-08",
            "due_date": "2026-10-10",
            "due_time": None,
            "ambiguous_utc_offset_minutes": None,
        },
    )
    assert makeup.status_code == 201

    replacement = schedule_payload(
        due_date="2026-10-07",
        recurrence=recurrence("daily", count=3),
    )
    replacement.pop("operation_id")
    replacement.pop("expected_task_version")
    operation_id = uuid4()
    split_payload = {
        "operation_id": str(operation_id),
        "expected_task_version": 5,
        "occurrence_key": "2026-10-07",
        "replacement": replacement,
    }
    split = client.post(
        f"/api/v1/tasks/{task_id}/schedule/split",
        json=split_payload,
    )
    assert split.status_code == 201, split.text
    body = split.json()
    successor = body["successor"]
    assert body["predecessor"]["version"] == 6
    assert body["predecessor"]["schedule"]["recurrence"]["count"] == 2
    assert successor["version"] == 1
    assert successor["schedule"]["predecessor_task_id"] == task_id
    assert body["moved_occurrence_state_count"] == 2
    assert client.post(
        f"/api/v1/tasks/{task_id}/schedule/split",
        json=split_payload,
    ).json() == body
    second_split = client.post(
        f"/api/v1/tasks/{task_id}/schedule/split",
        json={
            **split_payload,
            "operation_id": str(uuid4()),
            "expected_task_version": 6,
            "occurrence_key": "2026-10-06",
        },
    )
    assert second_split.status_code == 409
    assert second_split.json()["error"]["code"] == "task_series_already_split"

    items = get_occurrences(client, "2026-10-05", "2026-10-11")
    assert [
        (item["due_date"], item["status"], item["task_id"])
        for item in items
    ] == [
        ("2026-10-05", "open", task_id),
        ("2026-10-06", "completed", task_id),
        ("2026-10-07", "open", successor["id"]),
        ("2026-10-08", "completed", successor["id"]),
        ("2026-10-09", "open", successor["id"]),
        ("2026-10-10", "open", successor["id"]),
    ]
    moved_completed = next(item for item in items if item["due_date"] == "2026-10-08")
    successor_step_ids = {step["id"] for step in successor["steps"]}
    assert set(moved_completed["completed_step_ids"]).issubset(successor_step_ids)
    assert set(moved_completed["completed_step_ids"]).isdisjoint(old_step_ids)
    assert len(successor["links"]) == len(task["links"])
    assert {label["name"] for label in successor["labels"]} == {
        label["name"] for label in task["labels"]
    }

    with psycopg.connect(psycopg_url(database_url)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM tasks WHERE owner_id=%s",
            (user_id,),
        ).fetchone()[0] == 2
        assert connection.execute(
            """
            SELECT count(*) FROM task_occurrence_states
            WHERE task_id=%s
            """,
            (successor["id"],),
        ).fetchone()[0] == 2

    historical = create_task(client, steps=[], links=[], labels=[])
    historical_schedule = client.put(
        f"/api/v1/tasks/{historical['id']}/schedule",
        json=schedule_payload(
            due_date="2020-01-01",
            recurrence=recurrence("daily", count=3),
        ),
    )
    assert historical_schedule.status_code == 200
    historical_replacement = schedule_payload(
        due_date="2020-01-02",
        recurrence=recurrence("daily", count=2),
    )
    historical_replacement.pop("operation_id")
    historical_replacement.pop("expected_task_version")
    historical_split = client.post(
        f"/api/v1/tasks/{historical['id']}/schedule/split",
        json={
            "operation_id": str(uuid4()),
            "expected_task_version": 2,
            "occurrence_key": "2020-01-02",
            "replacement": historical_replacement,
        },
    )
    assert historical_split.status_code == 409
    assert historical_split.json()["error"]["code"] == (
        "historical_task_occurrence_immutable"
    )


def test_concurrent_task_schedule_split_creates_one_successor(
    client,
    database_url,
    live_server,
):
    email, user_id = create_account(client, database_url)
    initialize_settings(client)
    task = create_task(client, steps=[], links=[], labels=[])
    scheduled = client.put(
        f"/api/v1/tasks/{task['id']}/schedule",
        json=schedule_payload(
            due_date="2026-10-05",
            recurrence=recurrence("daily", count=4),
        ),
    )
    assert scheduled.status_code == 200
    barrier = Barrier(2)

    def split_with(session, due_time):
        replacement = schedule_payload(
            due_kind="datetime",
            due_date="2026-10-07",
            due_time=due_time,
            recurrence=recurrence("daily", count=2),
        )
        replacement.pop("operation_id")
        replacement.pop("expected_task_version")
        barrier.wait(timeout=5)
        return session.post(
            f"/api/v1/tasks/{task['id']}/schedule/split",
            json={
                "operation_id": str(uuid4()),
                "expected_task_version": 2,
                "occurrence_key": "2026-10-07",
                "replacement": replacement,
            },
        )

    with browser(live_server) as left, browser(live_server) as right:
        login_account(left, email)
        login_account(right, email)
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(
                executor.map(
                    lambda pair: split_with(*pair),
                    [(left, "17:00:00"), (right, "18:00:00")],
                )
            )
    assert sorted(response.status_code for response in responses) == [201, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["error"]["code"] == "task_version_conflict"
    with psycopg.connect(psycopg_url(database_url)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM tasks WHERE owner_id=%s",
            (user_id,),
        ).fetchone()[0] == 2


def test_task_schedule_migration_roundtrip_with_data(admin_url):
    with isolated_database(admin_url) as database_url:
        owner_id = uuid4()
        task_id = uuid4()
        state_id = uuid4()
        with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
            connection.execute(
                """
                INSERT INTO users (id, email, password_hash)
                VALUES (%s, %s, %s)
                """,
                (owner_id, address("task-schedule-migration"), "test-password-hash"),
            )
            connection.execute(
                """
                INSERT INTO tasks (
                    id, owner_id, title, priority, status
                )
                VALUES (%s, %s, '迁移任务', 0, 'open')
                """,
                (task_id, owner_id),
            )
            connection.execute(
                """
                INSERT INTO task_schedules (
                    task_id, owner_id, due_kind, start_date,
                    last_occurrence_date, time_zone,
                    recurrence_frequency, recurrence_weekdays, recurrence_count
                )
                VALUES (
                    %s, %s, 'date', '2026-10-06', '2026-10-07', 'Asia/Shanghai',
                    'daily', '[]'::jsonb, 2
                )
                """,
                (task_id, owner_id),
            )
            connection.execute(
                """
                INSERT INTO task_occurrence_states (
                    id, task_id, owner_id, occurrence_key, original_date,
                    kind, effective_date, status, completed_step_ids
                )
                VALUES (
                    %s, %s, %s, '2026-10-06', '2026-10-06',
                    'base', '2026-10-06', 'skipped', '[]'::jsonb
                )
                """,
                (state_id, task_id, owner_id),
            )
            with pytest.raises(psycopg.errors.CheckViolation):
                connection.execute(
                    """
                    UPDATE task_schedules
                    SET recurrence_count=NULL
                    WHERE task_id=%s
                    """,
                    (task_id,),
                )
            with pytest.raises(psycopg.errors.CheckViolation):
                connection.execute(
                    """
                    UPDATE task_schedules
                    SET due_kind='none', start_date=NULL,
                        last_occurrence_date=NULL, time_zone=NULL
                    WHERE task_id=%s
                    """,
                    (task_id,),
                )
            connection.execute(
                """
                INSERT INTO change_events (
                    owner_id, entity_type, entity_id, action, entity_version
                )
                VALUES (%s, 'task_occurrence', %s, 'skipped', 1)
                """,
                (owner_id, state_id),
            )
            connection.execute(
                """
                INSERT INTO user_operations (
                    owner_id, operation_id, scope, request_hash, response
                )
                VALUES (%s, %s, 'task_occurrences.status:test', %s, '{}'::jsonb)
                """,
                (owner_id, uuid4(), "0" * 64),
            )

        migrate(database_url, "downgrade", "0008_calendar_exceptions")
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT to_regclass('task_schedules')"
            ).fetchone()[0] is None
            assert connection.execute(
                """
                SELECT count(*) FROM change_events
                WHERE entity_type='task_occurrence'
                """
            ).fetchone()[0] == 0
            assert connection.execute(
                """
                SELECT count(*) FROM user_operations
                WHERE scope LIKE 'task_occurrences.%'
                """
            ).fetchone()[0] == 0

        migrate(database_url, "upgrade", "head")
        with psycopg.connect(psycopg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT to_regclass('task_schedules')"
            ).fetchone()[0] == "task_schedules"


def test_task_schedule_downgrade_refuses_split_successors(admin_url):
    with isolated_database(admin_url) as database_url:
        owner_id = uuid4()
        predecessor_id = uuid4()
        successor_id = uuid4()
        with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
            connection.execute(
                """
                INSERT INTO users (id, email, password_hash)
                VALUES (%s, %s, %s)
                """,
                (owner_id, address("split-downgrade"), "test-password-hash"),
            )
            connection.execute(
                """
                INSERT INTO tasks (id, owner_id, title, priority, status)
                VALUES
                    (%s, %s, '前序任务', 0, 'open'),
                    (%s, %s, '后继任务', 0, 'open')
                """,
                (predecessor_id, owner_id, successor_id, owner_id),
            )
            connection.execute(
                """
                INSERT INTO task_schedules (
                    task_id, owner_id, predecessor_task_id, due_kind,
                    start_date, last_occurrence_date, time_zone, recurrence_frequency,
                    recurrence_weekdays, recurrence_count
                )
                VALUES
                    (
                        %s, %s, NULL, 'date', '2026-10-05', '2026-10-06',
                        'Asia/Shanghai', 'daily', '[]'::jsonb, 2
                    ),
                    (
                        %s, %s, %s, 'date', '2026-10-07', '2026-10-08',
                        'Asia/Shanghai', 'daily', '[]'::jsonb, 2
                    )
                """,
                (
                    predecessor_id,
                    owner_id,
                    successor_id,
                    owner_id,
                    predecessor_id,
                ),
            )

        with pytest.raises(subprocess.CalledProcessError) as error:
            migrate(database_url, "downgrade", "0008_calendar_exceptions")
        assert "split successor tasks exist" in error.value.stderr
