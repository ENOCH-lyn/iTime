import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest

from tests.conftest import psycopg_url
from tests.test_calendar_series import create_account
from tests.test_calendar_subscriptions import (
    FakeFetcher,
    SubscriptionFetchError,
    calendar,
    create_subscription,
    event,
    result,
    run_worker,
)
from tests.test_planning_api import _manual_payload
from tests.test_tasks import create_task

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def cleanup_created_users(database_url):
    with psycopg.connect(psycopg_url(database_url)) as connection:
        existing_ids = {row[0] for row in connection.execute("SELECT id FROM users")}
    yield
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        created_ids = [
            row[0]
            for row in connection.execute("SELECT id FROM users")
            if row[0] not in existing_ids
        ]
        for user_id in created_ids:
            connection.execute("DELETE FROM users WHERE id=%s", (user_id,))


def next_monday() -> datetime:
    today = datetime.now(UTC)
    days = (7 - today.weekday()) or 7
    return datetime.combine(
        today.date() + timedelta(days=days),
        datetime.min.time(),
        UTC,
    )


def one_off_ics(
    name: str,
    target: datetime,
    *,
    starts_at: str,
    ends_at: str,
    status: str | None = None,
) -> bytes:
    stamp = target.strftime("%Y%m%d")
    lines = [
        "UID:source-plan-conflict",
        f"SUMMARY:{name}",
        f"DTSTART:{stamp}T{starts_at.replace(':', '')}Z",
        f"DTEND:{stamp}T{ends_at.replace(':', '')}Z",
    ]
    if status is not None:
        lines.append(f"STATUS:{status}")
    return calendar(event(*lines))


def planning_context(client, target: datetime) -> dict:
    response = client.get(
        "/api/v1/planning/context",
        params={
            "scope": "week",
            "date": target.date().isoformat(),
            "density": "balanced",
            "buffer": 0,
            "availability_start": "08:00:00",
            "availability_end": "22:00:00",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["context"]


def test_source_move_marks_confirmed_plan_without_rewriting_it(
    client,
    database_url,
):
    _, owner_text = create_account(client, database_url, time_zone="UTC")
    owner_id = UUID(owner_text)
    target = next_monday()
    source_id = create_subscription(client, name="会变化的学校课表")["id"]
    assert asyncio.run(
        run_worker(
            database_url,
            FakeFetcher(
                [
                    result(
                        one_off_ics(
                            "原课程",
                            target,
                            starts_at="11:00:00",
                            ends_at="12:00:00",
                        )
                    )
                ]
            ),
        )
    )
    task = create_task(
        client,
        title="需要保持的计划块",
        estimated_minutes=60,
        steps=[],
        links=[],
        labels=[],
    )
    created = client.post(
        "/api/v1/plans/versions",
        json=_manual_payload(
            target.date(),
            task["id"],
            starts_at="09:00:00",
            ends_at="10:00:00",
        ),
    )
    assert created.status_code == 201, created.text
    original_plan = created.json()["plan"]
    original_block = original_plan["current_version"]["blocks"][0]

    queued = client.post(
        f"/api/v1/calendar/subscriptions/{source_id}/refresh",
        json={"operation_id": str(uuid4())},
    )
    assert queued.status_code == 200, queued.text
    assert asyncio.run(
        run_worker(
            database_url,
            FakeFetcher(
                [
                    result(
                        one_off_ics(
                            "移动后的课程",
                            target,
                            starts_at="09:30:00",
                            ends_at="10:30:00",
                        ),
                        etag='"v2"',
                    )
                ]
            ),
        )
    )

    context = planning_context(client, target)
    current = context["current_plan"]
    assert current["version"] == original_plan["version"]
    assert current["blocks"][0]["starts_at"] == original_block["starts_at"]
    assert current["blocks"][0]["ends_at"] == original_block["ends_at"]
    conflict = current["source_conflicts"]["items"][0]
    assert conflict["source_id"] == source_id
    assert conflict["source_name"] == "会变化的学校课表"
    assert conflict["source_generation"] == 2
    assert conflict["status"] == "open"
    assert conflict["resolution"] is None
    assert conflict["affected_block_ids"] == [original_block["id"]]
    assert conflict["changes"] == [
        {
            "kind": "moved",
            "occurrence_key": conflict["changes"][0]["occurrence_key"],
            "event_id": conflict["changes"][0]["event_id"],
            "name": "移动后的课程",
            "before": {
                "name": "原课程",
                "local_start_date": target.date().isoformat(),
                "local_start_time": "11:00:00",
                "local_end_date": target.date().isoformat(),
                "local_end_time": "12:00:00",
                "time_zone": "UTC",
                "start_at": f"{target.date().isoformat()}T11:00:00Z",
                "end_at": f"{target.date().isoformat()}T12:00:00Z",
                "busy": True,
                "status": "scheduled",
            },
            "after": {
                "name": "移动后的课程",
                "local_start_date": target.date().isoformat(),
                "local_start_time": "09:30:00",
                "local_end_date": target.date().isoformat(),
                "local_end_time": "10:30:00",
                "time_zone": "UTC",
                "start_at": f"{target.date().isoformat()}T09:30:00Z",
                "end_at": f"{target.date().isoformat()}T10:30:00Z",
                "busy": True,
                "status": "scheduled",
            },
            "affected_block_ids": [original_block["id"]],
        }
    ]
    assert current["source_conflicts"]["open_count"] == 1
    assert current["source_conflicts"]["overlap_count"] == 1

    with psycopg.connect(psycopg_url(database_url)) as connection:
        assert connection.execute(
            """
            SELECT count(*)
            FROM plan_source_conflicts
            WHERE owner_id=%s AND plan_version_id=%s
            """,
            (owner_id, original_plan["current_version_id"]),
        ).fetchone()[0] == 1

    queued = client.post(
        f"/api/v1/calendar/subscriptions/{source_id}/refresh",
        json={"operation_id": str(uuid4())},
    )
    assert queued.status_code == 200
    assert asyncio.run(
        run_worker(
            database_url,
            FakeFetcher(
                [
                    result(
                        one_off_ics(
                            "移动后的课程",
                            target,
                            starts_at="09:30:00",
                            ends_at="10:30:00",
                            status="CANCELLED",
                        ),
                        etag='"v3"',
                    )
                ]
            ),
        )
    )
    after_cancel = planning_context(client, target)["current_plan"]
    assert after_cancel["version"] == original_plan["version"]
    assert after_cancel["blocks"][0]["starts_at"] == original_block["starts_at"]
    assert after_cancel["source_conflicts"]["open_count"] == 1
    assert after_cancel["source_conflicts"]["overlap_count"] == 0
    cancellation = after_cancel["source_conflicts"]["items"][0]
    assert cancellation["source_generation"] == 3
    assert cancellation["affected_block_ids"] == []
    assert cancellation["changes"][0]["kind"] == "removed"
    assert cancellation["changes"][0]["after"]["status"] == "source_cancelled"

    with psycopg.connect(psycopg_url(database_url)) as connection:
        states = connection.execute(
            """
            SELECT source_generation, status, resolution
            FROM plan_source_conflicts
            WHERE owner_id=%s AND plan_version_id=%s
            ORDER BY source_generation
            """,
            (owner_id, original_plan["current_version_id"]),
        ).fetchall()
    assert states == [
        (2, "resolved", "superseded"),
        (3, "open", None),
    ]

    queued = client.post(
        f"/api/v1/calendar/subscriptions/{source_id}/refresh",
        json={"operation_id": str(uuid4())},
    )
    assert queued.status_code == 200
    assert asyncio.run(
        run_worker(
            database_url,
            FakeFetcher(
                [
                    SubscriptionFetchError(
                        "calendar_subscription_timeout",
                        "订阅服务器响应超时，请稍后重试。",
                        retryable=True,
                    )
                ]
            ),
        )
    )
    after_failure = planning_context(client, target)["current_plan"]
    assert after_failure["source_conflicts"] == after_cancel["source_conflicts"]


def test_non_scheduling_source_change_does_not_mark_plan(
    client,
    database_url,
):
    create_account(client, database_url, time_zone="UTC")
    target = next_monday()
    source_id = create_subscription(client, name="只改说明的课表")["id"]
    assert asyncio.run(
        run_worker(
            database_url,
            FakeFetcher(
                [
                    result(
                        one_off_ics(
                            "原名称",
                            target,
                            starts_at="11:00:00",
                            ends_at="12:00:00",
                        )
                    )
                ]
            ),
        )
    )
    task = create_task(
        client,
        title="不受影响的计划块",
        estimated_minutes=60,
        steps=[],
        links=[],
        labels=[],
    )
    created = client.post(
        "/api/v1/plans/versions",
        json=_manual_payload(
            target.date(),
            task["id"],
            starts_at="09:00:00",
            ends_at="10:00:00",
        ),
    )
    assert created.status_code == 201, created.text

    queued = client.post(
        f"/api/v1/calendar/subscriptions/{source_id}/refresh",
        json={"operation_id": str(uuid4())},
    )
    assert queued.status_code == 200
    assert asyncio.run(
        run_worker(
            database_url,
            FakeFetcher(
                [
                    result(
                        one_off_ics(
                            "仅名称更新",
                            target,
                            starts_at="11:00:00",
                            ends_at="12:00:00",
                        ),
                        etag='"renamed"',
                    )
                ]
            ),
        )
    )

    current = planning_context(client, target)["current_plan"]
    assert current["source_conflicts"] == {
        "open_count": 0,
        "overlap_count": 0,
        "items": [],
    }


def test_new_busy_source_event_marks_existing_plan(
    client,
    database_url,
):
    create_account(client, database_url, time_zone="UTC")
    target = next_monday()
    source_id = create_subscription(client, name="新增课程来源")["id"]
    assert asyncio.run(
        run_worker(database_url, FakeFetcher([result(calendar())]))
    )
    task = create_task(
        client,
        title="已有计划",
        estimated_minutes=60,
        steps=[],
        links=[],
        labels=[],
    )
    created = client.post(
        "/api/v1/plans/versions",
        json=_manual_payload(
            target.date(),
            task["id"],
            starts_at="09:00:00",
            ends_at="10:00:00",
        ),
    )
    assert created.status_code == 201

    queued = client.post(
        f"/api/v1/calendar/subscriptions/{source_id}/refresh",
        json={"operation_id": str(uuid4())},
    )
    assert queued.status_code == 200
    assert asyncio.run(
        run_worker(
            database_url,
            FakeFetcher(
                [
                    result(
                        one_off_ics(
                            "新增课程",
                            target,
                            starts_at="09:15:00",
                            ends_at="09:45:00",
                        ),
                        etag='"added"',
                    )
                ]
            ),
        )
    )

    conflict = planning_context(client, target)["current_plan"][
        "source_conflicts"
    ]["items"][0]
    assert conflict["changes"][0]["kind"] == "added"
    assert conflict["affected_block_ids"] == [
        created.json()["plan"]["current_version"]["blocks"][0]["id"]
    ]


def test_local_non_busy_override_prevents_source_time_conflict(
    client,
    database_url,
):
    create_account(client, database_url, time_zone="UTC")
    target = next_monday()
    source_id = create_subscription(client, name="本地不占用来源")["id"]
    assert asyncio.run(
        run_worker(
            database_url,
            FakeFetcher(
                [
                    result(
                        one_off_ics(
                            "可选活动",
                            target,
                            starts_at="11:00:00",
                            ends_at="12:00:00",
                        )
                    )
                ]
            ),
        )
    )
    detail = client.get(f"/api/v1/calendar/subscriptions/{source_id}").json()
    source_event = detail["events"][0]
    override = client.put(
        f"/api/v1/calendar/subscriptions/{source_id}/events/"
        f"{source_event['id']}/overrides",
        json={
            "operation_id": str(uuid4()),
            "expected_version": 0,
            "values": {"busy": False},
            "reset_fields": [],
        },
    )
    assert override.status_code == 200, override.text
    task = create_task(
        client,
        title="本地不占用时允许的计划",
        estimated_minutes=60,
        steps=[],
        links=[],
        labels=[],
    )
    created = client.post(
        "/api/v1/plans/versions",
        json=_manual_payload(
            target.date(),
            task["id"],
            starts_at="09:00:00",
            ends_at="10:00:00",
        ),
    )
    assert created.status_code == 201, created.text

    queued = client.post(
        f"/api/v1/calendar/subscriptions/{source_id}/refresh",
        json={"operation_id": str(uuid4())},
    )
    assert queued.status_code == 200
    assert asyncio.run(
        run_worker(
            database_url,
            FakeFetcher(
                [
                    result(
                        one_off_ics(
                            "移动但仍不占用",
                            target,
                            starts_at="09:15:00",
                            ends_at="09:45:00",
                        ),
                        etag='"local-non-busy"',
                    )
                ]
            ),
        )
    )

    current = planning_context(client, target)["current_plan"]
    assert current["source_conflicts"]["open_count"] == 0


def test_projection_limit_records_conservative_review_without_losing_source(
    client,
    database_url,
    monkeypatch,
):
    import itime.planning.source_conflict_service as conflict_service

    create_account(client, database_url, time_zone="UTC")
    target = next_monday()
    source_id = create_subscription(client, name="大课表来源")["id"]
    assert asyncio.run(
        run_worker(
            database_url,
            FakeFetcher(
                [
                    result(
                        one_off_ics(
                            "原课程",
                            target,
                            starts_at="11:00:00",
                            ends_at="12:00:00",
                        )
                    )
                ]
            ),
        )
    )
    task = create_task(
        client,
        title="容量保护计划",
        estimated_minutes=60,
        steps=[],
        links=[],
        labels=[],
    )
    created = client.post(
        "/api/v1/plans/versions",
        json=_manual_payload(target.date(), task["id"]),
    )
    assert created.status_code == 201
    monkeypatch.setattr(conflict_service, "MAX_CONFLICT_OCCURRENCES", 0)

    queued = client.post(
        f"/api/v1/calendar/subscriptions/{source_id}/refresh",
        json={"operation_id": str(uuid4())},
    )
    assert queued.status_code == 200
    assert asyncio.run(
        run_worker(
            database_url,
            FakeFetcher(
                [
                    result(
                        one_off_ics(
                            "变更课程",
                            target,
                            starts_at="12:00:00",
                            ends_at="13:00:00",
                        ),
                        etag='"limited"',
                    )
                ]
            ),
        )
    )

    detail = client.get(f"/api/v1/calendar/subscriptions/{source_id}").json()
    assert detail["subscription"]["generation"] == 2
    conflict = planning_context(client, target)["current_plan"][
        "source_conflicts"
    ]["items"][0]
    assert conflict["details_limited"] is True
    assert conflict["changes"] == []
