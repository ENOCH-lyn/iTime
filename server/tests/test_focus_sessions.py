from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest

from tests.conftest import psycopg_url
from tests.test_planning_api import _current_week, _seed_plan
from tests.test_task_schedules import initialize_settings
from tests.test_tasks import PASSWORD, browser, create_account, create_task

pytestmark = pytest.mark.integration


def start_payload(operation_id=None, **changes) -> dict:
    payload = {
        "operation_id": str(operation_id or uuid4()),
        "mode": "stopwatch",
        "target_seconds": None,
        "title": "阅读资料",
        "task_id": None,
        "occurrence_key": None,
        "scheduled_block_id": None,
    }
    payload.update(changes)
    return payload


def transition_payload(
    expected_version: int,
    action: str,
    operation_id=None,
    client_recorded_at: datetime | None = None,
) -> dict:
    return {
        "operation_id": str(operation_id or uuid4()),
        "expected_version": expected_version,
        "action": action,
        "client_recorded_at": (
            client_recorded_at.isoformat()
            if client_recorded_at is not None
            else None
        ),
    }


def login_existing(client, email: str) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": PASSWORD, "device_name": "并发专注测试"},
    )
    assert response.status_code == 200, response.text
    client.headers["x-csrf-token"] = response.json()["csrf_token"]


def test_focus_requires_auth_csrf_and_valid_input(client, database_url):
    assert client.get("/api/v1/focus-sessions/current").status_code == 401
    create_account(client, database_url)
    initialize_settings(client)

    csrf = client.headers.pop("x-csrf-token")
    denied = client.post("/api/v1/focus-sessions", json=start_payload())
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "invalid_csrf"
    client.headers["x-csrf-token"] = csrf

    invalid = client.post(
        "/api/v1/focus-sessions",
        json=start_payload(mode="countdown", target_seconds=None),
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["saved"] == "not_saved"

    missing_title = client.post(
        "/api/v1/focus-sessions",
        json=start_payload(title="   "),
    )
    assert missing_title.status_code == 422


def test_focus_state_machine_replay_and_explicit_terminal_state(client, database_url):
    create_account(client, database_url)
    initialize_settings(client)
    operation_id = uuid4()
    request = start_payload(operation_id, mode="countdown", target_seconds=60)
    created = client.post("/api/v1/focus-sessions", json=request)
    assert created.status_code == 201, created.text
    focus = created.json()["focus"]
    assert focus["status"] == "running"
    assert focus["mode"] == "countdown"
    assert focus["target_seconds"] == 60
    assert focus["elapsed_seconds"] >= 0
    assert focus["task_id"] is None
    assert created.json()["server_now"]
    assert client.post("/api/v1/focus-sessions", json=request).json() == created.json()

    reused = client.post(
        "/api/v1/focus-sessions",
        json={**request, "title": "不同内容"},
    )
    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "operation_id_reused"

    second = client.post("/api/v1/focus-sessions", json=start_payload())
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "focus_session_active"
    assert second.json()["error"]["details"]["current_focus"]["id"] == focus["id"]

    pause_id = uuid4()
    pause_body = transition_payload(focus["version"], "pause", pause_id)
    paused_response = client.post(
        f"/api/v1/focus-sessions/{focus['id']}/transitions",
        json=pause_body,
    )
    assert paused_response.status_code == 200, paused_response.text
    paused = paused_response.json()["focus"]
    assert paused["status"] == "paused"
    assert paused["active_started_at"] is None
    assert (
        client.post(
            f"/api/v1/focus-sessions/{focus['id']}/transitions",
            json=pause_body,
        ).json()
        == paused_response.json()
    )
    assert client.post("/api/v1/focus-sessions", json=start_payload()).status_code == 409

    stale = client.post(
        f"/api/v1/focus-sessions/{focus['id']}/transitions",
        json=transition_payload(focus["version"], "finish"),
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "focus_version_conflict"
    assert stale.json()["error"]["details"]["current_focus"]["status"] == "paused"

    resumed_response = client.post(
        f"/api/v1/focus-sessions/{focus['id']}/transitions",
        json=transition_payload(paused["version"], "resume"),
    )
    assert resumed_response.status_code == 200, resumed_response.text
    resumed = resumed_response.json()["focus"]
    assert resumed["status"] == "running"

    finished_response = client.post(
        f"/api/v1/focus-sessions/{focus['id']}/transitions",
        json=transition_payload(resumed["version"], "finish"),
    )
    assert finished_response.status_code == 200, finished_response.text
    finished = finished_response.json()["focus"]
    assert finished["status"] == "finished"
    assert finished["effective_duration_seconds"] >= 0
    assert client.get("/api/v1/focus-sessions/current").json()["focus"] is None

    invalid_transition = client.post(
        f"/api/v1/focus-sessions/{focus['id']}/transitions",
        json=transition_payload(finished["version"], "resume"),
    )
    assert invalid_transition.status_code == 409
    assert invalid_transition.json()["error"]["code"] == "invalid_focus_transition"

    detail = client.get(f"/api/v1/focus-sessions/{focus['id']}")
    assert detail.status_code == 200
    assert [event["kind"] for event in detail.json()["events"]] == [
        "start",
        "pause",
        "resume",
        "finish",
    ]
    assert detail.json()["revisions"] == []


def test_focus_task_snapshot_and_owner_isolation(client, database_url, live_server):
    _, owner_id = create_account(client, database_url)
    initialize_settings(client)
    task = create_task(client, title="执行测试任务")
    created = client.post(
        "/api/v1/focus-sessions",
        json=start_payload(
            title=None,
            task_id=task["id"],
        ),
    )
    assert created.status_code == 201, created.text
    focus = created.json()["focus"]
    assert focus["task_id"] == task["id"]
    assert focus["task_title"] == "执行测试任务"
    assert [step["title"] for step in focus["steps"]] == [
        "列出研究问题",
        "整理章节结构",
    ]

    with browser(live_server) as other:
        create_account(other, database_url)
        initialize_settings(other)
        assert other.get(f"/api/v1/focus-sessions/{focus['id']}").status_code == 404
        cross_task = other.post(
            "/api/v1/focus-sessions",
            json=start_payload(title=None, task_id=task["id"]),
        )
        assert cross_task.status_code == 404
        assert cross_task.json()["error"]["code"] == "task_not_found"

    with psycopg.connect(psycopg_url(database_url)) as connection:
        assert connection.execute(
            """
            SELECT count(*)
            FROM focus_sessions
            WHERE owner_id=%s AND task_id=%s
            """,
            (owner_id, task["id"]),
        ).fetchone()[0] == 1


def test_two_devices_cannot_start_focus_concurrently(
    client,
    database_url,
    live_server,
):
    email, _ = create_account(client, database_url)
    initialize_settings(client)
    with browser(live_server) as other:
        login_existing(other, email)
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(
                executor.map(
                    lambda item: item[0].post(
                        "/api/v1/focus-sessions",
                        json=start_payload(title=item[1]),
                    ),
                    ((client, "设备一"), (other, "设备二")),
                )
            )
    assert sorted(response.status_code for response in responses) == [201, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["error"]["code"] == "focus_session_active"


def test_focus_can_start_only_from_current_plan_block(client, database_url):
    _, owner_text = create_account(client, database_url)
    initialize_settings(client)
    task = create_task(
        client,
        title="计划执行任务",
        estimated_minutes=60,
        steps=[],
        links=[],
        labels=[],
    )
    _, first_version_id, current_version_id = _seed_plan(
        database_url,
        UUID(owner_text),
        UUID(task["id"]),
        _current_week()[0],
    )
    with psycopg.connect(psycopg_url(database_url)) as connection:
        first_block_id = connection.execute(
            "SELECT id FROM scheduled_blocks WHERE plan_version_id=%s",
            (first_version_id,),
        ).fetchone()[0]
        current_block_id = connection.execute(
            "SELECT id FROM scheduled_blocks WHERE plan_version_id=%s",
            (current_version_id,),
        ).fetchone()[0]

    old = client.post(
        "/api/v1/focus-sessions",
        json=start_payload(title=None, scheduled_block_id=str(first_block_id)),
    )
    assert old.status_code == 404
    assert old.json()["error"]["code"] == "scheduled_block_not_found"

    current = client.post(
        "/api/v1/focus-sessions",
        json=start_payload(title=None, scheduled_block_id=str(current_block_id)),
    )
    assert current.status_code == 201, current.text
    focus = current.json()["focus"]
    assert focus["task_id"] == task["id"]
    assert focus["scheduled_block_id"] == str(current_block_id)
    assert focus["title"] == task["title"]


def test_offline_event_time_and_overtime_are_server_validated(client, database_url):
    create_account(client, database_url)
    initialize_settings(client)
    created = client.post(
        "/api/v1/focus-sessions",
        json=start_payload(mode="countdown", target_seconds=60),
    ).json()["focus"]
    started_at = datetime.fromisoformat(created["started_at"].replace("Z", "+00:00"))

    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        connection.execute(
            """
            UPDATE focus_sessions
            SET started_at=started_at - interval '2 minutes',
                active_started_at=active_started_at - interval '2 minutes'
            WHERE id=%s
            """,
            (created["id"],),
        )

    current = client.get("/api/v1/focus-sessions/current").json()["focus"]
    assert current["overtime"] is True
    assert current["overtime_seconds"] >= 59
    assert current["status"] == "running"

    future = client.post(
        f"/api/v1/focus-sessions/{created['id']}/transitions",
        json=transition_payload(
            current["version"],
            "pause",
            client_recorded_at=datetime.now(UTC) + timedelta(minutes=6),
        ),
    )
    assert future.status_code == 422
    assert future.json()["error"]["code"] == "invalid_focus_event_time"

    before_start = client.post(
        f"/api/v1/focus-sessions/{created['id']}/transitions",
        json=transition_payload(
            current["version"],
            "pause",
            client_recorded_at=started_at - timedelta(seconds=1),
        ),
    )
    assert before_start.status_code == 409
    assert before_start.json()["error"]["code"] == "focus_event_out_of_order"


def test_focus_list_is_bounded_and_cursor_is_stable(client, database_url):
    create_account(client, database_url)
    initialize_settings(client)
    created = client.post("/api/v1/focus-sessions", json=start_payload())
    focus = created.json()["focus"]
    client.post(
        f"/api/v1/focus-sessions/{focus['id']}/transitions",
        json=transition_payload(focus["version"], "abandon"),
    )

    listed = client.get(
        "/api/v1/focus-sessions",
        params={
            "start": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
            "end": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            "limit": 1,
        },
    )
    assert listed.status_code == 200, listed.text
    assert len(listed.json()["items"]) == 1
    assert listed.json()["items"][0]["status"] == "abandoned"

    too_large = client.get(
        "/api/v1/focus-sessions",
        params={
            "start": (datetime.now(UTC) - timedelta(days=94)).isoformat(),
            "end": datetime.now(UTC).isoformat(),
        },
    )
    assert too_large.status_code == 400
    assert too_large.json()["error"]["code"] == "focus_range_too_large"
