import asyncio
import json
import threading
from dataclasses import dataclass, field
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import UUID, uuid4

import psycopg
import pytest
from cryptography.fernet import Fernet

from itime.ai.worker import process_one
from itime.auth.security import now
from itime.calendar.service import activity_day_at
from itime.config import Settings
from itime.database import Database
from tests.conftest import psycopg_url
from tests.quota_helpers import ensure_quota_policy
from tests.test_task_schedules import initialize_settings
from tests.test_tasks import create_account

pytestmark = pytest.mark.integration


def report_arguments() -> dict:
    return {
        "summary": "今天完成了核心任务。",
        "facts": [],
        "user_statements": ["上午更适合持续思考。"],
        "insights": [
            {
                "text": "完整时间段可能更适合当前任务。",
                "basis": "ai_inference",
            }
        ],
        "next_actions": ["明早继续整理资料。"],
        "uncertainties": ["没有计划记录。"],
        "memory_candidates": [
            {
                "kind": "preference",
                "title": "深度工作时段",
                "content": "上午更适合持续思考。",
                "rationale": "来自用户补充事实。",
                "source_keys": ["user:supplemental_facts"],
            }
        ],
    }


@dataclass
class ReviewUpstreamState:
    mode: str = "success"
    requests: list[dict] = field(default_factory=list)


def review_upstream_handler(state: ReviewUpstreamState):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = json.loads(
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
            )
            state.requests.append(body)
            if state.mode == "rate_limit":
                status = 429
                response = {"error": "busy"}
            else:
                tool_name = body["tool_choice"]["function"]["name"]
                arguments = (
                    {"question": "今天最顺利的是哪一段？"}
                    if tool_name == "submit_itime_review_question"
                    else report_arguments()
                )
                status = 200
                response = {
                    "choices": [
                        {
                            "message": {
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "review-worker-call",
                                        "type": "function",
                                        "function": {
                                            "name": tool_name,
                                            "arguments": json.dumps(
                                                arguments,
                                                ensure_ascii=False,
                                            ),
                                        },
                                    }
                                ],
                            }
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 40,
                        "completion_tokens": 20,
                        "total_tokens": 60,
                    },
                }
            encoded = json.dumps(response, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("x-request-id", "review-worker-request")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args) -> None:
            pass

    return Handler


@pytest.fixture
def review_upstream():
    state = ReviewUpstreamState()
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        review_upstream_handler(state),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state, f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def install_review_config(database_url: str, endpoint: str) -> None:
    settings = Settings(database_url=database_url, environment="test")
    cipher = Fernet(
        (settings.ai_encryption_key or settings.encryption_key).get_secret_value()
    )
    config_id = uuid4()
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        connection.execute(
            """
            UPDATE ai_config_capabilities
            SET current_version_id=NULL
            WHERE capability='review'
            """
        )
        connection.execute(
            """
            UPDATE ai_config_versions
            SET status='retired', retired_at=coalesce(retired_at, now())
            WHERE capability='review' AND status='published'
            """
        )
        version = connection.execute(
            """
            SELECT next_version FROM ai_config_capabilities
            WHERE capability='review'
            """
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO ai_config_versions (
                id, capability, version, endpoint_url, model,
                api_key_ciphertext, timeout_seconds, max_output_tokens,
                price_currency, input_price_nanos_per_million_tokens,
                cached_input_price_nanos_per_million_tokens,
                output_price_nanos_per_million_tokens, status,
                last_test_id, last_test_started_at, last_test_completed_at,
                published_at
            ) VALUES (
                %s, 'review', %s, %s, 'review-model', %s, 10, 1024,
                'CNY', 1000000000, 500000000, 2000000000,
                'published', %s, now(), now(), now()
            )
            """,
            (
                config_id,
                version,
                endpoint,
                cipher.encrypt(b"review-worker-secret"),
                uuid4(),
            ),
        )
        connection.execute(
            """
            UPDATE ai_config_capabilities
            SET current_version_id=%s, next_version=%s, updated_at=now()
            WHERE capability='review'
            """,
            (config_id, version + 1),
        )


def create_review(client, database_url, offset_days: int = -1) -> dict:
    create_account(client, database_url)
    initialize_settings(client)
    activity_date = (
        activity_day_at(now(), "Asia/Shanghai") + timedelta(days=offset_days)
    )
    response = client.post(
        "/api/v1/reviews",
        json={
            "operation_id": str(uuid4()),
            "activity_date": activity_date.isoformat(),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["review"]


def test_review_question_and_report_complete_with_atomic_terminal_facts(
    client,
    database_url,
    review_upstream,
):
    state, endpoint = review_upstream
    review = create_review(client, database_url)
    ensure_quota_policy(database_url)
    install_review_config(database_url, endpoint)
    facts = client.put(
        f"/api/v1/reviews/{review['id']}/facts",
        json={
            "operation_id": str(uuid4()),
            "expected_version": review["version"],
            "supplemental_facts": "上午更适合持续思考。",
        },
    ).json()["review"]
    question = client.post(
        f"/api/v1/reviews/{review['id']}/questions",
        json={
            "operation_id": str(uuid4()),
            "expected_version": facts["version"],
            "answer": None,
        },
    )
    assert question.status_code == 202, question.text
    question_job_id = UUID(question.json()["job"]["id"])

    settings = Settings(database_url=database_url, environment="test")
    database = Database(settings)
    assert asyncio.run(process_one(database, settings))
    detail = client.get(f"/api/v1/reviews/{review['id']}").json()
    assert detail["review"]["question_count"] == 1
    assert detail["messages"][0]["role"] == "assistant"
    assert detail["messages"][0]["content"] == "今天最顺利的是哪一段？"

    report = client.post(
        f"/api/v1/reviews/{review['id']}/reports",
        json={
            "operation_id": str(uuid4()),
            "expected_version": detail["review"]["version"],
            "instruction": "",
        },
    )
    assert report.status_code == 202, report.text
    report_job_id = UUID(report.json()["job"]["id"])
    assert asyncio.run(process_one(database, settings))
    asyncio.run(database.close())

    detail = client.get(f"/api/v1/reviews/{review['id']}").json()
    assert detail["reports"][0]["version"] == 1
    assert detail["report_freshness"] == "current"
    report_detail = client.get(
        f"/api/v1/reviews/{review['id']}/reports/1"
    )
    assert report_detail.status_code == 200, report_detail.text
    assert report_detail.json()["report"]["summary"] == "今天完成了核心任务。"
    assert report_detail.json()["memory_proposals"][0]["status"] == "pending"

    with psycopg.connect(psycopg_url(database_url)) as connection:
        for job_id in (question_job_id, report_job_id):
            assert connection.execute(
                "SELECT status FROM ai_jobs WHERE id=%s",
                (job_id,),
            ).fetchone() == ("succeeded",)
            assert connection.execute(
                """
                SELECT status FROM ai_quota_reservations WHERE job_id=%s
                """,
                (job_id,),
            ).fetchone() == ("settled",)
            assert connection.execute(
                """
                SELECT count(*) FROM ai_quota_ledger_entries
                WHERE job_id=%s AND kind='usage_charge'
                """,
                (job_id,),
            ).fetchone() == (1,)
            assert connection.execute(
                """
                SELECT status FROM ai_body_logs WHERE job_id=%s
                """,
                (job_id,),
            ).fetchone() == ("succeeded",)
        assert connection.execute(
            """
            SELECT capability, status FROM ai_quality_samples
            WHERE source_job_id=%s
            """,
            (report_job_id,),
        ).fetchone() == ("review", "pending")
    assert len(state.requests) == 2


def test_review_question_limit_active_job_and_cancel_release(
    client,
    database_url,
    review_upstream,
):
    _, endpoint = review_upstream
    review = create_review(client, database_url, offset_days=-2)
    ensure_quota_policy(database_url)
    install_review_config(database_url, endpoint)
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        connection.execute(
            "UPDATE review_sessions SET question_count=3 WHERE id=%s",
            (review["id"],),
        )
    limited = client.post(
        f"/api/v1/reviews/{review['id']}/questions",
        json={
            "operation_id": str(uuid4()),
            "expected_version": review["version"],
            "answer": None,
        },
    )
    assert limited.status_code == 409
    assert limited.json()["error"]["code"] == "review_question_limit_reached"

    report = client.post(
        f"/api/v1/reviews/{review['id']}/reports",
        json={
            "operation_id": str(uuid4()),
            "expected_version": review["version"],
            "instruction": "",
        },
    )
    assert report.status_code == 202, report.text
    active = client.post(
        f"/api/v1/reviews/{review['id']}/reports",
        json={
            "operation_id": str(uuid4()),
            "expected_version": review["version"],
            "instruction": "",
        },
    )
    assert active.status_code == 409
    assert active.json()["error"]["code"] == "review_generation_active"

    cancelled = client.post(
        f"/api/v1/ai/jobs/{report.json()['job']['id']}/cancel",
        json={"operation_id": str(uuid4())},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["job"]["status"] == "cancelled"
    with psycopg.connect(psycopg_url(database_url)) as connection:
        assert connection.execute(
            """
            SELECT status, release_reason FROM ai_quota_reservations
            WHERE job_id=%s
            """,
            (report.json()["job"]["id"],),
        ).fetchone() == ("released", "cancelled")
