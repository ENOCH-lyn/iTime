import asyncio
import base64
import io
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4
from zipfile import ZipFile

import psycopg
import pytest

from itime.config import Settings
from itime.data_export.worker import process_one
from itime.database import Database
from tests.conftest import psycopg_url
from tests.test_tasks import browser, create_account, project_payload, task_payload

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def reset_exports(database_url):
    with psycopg.connect(psycopg_url(database_url), autocommit=True) as connection:
        connection.execute("DELETE FROM data_export_jobs")


def run_worker_once(database_url: str) -> bool:
    settings = Settings(database_url=database_url, environment="test")
    database = Database(settings)

    async def execute() -> bool:
        try:
            return await process_one(database, settings)
        finally:
            await database.close()

    return asyncio.run(execute())


def test_create_replay_active_reuse_and_listing(client, database_url):
    assert client.get("/api/v1/data-exports").status_code == 401
    _, owner_id = create_account(client, database_url)
    csrf = client.headers.pop("x-csrf-token")
    denied = client.post(
        "/api/v1/data-exports",
        json={"operation_id": str(uuid4())},
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "invalid_csrf"
    client.headers["x-csrf-token"] = csrf

    operation_id = uuid4()
    created = client.post(
        "/api/v1/data-exports",
        json={"operation_id": str(operation_id)},
    )
    assert created.status_code == 202, created.text
    body = created.json()
    assert body["operation_id"] == str(operation_id)
    assert body["reused"] is False
    assert body["job"]["status"] == "queued"

    replayed = client.post(
        "/api/v1/data-exports",
        json={"operation_id": str(operation_id)},
    )
    assert replayed.status_code == 202
    assert replayed.json() == body

    reused = client.post(
        "/api/v1/data-exports",
        json={"operation_id": str(uuid4())},
    )
    assert reused.status_code == 202
    assert reused.json()["reused"] is True
    assert reused.json()["job"]["id"] == body["job"]["id"]

    listing = client.get("/api/v1/data-exports")
    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()["jobs"]] == [body["job"]["id"]]
    detail = client.get(f"/api/v1/data-exports/{body['job']['id']}")
    assert detail.status_code == 200
    assert detail.json()["job"]["id"] == body["job"]["id"]

    reused_operation = uuid4()
    project = client.post(
        "/api/v1/projects",
        json=project_payload(reused_operation),
    )
    assert project.status_code == 201
    conflict = client.post(
        "/api/v1/data-exports",
        json={"operation_id": str(reused_operation)},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "operation_id_reused"
    assert conflict.json()["error"]["saved"] == "not_saved"

    assert owner_id


def test_download_headers_content_ownership_and_session_revocation(
    client,
    database_url,
    live_server,
):
    _, owner_id = create_account(client, database_url)
    marker = f"download-{uuid4().hex}"
    assert client.post(
        "/api/v1/tasks",
        json=task_payload(title=marker),
    ).status_code == 201
    created = client.post(
        "/api/v1/data-exports",
        json={"operation_id": str(uuid4())},
    ).json()["job"]
    queued_download = client.get(
        f"/api/v1/data-exports/{created['id']}/download",
    )
    assert queued_download.status_code == 409
    assert queued_download.json()["error"]["code"] == "data_export_not_ready"

    assert run_worker_once(database_url)
    detail = client.get(f"/api/v1/data-exports/{created['id']}").json()["job"]
    assert detail["status"] == "succeeded"
    response = client.get(f"/api/v1/data-exports/{created['id']}/download")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["cache-control"] == "no-store, private"
    assert response.headers["content-disposition"].startswith(
        'attachment; filename="itime-export-'
    )
    assert response.headers["digest"] == (
        "sha-256=" + base64.b64encode(sha256(response.content).digest()).decode()
    )
    with ZipFile(io.BytesIO(response.content)) as package:
        assert marker.encode() in package.read("itime-data.json")

    with browser(live_server) as other:
        _, other_id = create_account(other, database_url)
        assert other.get(f"/api/v1/data-exports/{created['id']}").status_code == 404
        assert other.get(
            f"/api/v1/data-exports/{created['id']}/download"
        ).status_code == 404
        assert other.get("/api/v1/data-exports").json()["jobs"] == []
        assert other_id != owner_id

    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            "UPDATE device_sessions SET revoked_at=now() WHERE user_id=%s",
            (owner_id,),
        )
    revoked = client.get(f"/api/v1/data-exports/{created['id']}/download")
    assert revoked.status_code == 401


def test_expired_and_corrupt_artifacts_have_recovery_errors(
    client,
    database_url,
):
    _, _ = create_account(client, database_url)
    first_id = client.post(
        "/api/v1/data-exports",
        json={"operation_id": str(uuid4())},
    ).json()["job"]["id"]
    assert run_worker_once(database_url)
    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            """
            UPDATE data_export_jobs
            SET finished_at=now()-interval '2 hours',
                expires_at=now()-interval '1 hour'
            WHERE id=%s
            """,
            (first_id,),
        )
    detail = client.get(f"/api/v1/data-exports/{first_id}")
    assert detail.json()["job"]["status"] == "expired"
    expired = client.get(f"/api/v1/data-exports/{first_id}/download")
    assert expired.status_code == 410
    assert expired.json()["error"]["code"] == "data_export_expired"

    second_id = client.post(
        "/api/v1/data-exports",
        json={"operation_id": str(uuid4())},
    ).json()["job"]["id"]
    assert run_worker_once(database_url)
    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,
    ) as connection:
        connection.execute(
            """
            UPDATE data_export_jobs
            SET artifact_ciphertext=%s,
                expires_at=%s
            WHERE id=%s
            """,
            (b"not-fernet", datetime.now(UTC) + timedelta(hours=1), second_id),
        )
    corrupt = client.get(f"/api/v1/data-exports/{second_id}/download")
    assert corrupt.status_code == 503
    assert corrupt.json()["error"]["code"] == "data_export_artifact_unavailable"
