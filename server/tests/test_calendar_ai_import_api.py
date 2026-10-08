from uuid import uuid4

import psycopg
import pytest

from itime.config import Settings
from tests.conftest import psycopg_url
from tests.test_calendar_screenshot_imports import (
    png_bytes,
    publish_config,
)
from tests.test_calendar_series import create_account

pytestmark = pytest.mark.integration
pytest_plugins = ("tests.test_calendar_screenshot_imports",)


def _post_json(client, source_type: str, content: str, *, operation_id=None):
    return client.post(
        "/api/v1/calendar/imports/ai",
        headers={"X-Operation-ID": str(operation_id or uuid4())},
        json={"source_type": source_type, "content": content},
    )


def test_text_and_url_create_recoverable_batches_with_operation_replay(
    screenshot_client,
    screenshot_database_url,
    vision_upstream,
):
    _, owner_id = create_account(screenshot_client, screenshot_database_url)
    _, endpoint = vision_upstream
    settings = Settings(
        database_url=screenshot_database_url,
        environment="test",
    )
    publish_config(screenshot_database_url, settings, endpoint)

    text_operation = uuid4()
    text = "每周一、三 08:00 上高等数学，持续到 2027-01-10"
    created = _post_json(
        screenshot_client,
        "text",
        text,
        operation_id=text_operation,
    )
    assert created.status_code == 202, created.text
    payload = created.json()
    assert payload["batch"]["format"] == "text"
    assert payload["batch"]["state"] == "queued"
    assert payload["batch"]["candidate_count"] == 0
    assert payload["job"]["status"] == "queued"
    assert (
        _post_json(
            screenshot_client,
            "text",
            text,
            operation_id=text_operation,
        ).json()
        == payload
    )

    url = _post_json(
        screenshot_client,
        "public_url",
        "https://schedule.example/private/path?token=secret",
    )
    assert url.status_code == 202, url.text
    assert url.json()["batch"]["format"] == "public_url"
    assert (
        url.json()["batch"]["source_display"]
        == "https://schedule.example/..."
    )
    with psycopg.connect(psycopg_url(screenshot_database_url)) as connection:
        rows = connection.execute(
            """
            SELECT source_type, input_body_ciphertext, input_summary,
                   origin_job_id
            FROM calendar_import_batches
            WHERE owner_id=%s
            ORDER BY created_at, id
            """,
            (owner_id,),
        ).fetchall()
        assert [row[0] for row in rows] == ["text", "public_url"]
        assert all(row[1] is not None for row in rows)
        assert text.encode() not in bytes(rows[0][1])
        assert rows[0][2]["character_count"] == len(text)
        assert all(row[3] is not None for row in rows)


def test_image_multipart_uses_same_batch_contract(
    screenshot_client,
    screenshot_database_url,
    vision_upstream,
):
    create_account(screenshot_client, screenshot_database_url)
    _, endpoint = vision_upstream
    settings = Settings(
        database_url=screenshot_database_url,
        environment="test",
    )
    publish_config(screenshot_database_url, settings, endpoint)

    created = screenshot_client.post(
        "/api/v1/calendar/imports/ai",
        headers={"X-Operation-ID": str(uuid4())},
        data={"source_type": "image"},
        files={"file": ("schedule.png", png_bytes(), "image/png")},
    )

    assert created.status_code == 202, created.text
    payload = created.json()
    assert payload["batch"]["format"] == "image"
    assert payload["batch"]["source"]["kind"] == "image"
    assert payload["batch"]["state"] == "queued"
    assert payload["job"]["input_summary"]["source_type"] == "image"


def test_unified_import_validates_access_and_owner_isolation(
    screenshot_client,
    screenshot_database_url,
    vision_upstream,
):
    create_account(
        screenshot_client,
        screenshot_database_url,
    )
    _, endpoint = vision_upstream
    settings = Settings(
        database_url=screenshot_database_url,
        environment="test",
    )
    publish_config(screenshot_database_url, settings, endpoint)
    first = _post_json(screenshot_client, "text", "明天 08:00 高数")
    assert first.status_code == 202, first.text
    batch_id = first.json()["batch"]["id"]

    _, second_owner = create_account(
        screenshot_client,
        screenshot_database_url,
    )
    assert (
        screenshot_client.get(
            f"/api/v1/calendar/imports/{batch_id}"
        ).status_code
        == 404
    )

    with psycopg.connect(
        psycopg_url(screenshot_database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            "UPDATE users SET ai_access='frozen' WHERE id=%s",
            (second_owner,),
        )
    frozen = _post_json(screenshot_client, "text", "明天 09:00 英语")
    assert frozen.status_code == 403
    assert frozen.json()["error"]["code"] == "ai_access_frozen"

    create_account(screenshot_client, screenshot_database_url)
    invalid = _post_json(screenshot_client, "text", " ")
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "validation_error"
