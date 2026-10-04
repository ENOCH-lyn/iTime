from datetime import datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest

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
