import threading
from http.server import ThreadingHTTPServer
from uuid import uuid4

import psycopg
import pytest

from itime.config import Settings
from tests.conftest import psycopg_url
from tests.test_ai_conversation_worker import (
    job_snapshot,
    publish_conversation_config,
    run_process_once,
)
from tests.test_calendar_imports import confirm_payload
from tests.test_calendar_screenshot_imports import (
    VisionState,
    png_bytes,
    publish_config,
    vision_handler,
)
from tests.test_calendar_series import create_account

pytestmark = pytest.mark.integration
pytest_plugins = ("tests.test_ai_conversation_worker",)


@pytest.fixture(scope="session")
def timetable_vision_upstream():
    state = VisionState()
    server = ThreadingHTTPServer(("127.0.0.1", 0), vision_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state, f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        state.release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _publish_import_config(database_url: str, endpoint: str) -> None:
    publish_config(
        database_url,
        Settings(database_url=database_url, environment="test"),
        endpoint,
    )


def test_agent_text_timetable_creates_review_batch_before_calendar_write(
    conversation_worker_client,
    conversation_worker_database_url,
    chat_upstream,
    timetable_vision_upstream,
):
    chat_state, chat_endpoint = chat_upstream
    vision_state, vision_endpoint = timetable_vision_upstream
    chat_state.reset("agent_timetable")
    vision_state.reset()
    publish_conversation_config(
        conversation_worker_database_url,
        chat_endpoint,
    )
    _publish_import_config(
        conversation_worker_database_url,
        vision_endpoint,
    )
    _, owner_id = create_account(
        conversation_worker_client,
        conversation_worker_database_url,
    )
    conversation = conversation_worker_client.post(
        "/api/v1/ai/conversations",
        json={"operation_id": str(uuid4())},
    ).json()["conversation"]
    submitted = conversation_worker_client.post(
        f"/api/v1/ai/conversations/{conversation['id']}/messages",
        json={
            "operation_id": str(uuid4()),
            "expected_conversation_version": conversation["version"],
            "content": (
                "把每周一、三 08:00 的高等数学导入课表，"
                "持续到 2027-01-10"
            ),
            "response_mode": "auto",
        },
    )
    assert submitted.status_code == 202, submitted.text

    assert run_process_once(conversation_worker_database_url)

    snapshot = job_snapshot(
        conversation_worker_database_url,
        submitted.json()["job"]["id"],
    )
    batch_id = snapshot["job"]["output"]["import_batch_id"]
    import_job_id = snapshot["job"]["output"]["import_job_id"]
    with psycopg.connect(
        psycopg_url(conversation_worker_database_url)
    ) as connection:
        assert connection.execute(
            "SELECT count(*) FROM calendar_series WHERE owner_id=%s",
            (owner_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            """
            SELECT conversation_id, message_id, state, candidate_count
            FROM calendar_import_batches
            WHERE id=%s
            """,
            (batch_id,),
        ).fetchone()[2:] == ("queued", 0)

    messages = conversation_worker_client.get(
        f"/api/v1/ai/conversations/{conversation['id']}/messages"
    ).json()["items"]
    assistant = messages[-1]
    assert assistant["calendar_imports"][0]["id"] == batch_id
    assert assistant["calendar_imports"][0]["state"] == "queued"

    assert run_process_once(conversation_worker_database_url)

    import_job = conversation_worker_client.get(
        f"/api/v1/ai/jobs/{import_job_id}"
    ).json()
    assert import_job["status"] == "succeeded"
    detail = conversation_worker_client.get(
        f"/api/v1/calendar/imports/{batch_id}"
    ).json()
    assert detail["batch"]["state"] == "needs_review"
    assert detail["batch"]["counts"]["valid"] == 1
    assert detail["batch"]["counts"]["invalid"] == 1
    assert vision_state.requests

    valid = next(
        item for item in detail["candidates"] if item["state"] == "valid"
    )
    confirmed = conversation_worker_client.post(
        f"/api/v1/calendar/imports/{batch_id}/confirm",
        json=confirm_payload(detail["batch"], [valid]),
    )
    assert confirmed.status_code == 200, confirmed.text
    with psycopg.connect(
        psycopg_url(conversation_worker_database_url)
    ) as connection:
        assert connection.execute(
            "SELECT count(*) FROM calendar_series WHERE owner_id=%s",
            (owner_id,),
        ).fetchone()[0] == 1


def test_uploaded_image_batch_can_be_attached_to_user_message(
    conversation_worker_client,
    conversation_worker_database_url,
    chat_upstream,
    timetable_vision_upstream,
):
    chat_state, chat_endpoint = chat_upstream
    _, vision_endpoint = timetable_vision_upstream
    chat_state.reset("split_sse")
    publish_conversation_config(
        conversation_worker_database_url,
        chat_endpoint,
    )
    _publish_import_config(
        conversation_worker_database_url,
        vision_endpoint,
    )
    create_account(
        conversation_worker_client,
        conversation_worker_database_url,
    )
    uploaded = conversation_worker_client.post(
        "/api/v1/calendar/imports/ai",
        headers={"X-Operation-ID": str(uuid4())},
        data={"source_type": "image"},
        files={"file": ("schedule.png", png_bytes(), "image/png")},
    )
    assert uploaded.status_code == 202, uploaded.text
    batch_id = uploaded.json()["batch"]["id"]

    conversation = conversation_worker_client.post(
        "/api/v1/ai/conversations",
        json={"operation_id": str(uuid4())},
    ).json()["conversation"]
    submitted = conversation_worker_client.post(
        f"/api/v1/ai/conversations/{conversation['id']}/messages",
        json={
            "operation_id": str(uuid4()),
            "expected_conversation_version": conversation["version"],
            "content": "导入这张课表",
            "response_mode": "auto",
            "import_batch_id": batch_id,
        },
    )
    assert submitted.status_code == 202, submitted.text
    request_message = submitted.json()["messages"][0]
    assert request_message["calendar_imports"][0]["id"] == batch_id
    with psycopg.connect(
        psycopg_url(conversation_worker_database_url)
    ) as connection:
        association = connection.execute(
            """
            SELECT conversation_id, message_id
            FROM calendar_import_batches
            WHERE id=%s
            """,
            (batch_id,),
        ).fetchone()
    assert str(association[0]) == conversation["id"]
    assert str(association[1]) == request_message["id"]
