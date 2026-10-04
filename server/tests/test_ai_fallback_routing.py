import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from threading import Barrier
from uuid import UUID

import httpx
import psycopg
import pytest

from tests.conftest import psycopg_url
from tests.test_ai_config import (
    AI_CONFIGS,
    UpstreamState,
    create_config,
    setup_admin,
    upstream_handler,
)
from tests.test_calendar_series import browser

pytestmark = pytest.mark.integration


@pytest.fixture
def upstream(database_url):
    state = UpstreamState()
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        upstream_handler(state, database_url),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state, f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        state.release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture(autouse=True)
def reset_ai_routes(database_url):
    def cleanup() -> None:
        with psycopg.connect(
            psycopg_url(database_url),
            autocommit=True,
        ) as connection:
            connection.execute(
                """
                UPDATE ai_config_capabilities
                SET current_version_id=NULL, next_version=1,
                    routing_version=1, updated_at=now()
                """
            )
            connection.execute("DELETE FROM ai_config_fallbacks")
            connection.execute("DELETE FROM ai_quota_ledger_entries")
            connection.execute("DELETE FROM ai_quota_reservations")
            connection.execute("DELETE FROM ai_job_attempts")
            connection.execute("DELETE FROM ai_jobs")
            connection.execute("DELETE FROM temporary_media")
            connection.execute("DELETE FROM ai_config_versions")

    cleanup()
    yield
    cleanup()


def test_admin_configures_reorders_and_clears_fallback_route(
    client,
    database_url,
    upstream,
):
    admin = setup_admin(client, database_url)
    _state, endpoint = upstream
    configs = [
        create_config(
            client,
            endpoint,
            capability="conversation",
            model=f"conversation-route-{index}",
        ).json()
        for index in range(3)
    ]
    for config in configs:
        tested = client.post(f"{AI_CONFIGS}/{config['id']}/test")
        assert tested.status_code == 200, tested.text
    published = client.post(f"{AI_CONFIGS}/{configs[0]['id']}/publish")
    assert published.status_code == 200, published.text

    initial = client.get(f"{AI_CONFIGS}/routing/conversation")
    assert initial.status_code == 200, initial.text
    assert initial.json()["routing_version"] == 2
    assert initial.json()["primary"]["id"] == configs[0]["id"]
    assert initial.json()["fallbacks"] == []

    saved = client.put(
        f"{AI_CONFIGS}/routing/conversation",
        json={
            "expected_routing_version": 2,
            "fallback_config_ids": [
                configs[1]["id"],
                configs[2]["id"],
            ],
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["routing_version"] == 3
    assert [
        (item["position"], item["config"]["id"])
        for item in saved.json()["fallbacks"]
    ] == [
        (1, configs[1]["id"]),
        (2, configs[2]["id"]),
    ]
    repeated = client.put(
        f"{AI_CONFIGS}/routing/conversation",
        json={
            "expected_routing_version": 3,
            "fallback_config_ids": [
                configs[1]["id"],
                configs[2]["id"],
            ],
        },
    )
    assert repeated.status_code == 200
    assert repeated.json()["routing_version"] == 3

    cannot_retest = client.post(f"{AI_CONFIGS}/{configs[1]['id']}/test")
    assert cannot_retest.status_code == 409
    assert cannot_retest.json()["error"]["code"] == (
        "ai_config_fallback_in_use"
    )
    promoted = client.post(f"{AI_CONFIGS}/{configs[1]['id']}/publish")
    assert promoted.status_code == 200, promoted.text
    route = client.get(f"{AI_CONFIGS}/routing/conversation").json()
    assert route["routing_version"] == 4
    assert route["primary"]["id"] == configs[1]["id"]
    assert [
        (item["position"], item["config"]["id"])
        for item in route["fallbacks"]
    ] == [(1, configs[2]["id"])]

    stale = client.put(
        f"{AI_CONFIGS}/routing/conversation",
        json={
            "expected_routing_version": 3,
            "fallback_config_ids": [],
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "ai_config_route_version_conflict"
    assert stale.json()["error"]["details"]["current_version"] == 4

    cleared = client.put(
        f"{AI_CONFIGS}/routing/conversation",
        json={
            "expected_routing_version": 4,
            "fallback_config_ids": [],
        },
    )
    assert cleared.status_code == 200
    assert cleared.json()["routing_version"] == 5
    assert cleared.json()["fallbacks"] == []

    with psycopg.connect(psycopg_url(database_url)) as connection:
        audit = connection.execute(
            """
            SELECT action, details
            FROM security_events
            WHERE user_id=%s
              AND action='ai_config_fallbacks_updated'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (admin["admin"]["id"],),
        ).fetchone()
    assert audit[0] == "ai_config_fallbacks_updated"
    assert audit[1]["before"] == [configs[2]["id"]]
    assert audit[1]["after"] == []


def test_fallback_route_rejects_invalid_configs_and_admin_races(
    client,
    database_url,
    upstream,
    live_server,
):
    setup_admin(client, database_url)
    _state, endpoint = upstream
    primary = create_config(
        client,
        endpoint,
        capability="conversation",
        model="route-primary",
    ).json()
    backup_one = create_config(
        client,
        endpoint,
        capability="conversation",
        model="route-backup-one",
    ).json()
    backup_two = create_config(
        client,
        endpoint,
        capability="conversation",
        model="route-backup-two",
    ).json()
    other_capability = create_config(
        client,
        endpoint,
        capability="planning",
        model="route-planning",
    ).json()
    usd_backup = create_config(
        client,
        endpoint,
        capability="conversation",
        model="route-usd",
        pricing={
            "currency": "USD",
            "input_per_million_tokens": "1",
            "cached_input_per_million_tokens": "1",
            "output_per_million_tokens": "2",
        },
    ).json()
    for config in (
        primary,
        backup_one,
        backup_two,
        other_capability,
        usd_backup,
    ):
        tested = client.post(f"{AI_CONFIGS}/{config['id']}/test")
        assert tested.status_code == 200, tested.text
    assert client.post(f"{AI_CONFIGS}/{primary['id']}/publish").status_code == 200
    route = client.get(f"{AI_CONFIGS}/routing/conversation").json()

    for fallback_ids, expected_status, expected_code in (
        ([primary["id"]], 409, "ai_config_route_primary_reused"),
        (
            [backup_one["id"], backup_one["id"]],
            422,
            "ai_config_route_duplicate",
        ),
        (
            [other_capability["id"]],
            404,
            "ai_config_route_config_not_found",
        ),
        ([usd_backup["id"]], 409, "ai_config_route_currency_mismatch"),
    ):
        response = client.put(
            f"{AI_CONFIGS}/routing/conversation",
            json={
                "expected_routing_version": route["routing_version"],
                "fallback_config_ids": fallback_ids,
            },
        )
        assert response.status_code == expected_status
        assert response.json()["error"]["code"] == expected_code

    with browser(live_server) as other_admin:
        setup_admin(other_admin, database_url)
        barrier = Barrier(2)

        def update_route(current_client: httpx.Client, config_id: str):
            barrier.wait()
            return current_client.put(
                f"{AI_CONFIGS}/routing/conversation",
                json={
                    "expected_routing_version": route["routing_version"],
                    "fallback_config_ids": [config_id],
                },
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = [
                future.result()
                for future in (
                    pool.submit(update_route, client, backup_one["id"]),
                    pool.submit(update_route, other_admin, backup_two["id"]),
                )
            ]
    assert sorted(response.status_code for response in responses) == [200, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["error"]["code"] == "ai_config_route_version_conflict"

    with browser(live_server) as outsider:
        assert outsider.get(
            f"{AI_CONFIGS}/routing/conversation"
        ).status_code == 401

    with psycopg.connect(psycopg_url(database_url)) as connection:
        stored = connection.execute(
            """
            SELECT position, config_version_id
            FROM ai_config_fallbacks
            WHERE capability='conversation'
            ORDER BY position
            """
        ).fetchall()
    assert len(stored) == 1
    assert stored[0][1] in {
        UUID(backup_one["id"]),
        UUID(backup_two["id"]),
    }
