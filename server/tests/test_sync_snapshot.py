from uuid import UUID, uuid4

import httpx
import psycopg
import pytest

from tests.conftest import psycopg_url

pytestmark = pytest.mark.integration
AUTH = "/api/v1/auth"
PASSWORD = "Valid-sync-password-123"


def create_account(client: httpx.Client, database_url: str) -> tuple[str, UUID]:
    email = f"snapshot-{uuid4().hex}@example.com"
    registered = client.post(
        f"{AUTH}/register",
        json={"email": email, "password": PASSWORD},
    )
    assert registered.status_code == 202, registered.text
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        owner_id = connection.execute(
            "UPDATE users SET email_verified_at=now() WHERE email=%s RETURNING id",
            (email,),
        ).fetchone()[0]
    logged_in = client.post(
        f"{AUTH}/login",
        json={"email": email, "password": PASSWORD, "device_name": "快照测试"},
    )
    assert logged_in.status_code == 200, logged_in.text
    client.headers["x-csrf-token"] = logged_in.json()["csrf_token"]
    return email, owner_id


def create_task(client: httpx.Client, **changes) -> dict:
    payload = {
        "operation_id": str(uuid4()),
        "title": "完成论文提纲",
        "description": "先整理研究问题。",
        "project_id": None,
        "estimated_minutes": 90,
        "priority": 2,
        "labels": ["学习"],
        "steps": [{"title": "列出研究问题"}, {"title": "整理章节结构"}],
    }
    payload.update(changes)
    response = client.post("/api/v1/tasks", json=payload)
    assert response.status_code == 201, response.text
    task = response.json()["task"]
    scheduled = client.put(
        f"/api/v1/tasks/{task['id']}/schedule",
        json={
            "operation_id": str(uuid4()),
            "expected_task_version": task["version"],
            "due_kind": "date",
            "due_date": "2026-11-12",
            "due_time": None,
            "time_zone": "Asia/Shanghai",
            "recurrence": {
                "frequency": "none",
                "interval": 1,
                "weekdays": [],
            },
        },
    )
    assert scheduled.status_code == 200, scheduled.text
    return scheduled.json()["task"]


def create_weekly_series(client: httpx.Client) -> dict:
    response = client.post(
        "/api/v1/calendar/series",
        json={
            "operation_id": str(uuid4()),
            "kind": "fixed",
            "name": "晨间跑步",
            "teacher": "",
            "description": "",
            "location": "操场",
            "busy": True,
            "time_zone": "Asia/Shanghai",
            "start_date": "2026-11-09",
            "starts_at": "07:00:00",
            "ends_at": "08:00:00",
            "recurrence": {
                "frequency": "weekly",
                "interval": 1,
                "weekdays": [1],
                "count": 8,
            },
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["series"]


def test_snapshot_projects_every_syncable_entity(client, database_url):
    assert client.get("/api/v1/sync/snapshot").status_code == 401
    create_account(client, database_url)

    task = create_task(client)
    series = create_weekly_series(client)

    response = client.get("/api/v1/sync/snapshot")
    assert response.status_code == 200, response.text
    body = response.json()

    assert isinstance(body["cursor"], str) and body["cursor"]
    assert body["server_time"]
    assert "initialized" in body["time_settings"]

    assert [item["id"] for item in body["tasks"]] == [task["id"]]
    snapshot_task = body["tasks"][0]
    assert len(snapshot_task["steps"]) == 2
    assert snapshot_task["schedule"]["due_kind"] == "date"

    assert [item["id"] for item in body["series"]] == [series["id"]]
    assert body["series"][0]["recurrence"]["frequency"] == "weekly"
    assert body["calendar_exceptions"] == []
    assert body["calendar_additions"] == []
    assert body["terms"] == []
    assert body["task_occurrences"] == []


def test_entity_read_materialises_single_entities_and_hides_other_owners(
    client,
    database_url,
    live_server,
):
    create_account(client, database_url)
    task = create_task(client)
    step_id = task["steps"][0]["id"]
    series = create_weekly_series(client)

    fetched = client.get(f"/api/v1/sync/entities/task/{task['id']}")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["deleted"] is False
    assert fetched.json()["data"]["title"] == "完成论文提纲"
    assert len(fetched.json()["data"]["steps"]) == 2

    step = client.get(f"/api/v1/sync/entities/step/{step_id}")
    assert step.status_code == 200, step.text
    assert step.json()["task_id"] == task["id"]
    assert step.json()["data"]["title"] == "列出研究问题"

    series_entity = client.get(
        f"/api/v1/sync/entities/calendar_series/{series['id']}"
    )
    assert series_entity.status_code == 200, series_entity.text
    assert series_entity.json()["data"]["name"] == "晨间跑步"

    owner_id = client.get("/api/v1/auth/me").json()["user"]["id"]
    time_settings = client.get(
        f"/api/v1/sync/entities/time_settings/{owner_id}"
    )
    assert time_settings.status_code == 200, time_settings.text
    assert time_settings.json()["deleted"] is False
    assert "initialized" in time_settings.json()["data"]

    other_owner = client.get(f"/api/v1/sync/entities/time_settings/{uuid4()}")
    assert other_owner.status_code == 200
    assert other_owner.json()["deleted"] is True

    unknown = client.get(f"/api/v1/sync/entities/plan/{uuid4()}")
    assert unknown.status_code == 400
    assert unknown.json()["error"]["code"] == "sync_entity_unsupported"

    missing = client.get(f"/api/v1/sync/entities/task/{uuid4()}")
    assert missing.status_code == 200
    assert missing.json()["deleted"] is True
    assert missing.json()["data"] is None

    with httpx.Client(
        base_url=live_server.origin,
        timeout=10,
        trust_env=False,
    ) as other:
        create_account(other, database_url)
        denied = other.get(f"/api/v1/sync/entities/task/{task['id']}")
        assert denied.status_code == 200
        assert denied.json()["deleted"] is True

        other_snapshot = other.get("/api/v1/sync/snapshot").json()
        assert other_snapshot["tasks"] == []
        assert other_snapshot["series"] == []
        assert other_snapshot["cursor"] != client.get(
            "/api/v1/sync/snapshot"
        ).json()["cursor"]


def test_snapshot_cursor_replays_writes_that_happen_after_it(client, database_url):
    create_account(client, database_url)
    snapshot = client.get("/api/v1/sync/snapshot").json()
    cursor = snapshot["cursor"]

    task = create_task(client, title="快照之后新建的任务")

    page_response = client.get(
        "/api/v1/sync/changes",
        params={"cursor": cursor, "limit": 100},
    )
    assert page_response.status_code == 200, page_response.text
    page = page_response.json()
    assert page["reset_required"] is False
    created = [event for event in page["events"] if event["entity_type"] == "task"]
    assert any(
        event["entity_id"] == task["id"] and event["action"] == "created"
        for event in created
    )

    materialised = client.get(
        f"/api/v1/sync/entities/task/{task['id']}"
    ).json()
    assert materialised["data"]["title"] == "快照之后新建的任务"

    refreshed = client.get("/api/v1/sync/snapshot").json()
    assert [item["id"] for item in refreshed["tasks"]] == [task["id"]]
    assert refreshed["cursor"] != cursor
