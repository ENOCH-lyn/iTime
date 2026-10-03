import asyncio
from contextlib import contextmanager
from uuid import uuid4

import httpx
import psycopg
import pytest

from itime.config import Settings
from itime.database import Database
from itime.public_links.fetcher import (
    PublicLinkFetchError,
    PublicLinkFetchResult,
)
from itime.public_links.parser import PublicDocument
from itime.public_links.service import (
    claim_next_public_link,
    complete_public_link,
)
from itime.public_links.worker import process_one
from tests.conftest import psycopg_url

pytestmark = pytest.mark.integration
AUTH = "/api/v1/auth"
PASSWORD = "Valid-public-link-password-123"


@pytest.fixture(autouse=True)
def reset_public_link_api_data(database_url):
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        connection.execute("DELETE FROM task_links")
        connection.execute(
            "DELETE FROM users WHERE email LIKE 'public-link-api-%'"
        )


def create_account(
    client: httpx.Client,
    database_url: str,
    *,
    label: str,
) -> str:
    email = f"public-link-api-{label}-{uuid4().hex}@example.com"
    registered = client.post(
        f"{AUTH}/register",
        json={"email": email, "password": PASSWORD},
    )
    assert registered.status_code == 202, registered.text
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        owner_id = connection.execute(
            "UPDATE users SET email_verified_at=now() WHERE email=%s RETURNING id",
            (email,),
        ).fetchone()[0]
    logged_in = client.post(
        f"{AUTH}/login",
        json={
            "email": email,
            "password": PASSWORD,
            "device_name": "公开链接测试",
        },
    )
    assert logged_in.status_code == 200, logged_in.text
    client.headers["x-csrf-token"] = logged_in.json()["csrf_token"]
    return str(owner_id)


@contextmanager
def browser(live_server):
    with httpx.Client(
        base_url=live_server.origin,
        timeout=10,
        trust_env=False,
    ) as client:
        yield client


def task_payload(*, url: str = "https://public.example/article") -> dict:
    return {
        "operation_id": str(uuid4()),
        "title": "阅读课程资料",
        "description": "根据公开页面准备讨论。",
        "project_id": None,
        "estimated_minutes": 45,
        "priority": 2,
        "labels": ["学习"],
        "links": [{"title": "课程要求", "url": url}],
        "steps": [{"title": "阅读"}],
    }


def task_update(task: dict, *, title: str, url: str) -> dict:
    return {
        "operation_id": str(uuid4()),
        "expected_version": task["version"],
        "title": task["title"],
        "description": task["description"],
        "project_id": task["project"]["id"] if task["project"] else None,
        "estimated_minutes": task["estimated_minutes"],
        "priority": task["priority"],
        "labels": [label["name"] for label in task["labels"]],
        "links": [{"title": title, "url": url}],
    }


def result(
    *,
    title: str = "Course page",
    summary: str = "Read chapters one and two.",
) -> PublicLinkFetchResult:
    return PublicLinkFetchResult(
        final_host="cdn.example",
        http_status=200,
        content_sha256="b" * 64,
        document=PublicDocument(
            media_type="text/html",
            page_title=title,
            summary=summary,
            extracted_text=f"{summary} Full source text.",
        ),
        redirect_count=1,
    )


def run_worker(
    database_url: str,
    fetch_result: PublicLinkFetchResult | PublicLinkFetchError,
) -> None:
    settings = Settings(database_url=database_url, environment="test")
    database = Database(settings)

    class FakeFetcher:
        async def fetch(self, url: str) -> PublicLinkFetchResult:
            assert url.startswith("https://")
            if isinstance(fetch_result, PublicLinkFetchError):
                raise fetch_result
            return fetch_result

    async def execute() -> None:
        try:
            assert await process_one(
                database,
                settings,
                fetcher=FakeFetcher(),
            )
        finally:
            await database.close()

    asyncio.run(execute())


def test_task_link_auto_queue_result_and_url_reconciliation(
    client,
    database_url,
    settings,
):
    create_account(client, database_url, label="reconcile")
    created_response = client.post("/api/v1/tasks", json=task_payload())
    assert created_response.status_code == 201, created_response.text
    task = created_response.json()["task"]
    link = task["links"][0]
    assert link["title"] == "课程要求"
    assert link["url"] == "https://public.example/article"
    assert link["fetch"] == {
        "status": "queued",
        "source_host": "public.example",
        "final_host": None,
        "page_title": None,
        "summary": None,
        "fetched_at": None,
        "content_sha256": None,
        "last_attempt_at": None,
        "next_attempt_at": link["fetch"]["next_attempt_at"],
        "using_previous_snapshot": False,
        "error": None,
    }
    assert link["fetch"]["next_attempt_at"] is not None
    serialized = str(link)
    for internal in ("extracted_text", "lease_id", "lease_until", "attempt_count"):
        assert internal not in serialized

    run_worker(database_url, result())
    ready = client.get(f"/api/v1/tasks/{task['id']}").json()["task"]
    ready_link = ready["links"][0]
    assert ready_link["id"] == link["id"]
    assert ready_link["fetch"]["status"] == "ready"
    assert ready_link["fetch"]["page_title"] == "Course page"
    assert ready_link["fetch"]["summary"] == "Read chapters one and two."
    assert ready_link["fetch"]["final_host"] == "cdn.example"
    assert ready_link["fetch"]["fetched_at"] is not None
    assert ready["version"] == task["version"]

    renamed_response = client.put(
        f"/api/v1/tasks/{task['id']}",
        json=task_update(
            ready,
            title="更新后的资料名",
            url="https://public.example/article",
        ),
    )
    assert renamed_response.status_code == 200, renamed_response.text
    renamed = renamed_response.json()["task"]
    assert renamed["links"][0]["id"] == link["id"]
    assert renamed["links"][0]["fetch"]["status"] == "ready"
    assert renamed["links"][0]["fetch"]["summary"] == "Read chapters one and two."

    with psycopg.connect(psycopg_url(database_url)) as connection:
        revision_before = connection.execute(
            "SELECT fetch_revision FROM task_links WHERE id=%s",
            (link["id"],),
        ).fetchone()[0]
    changed_response = client.put(
        f"/api/v1/tasks/{task['id']}",
        json=task_update(
            renamed,
            title="更新后的资料名",
            url="https://public.example/changed",
        ),
    )
    assert changed_response.status_code == 200, changed_response.text
    changed = changed_response.json()["task"]
    assert changed["links"][0]["id"] == link["id"]
    assert changed["links"][0]["fetch"]["status"] == "queued"
    assert changed["links"][0]["fetch"]["summary"] is None
    with psycopg.connect(psycopg_url(database_url)) as connection:
        assert connection.execute(
            """
            SELECT fetch_revision, attempt_count, content_sha256
            FROM task_links
            WHERE id=%s
            """,
            (link["id"],),
        ).fetchone() == (revision_before + 1, 0, None)

    database = Database(settings)

    async def stale_result() -> None:
        claim = await claim_next_public_link(database, lease_seconds=30)
        assert claim is not None
        current = client.get(f"/api/v1/tasks/{task['id']}").json()["task"]
        changed_again = client.put(
            f"/api/v1/tasks/{task['id']}",
            json=task_update(
                current,
                title="最终资料",
                url="https://public.example/final",
            ),
        )
        assert changed_again.status_code == 200
        assert not await complete_public_link(database, claim, result(title="Old"))
        await database.close()

    asyncio.run(stale_result())
    final = client.get(f"/api/v1/tasks/{task['id']}").json()["task"]["links"][0]
    assert final["id"] == link["id"]
    assert final["url"] == "https://public.example/final"
    assert final["fetch"]["status"] == "queued"
    assert final["fetch"]["page_title"] is None


def test_manual_refresh_is_idempotent_limited_and_preserves_previous_snapshot(
    client,
    database_url,
    live_server,
):
    create_account(client, database_url, label="refresh")
    task = client.post("/api/v1/tasks", json=task_payload()).json()["task"]
    link = task["links"][0]
    run_worker(database_url, result())
    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            """
            UPDATE task_links
            SET last_attempt_at=now()-interval '31 seconds'
            WHERE id=%s
            """,
            (link["id"],),
        )

    operation_id = str(uuid4())
    refresh_path = f"/api/v1/tasks/{task['id']}/links/{link['id']}/refresh"
    refreshed = client.post(refresh_path, json={"operation_id": operation_id})
    assert refreshed.status_code == 200, refreshed.text
    queued = refreshed.json()["task"]["links"][0]
    assert queued["fetch"]["status"] == "queued"
    assert queued["fetch"]["using_previous_snapshot"] is True
    assert queued["fetch"]["summary"] == "Read chapters one and two."
    assert client.post(
        refresh_path,
        json={"operation_id": operation_id},
    ).json() == refreshed.json()
    concurrent = client.post(
        refresh_path,
        json={"operation_id": str(uuid4())},
    )
    assert concurrent.status_code == 200

    run_worker(
        database_url,
        PublicLinkFetchError(
            "public_link_content_type_invalid",
            "网页返回的内容类型暂不支持。",
            retryable=False,
            http_status=200,
        ),
    )
    failed = client.get(f"/api/v1/tasks/{task['id']}").json()["task"]
    failed_link = failed["links"][0]
    assert failed_link["fetch"]["status"] == "failed"
    assert failed_link["fetch"]["using_previous_snapshot"] is True
    assert failed_link["fetch"]["summary"] == "Read chapters one and two."
    assert failed_link["fetch"]["error"] == {
        "code": "public_link_content_type_invalid",
        "message": "网页返回的内容类型暂不支持。",
    }

    limited = client.post(
        refresh_path,
        json={"operation_id": str(uuid4())},
    )
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "public_link_refresh_too_frequent"
    assert limited.json()["error"]["saved"] == "not_saved"

    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            """
            UPDATE task_links
            SET last_attempt_at=now()-interval '31 seconds'
            WHERE id=%s
            """,
            (link["id"],),
        )
    allowed = client.post(
        refresh_path,
        json={"operation_id": str(uuid4())},
    )
    assert allowed.status_code == 200
    assert allowed.json()["task"]["links"][0]["fetch"]["status"] == "queued"

    with browser(live_server) as other:
        create_account(other, database_url, label="other")
        assert other.post(
            refresh_path,
            json={"operation_id": str(uuid4())},
        ).status_code == 404

    archived = client.put(
        f"/api/v1/tasks/{task['id']}/archive",
        json={
            "operation_id": str(uuid4()),
            "expected_version": task["version"],
            "archived": True,
        },
    )
    assert archived.status_code == 200, archived.text
    denied = client.post(
        refresh_path,
        json={"operation_id": str(uuid4())},
    )
    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "task_archived"

    csrf = client.headers.pop("x-csrf-token")
    assert client.post(
        refresh_path,
        json={"operation_id": str(uuid4())},
    ).status_code == 403
    client.headers["x-csrf-token"] = csrf


def test_worker_completion_emits_link_sync_event_without_task_version_change(
    client,
    database_url,
):
    owner_id = create_account(client, database_url, label="sync")
    task = client.post("/api/v1/tasks", json=task_payload()).json()["task"]
    run_worker(database_url, result())

    with psycopg.connect(psycopg_url(database_url)) as connection:
        task_version = connection.execute(
            "SELECT version FROM tasks WHERE id=%s",
            (task["id"],),
        ).fetchone()[0]
        events = connection.execute(
            """
            SELECT entity_type, entity_id, action, entity_version
            FROM change_events
            WHERE owner_id=%s AND entity_type='task_link'
            ORDER BY sequence
            """,
            (owner_id,),
        ).fetchall()

    assert task_version == task["version"]
    assert [
        (entity_type, str(entity_id), action, entity_version)
        for entity_type, entity_id, action, entity_version in events
    ] == [("task_link", task["links"][0]["id"], "updated", 1)]
