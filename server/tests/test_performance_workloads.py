import json
import os
from pathlib import Path
from uuid import UUID

import pytest

os.environ.setdefault("LOCUST_SKIP_MONKEY_PATCH", "1")

from locust.env import Environment

import performance.locustfile as workloads


class FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self.payload = payload
        self.failures: list[str] = []
        self.successes = 0

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 400

    def json(self) -> dict:
        return self.payload

    def failure(self, message: str) -> None:
        self.failures.append(message)

    def success(self) -> None:
        self.successes += 1

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


class FakeClient:
    def __init__(self, responses: list[FakeResponse]):
        self.responses = responses
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, method: str, path: str, **kwargs) -> FakeResponse:
        self.calls.append((method, path, kwargs))
        return self.responses.pop(0)

    def get(self, path: str, **kwargs) -> FakeResponse:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs) -> FakeResponse:
        return self.request("POST", path, **kwargs)


def credential(email: str, dataset: str = "standard") -> dict[str, str]:
    return {
        "email": email,
        "password": "itime-performance-only",
        "dataset": dataset,
    }


def test_workload_registry_and_request_names():
    assert set(workloads.WORKLOADS) == {"standard", "history", "sync", "ai"}
    assert workloads.request_name("GET", "/api/v1/tasks") == "read::tasks.list"
    assert workloads.request_name("POST", "/api/v1/tasks") == "write::tasks.create"
    assert (
        workloads.request_name(
            "GET",
            "/api/v1/statistics",
            category="history",
        )
        == "history::statistics.read"
    )


def test_credentials_are_validated_sharded_and_never_shared(tmp_path: Path):
    path = tmp_path / "accounts.json"
    path.write_text(
        json.dumps(
            [
                credential("one@performance.example.com"),
                credential("two@performance.example.com"),
                credential("three@performance.example.com"),
                credential("heavy@performance.example.com", "history"),
            ]
        )
    )

    first = workloads.CredentialPool.from_file(
        path,
        dataset="standard",
        worker_index=0,
        worker_count=2,
    )
    second = workloads.CredentialPool.from_file(
        path,
        dataset="standard",
        worker_index=1,
        worker_count=2,
    )
    first_emails = {first.claim()["email"], first.claim()["email"]}
    second_emails = {second.claim()["email"]}
    assert first_emails.isdisjoint(second_emails)
    assert first_emails | second_emails == {
        "one@performance.example.com",
        "two@performance.example.com",
        "three@performance.example.com",
    }
    with pytest.raises(RuntimeError, match="账号数量不足"):
        second.claim()

    duplicate_path = tmp_path / "duplicates.json"
    duplicate_path.write_text(json.dumps([credential("same@example.com")] * 2))
    with pytest.raises(ValueError, match="重复"):
        workloads.CredentialPool.from_file(
            duplicate_path,
            dataset="standard",
            worker_index=0,
            worker_count=1,
        )


def test_standard_user_authenticates_and_writes_with_csrf_and_operation_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    credentials_path = tmp_path / "accounts.json"
    credentials_path.write_text(
        json.dumps([credential("user@performance.example.com")])
    )
    monkeypatch.setenv("ITIME_PERF_CREDENTIALS", str(credentials_path))
    monkeypatch.setenv("ITIME_PERF_ANCHOR_DATE", "2026-10-05")
    workloads.reset_credential_pools()

    login = FakeResponse(200, {"csrf_token": "csrf-value"})
    initial_sync = FakeResponse(
        200,
        {"next_cursor": "cursor-1", "reset_required": True},
    )
    created = FakeResponse(
        201,
        {"operation_id": "operation-1", "task": {"id": "task-1"}},
    )
    client = FakeClient([login, initial_sync, created])
    user = workloads.StandardUser(Environment())
    user.client = client

    user.on_start()
    user.create_task()

    assert user.csrf == "csrf-value"
    assert user.sync_cursor == "cursor-1"
    _, _, request = client.calls[-1]
    assert request["headers"] == {"X-CSRF-Token": "csrf-value"}
    UUID(request["json"]["operation_id"])
    assert request["json"]["title"].startswith("性能负载任务")
    assert created.failures == []
    assert created.successes == 1
    user.on_stop()


def test_response_validation_marks_status_and_shape_failures():
    status_failure = FakeResponse(500, {})
    with status_failure:
        assert (
            workloads.require_response(
                status_failure,
                expected_status=200,
                required_keys=("items",),
            )
            is None
        )
    assert status_failure.failures == ["unexpected status 500, expected 200"]

    shape_failure = FakeResponse(200, {"other": []})
    with shape_failure:
        assert (
            workloads.require_response(
                shape_failure,
                expected_status=200,
                required_keys=("items",),
            )
            is None
        )
    assert shape_failure.failures == ["response missing keys: items"]


def test_sync_change_matching_requires_the_created_entity():
    target = "25c2aab1-eac5-4eca-bd7d-c8ff4d4ed9bd"
    assert workloads.contains_change(
        {
            "events": [
                {"entity_type": "task", "entity_id": target},
            ]
        },
        target,
    )
    assert not workloads.contains_change(
        {
            "events": [
                {"entity_type": "task", "entity_id": "other"},
            ]
        },
        target,
    )


def test_ai_user_submits_once_and_emits_claim_and_completion_metrics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    credentials_path = tmp_path / "accounts.json"
    credentials_path.write_text(
        json.dumps([credential("ai-user@performance.example.com")])
    )
    monkeypatch.setenv("ITIME_PERF_CREDENTIALS", str(credentials_path))
    monkeypatch.setenv("ITIME_PERF_ANCHOR_DATE", "2026-10-05")
    workloads.reset_credential_pools()

    responses = [
        FakeResponse(200, {"csrf_token": "csrf-value"}),
        FakeResponse(200, {"next_cursor": "cursor-1", "reset_required": True}),
        FakeResponse(
            201,
            {"conversation": {"id": "conversation-1", "version": 1}},
        ),
        FakeResponse(
            202,
            {
                "conversation": {"id": "conversation-1", "version": 2},
                "messages": [{"id": "request"}, {"id": "response"}],
                "job": {"id": "job-1", "status": "queued"},
            },
        ),
        FakeResponse(200, {"id": "job-1", "status": "running"}),
        FakeResponse(
            200,
            {
                "id": "job-1",
                "status": "succeeded",
                "output": {"content": "完成"},
            },
        ),
    ]
    client = FakeClient(responses)
    fired: list[dict] = []
    monkeypatch.setattr(
        workloads.events.request,
        "fire",
        lambda **kwargs: fired.append(kwargs),
    )
    monkeypatch.setattr(workloads.gevent, "sleep", lambda _: None)
    user = workloads.AIUser(Environment())
    user.client = client

    user.on_start()
    user.generate_reply()
    user.generate_reply()

    submit = client.calls[3][2]
    assert submit["headers"] == {"X-CSRF-Token": "csrf-value"}
    UUID(submit["json"]["operation_id"])
    assert [item["name"] for item in fired] == [
        "ai::accepted-to-claimed",
        "ai::end-to-end",
    ]
    assert all(item["exception"] is None for item in fired)
    assert client.responses == []
    user.on_stop()
