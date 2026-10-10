from uuid import uuid4

import pytest

from tests.test_tasks import browser, create_account

pytestmark = pytest.mark.integration


def memory_payload(operation_id=None, **changes) -> dict:
    payload = {
        "operation_id": str(operation_id or uuid4()),
        "kind": "preference",
        "title": "深度工作时段",
        "content": "上午更适合需要持续思考的任务。",
        "tags": [],
    }
    payload.update(changes)
    return payload


def lifecycle_payload(version: int, operation_id=None) -> dict:
    return {
        "operation_id": str(operation_id or uuid4()),
        "expected_version": version,
    }


def test_memory_settings_default_update_replay_and_conflict(client, database_url):
    create_account(client, database_url)
    initial = client.get("/api/v1/memory-settings")
    assert initial.status_code == 200, initial.text
    settings = initial.json()["settings"]
    assert settings["enabled"] is True
    assert settings["version"] == 1

    operation_id = uuid4()
    payload = {
        "operation_id": str(operation_id),
        "expected_version": settings["version"],
        "enabled": False,
    }
    updated = client.put("/api/v1/memory-settings", json=payload)
    assert updated.status_code == 200, updated.text
    assert updated.json()["settings"]["enabled"] is False
    assert updated.json()["settings"]["version"] == 2
    assert client.put("/api/v1/memory-settings", json=payload).json() == updated.json()

    reused = client.put(
        "/api/v1/memory-settings",
        json={**payload, "enabled": True},
    )
    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "operation_id_reused"

    stale = client.put(
        "/api/v1/memory-settings",
        json={
            "operation_id": str(uuid4()),
            "expected_version": 1,
            "enabled": True,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "memory_settings_version_conflict"


def test_memory_crud_lifecycle_replay_and_owner_isolation(
    client,
    database_url,
    live_server,
):
    create_account(client, database_url)
    operation_id = uuid4()
    payload = memory_payload(operation_id)
    created = client.post("/api/v1/memories", json=payload)
    assert created.status_code == 201, created.text
    memory = created.json()["memory"]
    assert memory["source_type"] == "manual"
    assert memory["lifecycle"] == "active"
    assert client.post("/api/v1/memories", json=payload).json() == created.json()

    reused = client.post(
        "/api/v1/memories",
        json={**payload, "content": "另一个内容"},
    )
    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "operation_id_reused"

    updated = client.put(
        f"/api/v1/memories/{memory['id']}",
        json={
            **memory_payload(
                kind="constraint",
                title="晚间约束",
                content="晚上十点后不安排高强度任务。",
            ),
            "expected_version": memory["version"],
        },
    )
    assert updated.status_code == 200, updated.text
    memory = updated.json()["memory"]
    assert memory["kind"] == "constraint"
    assert memory["version"] == 2

    with browser(live_server) as other:
        create_account(other, database_url)
        assert other.get(f"/api/v1/memories/{memory['id']}").status_code == 404
        hidden_update = other.put(
            f"/api/v1/memories/{memory['id']}",
            json={
                **memory_payload(),
                "expected_version": memory["version"],
            },
        )
        assert hidden_update.status_code == 404

    stale = client.put(
        f"/api/v1/memories/{memory['id']}",
        json={
            **memory_payload(),
            "expected_version": 1,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "memory_version_conflict"

    trashed = client.request(
        "DELETE",
        f"/api/v1/memories/{memory['id']}",
        json=lifecycle_payload(memory["version"]),
    )
    assert trashed.status_code == 200, trashed.text
    memory = trashed.json()["memory"]
    assert memory["lifecycle"] == "trashed"
    assert memory["purge_after"] is not None
    assert client.get("/api/v1/memories").json()["items"] == []
    assert [
        item["id"]
        for item in client.get(
            "/api/v1/memories",
            params={"lifecycle": "trashed"},
        ).json()["items"]
    ] == [memory["id"]]

    restored = client.post(
        f"/api/v1/memories/{memory['id']}/restore",
        json=lifecycle_payload(memory["version"]),
    )
    assert restored.status_code == 200, restored.text
    memory = restored.json()["memory"]
    assert memory["lifecycle"] == "active"
    assert memory["version"] == 4


def test_memory_validation_and_cursor_contract(client, database_url):
    create_account(client, database_url)
    invalid = client.post(
        "/api/v1/memories",
        json=memory_payload(kind="unknown", title="   "),
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["saved"] == "not_saved"

    invalid_cursor = client.get(
        "/api/v1/memories",
        params={"before_time": "2026-10-03T00:00:00Z"},
    )
    assert invalid_cursor.status_code == 400
    assert invalid_cursor.json()["error"]["code"] == "invalid_cursor"


def _seed(client, **changes) -> dict:
    created = client.post("/api/v1/memories", json=memory_payload(**changes))
    assert created.status_code == 201, created.text
    return created.json()["memory"]


def test_memory_search_ilike_tags_order_and_touches_last_used(
    client,
    database_url,
):
    create_account(client, database_url)
    morning = _seed(
        client,
        title="晨会偏好",
        content="每天上午 10 点开始站会。",
        tags=["morning", "ritual"],
    )
    deep = _seed(
        client,
        title="深度工作",
        content="下午时段进行长思考任务。",
        tags=["focus"],
    )
    _seed(
        client,
        kind="constraint",
        title="晚间休息",
        content="晚上十点后不接工作消息。",
        tags=["evening"],
    )

    content_hit = client.post(
        "/api/v1/memory:search",
        json={"query": "站会", "limit": 5},
    )
    assert content_hit.status_code == 200, content_hit.text
    items = content_hit.json()["items"]
    assert [item["id"] for item in items] == [morning["id"]]
    assert items[0]["last_used_at"] is not None
    assert items[0]["tags"] == ["morning", "ritual"]

    tag_hit = client.post(
        "/api/v1/memory:search",
        json={"query": "focus", "limit": 5},
    )
    assert tag_hit.status_code == 200, tag_hit.text
    assert [item["id"] for item in tag_hit.json()["items"]] == [deep["id"]]

    empty_fallback = client.post(
        "/api/v1/memory:search",
        json={"query": "", "limit": 5},
    )
    assert empty_fallback.status_code == 200, empty_fallback.text
    ids = [item["id"] for item in empty_fallback.json()["items"]]
    # 命中过的放在前（last_used_at 更新），未命中按 updated_at 回落
    assert morning["id"] in ids and deep["id"] in ids
    assert ids.index(morning["id"]) < 2 and ids.index(deep["id"]) < 2

    trashed = client.request(
        "DELETE",
        f"/api/v1/memories/{deep['id']}",
        json=lifecycle_payload(deep["version"]),
    )
    assert trashed.status_code == 200, trashed.text
    hit_after_trash = client.post(
        "/api/v1/memory:search",
        json={"query": "focus", "limit": 5},
    )
    assert hit_after_trash.status_code == 200, hit_after_trash.text
    assert hit_after_trash.json()["items"] == []


def test_memory_search_disabled_returns_empty(client, database_url):
    create_account(client, database_url)
    _seed(client)
    settings = client.get("/api/v1/memory-settings").json()["settings"]
    toggled = client.put(
        "/api/v1/memory-settings",
        json={
            "operation_id": str(uuid4()),
            "expected_version": settings["version"],
            "enabled": False,
        },
    )
    assert toggled.status_code == 200
    disabled = client.post(
        "/api/v1/memory:search",
        json={"query": "", "limit": 5},
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["items"] == []


def test_memory_search_rejects_oversized_input(client, database_url):
    create_account(client, database_url)
    too_long = client.post(
        "/api/v1/memory:search",
        json={"query": "a" * 201, "limit": 5},
    )
    assert too_long.status_code == 422, too_long.text
    too_high = client.post(
        "/api/v1/memory:search",
        json={"query": "ok", "limit": 11},
    )
    assert too_high.status_code == 422, too_high.text


def test_memory_tags_round_trip_and_validation(client, database_url):
    create_account(client, database_url)
    tagged = client.post(
        "/api/v1/memories",
        json=memory_payload(tags=["focus", "ritual"]),
    )
    assert tagged.status_code == 201, tagged.text
    memory = tagged.json()["memory"]
    assert memory["tags"] == ["focus", "ritual"]

    cleared = client.put(
        f"/api/v1/memories/{memory['id']}",
        json={
            **memory_payload(tags=[]),
            "expected_version": memory["version"],
        },
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["memory"]["tags"] == []

    duplicate = client.post(
        "/api/v1/memories",
        json=memory_payload(tags=["focus", "focus"]),
    )
    assert duplicate.status_code == 422, duplicate.text

    too_many = client.post(
        "/api/v1/memories",
        json=memory_payload(tags=[f"t{i}" for i in range(11)]),
    )
    assert too_many.status_code == 422, too_many.text
