import json
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import gevent
from locust import HttpUser, between, events, task
from locust.clients import HttpSession
from locust.exception import StopUser

ROUTE_NAMES = {
    ("GET", "/api/v1/auth/me"): "account.me",
    ("GET", "/api/v1/calendar/occurrences"): "calendar.occurrences",
    ("GET", "/api/v1/difficulties"): "difficulties.list",
    ("GET", "/api/v1/focus-sessions/current"): "focus.current",
    ("GET", "/api/v1/planning/blocks"): "planning.blocks",
    ("GET", "/api/v1/quota"): "quota.current",
    ("GET", "/api/v1/reviews"): "reviews.list",
    ("GET", "/api/v1/statistics"): "statistics.read",
    ("GET", "/api/v1/sync/changes"): "sync.changes",
    ("GET", "/api/v1/sync/status"): "sync.status",
    ("GET", "/api/v1/tasks"): "tasks.list",
    ("POST", "/api/v1/auth/login"): "login",
    ("POST", "/api/v1/tasks"): "tasks.create",
}


def request_name(method: str, path: str, *, category: str | None = None) -> str:
    normalized_method = method.upper()
    operation = ROUTE_NAMES.get((normalized_method, path))
    if operation is None and normalized_method == "GET" and path.startswith(
        "/api/v1/tasks/"
    ):
        operation = "tasks.detail"
    if operation is None:
        operation = path.removeprefix("/api/v1/").strip("/").replace("/", ".")
    if category is None:
        category = "read" if normalized_method == "GET" else "write"
    return f"{category}::{operation}"


def require_response(
    response: Any,
    *,
    expected_status: int,
    required_keys: tuple[str, ...] = (),
) -> dict[str, Any] | None:
    if response.status_code != expected_status:
        response.failure(
            f"unexpected status {response.status_code}, expected {expected_status}"
        )
        return None
    try:
        payload = response.json()
    except (TypeError, ValueError):
        response.failure("response is not valid JSON")
        return None
    if not isinstance(payload, dict):
        response.failure("response JSON must be an object")
        return None
    missing = tuple(key for key in required_keys if key not in payload)
    if missing:
        response.failure(f"response missing keys: {', '.join(missing)}")
        return None
    response.success()
    return payload


def contains_change(payload: dict[str, Any], entity_id: str) -> bool:
    events_payload = payload.get("events")
    if not isinstance(events_payload, list):
        return False
    return any(
        isinstance(item, dict)
        and item.get("entity_type") == "task"
        and str(item.get("entity_id")) == entity_id
        for item in events_payload
    )


class CredentialPool:
    def __init__(self, credentials: list[dict[str, str]]) -> None:
        self._available = deque(credentials)
        self._claimed: set[str] = set()
        self._lock = threading.Lock()

    @classmethod
    def from_file(
        cls,
        path: Path,
        *,
        dataset: str,
        worker_index: int,
        worker_count: int,
    ) -> "CredentialPool":
        if worker_count < 1 or not 0 <= worker_index < worker_count:
            raise ValueError("Locust worker 分片参数无效")
        payload = json.loads(path.read_text())
        if not isinstance(payload, list):
            raise ValueError("性能凭据文件必须是数组")

        credentials: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in payload:
            if (
                not isinstance(item, dict)
                or set(item) != {"email", "password", "dataset"}
                or not all(isinstance(value, str) and value for value in item.values())
            ):
                raise ValueError("性能凭据项格式无效")
            email = item["email"]
            if email in seen:
                raise ValueError("性能凭据包含重复账号")
            seen.add(email)
            if item["dataset"] == dataset:
                credentials.append(dict(item))
        shard = [
            item
            for index, item in enumerate(credentials)
            if index % worker_count == worker_index
        ]
        return cls(shard)

    def claim(self) -> dict[str, str]:
        with self._lock:
            if not self._available:
                raise RuntimeError("性能账号数量不足，拒绝复用账号")
            credential = self._available.popleft()
            email = credential["email"]
            if email in self._claimed:
                raise RuntimeError("性能账号已被其他虚拟用户占用")
            self._claimed.add(email)
            return credential

    def release(self, credential: dict[str, str]) -> None:
        with self._lock:
            email = credential["email"]
            if email not in self._claimed:
                return
            self._claimed.remove(email)
            self._available.append(credential)


@dataclass
class AccountDataset:
    anchor_date: date
    mutation_sequence: int = 0

    @classmethod
    def from_environment(cls) -> "AccountDataset":
        raw = os.environ.get("ITIME_PERF_ANCHOR_DATE")
        if not raw:
            raise RuntimeError("缺少 ITIME_PERF_ANCHOR_DATE")
        try:
            anchor = date.fromisoformat(raw)
        except ValueError as exc:
            raise RuntimeError("ITIME_PERF_ANCHOR_DATE 必须为 ISO 日期") from exc
        return cls(anchor_date=anchor)

    @property
    def week_range(self) -> dict[str, str]:
        week_start = self.anchor_date - timedelta(days=self.anchor_date.weekday())
        return {
            "start": week_start.isoformat(),
            "end": (week_start + timedelta(days=7)).isoformat(),
        }

    @property
    def history_range(self) -> dict[str, str]:
        return {
            "start": (self.anchor_date - timedelta(days=92)).isoformat(),
            "end": (self.anchor_date + timedelta(days=1)).isoformat(),
        }

    def task_payload(self) -> dict[str, object]:
        self.mutation_sequence += 1
        return {
            "operation_id": str(uuid4()),
            "title": f"性能负载任务 {self.mutation_sequence}",
            "description": "隔离性能测试写入",
            "project_id": None,
            "estimated_minutes": 25,
            "priority": self.mutation_sequence % 4,
            "labels": [],
            "links": [],
            "steps": [],
        }


_credential_pools: dict[tuple[str, str, int, int], CredentialPool] = {}
_credential_pools_lock = threading.Lock()


def reset_credential_pools() -> None:
    with _credential_pools_lock:
        _credential_pools.clear()


def credential_pool(dataset: str) -> CredentialPool:
    raw_path = os.environ.get("ITIME_PERF_CREDENTIALS")
    if not raw_path:
        raise RuntimeError("缺少 ITIME_PERF_CREDENTIALS")
    worker_index = int(os.environ.get("ITIME_PERF_WORKER_INDEX", "0"))
    worker_count = int(os.environ.get("ITIME_PERF_WORKER_COUNT", "1"))
    key = (str(Path(raw_path).resolve()), dataset, worker_index, worker_count)
    with _credential_pools_lock:
        pool = _credential_pools.get(key)
        if pool is None:
            pool = CredentialPool.from_file(
                Path(raw_path),
                dataset=dataset,
                worker_index=worker_index,
                worker_count=worker_count,
            )
            _credential_pools[key] = pool
        return pool


class AuthenticatedUser(HttpUser):
    abstract = True
    host = "http://127.0.0.1"
    wait_time = between(1, 3)
    dataset_kind = "standard"

    credential: dict[str, str] | None = None
    csrf = ""
    sync_cursor = ""

    def request_json(
        self,
        method: str,
        path: str,
        *,
        expected_status: int = 200,
        required_keys: tuple[str, ...] = (),
        category: str | None = None,
        client: HttpSession | None = None,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        active_client = client or self.client
        with active_client.request(
            method,
            path,
            name=request_name(method, path, category=category),
            catch_response=True,
            **kwargs,
        ) as response:
            return require_response(
                response,
                expected_status=expected_status,
                required_keys=required_keys,
            )

    def on_start(self) -> None:
        self.dataset = AccountDataset.from_environment()
        self._pool = credential_pool(self.dataset_kind)
        self.credential = self._pool.claim()
        login = self.request_json(
            "POST",
            "/api/v1/auth/login",
            expected_status=200,
            required_keys=("csrf_token",),
            category="auth",
            json={
                "email": self.credential["email"],
                "password": self.credential["password"],
                "device_name": "M8 性能测试",
            },
        )
        if login is None:
            raise StopUser()
        self.csrf = str(login["csrf_token"])
        initial = self.request_json(
            "GET",
            "/api/v1/sync/changes",
            required_keys=("next_cursor", "reset_required"),
            params={"limit": 200},
        )
        if initial is None:
            raise StopUser()
        self.sync_cursor = str(initial["next_cursor"])

    def on_stop(self) -> None:
        if self.credential is not None:
            self._pool.release(self.credential)
            self.credential = None

    def write_headers(self) -> dict[str, str]:
        return {"X-CSRF-Token": self.csrf}


class StandardUser(AuthenticatedUser):
    weight = 8
    dataset_kind = "standard"

    @task(5)
    def task_list(self) -> None:
        self.request_json(
            "GET",
            "/api/v1/tasks",
            required_keys=("items", "next_cursor"),
            params={"status": "open", "limit": 50},
        )

    @task(2)
    def task_detail(self) -> None:
        listing = self.request_json(
            "GET",
            "/api/v1/tasks",
            required_keys=("items",),
            params={"limit": 1},
        )
        if listing and listing["items"]:
            task_id = listing["items"][0]["id"]
            self.request_json(
                "GET",
                f"/api/v1/tasks/{task_id}",
                required_keys=("task",),
            )

    @task(4)
    def calendar_week(self) -> None:
        self.request_json(
            "GET",
            "/api/v1/calendar/occurrences",
            required_keys=("items", "time_zone"),
            params=self.dataset.week_range,
        )

    @task(2)
    def planning_blocks(self) -> None:
        self.request_json(
            "GET",
            "/api/v1/planning/blocks",
            required_keys=("items", "source_conflicts"),
            params=self.dataset.week_range,
        )

    @task(4)
    def sync_changes(self) -> None:
        payload = self.request_json(
            "GET",
            "/api/v1/sync/changes",
            required_keys=("events", "next_cursor", "reset_required"),
            params={"cursor": self.sync_cursor, "limit": 200},
        )
        if payload is not None:
            self.sync_cursor = str(payload["next_cursor"])

    @task(1)
    def sync_status(self) -> None:
        self.request_json(
            "GET",
            "/api/v1/sync/status",
            required_keys=("server_time", "retention_days"),
        )

    @task(2)
    def statistics(self) -> None:
        self.request_json(
            "GET",
            "/api/v1/statistics",
            required_keys=("summary", "days"),
            params={
                "scope": "week",
                "anchor_date": self.dataset.anchor_date.isoformat(),
            },
        )

    @task(1)
    def current_focus(self) -> None:
        self.request_json(
            "GET",
            "/api/v1/focus-sessions/current",
            required_keys=("focus", "server_now"),
        )

    @task(1)
    def difficulties(self) -> None:
        self.request_json(
            "GET",
            "/api/v1/difficulties",
            required_keys=("items", "next_cursor"),
            params={"limit": 30},
        )

    @task(1)
    def reviews(self) -> None:
        self.request_json(
            "GET",
            "/api/v1/reviews",
            required_keys=("items", "next_cursor"),
            params={"limit": 30},
        )

    @task(1)
    def account_and_quota(self) -> None:
        self.request_json(
            "GET",
            "/api/v1/auth/me",
            required_keys=("user", "session_id", "csrf_token"),
        )
        self.request_json(
            "GET",
            "/api/v1/quota",
            required_keys=("configured", "current", "reservations"),
        )

    @task(1)
    def create_task(self) -> None:
        self.request_json(
            "POST",
            "/api/v1/tasks",
            expected_status=201,
            required_keys=("operation_id", "task"),
            headers=self.write_headers(),
            json=self.dataset.task_payload(),
        )


class HistoryUser(AuthenticatedUser):
    weight = 1
    dataset_kind = "history"

    @task(4)
    def task_page(self) -> None:
        self.request_json(
            "GET",
            "/api/v1/tasks",
            category="history",
            required_keys=("items", "next_cursor"),
            params={"limit": 100},
        )

    @task(4)
    def calendar_range(self) -> None:
        self.request_json(
            "GET",
            "/api/v1/calendar/occurrences",
            category="history",
            required_keys=("items", "time_zone"),
            params=self.dataset.history_range,
        )

    @task(2)
    def statistics_month(self) -> None:
        self.request_json(
            "GET",
            "/api/v1/statistics",
            category="history",
            required_keys=("summary", "days"),
            params={
                "scope": "month",
                "anchor_date": self.dataset.anchor_date.isoformat(),
            },
        )

    @task(2)
    def sync_page(self) -> None:
        payload = self.request_json(
            "GET",
            "/api/v1/sync/changes",
            category="history",
            required_keys=("events", "next_cursor", "reset_required"),
            params={"cursor": self.sync_cursor, "limit": 200},
        )
        if payload is not None:
            self.sync_cursor = str(payload["next_cursor"])


class SyncUser(AuthenticatedUser):
    weight = 1
    dataset_kind = "standard"

    def on_start(self) -> None:
        super().on_start()
        assert self.credential is not None
        self.secondary = HttpSession(
            base_url=self.host,
            request_event=self.environment.events.request,
            user=self,
        )
        login = self.request_json(
            "POST",
            "/api/v1/auth/login",
            expected_status=200,
            required_keys=("csrf_token",),
            category="auth",
            client=self.secondary,
            json={
                "email": self.credential["email"],
                "password": self.credential["password"],
                "device_name": "M8 性能测试设备 B",
            },
        )
        if login is None:
            raise StopUser()
        initial = self.request_json(
            "GET",
            "/api/v1/sync/changes",
            required_keys=("next_cursor", "reset_required"),
            category="sync-probe",
            client=self.secondary,
            params={"limit": 200},
        )
        if initial is None:
            raise StopUser()
        self.secondary_cursor = str(initial["next_cursor"])

    def on_stop(self) -> None:
        secondary = getattr(self, "secondary", None)
        if secondary is not None:
            secondary.close()
        super().on_stop()

    @task
    def cross_device_visibility(self) -> None:
        started = time.perf_counter()
        created = self.request_json(
            "POST",
            "/api/v1/tasks",
            expected_status=201,
            required_keys=("task",),
            headers=self.write_headers(),
            json=self.dataset.task_payload(),
        )
        if created is None:
            return
        entity_id = str(created["task"]["id"])
        deadline = started + 5
        while time.perf_counter() < deadline:
            payload = self.request_json(
                "GET",
                "/api/v1/sync/changes",
                category="sync-probe",
                required_keys=("events", "next_cursor", "reset_required"),
                client=self.secondary,
                params={"cursor": self.secondary_cursor, "limit": 200},
            )
            if payload is None:
                return
            self.secondary_cursor = str(payload["next_cursor"])
            if contains_change(payload, entity_id):
                events.request.fire(
                    request_type="SYNC",
                    name="sync::cross-device-visible",
                    response_time=(time.perf_counter() - started) * 1000,
                    response_length=0,
                    exception=None,
                    context={},
                )
                return
            gevent.sleep(0.25)
        events.request.fire(
            request_type="SYNC",
            name="sync::cross-device-visible",
            response_time=(time.perf_counter() - started) * 1000,
            response_length=0,
            exception=TimeoutError("同步事件超过 5 秒仍不可见"),
            context={},
        )


WORKLOADS = {
    "standard": StandardUser,
    "history": HistoryUser,
    "sync": SyncUser,
}
