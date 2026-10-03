from contextlib import contextmanager
from uuid import uuid4

import httpx
import psycopg
import pytest

from tests.conftest import psycopg_url
from tests.quota_helpers import ensure_quota_policy
from tests.test_calendar_series import create_account

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def reset_conversation_api_data(database_url):
    def cleanup() -> None:
        with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
            connection.execute(
                """
                UPDATE ai_config_capabilities
                SET current_version_id=NULL, next_version=1, updated_at=now()
                WHERE capability='conversation'
                """
            )
            connection.execute(
                "DELETE FROM ai_jobs WHERE capability='conversation'"
            )
            connection.execute("DELETE FROM ai_conversations")
            connection.execute(
                """
                DELETE FROM user_operations
                WHERE scope LIKE 'ai_conversation.%'
                   OR scope LIKE 'ai_message.%'
                   OR scope LIKE 'ai_job.cancel:%'
                """
            )
            connection.execute(
                "DELETE FROM ai_config_versions WHERE capability='conversation'"
            )

    cleanup()
    yield
    cleanup()


@contextmanager
def browser(live_server):
    with httpx.Client(base_url=live_server.origin, timeout=10, trust_env=False) as client:
        yield client


def create_conversation(client, *, title=None, operation_id=None):
    body = {"operation_id": str(operation_id or uuid4())}
    if title is not None:
        body["title"] = title
    response = client.post("/api/v1/ai/conversations", json=body)
    assert response.status_code == 201, response.text
    return response


def publish_conversation_config(database_url: str) -> str:
    config_id = uuid4()
    test_id = uuid4()
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        current_id, version = connection.execute(
            """
            SELECT current_version_id, next_version
            FROM ai_config_capabilities
            WHERE capability='conversation'
            FOR UPDATE
            """
        ).fetchone()
        if current_id is not None:
            connection.execute(
                """
                UPDATE ai_config_versions
                SET status='retired', retired_at=now(), updated_at=now()
                WHERE id=%s AND status='published'
                """,
                (current_id,),
            )
        connection.execute(
            """
            INSERT INTO ai_config_versions (
                id, capability, version, endpoint_url, model, api_key_ciphertext,
                timeout_seconds, max_output_tokens, price_currency,
                input_price_nanos_per_million_tokens,
                cached_input_price_nanos_per_million_tokens,
                output_price_nanos_per_million_tokens, status, last_test_id,
                last_test_started_at, last_test_completed_at, published_at
            ) VALUES (
                %s, 'conversation', %s, 'https://ai.example.com/v1',
                'chat-model', %s, 60, 4096, 'CNY',
                1000000000, 500000000, 2000000000,
                'published', %s, now(), now(), now()
            )
            """,
            (config_id, version, b"encrypted-key", test_id),
        )
        connection.execute(
            """
            UPDATE ai_config_capabilities
            SET current_version_id=%s, next_version=%s, updated_at=now()
            WHERE capability='conversation'
            """,
            (config_id, version + 1),
        )
    ensure_quota_policy(database_url)
    return str(config_id)


def seed_completed_pair(
    database_url: str,
    owner_id: str,
    *,
    title: str,
    user_content: str,
    assistant_content: str,
) -> tuple[str, str]:
    conversation_id = uuid4()
    request_id = uuid4()
    response_id = uuid4()
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        connection.execute(
            """
            INSERT INTO ai_conversations (
                id, owner_id, title, title_source
            ) VALUES (%s, %s, %s, 'user')
            """,
            (conversation_id, owner_id, title),
        )
        connection.execute(
            """
            INSERT INTO ai_conversation_messages (
                id, owner_id, conversation_id, sequence, role, status,
                content, completed_at
            ) VALUES
                (%s, %s, %s, 1, 'user', 'complete', %s, now()),
                (%s, %s, %s, 2, 'assistant', 'complete', %s, now())
            """,
            (
                request_id,
                owner_id,
                conversation_id,
                user_content,
                response_id,
                owner_id,
                conversation_id,
                assistant_content,
            ),
        )
    return str(request_id), str(response_id)


def send_message(client, conversation: dict, content: str, *, operation_id=None):
    return client.post(
        f"/api/v1/ai/conversations/{conversation['id']}/messages",
        json={
            "operation_id": str(operation_id or uuid4()),
            "expected_conversation_version": conversation["version"],
            "content": content,
        },
    )


def test_conversation_crud_pagination_replay_and_owner_isolation(
    client,
    live_server,
    database_url,
):
    create_account(client, database_url)
    operation_id = uuid4()
    created = create_conversation(
        client,
        title="  学习 安排  ",
        operation_id=operation_id,
    )
    assert created.json()["conversation"]["title"] == "学习 安排"
    assert (
        create_conversation(
            client,
            title="学习 安排",
            operation_id=operation_id,
        ).json()
        == created.json()
    )
    conversation = created.json()["conversation"]
    renamed = client.patch(
        f"/api/v1/ai/conversations/{conversation['id']}",
        json={
            "operation_id": str(uuid4()),
            "expected_version": conversation["version"],
            "title": "本周学习",
        },
    )
    assert renamed.status_code == 200, renamed.text
    renamed_conversation = renamed.json()["conversation"]
    assert renamed_conversation["title"] == "本周学习"
    assert renamed_conversation["title_source"] == "user"
    assert renamed_conversation["version"] == 2

    stale = client.patch(
        f"/api/v1/ai/conversations/{conversation['id']}",
        json={
            "operation_id": str(uuid4()),
            "expected_version": 1,
            "archived": True,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "conversation_version_conflict"
    archived = client.patch(
        f"/api/v1/ai/conversations/{conversation['id']}",
        json={
            "operation_id": str(uuid4()),
            "expected_version": renamed_conversation["version"],
            "archived": True,
        },
    )
    assert archived.status_code == 200
    assert archived.json()["conversation"]["archived"]
    assert client.get("/api/v1/ai/conversations").json()["items"] == []
    archived_page = client.get(
        "/api/v1/ai/conversations",
        params={"archived": "true", "limit": 1},
    ).json()
    assert [item["id"] for item in archived_page["items"]] == [conversation["id"]]

    with browser(live_server) as other:
        create_account(other, database_url)
        assert other.get(
            f"/api/v1/ai/conversations/{conversation['id']}"
        ).status_code == 404
        assert other.get(
            f"/api/v1/ai/conversations/{conversation['id']}/messages"
        ).status_code == 404


def test_message_admission_requires_policy_time_zone_and_pricing(
    client,
    database_url,
):
    _, owner_id = create_account(client, database_url)
    config_id = publish_conversation_config(database_url)

    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        connection.execute(
            """
            UPDATE ai_quota_policy_state
            SET current_version_id=NULL, updated_at=now()
            WHERE id=1
            """
        )
        connection.execute("DELETE FROM ai_quota_policy_versions")
    no_policy_conversation = create_conversation(client).json()["conversation"]
    no_policy = send_message(client, no_policy_conversation, "没有额度策略")
    assert no_policy.status_code == 503
    assert no_policy.json()["error"]["code"] == "ai_quota_policy_unavailable"

    ensure_quota_policy(database_url)
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        connection.execute(
            "DELETE FROM user_time_settings WHERE owner_id=%s",
            (owner_id,),
        )
    no_time_conversation = create_conversation(client).json()["conversation"]
    no_time = send_message(client, no_time_conversation, "没有时区")
    assert no_time.status_code == 409
    assert no_time.json()["error"]["code"] == "time_settings_required"

    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        connection.execute(
            """
            INSERT INTO user_time_settings (owner_id, time_zone)
            VALUES (%s, 'Asia/Shanghai')
            """,
            (owner_id,),
        )
        connection.execute(
            """
            UPDATE ai_config_versions
            SET status='retired', retired_at=now(), updated_at=now()
            WHERE id=%s
            """,
            (config_id,),
        )
        unpriced_id = uuid4()
        connection.execute(
            """
            INSERT INTO ai_config_versions (
                id, capability, version, endpoint_url, model,
                api_key_ciphertext, timeout_seconds, max_output_tokens,
                status, last_test_id, last_test_started_at,
                last_test_completed_at, published_at
            ) VALUES (
                %s, 'conversation', 2, 'https://ai.example.com/v1',
                'unpriced-model', %s, 60, 4096, 'published',
                %s, now(), now(), now()
            )
            """,
            (unpriced_id, b"encrypted-key", uuid4()),
        )
        connection.execute(
            """
            UPDATE ai_config_capabilities
            SET current_version_id=%s, next_version=3, updated_at=now()
            WHERE capability='conversation'
            """,
            (unpriced_id,),
        )
    unpriced_conversation = create_conversation(client).json()["conversation"]
    unpriced = send_message(client, unpriced_conversation, "没有价格")
    assert unpriced.status_code == 503
    assert unpriced.json()["error"]["code"] == "ai_quota_pricing_unavailable"

    with psycopg.connect(psycopg_url(database_url)) as connection:
        assert connection.execute(
            """
            SELECT
                (SELECT count(*) FROM ai_jobs WHERE owner_id=%s),
                (SELECT count(*) FROM ai_quota_reservations WHERE owner_id=%s),
                (SELECT count(*) FROM ai_conversation_messages WHERE owner_id=%s)
            """,
            (owner_id, owner_id, owner_id),
        ).fetchone() == (0, 0, 0)


def test_message_submission_is_atomic_relevant_and_bounded(
    client,
    database_url,
):
    _, owner_id = create_account(client, database_url)
    publish_conversation_config(database_url)
    relevant_ids = seed_completed_pair(
        database_url,
        owner_id,
        title="课程复盘",
        user_content="高等数学周报需要复盘错题",
        assistant_content="先整理错题，再安排复习时段。",
    )
    unrelated_ids = seed_completed_pair(
        database_url,
        owner_id,
        title="购物",
        user_content="购买厨房清洁用品",
        assistant_content="已经列出清单。",
    )
    conversation = create_conversation(client).json()["conversation"]
    operation_id = uuid4()
    submitted = send_message(
        client,
        conversation,
        "帮我继续复盘高等数学周报",
        operation_id=operation_id,
    )
    assert submitted.status_code == 202, submitted.text
    body = submitted.json()
    assert body["conversation"]["title"] == "帮我继续复盘高等数学周报"
    assert body["conversation"]["version"] == 2
    assert [message["role"] for message in body["messages"]] == ["user", "assistant"]
    assert body["messages"][1]["status"] == "queued"
    assert body["job"]["status"] == "queued"
    assert body["job"]["last_event_sequence"] == 1
    assert (
        send_message(
            client,
            conversation,
            "帮我继续复盘高等数学周报",
            operation_id=operation_id,
        ).json()
        == body
    )
    changed_replay = send_message(
        client,
        conversation,
        "换一个问题",
        operation_id=operation_id,
    )
    assert changed_replay.status_code == 409
    assert changed_replay.json()["error"]["code"] == "operation_id_reused"

    active = send_message(
        client,
        body["conversation"],
        "继续补充",
    )
    assert active.status_code == 409
    assert active.json()["error"]["code"] == "conversation_generation_active"
    archive = client.patch(
        f"/api/v1/ai/conversations/{conversation['id']}",
        json={
            "operation_id": str(uuid4()),
            "expected_version": body["conversation"]["version"],
            "archived": True,
        },
    )
    assert archive.status_code == 409
    assert archive.json()["error"]["code"] == "conversation_generation_active"

    detail = client.get(
        f"/api/v1/ai/conversations/{conversation['id']}"
    ).json()
    assert detail["active_job"]["id"] == body["job"]["id"]
    messages = client.get(
        f"/api/v1/ai/conversations/{conversation['id']}/messages",
        params={"limit": 1},
    ).json()
    assert [item["role"] for item in messages["items"]] == ["assistant"]
    assert messages["items"][0]["job"]["id"] == body["job"]["id"]
    older = client.get(
        f"/api/v1/ai/conversations/{conversation['id']}/messages",
        params={"before_sequence": messages["next_before_sequence"]},
    ).json()
    assert [item["role"] for item in older["items"]] == ["user"]

    with psycopg.connect(psycopg_url(database_url)) as connection:
        generation = connection.execute(
            """
            SELECT context_message_ids
            FROM ai_chat_generations
            WHERE job_id=%s
            """,
            (body["job"]["id"],),
        ).fetchone()[0]
        assert generation == list(relevant_ids)
        assert not set(generation) & set(unrelated_ids)
        stored = connection.execute(
            """
            SELECT
                (SELECT count(*) FROM ai_conversation_messages
                 WHERE conversation_id=%s),
                (SELECT count(*) FROM ai_jobs WHERE id=%s),
                (SELECT count(*) FROM ai_chat_generations WHERE job_id=%s),
                (SELECT count(*) FROM ai_job_events WHERE job_id=%s),
                (SELECT count(*) FROM ai_quota_reservations WHERE job_id=%s),
                (SELECT quota_mode FROM ai_jobs WHERE id=%s)
            """,
            (
                conversation["id"],
                body["job"]["id"],
                body["job"]["id"],
                body["job"]["id"],
                body["job"]["id"],
                body["job"]["id"],
            ),
        ).fetchone()
        assert stored == (2, 1, 1, 1, 1, "user")
        assert connection.execute(
            "SELECT input_summary::text FROM ai_jobs WHERE id=%s",
            (body["job"]["id"],),
        ).fetchone()[0].find("帮我继续") == -1

    stale_conversation = create_conversation(client).json()["conversation"]
    stale = client.post(
        f"/api/v1/ai/conversations/{stale_conversation['id']}/messages",
        json={
            "operation_id": str(uuid4()),
            "expected_conversation_version": 99,
            "content": "不会保存",
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "conversation_version_conflict"

    archived_conversation = create_conversation(client).json()["conversation"]
    archived = client.patch(
        f"/api/v1/ai/conversations/{archived_conversation['id']}",
        json={
            "operation_id": str(uuid4()),
            "expected_version": archived_conversation["version"],
            "archived": True,
        },
    ).json()["conversation"]
    archived_send = send_message(client, archived, "归档后不应发送")
    assert archived_send.status_code == 409
    assert archived_send.json()["error"]["code"] == "conversation_archived"

    for index in range(2):
        extra = create_conversation(client, title=f"并行会话 {index}").json()[
            "conversation"
        ]
        assert send_message(client, extra, f"并行请求 {index}").status_code == 202
    limited = create_conversation(client, title="第四个会话").json()["conversation"]
    limited_send = send_message(client, limited, "超过账号并发限制")
    assert limited_send.status_code == 429
    assert limited_send.json()["error"]["code"] == "ai_job_limit_reached"

    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            "UPDATE users SET ai_access='frozen' WHERE id=%s",
            (owner_id,),
        )
    assert (
        send_message(
            client,
            conversation,
            "帮我继续复盘高等数学周报",
            operation_id=operation_id,
        ).json()
        == body
    )
    blank = create_conversation(client).json()["conversation"]
    frozen = send_message(client, blank, "新请求")
    assert frozen.status_code == 403
    assert frozen.json()["error"]["code"] == "ai_access_frozen"
