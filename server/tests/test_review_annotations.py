import asyncio
from uuid import uuid4

import pytest

from itime.ai.worker import process_one
from itime.config import Settings
from itime.database import Database
from tests.quota_helpers import ensure_quota_policy
from tests.test_review_worker import (
    create_review,
    install_review_config,
)
from tests.test_review_worker import (
    review_upstream as _review_upstream,
)

review_upstream = _review_upstream

pytestmark = pytest.mark.integration


def generated_report(client, database_url, endpoint) -> dict:
    review = create_review(client, database_url, offset_days=-3)
    ensure_quota_policy(database_url)
    install_review_config(database_url, endpoint)
    review = client.put(
        f"/api/v1/reviews/{review['id']}/facts",
        json={
            "operation_id": str(uuid4()),
            "expected_version": review["version"],
            "supplemental_facts": "上午更适合持续思考。",
        },
    ).json()["review"]
    queued = client.post(
        f"/api/v1/reviews/{review['id']}/reports",
        json={
            "operation_id": str(uuid4()),
            "expected_version": review["version"],
            "instruction": "",
        },
    )
    assert queued.status_code == 202, queued.text
    settings = Settings(database_url=database_url, environment="test")
    database = Database(settings)
    try:
        assert asyncio.run(process_one(database, settings))
    finally:
        asyncio.run(database.close())
    return client.get(f"/api/v1/reviews/{review['id']}/reports/1").json()


def test_report_annotations_are_versioned_idempotent_and_removable(
    client,
    database_url,
    review_upstream,
):
    _, endpoint = review_upstream
    detail = generated_report(client, database_url, endpoint)
    report = detail["report"]
    operation_id = uuid4()
    create_payload = {
        "operation_id": str(operation_id),
        "content": "这里还需要补充一次临时讨论。",
    }
    created = client.post(
        f"/api/v1/review-reports/{report['id']}/annotations",
        json=create_payload,
    )
    assert created.status_code == 201, created.text
    annotation = created.json()["annotation"]
    assert client.post(
        f"/api/v1/review-reports/{report['id']}/annotations",
        json=create_payload,
    ).json() == created.json()

    updated = client.put(
        f"/api/v1/review-annotations/{annotation['id']}",
        json={
            "operation_id": str(uuid4()),
            "expected_version": annotation["version"],
            "content": "临时讨论实际发生在下午。",
        },
    )
    assert updated.status_code == 200, updated.text
    annotation = updated.json()["annotation"]
    assert annotation["version"] == 2

    stale = client.put(
        f"/api/v1/review-annotations/{annotation['id']}",
        json={
            "operation_id": str(uuid4()),
            "expected_version": 1,
            "content": "陈旧批注",
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "review_annotation_version_conflict"

    deleted = client.request(
        "DELETE",
        f"/api/v1/review-annotations/{annotation['id']}",
        json={
            "operation_id": str(uuid4()),
            "expected_version": annotation["version"],
        },
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["annotation"]["deleted_at"] is not None


def test_memory_proposals_require_explicit_one_way_decisions(
    client,
    database_url,
    review_upstream,
):
    _, endpoint = review_upstream
    detail = generated_report(client, database_url, endpoint)
    first, second = detail["memory_proposals"]
    operation_id = uuid4()
    payload = {
        "operation_id": str(operation_id),
        "expected_version": first["version"],
    }
    confirmed = client.post(
        f"/api/v1/memory-proposals/{first['id']}/confirm",
        json=payload,
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["proposal"]["status"] == "confirmed"
    assert confirmed.json()["memory"]["source_proposal_id"] == first["id"]
    assert client.post(
        f"/api/v1/memory-proposals/{first['id']}/confirm",
        json=payload,
    ).json() == confirmed.json()

    reverse = client.post(
        f"/api/v1/memory-proposals/{first['id']}/reject",
        json={
            "operation_id": str(uuid4()),
            "expected_version": confirmed.json()["proposal"]["version"],
        },
    )
    assert reverse.status_code == 409
    assert reverse.json()["error"]["code"] == "memory_proposal_not_pending"

    rejected = client.post(
        f"/api/v1/memory-proposals/{second['id']}/reject",
        json={
            "operation_id": str(uuid4()),
            "expected_version": second["version"],
        },
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["proposal"]["status"] == "rejected"

    memories = client.get("/api/v1/memories").json()["items"]
    assert len(memories) == 1
    assert memories[0]["title"] == first["title"]
