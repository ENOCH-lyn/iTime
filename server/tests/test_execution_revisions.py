from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from tests.test_focus_sessions import start_payload, transition_payload
from tests.test_task_schedules import initialize_settings
from tests.test_tasks import create_account

pytestmark = pytest.mark.integration


def manual_payload(
    *,
    local_started_at: str,
    local_ended_at: str,
    effective_duration_seconds: int,
    operation_id=None,
    **changes,
) -> dict:
    payload = {
        "operation_id": str(operation_id or uuid4()),
        "title": "补记专注",
        "task_id": None,
        "occurrence_key": None,
        "local_started_at": local_started_at,
        "local_ended_at": local_ended_at,
        "time_zone": "Asia/Shanghai",
        "start_utc_offset_minutes": None,
        "end_utc_offset_minutes": None,
        "effective_duration_seconds": effective_duration_seconds,
    }
    payload.update(changes)
    return payload


def revision_payload(focus: dict, operation_id=None, **changes) -> dict:
    started_at = datetime.fromisoformat(
        focus["effective_started_at"].replace("Z", "+00:00")
    )
    ended_at = datetime.fromisoformat(
        focus["effective_ended_at"].replace("Z", "+00:00")
    )
    local_start = started_at.astimezone().replace(tzinfo=None)
    local_end = ended_at.astimezone().replace(tzinfo=None)
    payload = {
        "operation_id": str(operation_id or uuid4()),
        "expected_version": focus["version"],
        "title": focus["title"],
        "task_id": focus["task_id"],
        "occurrence_key": focus["occurrence_key"],
        "local_started_at": local_start.isoformat(timespec="seconds"),
        "local_ended_at": local_end.isoformat(timespec="seconds"),
        "time_zone": focus["time_zone"],
        "start_utc_offset_minutes": None,
        "end_utc_offset_minutes": None,
        "effective_duration_seconds": focus["effective_duration_seconds"],
        "reason": "修正记录",
    }
    payload.update(changes)
    return payload


def test_manual_focus_uses_four_am_activity_day_and_replays(client, database_url):
    create_account(client, database_url)
    initialize_settings(client)
    operation_id = uuid4()
    before_boundary = manual_payload(
        operation_id=operation_id,
        local_started_at="2026-10-03T03:30:00",
        local_ended_at="2026-10-03T03:50:00",
        effective_duration_seconds=1200,
    )
    created = client.post("/api/v1/focus-sessions/manual", json=before_boundary)
    assert created.status_code == 201, created.text
    focus = created.json()["focus"]
    assert focus["source"] == "manual"
    assert focus["status"] == "finished"
    assert focus["activity_day"] == "2026-10-02"
    assert focus["effective_duration_seconds"] == 1200
    assert client.post(
        "/api/v1/focus-sessions/manual",
        json=before_boundary,
    ).json() == created.json()

    at_boundary = client.post(
        "/api/v1/focus-sessions/manual",
        json=manual_payload(
            local_started_at="2026-10-03T04:00:00",
            local_ended_at="2026-10-03T04:20:00",
            effective_duration_seconds=1200,
        ),
    )
    assert at_boundary.status_code == 201, at_boundary.text
    assert at_boundary.json()["focus"]["activity_day"] == "2026-10-03"


def test_manual_focus_rejects_dst_gaps_and_requires_ambiguous_offset(
    client,
    database_url,
):
    create_account(client, database_url)
    initialize_settings(client, "America/New_York")

    gap = client.post(
        "/api/v1/focus-sessions/manual",
        json=manual_payload(
            local_started_at="2025-03-09T02:15:00",
            local_ended_at="2025-03-09T03:15:00",
            effective_duration_seconds=1800,
            time_zone="America/New_York",
        ),
    )
    assert gap.status_code == 422
    assert gap.json()["error"]["code"] == "invalid_local_time"

    ambiguous_payload = manual_payload(
        local_started_at="2025-11-02T01:15:00",
        local_ended_at="2025-11-02T01:45:00",
        effective_duration_seconds=1800,
        time_zone="America/New_York",
    )
    ambiguous = client.post(
        "/api/v1/focus-sessions/manual",
        json=ambiguous_payload,
    )
    assert ambiguous.status_code == 400
    assert ambiguous.json()["error"]["code"] == "ambiguous_local_time"

    resolved = client.post(
        "/api/v1/focus-sessions/manual",
        json={
            **ambiguous_payload,
            "operation_id": str(uuid4()),
            "start_utc_offset_minutes": -240,
            "end_utc_offset_minutes": -240,
        },
    )
    assert resolved.status_code == 201, resolved.text


def test_terminal_focus_revision_preserves_original_events(client, database_url):
    create_account(client, database_url)
    initialize_settings(client)
    started = client.post(
        "/api/v1/focus-sessions",
        json=start_payload(title="原始标题"),
    ).json()["focus"]
    finished = client.post(
        f"/api/v1/focus-sessions/{started['id']}/transitions",
        json=transition_payload(started["version"], "finish"),
    ).json()["focus"]
    original_started_at = finished["started_at"]
    original_ended_at = finished["ended_at"]

    operation_id = uuid4()
    revised_body = revision_payload(
        finished,
        operation_id,
        title="修订后的标题",
        local_started_at="2026-10-01T09:00:00",
        local_ended_at="2026-10-01T09:45:00",
        effective_duration_seconds=2400,
        reason="忘记暂停了五分钟",
    )
    revised_response = client.put(
        f"/api/v1/focus-sessions/{finished['id']}",
        json=revised_body,
    )
    assert revised_response.status_code == 200, revised_response.text
    revised = revised_response.json()["focus"]
    assert revised["version"] == finished["version"] + 1
    assert revised["title"] == "修订后的标题"
    assert revised["effective_duration_seconds"] == 2400
    assert revised["started_at"] == original_started_at
    assert revised["ended_at"] == original_ended_at
    assert revised["revised"] is True
    assert client.put(
        f"/api/v1/focus-sessions/{finished['id']}",
        json=revised_body,
    ).json() == revised_response.json()

    detail = client.get(f"/api/v1/focus-sessions/{finished['id']}").json()
    assert [event["kind"] for event in detail["events"]] == ["start", "finish"]
    assert len(detail["revisions"]) == 1
    assert detail["revisions"][0]["reason"] == "忘记暂停了五分钟"
    assert detail["revisions"][0]["before_data"]["title"] == "原始标题"
    assert detail["revisions"][0]["after_data"]["title"] == "修订后的标题"

    stale = client.put(
        f"/api/v1/focus-sessions/{finished['id']}",
        json={
            **revised_body,
            "operation_id": str(uuid4()),
            "expected_version": finished["version"],
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "focus_version_conflict"


def test_running_focus_cannot_be_revised_and_manual_overlap_is_rejected(
    client,
    database_url,
):
    create_account(client, database_url)
    initialize_settings(client)
    running = client.post(
        "/api/v1/focus-sessions",
        json=start_payload(title="仍在进行"),
    ).json()["focus"]
    blocked = client.put(
        f"/api/v1/focus-sessions/{running['id']}",
        json={
            "operation_id": str(uuid4()),
            "expected_version": running["version"],
            "title": "不应修改",
            "task_id": None,
            "occurrence_key": None,
            "local_started_at": "2026-10-01T09:00:00",
            "local_ended_at": "2026-10-01T09:30:00",
            "time_zone": "Asia/Shanghai",
            "start_utc_offset_minutes": None,
            "end_utc_offset_minutes": None,
            "effective_duration_seconds": 1800,
            "reason": "运行中不允许",
        },
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "focus_not_terminal"

    finished = client.post(
        f"/api/v1/focus-sessions/{running['id']}/transitions",
        json=transition_payload(running["version"], "finish"),
    )
    assert finished.status_code == 200

    first = client.post(
        "/api/v1/focus-sessions/manual",
        json=manual_payload(
            local_started_at="2026-09-01T09:00:00",
            local_ended_at="2026-09-01T10:00:00",
            effective_duration_seconds=3600,
        ),
    )
    assert first.status_code == 201
    overlap = client.post(
        "/api/v1/focus-sessions/manual",
        json=manual_payload(
            local_started_at="2026-09-01T09:30:00",
            local_ended_at="2026-09-01T10:30:00",
            effective_duration_seconds=3600,
        ),
    )
    assert overlap.status_code == 409
    assert overlap.json()["error"]["code"] == "focus_time_conflict"


def test_manual_focus_rejects_future_and_invalid_duration(client, database_url):
    create_account(client, database_url)
    initialize_settings(client)
    future = datetime.now(UTC) + timedelta(minutes=6)
    response = client.post(
        "/api/v1/focus-sessions/manual",
        json=manual_payload(
            local_started_at=future.replace(tzinfo=None).isoformat(),
            local_ended_at=(future + timedelta(minutes=30))
            .replace(tzinfo=None)
            .isoformat(),
            effective_duration_seconds=1800,
            time_zone="UTC",
        ),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_focus_event_time"

    too_long = client.post(
        "/api/v1/focus-sessions/manual",
        json=manual_payload(
            local_started_at="2026-08-01T09:00:00",
            local_ended_at="2026-08-01T09:30:00",
            effective_duration_seconds=3600,
        ),
    )
    assert too_long.status_code == 422
    assert too_long.json()["error"]["code"] == "invalid_focus_duration"
