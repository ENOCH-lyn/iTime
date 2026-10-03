from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import httpx
import psycopg
import pytest

from tests.conftest import psycopg_url
from tests.test_calendar_series import browser, create_account, login_account

pytestmark = pytest.mark.integration


def update_payload(
    *,
    operation_id=None,
    expected_version: int = 1,
    enabled: bool = False,
) -> dict:
    return {
        "operation_id": str(operation_id or uuid4()),
        "expected_version": expected_version,
        "quality_improvement_enabled": enabled,
    }


def test_default_quality_setting_update_replay_and_audit(client, database_url):
    _, user_id = create_account(client, database_url)

    initial = client.get("/api/v1/privacy-settings")
    assert initial.status_code == 200
    assert initial.json()["settings"]["quality_improvement_enabled"] is True
    assert initial.json()["settings"]["version"] == 1

    operation_id = uuid4()
    payload = update_payload(operation_id=operation_id)
    changed = client.put("/api/v1/privacy-settings", json=payload)
    assert changed.status_code == 200
    assert changed.json()["settings"]["quality_improvement_enabled"] is False
    assert changed.json()["settings"]["version"] == 2
    assert client.put("/api/v1/privacy-settings", json=payload).json() == changed.json()

    reused = client.put(
        "/api/v1/privacy-settings",
        json=update_payload(
            operation_id=operation_id,
            expected_version=2,
            enabled=True,
        ),
    )
    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "operation_id_reused"

    unchanged = client.put(
        "/api/v1/privacy-settings",
        json=update_payload(expected_version=2),
    )
    assert unchanged.status_code == 200
    assert unchanged.json()["settings"]["version"] == 2

    with psycopg.connect(psycopg_url(database_url)) as connection:
        changes = connection.execute(
            """
            SELECT action, entity_version
            FROM change_events
            WHERE owner_id=%s AND entity_type='privacy_settings'
            ORDER BY sequence
            """,
            (user_id,),
        ).fetchall()
        assert changes == [("updated", 2)]
        events = connection.execute(
            """
            SELECT action, target_id, details
            FROM security_events
            WHERE user_id=%s AND action IN (
                'ai_quality_improvement_enabled',
                'ai_quality_improvement_disabled'
            )
            ORDER BY created_at
            """,
            (user_id,),
        ).fetchall()
        assert events == [
            ("ai_quality_improvement_disabled", user_id, {}),
        ]


def test_quality_setting_csrf_missing_state_and_concurrent_version(
    client,
    database_url,
    live_server,
):
    email, user_id = create_account(client, database_url)
    csrf = client.headers.pop("x-csrf-token")
    rejected = client.put(
        "/api/v1/privacy-settings",
        json=update_payload(),
    )
    assert rejected.status_code == 403
    assert rejected.json()["error"]["code"] == "invalid_csrf"
    client.headers["x-csrf-token"] = csrf

    barrier = Barrier(2)

    def disable(peer: httpx.Client) -> httpx.Response:
        barrier.wait(timeout=5)
        return peer.put(
            "/api/v1/privacy-settings",
            json=update_payload(),
        )

    with browser(live_server) as first, browser(live_server) as second:
        login_account(first, email)
        login_account(second, email)
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(disable, [first, second]))

    assert sorted(response.status_code for response in responses) == [200, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["error"]["code"] == "privacy_settings_version_conflict"
    assert conflict.json()["error"]["details"]["current_version"] == 2

    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            "DELETE FROM user_privacy_settings WHERE owner_id=%s",
            (user_id,),
        )
    unavailable = client.get("/api/v1/privacy-settings")
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "privacy_settings_unavailable"
