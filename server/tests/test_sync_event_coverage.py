from uuid import UUID, uuid4

import psycopg
import pytest

from tests.conftest import psycopg_url
from tests.test_ai_conversation_api import (
    create_conversation,
    publish_conversation_config,
    send_message,
)
from tests.test_calendar_series import create_series
from tests.test_calendar_subscriptions import create_subscription

pytestmark = pytest.mark.integration
PASSWORD = "Valid-sync-event-password-123"


def create_sync_account(client, database_url):
    email = f"sync-event-{uuid4().hex}@example.com"
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": PASSWORD,
            "time_zone": "Asia/Shanghai",
        },
    )
    assert response.status_code == 202, response.text
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        owner_id = connection.execute(
            "UPDATE users SET email_verified_at=now() WHERE email=%s RETURNING id",
            (email,),
        ).fetchone()[0]
    login = client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": PASSWORD,
            "device_name": "同步事件测试",
        },
    )
    assert login.status_code == 200, login.text
    client.headers["x-csrf-token"] = login.json()["csrf_token"]
    return email, str(owner_id)


@pytest.fixture(autouse=True)
def reset_sync_event_coverage_data(database_url):
    def cleanup() -> None:
        with psycopg.connect(
            psycopg_url(database_url),
            autocommit=True,
        ) as connection:
            connection.execute(
                "DELETE FROM users WHERE email LIKE 'sync-event-%'"
            )
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

    cleanup()
    yield
    cleanup()


def test_conversation_events_cover_create_update_message_and_cancel(
    client,
    database_url,
):
    _, owner_id = create_sync_account(client, database_url)
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


def test_calendar_lifecycle_events_are_content_free(
    client,
    database_url,
):
    _, owner_id = create_sync_account(client, database_url)
    series = create_series(client, name="同步回收安排")["series"]
    subscription = create_subscription(client, name="同步回收订阅")
    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            "DELETE FROM change_events WHERE owner_id=%s",
            (owner_id,),
        )

    deleted_series = client.request(
        "DELETE",
        f"/api/v1/calendar/series/{series['id']}",
        json={
            "operation_id": str(uuid4()),
            "expected_version": series["version"],
        },
    )
    assert deleted_series.status_code == 200
    restored_series = client.post(
        f"/api/v1/calendar/series/{series['id']}/restore",
        json={
            "operation_id": str(uuid4()),
            "expected_version": deleted_series.json()["series"]["version"],
        },
    )
    assert restored_series.status_code == 200
    deleted_source = client.request(
        "DELETE",
        f"/api/v1/calendar/subscriptions/{subscription['id']}",
        json={
            "operation_id": str(uuid4()),
            "expected_version": subscription["version"],
        },
    )
    assert deleted_source.status_code == 200
    restored_source = client.post(
        f"/api/v1/calendar/subscriptions/{subscription['id']}/restore",
        json={
            "operation_id": str(uuid4()),
            "expected_version": deleted_source.json()["subscription"]["version"],
        },
    )
    assert restored_source.status_code == 200

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
        columns = {
            row[0]
            for row in connection.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema='public'
                  AND table_name='change_events'
                """
            )
        }
    assert events == [
        ("calendar_series", UUID(series["id"]), "deleted", 2),
        ("calendar_series", UUID(series["id"]), "restored", 3),
        ("calendar_source", UUID(subscription["id"]), "deleted", 2),
        ("calendar_source", UUID(subscription["id"]), "restored", 3),
    ]
    assert columns == {
        "sequence",
        "owner_id",
        "entity_type",
        "entity_id",
        "action",
        "entity_version",
        "changed_at",
    }
