import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import psycopg
import pytest

from tests.conftest import psycopg_url
from tests.test_admin_management import browser, setup_admin
from tests.test_ai_jobs import seed_config, seed_job, seed_media
from tests.test_calendar_series import create_account

pytestmark = pytest.mark.integration


def seed_governance_records(
    database_url: str,
    owner_id: str,
) -> tuple[str, str, str]:
    body_log_id = uuid4()
    sample_id = uuid4()
    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,
    ) as connection:
        config_id = seed_config(connection)
        job_id = seed_job(
            connection,
            owner_id,
            config_id,
            seed_media(connection, owner_id),
            status="running",
            lease_delta=timedelta(minutes=2),
        )
        request_body = {
            "model": "governance-test-model",
            "messages": [
                {"role": "user", "content": "需要审计的请求正文"}
            ],
        }
        response_body = "需要审计的响应正文"
        connection.execute(
            """
            UPDATE ai_job_attempts
            SET status='succeeded', provider_request_id='provider-request-1',
                http_status=200, finished_at=now()
            WHERE job_id=%s AND attempt_number=1
            """,
            (job_id,),
        )
        connection.execute(
            """
            INSERT INTO ai_body_logs (
                id, job_id, owner_id, attempt_number, capability,
                request_body, response_body, status, provider_request_id,
                http_status, started_at, request_prepared_at, finished_at,
                expires_at
            ) VALUES (
                %s, %s, %s, 1, 'calendar_screenshot',
                %s::jsonb, %s, 'succeeded', 'provider-request-1',
                200, now() - interval '1 minute', now() - interval '50 seconds',
                now() - interval '40 seconds', now() + interval '7 days'
            )
            """,
            (
                body_log_id,
                job_id,
                owner_id,
                json.dumps(request_body),
                response_body,
            ),
        )
        connection.execute(
            """
            UPDATE ai_jobs
            SET status='succeeded', output='{"resource_type":"test"}'::jsonb,
                lease_id=NULL, lease_until=NULL, finished_at=now(),
                updated_at=now()
            WHERE id=%s
            """,
            (job_id,),
        )
        connection.execute(
            """
            INSERT INTO ai_quality_samples (
                id, source_job_id, source_body_log_id, owner_id, capability,
                request_body, response_body, sampled_at, expires_at
            ) VALUES (
                %s, %s, %s, %s, 'calendar_screenshot',
                %s::jsonb, %s, now(), now() + interval '30 days'
            )
            """,
            (
                sample_id,
                job_id,
                body_log_id,
                owner_id,
                json.dumps(request_body),
                response_body,
            ),
        )
    return str(body_log_id), str(sample_id), job_id


def test_admin_content_access_review_and_audit(
    client,
    database_url,
    live_server,
):
    admin = setup_admin(client, database_url)
    with browser(live_server) as user_client:
        owner_email, owner_id = create_account(user_client, database_url)
        assert user_client.get("/api/v1/admin/ai-body-logs").status_code == 401
    body_log_id, sample_id, job_id = seed_governance_records(
        database_url,
        owner_id,
    )

    body_listing = client.get(
        "/api/v1/admin/ai-body-logs",
        params={"search": owner_email, "capability": "calendar_screenshot"},
    )
    assert body_listing.status_code == 200
    body_item = body_listing.json()["items"][0]
    assert body_item["id"] == body_log_id
    assert body_item["job_id"] == job_id
    assert body_item["owner"]["email"] == owner_email
    assert "request_body" not in body_item
    assert "response_body" not in body_item
    assert "需要审计的请求正文" not in body_listing.text
    assert "需要审计的响应正文" not in body_listing.text

    csrf = client.headers.pop("x-csrf-token")
    no_csrf = client.post(
        f"/api/v1/admin/ai-body-logs/{body_log_id}/access",
        json={"purpose": "troubleshooting"},
    )
    assert no_csrf.status_code == 403
    client.headers["x-csrf-token"] = csrf

    accessed = client.post(
        f"/api/v1/admin/ai-body-logs/{body_log_id}/access",
        json={"purpose": "troubleshooting"},
    )
    assert accessed.status_code == 200
    assert (
        accessed.json()["request_body"]["messages"][0]["content"]
        == "需要审计的请求正文"
    )
    assert accessed.json()["response_body"] == "需要审计的响应正文"
    assert accessed.headers["cache-control"] == "no-store"

    missing_id = uuid4()
    missing = client.post(
        f"/api/v1/admin/ai-body-logs/{missing_id}/access",
        json={"purpose": "troubleshooting"},
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "ai_body_log_not_found"

    sample_listing = client.get(
        "/api/v1/admin/ai-quality-samples",
        params={"status": "pending", "search": owner_email},
    )
    assert sample_listing.status_code == 200
    sample_item = sample_listing.json()["items"][0]
    assert sample_item["id"] == sample_id
    assert "request_body" not in sample_item
    assert "response_body" not in sample_item
    assert "review_notes" not in sample_item
    assert "需要审计的响应正文" not in sample_listing.text

    sample_access = client.post(
        f"/api/v1/admin/ai-quality-samples/{sample_id}/access",
        json={"purpose": "manual_quality_review"},
    )
    assert sample_access.status_code == 200
    assert sample_access.json()["response_body"] == "需要审计的响应正文"

    reviewed = client.put(
        f"/api/v1/admin/ai-quality-samples/{sample_id}/review",
        json={
            "expected_version": 1,
            "outcome": "needs_improvement",
            "review_notes": "事实边界不够清晰",
        },
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["status"] == "reviewed"
    assert reviewed.json()["outcome"] == "needs_improvement"
    assert reviewed.json()["review_notes"] == "事实边界不够清晰"
    assert reviewed.json()["version"] == 2
    stale = client.put(
        f"/api/v1/admin/ai-quality-samples/{sample_id}/review",
        json={
            "expected_version": 1,
            "outcome": "acceptable",
            "review_notes": None,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["details"]["current_version"] == 2

    accessed_events = client.get(
        "/api/v1/admin/audit-events",
        params={"action": "ai_body_log_accessed"},
    ).json()["items"]
    assert len(accessed_events) == 1
    assert accessed_events[0]["actor"]["id"] == admin["admin"]["id"]
    assert accessed_events[0]["target_id"] == body_log_id
    assert accessed_events[0]["details"]["purpose"] == "troubleshooting"
    assert accessed_events[0]["details"]["result"] == "succeeded"
    assert accessed_events[0]["details"]["target_owner_id"] == owner_id
    assert accessed_events[0]["details"]["admin_user_id"] == admin["admin"]["id"]
    assert accessed_events[0]["details"]["admin_session_id"]

    denied_events = client.get(
        "/api/v1/admin/audit-events",
        params={"action": "ai_body_log_access_denied"},
    ).json()["items"]
    assert len(denied_events) == 1
    assert denied_events[0]["target_id"] == str(missing_id)
    assert denied_events[0]["details"]["result"] == "not_found"
    reviewed_events = client.get(
        "/api/v1/admin/audit-events",
        params={"action": "ai_quality_sample_reviewed"},
    ).json()["items"]
    assert reviewed_events[0]["details"]["outcome"] == "needs_improvement"


def test_expired_content_is_hidden_before_physical_cleanup(
    client,
    database_url,
    live_server,
):
    setup_admin(client, database_url)
    with browser(live_server) as user_client:
        owner_email, owner_id = create_account(user_client, database_url)
    body_log_id, sample_id, _ = seed_governance_records(
        database_url,
        owner_id,
    )
    with psycopg.connect(psycopg_url(database_url)) as connection:
        connection.execute(
            "SELECT id FROM ai_body_logs WHERE id=%s FOR UPDATE",
            (body_log_id,),
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            access = pool.submit(
                client.post,
                f"/api/v1/admin/ai-body-logs/{body_log_id}/access",
                json={"purpose": "troubleshooting"},
            )
            time.sleep(0.1)
            assert not access.done()
            connection.execute(
                """
                UPDATE ai_body_logs
                SET started_at=started_at - interval '8 days',
                    request_prepared_at=request_prepared_at - interval '8 days',
                    finished_at=finished_at - interval '8 days',
                    expires_at=now() - interval '1 second'
                WHERE id=%s
                """,
                (body_log_id,),
            )
            connection.commit()
            assert access.result(timeout=5).status_code == 404

    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            """
            UPDATE ai_quality_samples
            SET sampled_at=sampled_at - interval '31 days',
                expires_at=now() - interval '1 second'
            WHERE id=%s
            """,
            (sample_id,),
        )

    assert client.get(
        "/api/v1/admin/ai-body-logs",
        params={"search": owner_email},
    ).json()["items"] == []
    assert client.get(
        "/api/v1/admin/ai-quality-samples",
        params={"search": owner_email},
    ).json()["items"] == []
    assert client.post(
        f"/api/v1/admin/ai-quality-samples/{sample_id}/access",
        json={"purpose": "effect_evaluation"},
    ).status_code == 404
