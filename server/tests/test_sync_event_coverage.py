from uuid import UUID, uuid4

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
def reset_sync_event_coverage_data(database_url):
    def cleanup() -> None:
        with psycopg.connect(
            psycopg_url(database_url),
            autocommit=True,
        ) as connection:
            connection.execute("DELETE FROM users")
            connection.execute(
                """
                UPDATE ai_config_capabilities
                SET current_version_id=NULL, next_version=1, updated_at=now()
                WHERE capability='conversation'
                """
            )
            connection.execute(
                "DELETE FROM ai_config_versions WHERE capability='conversation'"
            )
            connection.execute(
                """
                UPDATE ai_quota_policy_state
                SET current_version_id=NULL, next_version=1, updated_at=now()
                WHERE id=1
                """
            )
            connection.execute("DELETE FROM ai_quota_policy_versions")

    cleanup()
    yield
    cleanup()


def test_conversation_events_cover_create_update_message_and_cancel(
    client,
    database_url,
):
    _, owner_id = create_account(client, database_url)
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        connection.execute(
            "DELETE FROM change_events WHERE owner_id=%s",
            (owner_id,),
        )

    create_operation = uuid4()
    created_response = create_conversation(
        client,
        title="同步会话",
        operation_id=create_operation,
    )
    conversation = created_response.json()["conversation"]
    replayed = create_conversation(
        client,
        title="同步会话",
        operation_id=create_operation,
    )
    assert replayed.json() == created_response.json()

    noop = client.patch(
        f"/api/v1/ai/conversations/{conversation['id']}",
        json={
            "operation_id": str(uuid4()),
            "expected_version": conversation["version"],
            "title": conversation["title"],
        },
    )
    assert noop.status_code == 200
    assert noop.json()["conversation"]["version"] == 1

    renamed = client.patch(
        f"/api/v1/ai/conversations/{conversation['id']}",
        json={
            "operation_id": str(uuid4()),
            "expected_version": 1,
            "title": "跨设备同步会话",
        },
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["conversation"]["version"] == 2

    publish_conversation_config(database_url)
    message_operation = uuid4()
    submitted = send_message(
        client,
        renamed.json()["conversation"],
        "同步这一条消息",
        operation_id=message_operation,
    )
    assert submitted.status_code == 202, submitted.text
    assert send_message(
        client,
        renamed.json()["conversation"],
        "同步这一条消息",
        operation_id=message_operation,
    ).json() == submitted.json()
    assert submitted.json()["conversation"]["version"] == 3

    cancelled = client.post(
        f"/api/v1/ai/jobs/{submitted.json()['job']['id']}/cancel",
        json={"operation_id": str(uuid4())},
    )
    assert cancelled.status_code == 200, cancelled.text

    with psycopg.connect(psycopg_url(database_url)) as connection:
        events = connection.execute(
            """
            SELECT entity_type, entity_id, action, entity_version
            FROM change_events
            WHERE owner_id=%s
            ORDER BY sequence
            """,
            (owner_id,),
        ).fetchall()
    assert events == [
        ("conversation", UUID(conversation["id"]), "created", 1),
        ("conversation", UUID(conversation["id"]), "updated", 2),
        ("conversation", UUID(conversation["id"]), "updated", 3),
        ("conversation", UUID(conversation["id"]), "updated", 4),
    ]
