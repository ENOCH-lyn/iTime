from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
import psycopg
import pytest
from cryptography.fernet import Fernet

from itime.config import Settings
from tests.conftest import psycopg_url
from tests.test_ai_config import setup_admin

pytestmark = pytest.mark.integration
POLICIES = "/api/v1/admin/quota/policies"


@pytest.fixture(autouse=True)
def reset_quota_policy(database_url):
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        connection.execute("DELETE FROM ai_quota_ledger_entries")
        connection.execute("DELETE FROM ai_quota_admin_adjustments")
        connection.execute("DELETE FROM ai_job_attempts")
        connection.execute("DELETE FROM ai_jobs")
        connection.execute("DELETE FROM ai_daily_allowances")
        connection.execute("DELETE FROM temporary_media")
        connection.execute(
            "UPDATE ai_quota_policy_state "
            "SET current_version_id=NULL, next_version=1, updated_at=now()"
        )
        connection.execute("DELETE FROM ai_quota_policy_versions")
        connection.execute(
            "UPDATE ai_config_capabilities "
            "SET current_version_id=NULL, next_version=1, updated_at=now()"
        )
        connection.execute("DELETE FROM ai_config_versions")
        connection.execute(
            "DELETE FROM user_operations "
            "WHERE scope LIKE 'admin_quota_policy:%'"
        )
    yield
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        connection.execute("DELETE FROM ai_quota_ledger_entries")
        connection.execute("DELETE FROM ai_quota_admin_adjustments")
        connection.execute("DELETE FROM ai_job_attempts")
        connection.execute("DELETE FROM ai_jobs")
        connection.execute("DELETE FROM ai_daily_allowances")
        connection.execute("DELETE FROM temporary_media")
        connection.execute(
            "UPDATE ai_quota_policy_state "
            "SET current_version_id=NULL, next_version=1, updated_at=now()"
        )
        connection.execute("DELETE FROM ai_quota_policy_versions")
        connection.execute(
            "UPDATE ai_config_capabilities "
            "SET current_version_id=NULL, next_version=1, updated_at=now()"
        )
        connection.execute("DELETE FROM ai_config_versions")


def create_policy(
    client: httpx.Client,
    *,
    operation_id: str | None = None,
    amount: str = "0.5",
) -> httpx.Response:
    return client.post(
        POLICIES,
        json={
            "operation_id": operation_id or str(uuid4()),
            "currency": "cny",
            "daily_allowance": amount,
        },
    )


def seed_published_ai_configs(database_url: str) -> None:
    settings = Settings(database_url=database_url, environment="test")
    cipher = Fernet(
        (settings.ai_encryption_key or settings.encryption_key).get_secret_value()
    )
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        for capability in ("calendar_screenshot", "conversation"):
            config_id = uuid4()
            test_id = uuid4()
            connection.execute(
                """
                INSERT INTO ai_config_versions (
                    id, capability, version, endpoint_url, model,
                    api_key_ciphertext, timeout_seconds, max_output_tokens,
                    price_currency, input_price_nanos_per_million_tokens,
                    cached_input_price_nanos_per_million_tokens,
                    output_price_nanos_per_million_tokens, status,
                    last_test_id, last_test_started_at,
                    last_test_completed_at, published_at
                ) VALUES (
                    %s, %s, 1, 'https://ai.example.com/v1', 'quota-model',
                    %s, 60, 1024, 'CNY',
                    1000000000, 500000000, 2000000000,
                    'published', %s, now(), now(), now()
                )
                """,
                (
                    config_id,
                    capability,
                    cipher.encrypt(b"quota-key"),
                    test_id,
                ),
            )
            connection.execute(
                """
                UPDATE ai_config_capabilities
                SET current_version_id=%s, next_version=2, updated_at=now()
                WHERE capability=%s
                """,
                (config_id, capability),
            )


def test_quota_policy_create_publish_idempotency_and_concurrency(
    client,
    live_server,
    database_url,
):
    setup_admin(client, database_url)

    operation_id = str(uuid4())
    created = create_policy(client, operation_id=operation_id)
    assert created.status_code == 201, created.text
    assert created.json()["version"] == 1
    assert created.json()["daily_allowance"] == "0.5"
    assert created.json()["currency"] == "CNY"
    assert created.json()["status"] == "draft"
    assert create_policy(client, operation_id=operation_id).json() == created.json()
    reused = create_policy(client, operation_id=operation_id, amount="0.6")
    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "operation_id_reused"
    assert create_policy(client, amount="0.0000000001").status_code == 422

    no_prices = client.post(
        f"{POLICIES}/{created.json()['id']}/publish",
        json={"operation_id": str(uuid4())},
    )
    assert no_prices.status_code == 409
    assert no_prices.json()["error"]["code"] == "ai_quota_pricing_unavailable"

    seed_published_ai_configs(database_url)
    second = create_policy(client, amount="0.75")
    assert second.status_code == 201
    cookies = dict(client.cookies)
    csrf = {"x-csrf-token": client.headers["x-csrf-token"]}

    def publish(policy_id: str) -> httpx.Response:
        return httpx.post(
            f"{live_server.origin}{POLICIES}/{policy_id}/publish",
            json={"operation_id": str(uuid4())},
            cookies=cookies,
            headers=csrf,
            timeout=10,
            trust_env=False,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                publish,
                [created.json()["id"], second.json()["id"]],
            )
        )
    assert [response.status_code for response in responses] == [200, 200]

    listing = client.get(POLICIES)
    assert listing.status_code == 200
    items = listing.json()["items"]
    assert len(items) == 2
    assert sum(item["status"] == "published" for item in items) == 1
    assert sum(item["status"] == "retired" for item in items) == 1

    current = next(item for item in items if item["status"] == "published")
    replay_id = str(uuid4())
    first_replay = client.post(
        f"{POLICIES}/{current['id']}/publish",
        json={"operation_id": replay_id},
    )
    second_replay = client.post(
        f"{POLICIES}/{current['id']}/publish",
        json={"operation_id": replay_id},
    )
    assert first_replay.status_code == 200
    assert second_replay.json() == first_replay.json()

    with psycopg.connect(psycopg_url(database_url)) as connection:
        stored = connection.execute(
            """
            SELECT daily_allowance_nanos
            FROM ai_quota_policy_versions
            WHERE id=%s
            """,
            (created.json()["id"],),
        ).fetchone()[0]
        assert stored == 500_000_000
        assert connection.execute(
            """
            SELECT count(*)
            FROM security_events
            WHERE action IN (
                'ai_quota_policy_created',
                'ai_quota_policy_published',
                'ai_quota_policy_rolled_back'
            )
            """
        ).fetchone()[0] >= 4

    with httpx.Client(base_url=live_server.origin, trust_env=False) as outsider:
        assert outsider.get(POLICIES).status_code == 401
def test_quota_policy_currency_is_stable_after_first_version(
    client,
    database_url,
):
    setup_admin(client, database_url)
    assert create_policy(client).status_code == 201
    changed = client.post(
        POLICIES,
        json={
            "operation_id": str(uuid4()),
            "currency": "USD",
            "daily_allowance": "1",
        },
    )
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "ai_quota_currency_locked"
