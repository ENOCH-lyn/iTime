from uuid import uuid4

import httpx
import psycopg
import pytest

from tests.conftest import psycopg_url
from tests.test_ai_config import setup_admin
from tests.test_calendar_series import create_account

pytestmark = pytest.mark.integration


def seed_provider_costs(database_url: str, owner_id: str) -> str:
    config_id = uuid4()
    media_id = uuid4()
    job_id = uuid4()
    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,
    ) as connection:
        version = connection.execute(
            """
            UPDATE ai_config_capabilities
            SET next_version=next_version + 1, updated_at=now()
            WHERE capability='calendar_screenshot'
            RETURNING next_version - 1
            """
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO ai_config_versions (
                id, capability, version, endpoint_url, model,
                api_key_ciphertext, timeout_seconds, max_output_tokens,
                status, price_currency,
                input_price_nanos_per_million_tokens,
                cached_input_price_nanos_per_million_tokens,
                output_price_nanos_per_million_tokens
            ) VALUES (
                %s, 'calendar_screenshot', %s, 'https://ai.example.com/v1',
                'cost-model', %s, 60, 1024, 'draft', 'CNY',
                1000000000, 500000000, 2000000000
            )
            """,
            (config_id, version, b"encrypted-key"),
        )
        connection.execute(
            """
            INSERT INTO temporary_media (
                id, owner_id, purpose, original_filename, content_type,
                content_sha256, byte_size, width, height,
                encrypted_content, expires_at
            ) VALUES (
                %s, %s, 'calendar_screenshot', 'cost.png', 'image/png',
                %s, 16, 2, 2, %s, now() + interval '1 day'
            )
            """,
            (media_id, owner_id, "a" * 64, b"encrypted-image"),
        )
        connection.execute(
            """
            INSERT INTO ai_jobs (
                id, owner_id, capability, config_version_id, input_media_id,
                status, input_summary, output, attempt_count, max_attempts,
                available_at, deadline_at, finished_at
            ) VALUES (
                %s, %s, 'calendar_screenshot', %s, %s, 'succeeded',
                '{"filename":"cost.png"}', '{"candidate_count":0}',
                2, 3, now(), now() + interval '3 minutes', now()
            )
            """,
            (job_id, owner_id, config_id, media_id),
        )
        connection.execute(
            """
            INSERT INTO ai_job_attempts (
                id, job_id, owner_id, attempt_number, lease_id, status,
                provider_request_id, http_status, error_code, error_stage,
                provider_started_at, started_at, finished_at
            ) VALUES
                (
                    %s, %s, %s, 1, %s, 'retryable_failed',
                    'provider-retry', 429, 'ai_rate_limited', 'upstream',
                    now(), now(), now()
                ),
                (
                    %s, %s, %s, 2, %s, 'succeeded',
                    'provider-success', 200, NULL, NULL,
                    now(), now(), now()
                )
            """,
            (
                uuid4(),
                job_id,
                owner_id,
                uuid4(),
                uuid4(),
                job_id,
                owner_id,
                uuid4(),
            ),
        )
        connection.execute(
            """
            INSERT INTO ai_provider_costs (
                id, job_id, owner_id, attempt_number, attempt_status,
                provider_started_at, usage_source, estimation_reason,
                estimator_version, input_tokens, cached_input_tokens,
                output_tokens, total_tokens, usage_details, price_currency,
                input_price_nanos_per_million_tokens,
                cached_input_price_nanos_per_million_tokens,
                output_price_nanos_per_million_tokens,
                input_cost_nanos, cached_input_cost_nanos,
                output_cost_nanos, total_cost_nanos, created_at
            ) VALUES
                (
                    %s, %s, %s, 1, 'retryable_failed', now(), 'estimated',
                    'missing_usage', 'utf8-vision-v1', 300, 0, 0, 300,
                    '{"estimator":"utf8-vision-v1"}', 'CNY',
                    1000000000, 500000000, 2000000000,
                    300000, 0, 0, 300000, now() - interval '1 second'
                ),
                (
                    %s, %s, %s, 2, 'succeeded', now(), 'provider',
                    NULL, NULL, 400, 100, 120, 520,
                    '{"prompt_tokens":400}', 'CNY',
                    1000000000, 500000000, 2000000000,
                    300000, 50000, 240000, 590000, now()
                )
            """,
            (
                uuid4(),
                job_id,
                owner_id,
                uuid4(),
                job_id,
                owner_id,
            ),
        )
    return str(job_id)


def test_admin_lists_filters_and_paginates_provider_costs(
    client,
    live_server,
    database_url,
):
    email, owner_id = create_account(client, database_url)
    setup_admin(client, database_url)
    job_id = seed_provider_costs(database_url, owner_id)

    with httpx.Client(base_url=live_server.origin, trust_env=False) as outsider:
        assert outsider.get("/api/v1/admin/provider-costs").status_code == 401

    first = client.get(
        "/api/v1/admin/provider-costs",
        params={"limit": 1},
    )
    assert first.status_code == 200, first.text
    page = first.json()
    assert len(page["items"]) == 1
    assert page["next_cursor"] is not None
    item = page["items"][0]
    assert item["job_id"] == job_id
    assert item["owner"]["email"] == email
    assert item["attempt_status"] == "succeeded"
    assert item["usage_source"] == "provider"
    assert item["usage"] == {
        "input_tokens": 400,
        "cached_input_tokens": 100,
        "output_tokens": 120,
        "total_tokens": 520,
        "details": {"prompt_tokens": 400},
    }
    assert item["pricing"]["currency"] == "CNY"
    assert item["amounts"]["total"] == "0.00059"
    assert item["config"]["model"] == "cost-model"
    assert "request_body" not in item and "response_body" not in item

    second = client.get(
        "/api/v1/admin/provider-costs",
        params={
            "limit": 1,
            **page["next_cursor"],
        },
    )
    assert second.status_code == 200
    assert second.json()["items"][0]["attempt_status"] == "retryable_failed"

    estimated = client.get(
        "/api/v1/admin/provider-costs",
        params={
            "usage_source": "estimated",
            "attempt_status": "retryable_failed",
            "capability": "calendar_screenshot",
            "priced": True,
            "search": email,
        },
    )
    assert estimated.status_code == 200
    assert len(estimated.json()["items"]) == 1
    assert estimated.json()["items"][0]["amounts"]["total"] == "0.0003"

    invalid_cursor = client.get(
        "/api/v1/admin/provider-costs",
        params={"before_time": "2026-10-03T00:00:00Z"},
    )
    assert invalid_cursor.status_code == 400
    assert invalid_cursor.json()["error"]["code"] == "invalid_cursor"
