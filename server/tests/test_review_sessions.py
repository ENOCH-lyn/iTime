from datetime import UTC, datetime, time, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import psycopg
import pytest

from itime.auth.security import now
from itime.calendar.service import activity_day_at
from tests.conftest import psycopg_url
from tests.test_task_schedules import initialize_settings
from tests.test_tasks import browser, create_account

pytestmark = pytest.mark.integration


def review_payload(activity_date, operation_id=None) -> dict:
    return {
        "operation_id": str(operation_id or uuid4()),
        "activity_date": activity_date.isoformat(),
    }


def activity_window(activity_date):
    zone = ZoneInfo("Asia/Shanghai")
    start = datetime.combine(activity_date, time(4), tzinfo=zone).astimezone(UTC)
    end = datetime.combine(
        activity_date + timedelta(days=1),
        time(4),
        tzinfo=zone,
    ).astimezone(UTC)
    return start, end


def seed_review_facts(database_url: str, owner_id: UUID, activity_date) -> None:
    start, end = activity_window(activity_date)
    task_id = uuid4()
    plan_id = uuid4()
    plan_version_id = uuid4()
    block_id = uuid4()
    focus_id = uuid4()
    difficulty_id = uuid4()
    week_start = activity_date - timedelta(days=activity_date.weekday())
    with psycopg.connect(psycopg_url(database_url)) as connection:
        connection.execute(
            """
            INSERT INTO tasks (
                id, owner_id, title, status, completed_at, version
            ) VALUES (%s, %s, '完成复盘测试任务', 'completed', %s, 2)
            """,
            (task_id, owner_id, start + timedelta(hours=1)),
        )
        connection.execute(
            """
            INSERT INTO plans (
                id, owner_id, week_start_date, time_zone,
                current_version_id, version
            ) VALUES (%s, %s, %s, 'Asia/Shanghai', %s, 1)
            """,
            (plan_id, owner_id, week_start, plan_version_id),
        )
        connection.execute(
            """
            INSERT INTO plan_versions (
                id, plan_id, owner_id, version, source, proposal_id,
                scope, target_date, density, buffer_minutes,
                availability_start, availability_end, summary,
                assumptions, input_revision
            ) VALUES (
                %s, %s, %s, 1, 'manual', NULL,
                'day', %s, 'balanced', 15,
                TIME '08:00', TIME '22:00', '测试计划', '[]', 0
            )
            """,
            (plan_version_id, plan_id, owner_id, activity_date),
        )
        connection.execute(
            """
            INSERT INTO scheduled_blocks (
                id, plan_version_id, owner_id, position, task_id,
                task_title_snapshot, step_ids, step_titles_snapshot,
                local_date, local_starts_at, local_ends_at, time_zone,
                starts_at, ends_at, estimate_source, estimated_minutes
            ) VALUES (
                %s, %s, %s, 0, %s,
                '完成复盘测试任务', '[]', '[]',
                %s, TIME '09:00', TIME '10:00', 'Asia/Shanghai',
                %s, %s, 'user', 60
            )
            """,
            (
                block_id,
                plan_version_id,
                owner_id,
                task_id,
                activity_date,
                start + timedelta(hours=5),
                start + timedelta(hours=6),
            ),
        )
        connection.execute(
            """
            INSERT INTO focus_sessions (
                id, owner_id, source, task_id, title_snapshot,
                task_title_snapshot, step_snapshots, mode, status,
                started_at, ended_at, accumulated_seconds,
                effective_started_at, effective_ended_at,
                effective_duration_seconds, time_zone, version
            ) VALUES (
                %s, %s, 'timer', %s, '跨活动日专注',
                '完成复盘测试任务', '[]', 'stopwatch', 'finished',
                %s, %s, 3600, %s, %s, 3600, 'Asia/Shanghai', 1
            )
            """,
            (
                focus_id,
                owner_id,
                task_id,
                start - timedelta(minutes=30),
                start + timedelta(minutes=30),
                start - timedelta(minutes=30),
                start + timedelta(minutes=30),
            ),
        )
        connection.execute(
            """
            INSERT INTO difficulty_records (
                id, owner_id, task_id, task_title_snapshot, content,
                category, occurred_at, time_zone, version
            ) VALUES (
                %s, %s, %s, '完成复盘测试任务', '资料入口分散',
                '资料', %s, 'Asia/Shanghai', 1
            )
            """,
            (difficulty_id, owner_id, task_id, start + timedelta(hours=2)),
        )
        connection.commit()
    assert end > start


def test_review_session_creation_replay_update_and_owner_isolation(
    client,
    database_url,
    live_server,
):
    create_account(client, database_url)
    initialize_settings(client)
    target = activity_day_at(now(), "Asia/Shanghai") - timedelta(days=1)
    operation_id = uuid4()
    payload = review_payload(target, operation_id)
    created = client.post("/api/v1/reviews", json=payload)
    assert created.status_code == 201, created.text
    review = created.json()["review"]
    assert review["activity_date"] == target.isoformat()
    assert review["question_count"] == 0
    assert created.json()["created"] is True
    assert client.post("/api/v1/reviews", json=payload).json() == created.json()

    second_operation = client.post(
        "/api/v1/reviews",
        json=review_payload(target),
    )
    assert second_operation.status_code == 200
    assert second_operation.json()["review"]["id"] == review["id"]
    assert second_operation.json()["created"] is False

    updated = client.put(
        f"/api/v1/reviews/{review['id']}/facts",
        json={
            "operation_id": str(uuid4()),
            "expected_version": review["version"],
            "supplemental_facts": "下午临时完成了资料整理。",
        },
    )
    assert updated.status_code == 200, updated.text
    review = updated.json()["review"]
    assert review["input_revision"] == 1
    assert review["version"] == 2

    stale = client.put(
        f"/api/v1/reviews/{review['id']}/facts",
        json={
            "operation_id": str(uuid4()),
            "expected_version": 1,
            "supplemental_facts": "陈旧内容",
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "review_version_conflict"

    with browser(live_server) as other:
        create_account(other, database_url)
        initialize_settings(other)
        assert other.get(f"/api/v1/reviews/{review['id']}").status_code == 404

    listed = client.get("/api/v1/reviews")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [review["id"]]


def test_review_fact_snapshot_uses_activity_window_and_current_plan(
    client,
    database_url,
):
    _, owner_id_value = create_account(client, database_url)
    initialize_settings(client)
    owner_id = UUID(owner_id_value)
    target = activity_day_at(now(), "Asia/Shanghai") - timedelta(days=2)
    seed_review_facts(database_url, owner_id, target)
    created = client.post("/api/v1/reviews", json=review_payload(target))
    assert created.status_code == 201, created.text

    detail = client.get(f"/api/v1/reviews/{created.json()['review']['id']}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    facts = body["facts"]
    start, end = activity_window(target)
    assert datetime.fromisoformat(facts["activity_window"]["start"]) == start
    assert datetime.fromisoformat(facts["activity_window"]["end"]) == end
    assert len(facts["planned_blocks"]["items"]) == 1
    assert len(facts["task_completions"]["items"]) == 1
    assert facts["focus"]["total_seconds"] == 1800
    assert len(facts["focus"]["items"]) == 1
    assert len(facts["difficulties"]["items"]) == 1
    assert body["facts_sha256"]
    assert body["report_freshness"] == "none"
    first_hash = body["facts_sha256"]

    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        connection.execute(
            """
            INSERT INTO difficulty_records (
                id, owner_id, content, occurred_at, time_zone, version
            ) VALUES (%s, %s, '新增困难', %s, 'Asia/Shanghai', 1)
            """,
            (uuid4(), owner_id, start + timedelta(hours=3)),
        )
    refreshed = client.get(f"/api/v1/reviews/{body['review']['id']}").json()
    assert refreshed["facts_sha256"] != first_hash
    assert len(refreshed["facts"]["difficulties"]["items"]) == 2


def test_review_allows_empty_day_and_rejects_future_activity_day(
    client,
    database_url,
):
    create_account(client, database_url)
    initialize_settings(client)
    today = activity_day_at(now(), "Asia/Shanghai")
    empty = client.post(
        "/api/v1/reviews",
        json=review_payload(today - timedelta(days=20)),
    )
    assert empty.status_code == 201, empty.text
    detail = client.get(f"/api/v1/reviews/{empty.json()['review']['id']}").json()
    assert detail["facts"]["planned_blocks"]["items"] == []
    assert detail["facts"]["task_completions"]["items"] == []
    assert detail["facts"]["focus"]["items"] == []
    assert detail["facts"]["difficulties"]["items"] == []

    future = client.post(
        "/api/v1/reviews",
        json=review_payload(today + timedelta(days=1)),
    )
    assert future.status_code == 422
    assert future.json()["error"]["code"] == "review_date_in_future"
