from uuid import uuid4

import httpx
import psycopg
import pytest

from tests.conftest import psycopg_url
from tests.test_ai_conversation_api import (
    create_conversation,
    publish_conversation_config,
    send_message,
)
from tests.test_calendar_series import create_account

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def reset_quota_api_data(database_url):
    def cleanup() -> None:
        with psycopg.connect(
            psycopg_url(database_url),
            autocommit=True,
        ) as connection:
            connection.execute("DELETE FROM ai_quota_ledger_entries")
            connection.execute("DELETE FROM ai_quota_admin_adjustments")
            connection.execute("DELETE FROM ai_jobs")
            connection.execute("DELETE FROM ai_conversations")
            connection.execute("DELETE FROM ai_daily_allowances")
            connection.execute(
                """
                UPDATE ai_config_capabilities
                SET current_version_id=NULL, next_version=1, updated_at=now()
                WHERE capability='conversation'
                """
            )
            connection.execute(
                """
                DELETE FROM ai_config_versions
                WHERE capability='conversation'
                """
            )

    cleanup()
    yield
    cleanup()


def test_user_quota_summary_reservation_release_and_ledger(
    client,
    live_server,
    database_url,
):
    _, owner_id = create_account(client, database_url)
    publish_conversation_config(database_url)

    initial = client.get("/api/v1/quota")
    assert initial.status_code == 200
    assert initial.json()["configured"] is True
    assert initial.json()["current"]["base"] == "1"
    assert initial.json()["current"]["available"] == "1"
    assert initial.json()["reservations"] == []

    conversation = create_conversation(client).json()["conversation"]
    submitted = send_message(client, conversation, "预留额度")
    assert submitted.status_code == 202, submitted.text
    job_id = submitted.json()["job"]["id"]

    reserved = client.get("/api/v1/quota")
    assert reserved.status_code == 200
    reserved_body = reserved.json()
    assert len(reserved_body["reservations"]) == 1
    assert reserved_body["reservations"][0]["job_id"] == job_id
    assert float(reserved_body["current"]["reserved"]) > 0
    assert float(reserved_body["current"]["available"]) < 1

    cancelled = client.post(
        f"/api/v1/ai/jobs/{job_id}/cancel",
        json={"operation_id": str(uuid4())},
    )
    assert cancelled.status_code == 200
    after_cancel = client.get("/api/v1/quota").json()
    assert after_cancel["current"]["reserved"] == "0"
    assert after_cancel["current"]["charged"] == "0"
    assert after_cancel["reservations"] == []

    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        daily_id = connection.execute(
            """
            SELECT id FROM ai_daily_allowances
            WHERE owner_id=%s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (owner_id,),
        ).fetchone()[0]
        adjustment_id = uuid4()
        connection.execute(
            """
            INSERT INTO ai_quota_admin_adjustments (
                id, operation_id, request_hash, target_owner_id,
                daily_allowance_id, delta_nanos, reason, request_id
            ) VALUES (
                %s, %s, %s, %s, %s, 250000000,
                '测试补偿', %s
            )
            """,
            (
                adjustment_id,
                uuid4(),
                "d" * 64,
                owner_id,
                daily_id,
                str(uuid4()),
            ),
        )
        connection.execute(
            """
            INSERT INTO ai_quota_ledger_entries (
                id, owner_id, daily_allowance_id, adjustment_id,
                kind, delta_nanos, currency
            ) VALUES (
                %s, %s, %s, %s, 'admin_credit', 250000000, 'CNY'
            )
            """,
            (uuid4(), owner_id, daily_id, adjustment_id),
        )

    adjusted = client.get("/api/v1/quota").json()
    assert adjusted["current"]["adjustment"] == "0.25"
    assert adjusted["current"]["available"] == "1.25"
    ledger = client.get(
        "/api/v1/quota/ledger",
        params={"kind": "admin_credit", "limit": 1},
    )
    assert ledger.status_code == 200
    assert ledger.json()["items"][0]["delta"] == "0.25"
    assert ledger.json()["items"][0]["job"] is None

    with httpx.Client(base_url=live_server.origin, trust_env=False) as other:
        _, other_id = create_account(other, database_url)
        assert other.get("/api/v1/quota/ledger").json()["items"] == []
        with psycopg.connect(
            psycopg_url(database_url),
            autocommit=True,
        ) as connection:
            connection.execute(
                "DELETE FROM user_time_settings WHERE owner_id=%s",
                (other_id,),
            )
        missing_time = other.get("/api/v1/quota")
        assert missing_time.status_code == 409
        assert missing_time.json()["error"]["code"] == "time_settings_required"
