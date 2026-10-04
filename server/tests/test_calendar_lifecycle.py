import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest

from itime.calendar.subscription_parser import parse_subscription_ics
from itime.calendar.subscription_service import (
    claim_next_subscription,
    complete_not_modified,
    fail_subscription_refresh,
    renew_subscription_lease,
)
from itime.database import Database
from tests.conftest import psycopg_url
from tests.test_calendar_additions import addition_payload
from tests.test_calendar_exceptions import exception_payload
from tests.test_calendar_series import (
    browser,
    create_account,
    create_series,
    fixed_payload,
    login_account,
    recurrence,
)
from tests.test_calendar_subscriptions import (
    FakeFetcher,
    apply_stale_result,
    create_subscription,
    daily_ics,
    exercise_claim_and_stale_result,
    result,
    run_worker,
    worker_settings,
)

pytestmark = pytest.mark.integration


def lifecycle_payload(expected_version: int, *, operation_id=None) -> dict:
    return {
        "operation_id": str(operation_id or uuid4()),
        "expected_version": expected_version,
    }


def trash_series(client, series: dict, *, operation_id=None):
    return client.request(
        "DELETE",
        f"/api/v1/calendar/series/{series['id']}",
        json=lifecycle_payload(
            series["version"],
            operation_id=operation_id,
        ),
    )


def restore_series(client, series: dict, *, operation_id=None):
    return client.post(
        f"/api/v1/calendar/series/{series['id']}/restore",
        json=lifecycle_payload(
            series["version"],
            operation_id=operation_id,
        ),
    )


def trash_subscription(client, subscription: dict, *, operation_id=None):
    return client.request(
        "DELETE",
        f"/api/v1/calendar/subscriptions/{subscription['id']}",
        json=lifecycle_payload(
            subscription["version"],
            operation_id=operation_id,
        ),
    )


def restore_subscription(client, subscription: dict, *, operation_id=None):
    return client.post(
        f"/api/v1/calendar/subscriptions/{subscription['id']}/restore",
        json=lifecycle_payload(
            subscription["version"],
            operation_id=operation_id,
        ),
    )


def subscription_items(client, lifecycle: str = "active") -> list[dict]:
    response = client.get(
        "/api/v1/calendar/subscriptions",
        params={"lifecycle": lifecycle},
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


def planning_revision(database_url: str, owner_id: str) -> int:
    with psycopg.connect(psycopg_url(database_url)) as connection:
        return connection.execute(
            """
            SELECT revision
            FROM planning_input_revisions
            WHERE owner_id=%s
            """,
            (owner_id,),
        ).fetchone()[0]


def lifecycle_items(client, lifecycle: str = "active") -> list[dict]:
    response = client.get(
        "/api/v1/calendar/series",
        params={"lifecycle": lifecycle},
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


def occurrence_names(client, start: str, end: str) -> list[str]:
    response = client.get(
        "/api/v1/calendar/occurrences",
        params={"start": start, "end": end},
    )
    assert response.status_code == 200, response.text
    return [item["name"] for item in response.json()["items"]]


def assert_trashed(series: dict) -> None:
    assert series["lifecycle"] == "trashed"
    assert series["deleted_at"] is not None
    assert series["purge_after"] is not None
    assert series["purged_at"] is None
    assert series["trash_remaining_days"] == 30
    deleted_at = datetime.fromisoformat(series["deleted_at"].replace("Z", "+00:00"))
    purge_after = datetime.fromisoformat(series["purge_after"].replace("Z", "+00:00"))
    assert purge_after - deleted_at == timedelta(days=30)


async def late_subscription_results(database_url: str, claim) -> tuple[bool, bool, bool]:
    settings = worker_settings(database_url)
    database = Database(settings)
    try:
        renewed = await renew_subscription_lease(
            database,
            claim,
            lease_seconds=settings.calendar_subscription_lease_seconds,
        )
        not_modified = await complete_not_modified(
            database,
            claim,
            result(None, status=304),
        )
        failed = await fail_subscription_refresh(
            database,
            claim,
            error_code="late_failure",
            error_message="迟到失败不应覆盖删除状态。",
        )
        return renewed, not_modified, failed
    finally:
        await database.close()


async def claim_subscription(database_url: str):
    settings = worker_settings(database_url)
    database = Database(settings)
    try:
        return await claim_next_subscription(
            database,
            lease_seconds=settings.calendar_subscription_lease_seconds,
        )
    finally:
        await database.close()


def test_series_trash_restore_preserves_aggregate_and_filters_projection(
    client,
    database_url,
):
    _, owner_id = create_account(client, database_url)
    series = create_series(
        client,
        name="回收测试安排",
        start_date="2026-10-05",
        recurrence=recurrence("daily", count=3),
    )["series"]
    cancelled = client.post(
        f"/api/v1/calendar/series/{series['id']}/exceptions",
        json=exception_payload(
            expected_series_version=series["version"],
            occurrence_key="2026-10-06T09:00:00",
            action="cancelled",
        ),
    )
    assert cancelled.status_code == 201, cancelled.text
    series = cancelled.json()["series"]
    added = client.post(
        f"/api/v1/calendar/series/{series['id']}/additions",
        json=addition_payload(
            expected_series_version=series["version"],
            source_occurrence_key="2026-10-06T09:00:00",
            local_start_date="2026-10-08",
        ),
    )
    assert added.status_code == 201, added.text
    series = added.json()["series"]
    revision_before = planning_revision(database_url, owner_id)

    operation_id = uuid4()
    deleted_response = trash_series(
        client,
        series,
        operation_id=operation_id,
    )
    assert deleted_response.status_code == 200, deleted_response.text
    deleted = deleted_response.json()["series"]
    assert deleted["version"] == series["version"] + 1
    assert_trashed(deleted)
    assert planning_revision(database_url, owner_id) == revision_before + 1

    replayed = trash_series(client, series, operation_id=operation_id)
    assert replayed.status_code == 200
    assert replayed.json() == deleted_response.json()

    repeated = trash_series(client, deleted)
    assert repeated.status_code == 200, repeated.text
    repeated_series = repeated.json()["series"]
    assert repeated_series["version"] == deleted["version"]
    assert repeated_series["purge_after"] == deleted["purge_after"]

    assert {item["id"] for item in lifecycle_items(client)} == set()
    trashed_items = lifecycle_items(client, "trashed")
    assert [item["id"] for item in trashed_items] == [series["id"]]
    assert_trashed(trashed_items[0])
    assert "回收测试安排" not in occurrence_names(
        client,
        "2026-10-05",
        "2026-10-10",
    )

    detail = client.get(f"/api/v1/calendar/series/{series['id']}")
    assert detail.status_code == 200
    assert_trashed(detail.json()["series"])

    blocked_update = client.put(
        f"/api/v1/calendar/series/{series['id']}",
        json={
            **fixed_payload(name="不应覆盖"),
            "expected_version": deleted["version"],
        },
    )
    assert blocked_update.status_code == 409
    assert blocked_update.json()["error"]["code"] == "calendar_item_trashed"
    assert blocked_update.json()["error"]["request_id"]

    blocked_exception = client.post(
        f"/api/v1/calendar/series/{series['id']}/exceptions",
        json=exception_payload(
            expected_series_version=deleted["version"],
            occurrence_key="2026-10-05T09:00:00",
            action="overridden",
            override_name="不应保存",
        ),
    )
    assert blocked_exception.status_code == 409
    assert blocked_exception.json()["error"]["code"] == "calendar_item_trashed"

    restored_response = restore_series(client, deleted)
    assert restored_response.status_code == 200, restored_response.text
    restored = restored_response.json()["series"]
    assert restored["lifecycle"] == "active"
    assert restored["deleted_at"] is None
    assert restored["purge_after"] is None
    assert restored["purged_at"] is None
    assert restored["trash_remaining_days"] is None
    assert restored["version"] == deleted["version"] + 1
    assert planning_revision(database_url, owner_id) == revision_before + 2
    assert {item["id"] for item in lifecycle_items(client)} == {series["id"]}
    assert lifecycle_items(client, "trashed") == []

    names = occurrence_names(client, "2026-10-05", "2026-10-10")
    assert names.count("回收测试安排") == 4


def test_series_lifecycle_ownership_conflicts_and_operation_reuse(
    client,
    live_server,
    database_url,
):
    email, _ = create_account(client, database_url)
    series = create_series(client, name="本人安排")["series"]

    with browser(live_server) as other:
        create_account(other, database_url)
        hidden_delete = trash_series(other, series)
        assert hidden_delete.status_code == 404
        hidden_restore = restore_series(other, series)
        assert hidden_restore.status_code == 404

    login_account(client, email)
    stale = trash_series(client, {**series, "version": series["version"] + 1})
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "calendar_series_version_conflict"

    operation_id = uuid4()
    deleted = trash_series(client, series, operation_id=operation_id)
    assert deleted.status_code == 200
    deleted_series = deleted.json()["series"]

    reused = client.request(
        "DELETE",
        f"/api/v1/calendar/series/{series['id']}",
        json={
            "operation_id": str(operation_id),
            "expected_version": deleted_series["version"],
        },
    )
    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "operation_id_reused"

    active_restore = restore_series(client, deleted_series)
    assert active_restore.status_code == 200
    restored = active_restore.json()["series"]
    duplicate_restore = restore_series(client, restored)
    assert duplicate_restore.status_code == 409
    assert duplicate_restore.json()["error"]["code"] == "calendar_item_not_trashed"

    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        connection.execute(
            """
            UPDATE calendar_series
            SET deleted_at=now() - interval '31 days',
                purge_after=now() - interval '1 day',
                purged_at=now(),
                version=version + 1
            WHERE id=%s
            """,
            (UUID(series["id"]),),
        )
    purged_detail = client.get(f"/api/v1/calendar/series/{series['id']}")
    assert purged_detail.status_code == 410
    purged_restore = client.post(
        f"/api/v1/calendar/series/{series['id']}/restore",
        json=lifecycle_payload(restored["version"] + 1),
    )
    assert purged_restore.status_code == 410


def test_subscription_trash_invalidates_lease_and_restores_active_source(
    client,
    database_url,
):
    _, owner_id = create_account(client, database_url, time_zone="UTC")
    subscription = create_subscription(client, name="待回收活动订阅")
    claim = asyncio.run(
        exercise_claim_and_stale_result(database_url, subscription["id"])
    )
    revision_before = planning_revision(database_url, owner_id)

    operation_id = uuid4()
    deleted_response = trash_subscription(
        client,
        subscription,
        operation_id=operation_id,
    )
    assert deleted_response.status_code == 200, deleted_response.text
    deleted = deleted_response.json()["subscription"]
    assert deleted["lifecycle"] == "trashed"
    assert deleted["lifecycle_status"] == "active"
    assert deleted["refresh_status"] == "idle"
    assert deleted["trash_remaining_days"] == 30
    assert planning_revision(database_url, owner_id) == revision_before + 1

    stale_snapshot = parse_subscription_ics(
        daily_ics("迟到内容", datetime.now(UTC).date()),
        default_time_zone="UTC",
    )
    assert not asyncio.run(apply_stale_result(database_url, claim, stale_snapshot))
    assert asyncio.run(late_subscription_results(database_url, claim)) == (
        False,
        False,
        False,
    )
    assert asyncio.run(claim_subscription(database_url)) is None
    assert subscription_items(client) == []
    trashed_items = subscription_items(client, "trashed")
    assert [item["id"] for item in trashed_items] == [subscription["id"]]

    detail_response = client.get(
        f"/api/v1/calendar/subscriptions/{subscription['id']}"
    )
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["subscription"]["lifecycle"] == "trashed"
    assert detail["refreshes"][0]["status"] == "stale"
    assert detail["events"] == []

    blocked_refresh = client.post(
        f"/api/v1/calendar/subscriptions/{subscription['id']}/refresh",
        json={"operation_id": str(uuid4())},
    )
    assert blocked_refresh.status_code == 409
    assert blocked_refresh.json()["error"]["code"] == "calendar_item_trashed"
    blocked_update = client.put(
        f"/api/v1/calendar/subscriptions/{subscription['id']}",
        json={
            "operation_id": str(uuid4()),
            "expected_version": deleted["version"],
            "name": "不应覆盖",
            "refresh_interval_minutes": 60,
        },
    )
    assert blocked_update.status_code == 409
    assert blocked_update.json()["error"]["code"] == "calendar_item_trashed"

    duplicate = client.post(
        "/api/v1/calendar/subscriptions",
        json={
            "operation_id": str(uuid4()),
            "name": "重复来源",
            "url": "http://calendar.test/feed.ics?token=do-not-display",
            "refresh_interval_minutes": 60,
        },
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == (
        "calendar_subscription_trashed_duplicate"
    )
    assert duplicate.json()["error"]["details"]["restore_source_id"] == (
        subscription["id"]
    )

    replayed = trash_subscription(
        client,
        subscription,
        operation_id=operation_id,
    )
    assert replayed.json() == deleted_response.json()

    restored_response = restore_subscription(client, deleted)
    assert restored_response.status_code == 200, restored_response.text
    restored = restored_response.json()["subscription"]
    assert restored["lifecycle"] == "active"
    assert restored["lifecycle_status"] == "active"
    assert restored["refresh_status"] == "queued"
    assert restored["version"] == deleted["version"] + 1
    assert planning_revision(database_url, owner_id) == revision_before + 2

    assert asyncio.run(
        run_worker(
            database_url,
            FakeFetcher(
                [
                    result(
                        daily_ics(
                            "恢复后的订阅安排",
                            datetime.now(UTC).date(),
                        )
                    )
                ]
            ),
        )
    )
    assert "恢复后的订阅安排" in occurrence_names(
        client,
        (datetime.now(UTC).date() - timedelta(days=1)).isoformat(),
        (datetime.now(UTC).date() + timedelta(days=2)).isoformat(),
    )


def test_subscription_restore_preserves_paused_state_and_ownership(
    client,
    live_server,
    database_url,
):
    email, _ = create_account(client, database_url, time_zone="UTC")
    subscription = create_subscription(client, name="暂停后回收")
    paused_response = client.post(
        f"/api/v1/calendar/subscriptions/{subscription['id']}/pause",
        json=lifecycle_payload(subscription["version"]),
    )
    assert paused_response.status_code == 200
    paused = paused_response.json()["subscription"]

    deleted_response = trash_subscription(client, paused)
    assert deleted_response.status_code == 200
    deleted = deleted_response.json()["subscription"]
    restored_response = restore_subscription(client, deleted)
    assert restored_response.status_code == 200
    restored = restored_response.json()["subscription"]
    assert restored["lifecycle_status"] == "paused"
    assert restored["refresh_status"] == "idle"

    with browser(live_server) as other:
        create_account(other, database_url, time_zone="UTC")
        assert trash_subscription(other, restored).status_code == 404
        assert restore_subscription(other, restored).status_code == 404

    login_account(client, email)
    stale = trash_subscription(
        client,
        {**restored, "version": restored["version"] + 1},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == (
        "calendar_subscription_version_conflict"
    )
