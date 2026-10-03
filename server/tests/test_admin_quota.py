from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import httpx
import psycopg
import pytest

from tests.conftest import psycopg_url
from tests.quota_helpers import ensure_quota_policy
from tests.test_admin_management import setup_admin
from tests.test_ai_conversation_api import (
    create_conversation,
    publish_conversation_config,
    send_message,
)
from tests.test_calendar_series import create_account

pytestmark = pytest.mark.integration
ADMIN_QUOTA = "/api/v1/admin/quota"


@pytest.fixture(autouse=True)
def reset_admin_quota_data(database_url):
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
                DELETE FROM user_operations
                WHERE scope LIKE 'admin_quota_adjustment:%'
                """
            )

    cleanup()
    yield
    cleanup()


@contextmanager
def browser(live_server):
    with httpx.Client(
        base_url=live_server.origin,
        timeout=10,
        trust_env=False,
    ) as client:
        yield client


def adjustment_payload(
    activity_date: str,
    expected_daily_version: int,
    *,
    mode: str = "credit",
    amount: str = "0.25",
    reason: str = "人工补偿",
    operation_id: str | None = None,
) -> dict:
    return {
        "operation_id": operation_id or str(uuid4()),
        "expected_daily_version": expected_daily_version,
        "activity_date": activity_date,
        "mode": mode,
        "amount": amount,
        "reason": reason,
    }


def test_admin_quota_adjustments_are_versioned_idempotent_and_audited(
    client,
    live_server,
    database_url,
):
    admin = setup_admin(client, database_url)
    ensure_quota_policy(database_url)
    with browser(live_server) as user_client:
        user_email, user_id = create_account(user_client, database_url)

        initial = client.get(f"{ADMIN_QUOTA}/users/{user_id}")
        assert initial.status_code == 200, initial.text
        initial_body = initial.json()
        assert initial_body["user"] == {
            "id": user_id,
            "email": user_email,
            "status": "active",
            "ai_access": "allowed",
        }
        assert initial_body["configured"] is True
        assert initial_body["current"]["id"] is None
        assert initial_body["current"]["version"] == 0
        assert initial_body["current"]["available"] == "1"
        activity_date = initial_body["current"]["activity_date"]

        operation_id = str(uuid4())
        payload = adjustment_payload(
            activity_date,
            0,
            operation_id=operation_id,
        )
        created = client.post(
            f"{ADMIN_QUOTA}/users/{user_id}/adjustments",
            json=payload,
        )
        assert created.status_code == 201, created.text
        created_body = created.json()
        assert created_body["adjustment"]["operation_id"] == operation_id
        assert created_body["adjustment"]["mode"] == "credit"
        assert created_body["adjustment"]["amount"] == "0.25"
        assert created_body["adjustment"]["delta"] == "0.25"
        assert created_body["adjustment"]["reason"] == "人工补偿"
        after_credit = client.get(
            f"{ADMIN_QUOTA}/users/{user_id}"
        ).json()["current"]
        assert after_credit["version"] == 2
        assert after_credit["adjustment"] == "0.25"
        assert after_credit["available"] == "1.25"

        replay = client.post(
            f"{ADMIN_QUOTA}/users/{user_id}/adjustments",
            json=payload,
        )
        assert replay.status_code == 201
        assert replay.json() == created_body
        with browser(live_server) as second_admin:
            setup_admin(second_admin, database_url)
            cross_admin_replay = second_admin.post(
                f"{ADMIN_QUOTA}/users/{user_id}/adjustments",
                json=payload,
            )
            assert cross_admin_replay.status_code == 201
            assert cross_admin_replay.json() == created_body
        changed_replay = client.post(
            f"{ADMIN_QUOTA}/users/{user_id}/adjustments",
            json={**payload, "amount": "0.3"},
        )
        assert changed_replay.status_code == 409
        assert changed_replay.json()["error"]["code"] == "operation_id_reused"

        stale = client.post(
            f"{ADMIN_QUOTA}/users/{user_id}/adjustments",
            json=adjustment_payload(activity_date, 0),
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "ai_quota_daily_version_conflict"
        assert stale.json()["error"]["details"]["current_version"] == 2

        excessive = client.post(
            f"{ADMIN_QUOTA}/users/{user_id}/adjustments",
            json=adjustment_payload(
                activity_date,
                2,
                mode="debit",
                amount="1.250000001",
                reason="纠正误发",
            ),
        )
        assert excessive.status_code == 409
        assert excessive.json()["error"]["code"] == "ai_quota_adjustment_insufficient"
        assert excessive.json()["error"]["details"]["available"] == "1.25"

        debited = client.post(
            f"{ADMIN_QUOTA}/users/{user_id}/adjustments",
            json=adjustment_payload(
                activity_date,
                2,
                mode="debit",
                amount="0.5",
                reason="纠正重复补偿",
            ),
        )
        assert debited.status_code == 201, debited.text
        assert debited.json()["adjustment"]["delta"] == "-0.5"
        after_debit = client.get(
            f"{ADMIN_QUOTA}/users/{user_id}"
        ).json()["current"]
        assert after_debit["version"] == 3
        assert after_debit["available"] == "0.75"

        user_summary = user_client.get("/api/v1/quota")
        assert user_summary.status_code == 200
        assert user_summary.json()["current"]["adjustment"] == "-0.25"
        assert user_summary.json()["current"]["available"] == "0.75"
        user_ledger = user_client.get("/api/v1/quota/ledger")
        assert user_ledger.status_code == 200
        assert [item["kind"] for item in user_ledger.json()["items"]] == [
            "admin_debit",
            "admin_credit",
        ]

        zero = client.post(
            f"{ADMIN_QUOTA}/users/{user_id}/adjustments",
            json=adjustment_payload(activity_date, 3, amount="0"),
        )
        assert zero.status_code == 422

        ledger = client.get(
            f"{ADMIN_QUOTA}/ledger",
            params={
                "user_id": user_id,
                "activity_date": activity_date,
                "kind": "admin_debit",
                "currency": "cny",
                "limit": 1,
            },
        )
        assert ledger.status_code == 200, ledger.text
        ledger_body = ledger.json()
        assert len(ledger_body["items"]) == 1
        item = ledger_body["items"][0]
        assert item["user"] == {"id": user_id, "email": user_email}
        assert item["kind"] == "admin_debit"
        assert item["delta"] == "-0.5"
        assert item["adjustment"]["reason"] == "纠正重复补偿"
        assert item["adjustment"]["actor"]["id"] == admin["admin"]["id"]
        assert item["job"] is None

        operation_lookup = client.get(
            f"{ADMIN_QUOTA}/ledger",
            params={"operation_id": operation_id},
        )
        assert operation_lookup.status_code == 200
        assert len(operation_lookup.json()["items"]) == 1
        assert operation_lookup.json()["items"][0]["adjustment"]["operation_id"] == operation_id

        all_entries = client.get(
            f"{ADMIN_QUOTA}/ledger",
            params={"user_id": user_id, "limit": 1},
        ).json()
        assert len(all_entries["items"]) == 1
        assert all_entries["next_cursor"] is not None
        second_page = client.get(
            f"{ADMIN_QUOTA}/ledger",
            params={
                "user_id": user_id,
                "limit": 1,
                **all_entries["next_cursor"],
            },
        )
        assert second_page.status_code == 200
        assert len(second_page.json()["items"]) == 1
        assert second_page.json()["next_cursor"] is None

        with psycopg.connect(psycopg_url(database_url)) as connection:
            facts = connection.execute(
                """
                SELECT
                    (SELECT count(*) FROM ai_quota_admin_adjustments
                     WHERE target_owner_id=%s),
                    (SELECT count(*) FROM ai_quota_ledger_entries
                     WHERE owner_id=%s AND kind LIKE 'admin_%%'),
                    (SELECT count(*) FROM security_events
                     WHERE user_id=%s
                       AND target_id=%s
                       AND action IN (
                           'ai_quota_admin_credit',
                           'ai_quota_admin_debit'
                       ))
                """,
                (
                    user_id,
                    user_id,
                    admin["admin"]["id"],
                    user_id,
                ),
            ).fetchone()
        assert facts == (2, 2, 2)

        with browser(live_server) as outsider:
            assert outsider.get(f"{ADMIN_QUOTA}/users/{user_id}").status_code == 401
            assert outsider.get(f"{ADMIN_QUOTA}/ledger").status_code == 401
        assert client.get(f"{ADMIN_QUOTA}/users/{uuid4()}").status_code == 404
        protected = client.post(
            f"{ADMIN_QUOTA}/users/{admin['admin']['id']}/adjustments",
            json=adjustment_payload(activity_date, 0),
        )
        assert protected.status_code == 409
        assert protected.json()["error"]["code"] == "admin_account_protected"
        assert (
            client.get(
                f"{ADMIN_QUOTA}/ledger",
                params={"before_time": created_body["adjustment"]["created_at"]},
            ).json()["error"]["code"]
            == "invalid_cursor"
        )

        csrf = client.headers.pop("x-csrf-token")
        denied = client.post(
            f"{ADMIN_QUOTA}/users/{user_id}/adjustments",
            json=adjustment_payload(activity_date, 3),
        )
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "invalid_csrf"
        client.headers["x-csrf-token"] = csrf

        missing_date = str((datetime.fromisoformat(activity_date) - timedelta(days=1)).date())
        missing = client.get(
            f"{ADMIN_QUOTA}/users/{user_id}",
            params={"activity_date": missing_date},
        )
        assert missing.status_code == 200
        assert missing.json()["current"] is None
        rejected_missing = client.post(
            f"{ADMIN_QUOTA}/users/{user_id}/adjustments",
            json=adjustment_payload(missing_date, 0),
        )
        assert rejected_missing.status_code == 404
        assert rejected_missing.json()["error"]["code"] == "ai_quota_daily_not_found"


def test_admin_debit_cannot_consume_an_active_reservation(
    client,
    live_server,
    database_url,
):
    setup_admin(client, database_url)
    publish_conversation_config(database_url)
    with browser(live_server) as user_client:
        _, user_id = create_account(user_client, database_url)
        conversation = create_conversation(user_client).json()["conversation"]
        submitted = send_message(user_client, conversation, "保留这次请求的额度")
        assert submitted.status_code == 202, submitted.text

        summary = client.get(f"{ADMIN_QUOTA}/users/{user_id}")
        assert summary.status_code == 200, summary.text
        current = summary.json()["current"]
        assert Decimal(current["reserved"]) > 0
        available = Decimal(current["available"])

        rejected = client.post(
            f"{ADMIN_QUOTA}/users/{user_id}/adjustments",
            json=adjustment_payload(
                current["activity_date"],
                current["version"],
                mode="debit",
                amount=str(available + Decimal("0.000000001")),
                reason="不得占用处理中额度",
            ),
        )
        assert rejected.status_code == 409
        assert rejected.json()["error"]["code"] == "ai_quota_adjustment_insufficient"
        assert rejected.json()["error"]["details"]["reserved"] == current["reserved"]

        accepted = client.post(
            f"{ADMIN_QUOTA}/users/{user_id}/adjustments",
            json=adjustment_payload(
                current["activity_date"],
                current["version"],
                mode="debit",
                amount=str(available),
                reason="扣减剩余可用额度",
            ),
        )
        assert accepted.status_code == 201, accepted.text
        after_debit = client.get(
            f"{ADMIN_QUOTA}/users/{user_id}"
        ).json()["current"]
        assert after_debit["available"] == "0"
        assert after_debit["reserved"] == current["reserved"]


def test_admin_quota_adjustment_concurrency_serializes_replay_and_debits(
    client,
    live_server,
    database_url,
):
    setup_admin(client, database_url)
    ensure_quota_policy(database_url)
    with browser(live_server) as user_client:
        _, user_id = create_account(user_client, database_url)
    initial = client.get(f"{ADMIN_QUOTA}/users/{user_id}").json()["current"]
    cookies = dict(client.cookies)
    headers = {"x-csrf-token": client.headers["x-csrf-token"]}
    operation_id = str(uuid4())
    credit = adjustment_payload(
        initial["activity_date"],
        0,
        operation_id=operation_id,
    )

    def post_adjustment(payload: dict) -> httpx.Response:
        return httpx.post(
            f"{live_server.origin}{ADMIN_QUOTA}/users/{user_id}/adjustments",
            json=payload,
            cookies=cookies,
            headers=headers,
            timeout=10,
            trust_env=False,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        replays = list(pool.map(post_adjustment, [credit, credit]))
    assert [response.status_code for response in replays] == [201, 201]
    assert replays[0].json() == replays[1].json()

    current = client.get(f"{ADMIN_QUOTA}/users/{user_id}").json()["current"]
    assert current["version"] == 2
    first_debit = adjustment_payload(
        current["activity_date"],
        current["version"],
        mode="debit",
        amount="0.75",
        reason="并发扣减一",
    )
    second_debit = adjustment_payload(
        current["activity_date"],
        current["version"],
        mode="debit",
        amount="0.75",
        reason="并发扣减二",
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        debits = list(pool.map(post_adjustment, [first_debit, second_debit]))
    assert sorted(response.status_code for response in debits) == [201, 409]
    conflict = next(response for response in debits if response.status_code == 409)
    assert conflict.json()["error"]["code"] == "ai_quota_daily_version_conflict"

    final = client.get(f"{ADMIN_QUOTA}/users/{user_id}").json()["current"]
    assert final["version"] == 3
    assert final["available"] == "0.5"
    with psycopg.connect(psycopg_url(database_url)) as connection:
        counts = connection.execute(
            """
            SELECT
                count(*),
                count(DISTINCT operation_id)
            FROM ai_quota_admin_adjustments
            WHERE target_owner_id=%s
            """,
            (user_id,),
        ).fetchone()
    assert counts == (2, 2)


def test_admin_quota_adjustment_rolls_back_when_audit_fails(
    client,
    live_server,
    database_url,
):
    setup_admin(client, database_url)
    ensure_quota_policy(database_url)
    with browser(live_server) as user_client:
        _, user_id = create_account(user_client, database_url)
    initial = client.get(f"{ADMIN_QUOTA}/users/{user_id}").json()["current"]

    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            """
            CREATE FUNCTION reject_test_quota_audit()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                IF NEW.action = 'ai_quota_admin_credit' THEN
                    RAISE EXCEPTION 'test quota audit failure';
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
        connection.execute(
            """
            CREATE TRIGGER trg_reject_test_quota_audit
            BEFORE INSERT ON security_events
            FOR EACH ROW
            EXECUTE FUNCTION reject_test_quota_audit()
            """
        )
    try:
        failed = client.post(
            f"{ADMIN_QUOTA}/users/{user_id}/adjustments",
            json=adjustment_payload(initial["activity_date"], 0),
        )
        assert failed.status_code == 503
        assert failed.json()["error"]["code"] == "database_unavailable"
        assert failed.json()["error"]["saved"] == "unknown"
    finally:
        with psycopg.connect(
            psycopg_url(database_url),
            autocommit=True,
        ) as connection:
            connection.execute("DROP TRIGGER trg_reject_test_quota_audit ON security_events")
            connection.execute("DROP FUNCTION reject_test_quota_audit()")

    with psycopg.connect(psycopg_url(database_url)) as connection:
        facts = connection.execute(
            """
            SELECT
                (SELECT count(*) FROM ai_daily_allowances
                 WHERE owner_id=%s),
                (SELECT count(*) FROM ai_quota_admin_adjustments
                 WHERE target_owner_id=%s),
                (SELECT count(*) FROM ai_quota_ledger_entries
                 WHERE owner_id=%s)
            """,
            (user_id, user_id, user_id),
        ).fetchone()
    assert facts == (0, 0, 0)
    assert client.get(f"{ADMIN_QUOTA}/users/{user_id}").json()["current"]["version"] == 0
