import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import select, text

from itime.auth.models import User
from itime.calendar.models import CalendarSource, CalendarSubscription
from itime.calendar.subscription_service import (
    encrypt_subscription_url,
    subscription_url_hmac,
)
from itime.config import Settings
from itime.data_export.models import DataExportJob
from itime.database import Database
from itime.tasks.models import Task, TaskLink
from performance.contracts import RequestStat, ResourceSummary
from performance.report import evaluate_gates
from performance.seed import require_safe_seed_settings
from performance.seed_core import TIME_ZONE, stable_uuid

QUEUE_BACKLOG_SIZE = 50
QUEUE_DRAIN_TIMEOUT_SECONDS = 180
QUEUE_MINIMUM_OBSERVATION_SECONDS = 30
QUEUE_POLL_SECONDS = 0.25
QUEUE_USERS = 20
QUEUE_SPAWN_RATE = 4.0


class QueueKind(StrEnum):
    SUBSCRIPTIONS = "subscriptions"
    PUBLIC_LINKS = "public_links"
    EXPORTS = "exports"


QUEUE_SERVICES = {
    QueueKind.SUBSCRIPTIONS: "subscription-worker",
    QueueKind.PUBLIC_LINKS: "public-link-worker",
    QueueKind.EXPORTS: "export-worker",
}

QUEUE_STATE_QUERIES = {
    QueueKind.SUBSCRIPTIONS: """
        SELECT
          count(*) FILTER (WHERE refresh_status='queued') AS queued,
          count(*) FILTER (WHERE refresh_status='running') AS running,
          count(*) FILTER (WHERE refresh_status='idle') AS succeeded,
          count(*) FILTER (WHERE refresh_status='error') AS failed,
          min(next_refresh_at) FILTER (WHERE refresh_status='queued') AS oldest
        FROM calendar_subscriptions
    """,
    QueueKind.PUBLIC_LINKS: """
        SELECT
          count(*) FILTER (WHERE fetch_status='queued') AS queued,
          count(*) FILTER (WHERE fetch_status='running') AS running,
          count(*) FILTER (WHERE fetch_status='ready') AS succeeded,
          count(*) FILTER (WHERE fetch_status='failed') AS failed,
          min(available_at) FILTER (WHERE fetch_status='queued') AS oldest
        FROM task_links
    """,
    QueueKind.EXPORTS: """
        SELECT
          count(*) FILTER (WHERE status='queued') AS queued,
          count(*) FILTER (WHERE status='running') AS running,
          count(*) FILTER (WHERE status='succeeded') AS succeeded,
          count(*) FILTER (WHERE status IN ('failed','expired')) AS failed,
          min(available_at) FILTER (WHERE status='queued') AS oldest
        FROM data_export_jobs
    """,
}


@dataclass(frozen=True)
class QueueState:
    queued: int
    running: int
    succeeded: int
    failed: int
    oldest: str | None

    @property
    def pending(self) -> int:
        return self.queued + self.running


@dataclass(frozen=True)
class QueueDrainObservation:
    final: QueueState
    drained: bool
    drain_seconds: float


@dataclass(frozen=True)
class QueueTypeResult:
    kind: str
    service: str
    expected: int
    started_at: str
    ended_at: str
    observation_seconds: float
    drain_seconds: float
    drained: bool
    initial: QueueState
    final: QueueState
    throughput_per_second: float
    oldest_remaining_age_seconds: float | None
    requests: tuple[RequestStat, ...]
    resources: ResourceSummary
    violations: tuple[str, ...]


@dataclass(frozen=True)
class QueueRunSummary:
    users: int
    backlog_per_type: int
    drain_timeout_seconds: int
    minimum_observation_seconds: int
    passed: bool
    violations: tuple[str, ...]
    requests: tuple[RequestStat, ...]
    resources: ResourceSummary
    queues: tuple[QueueTypeResult, ...]


def _queue_state(row: object) -> QueueState:
    if row is None:
        raise RuntimeError("队列状态查询未返回数据")
    mapping = row._mapping  # type: ignore[attr-defined]
    oldest = mapping["oldest"]
    return QueueState(
        queued=int(mapping["queued"]),
        running=int(mapping["running"]),
        succeeded=int(mapping["succeeded"]),
        failed=int(mapping["failed"]),
        oldest=oldest.isoformat() if oldest is not None else None,
    )


async def read_queue_state(
    database: Database,
    kind: QueueKind,
) -> QueueState:
    async with database.sessions() as session:
        row = (await session.execute(text(QUEUE_STATE_QUERIES[kind]))).one_or_none()
    return _queue_state(row)


async def _active_owner_ids(
    database: Database,
    *,
    limit: int,
) -> tuple[UUID, ...]:
    async with database.sessions() as session:
        owner_ids = tuple(
            await session.scalars(
                select(User.id)
                .join(Task, Task.owner_id == User.id)
                .where(
                    User.status == "active",
                    Task.archived_at.is_(None),
                    Task.deleted_at.is_(None),
                    Task.purged_at.is_(None),
                )
                .distinct()
                .order_by(User.id)
                .limit(limit)
            )
        )
    if len(owner_ids) != limit:
        raise RuntimeError(f"队列场景需要 {limit} 个活跃数据账号，实际 {len(owner_ids)}")
    return owner_ids


async def _prepare_subscriptions(
    database: Database,
    settings: Settings,
    owner_ids: Sequence[UUID],
    *,
    seed: int,
    upstream_origin: str,
) -> None:
    current = datetime.now(UTC)
    async with database.transaction() as session:
        for index, owner_id in enumerate(owner_ids):
            source_id = stable_uuid(
                "queue",
                seed,
                "subscription-source",
                index,
            )
            source_url = f"{upstream_origin}/queue-{index}.ics"
            session.add(
                CalendarSource(
                    id=source_id,
                    owner_id=owner_id,
                    kind="ics_subscription",
                    name=f"性能队列订阅 {index + 1}",
                    deleted_at=None,
                    purge_after=None,
                    purged_at=None,
                    version=1,
                    created_at=current,
                    updated_at=current,
                )
            )
            session.add(
                CalendarSubscription(
                    source_id=source_id,
                    owner_id=owner_id,
                    encrypted_url=encrypt_subscription_url(
                        settings,
                        source_url,
                    ),
                    url_hmac=subscription_url_hmac(settings, source_url),
                    display_url=source_url,
                    display_host="host.docker.internal",
                    default_time_zone=TIME_ZONE,
                    lifecycle_status="active",
                    refresh_status="queued",
                    refresh_interval_minutes=360,
                    next_refresh_at=current,
                    fetch_revision=1,
                    generation=0,
                    attempt_count=0,
                    lease_id=None,
                    lease_until=None,
                    consecutive_failures=0,
                    version=1,
                    created_at=current,
                    updated_at=current,
                )
            )


async def _prepare_public_links(
    database: Database,
    owner_ids: Sequence[UUID],
) -> None:
    current = datetime.now(UTC)
    async with database.transaction() as session:
        links = list(
            await session.scalars(
                select(TaskLink)
                .join(
                    Task,
                    (Task.id == TaskLink.task_id) & (Task.owner_id == TaskLink.owner_id),
                )
                .where(
                    TaskLink.owner_id.in_(owner_ids),
                    TaskLink.fetch_status == "ready",
                    Task.archived_at.is_(None),
                    Task.deleted_at.is_(None),
                    Task.purged_at.is_(None),
                )
                .distinct(TaskLink.owner_id)
                .order_by(TaskLink.owner_id, TaskLink.id)
            )
        )
        if len(links) != len(owner_ids):
            raise RuntimeError("公开链接队列缺少每账号一条可刷新链接")
        for link in links:
            link.fetch_status = "queued"
            link.fetch_revision += 1
            link.attempt_count = 0
            link.available_at = current
            link.lease_id = None
            link.lease_until = None
            link.last_error_code = None
            link.last_error_message = None
            link.updated_at = current


async def _prepare_exports(
    database: Database,
    owner_ids: Sequence[UUID],
    *,
    seed: int,
) -> None:
    current = datetime.now(UTC)
    async with database.transaction() as session:
        session.add_all(
            [
                DataExportJob(
                    id=stable_uuid("queue", seed, "export", index),
                    owner_id=owner_id,
                    status="queued",
                    schema_version=1,
                    attempt_count=0,
                    max_attempts=3,
                    available_at=current,
                    created_at=current,
                    updated_at=current,
                )
                for index, owner_id in enumerate(owner_ids)
            ]
        )


async def prepare_queue_backlog(
    database: Database,
    settings: Settings,
    kind: QueueKind,
    *,
    count: int = QUEUE_BACKLOG_SIZE,
    seed: int = 20261005,
    upstream_origin: str,
) -> QueueState:
    require_safe_seed_settings(settings)
    if count < 1:
        raise ValueError("队列积压数量必须为正数")
    before = await read_queue_state(database, kind)
    if before.pending:
        raise RuntimeError(f"{kind.value} 队列在播种前不是空闲状态")
    owner_ids = await _active_owner_ids(database, limit=count)
    if kind == QueueKind.SUBSCRIPTIONS:
        await _prepare_subscriptions(
            database,
            settings,
            owner_ids,
            seed=seed,
            upstream_origin=upstream_origin,
        )
    elif kind == QueueKind.PUBLIC_LINKS:
        await _prepare_public_links(database, owner_ids)
    elif kind == QueueKind.EXPORTS:
        await _prepare_exports(database, owner_ids, seed=seed)
    else:
        raise ValueError(f"不支持的队列类型：{kind}")
    prepared = await read_queue_state(database, kind)
    if prepared.queued - before.queued != count or prepared.running:
        raise RuntimeError(f"{kind.value} 队列积压数量与预期不一致")
    return prepared


async def wait_for_queue_drain(
    database: Database,
    kind: QueueKind,
    initial: QueueState,
    *,
    expected: int,
    timeout_seconds: float = QUEUE_DRAIN_TIMEOUT_SECONDS,
    poll_seconds: float = QUEUE_POLL_SECONDS,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> QueueDrainObservation:
    started = monotonic()
    deadline = started + timeout_seconds
    final = initial
    while True:
        final = await read_queue_state(database, kind)
        terminal_delta = final.succeeded + final.failed - initial.succeeded - initial.failed
        if final.pending == 0 and terminal_delta >= expected:
            return QueueDrainObservation(
                final=final,
                drained=True,
                drain_seconds=monotonic() - started,
            )
        if monotonic() >= deadline:
            return QueueDrainObservation(
                final=final,
                drained=False,
                drain_seconds=monotonic() - started,
            )
        await sleep(poll_seconds)


def build_queue_type_result(
    *,
    kind: QueueKind,
    initial: QueueState,
    observation: QueueDrainObservation,
    started_at: datetime,
    ended_at: datetime,
    requests: tuple[RequestStat, ...],
    resources: ResourceSummary,
    expected: int = QUEUE_BACKLOG_SIZE,
) -> QueueTypeResult:
    success_delta = observation.final.succeeded - initial.succeeded
    failed_delta = observation.final.failed - initial.failed
    elapsed = max(
        0.000001,
        (ended_at - started_at).total_seconds(),
    )
    violations: list[str] = []
    if initial.queued != expected or initial.running:
        violations.append(
            f"initial queue state queued={initial.queued} "
            f"running={initial.running}, expected queued={expected}"
        )
    if not observation.drained:
        violations.append(f"queue did not drain within {QUEUE_DRAIN_TIMEOUT_SECONDS}s")
    if observation.final.pending:
        violations.append(
            f"queue remaining queued={observation.final.queued} running={observation.final.running}"
        )
    if success_delta != expected:
        violations.append(f"successful terminal delta {success_delta}, expected {expected}")
    if failed_delta:
        violations.append(f"failed terminal delta {failed_delta}")
    gate = evaluate_gates(
        scenario="queues",
        users=QUEUE_USERS,
        requests=requests,
        resources=resources,
        invariants={},
    )
    violations.extend(gate.failures)
    oldest_age = None
    if observation.final.oldest is not None:
        oldest_age = max(
            0.0,
            (ended_at - datetime.fromisoformat(observation.final.oldest)).total_seconds(),
        )
    return QueueTypeResult(
        kind=kind.value,
        service=QUEUE_SERVICES[kind],
        expected=expected,
        started_at=started_at.isoformat(),
        ended_at=ended_at.isoformat(),
        observation_seconds=elapsed,
        drain_seconds=observation.drain_seconds,
        drained=observation.drained,
        initial=initial,
        final=observation.final,
        throughput_per_second=success_delta
        / max(
            observation.drain_seconds,
            0.000001,
        ),
        oldest_remaining_age_seconds=oldest_age,
        requests=requests,
        resources=resources,
        violations=tuple(dict.fromkeys(violations)),
    )


def _aggregate_requests(
    results: Sequence[QueueTypeResult],
) -> tuple[RequestStat, ...]:
    grouped: dict[str, list[RequestStat]] = {}
    for result in results:
        for request in result.requests:
            grouped.setdefault(request.name, []).append(request)
    return tuple(
        RequestStat(
            name=name,
            count=sum(item.count for item in rows),
            failures=sum(item.failures for item in rows),
            p50_ms=max(item.p50_ms for item in rows),
            p95_ms=max(item.p95_ms for item in rows),
            p99_ms=max(item.p99_ms for item in rows),
        )
        for name, rows in sorted(grouped.items())
    )


def _aggregate_resources(
    results: Sequence[QueueTypeResult],
) -> ResourceSummary:
    resources = [result.resources for result in results]
    return ResourceSummary(
        cpu_mean_ratio=max(item.cpu_mean_ratio for item in resources),
        cpu_peak_over_90_seconds=max(item.cpu_peak_over_90_seconds for item in resources),
        memory_peak_ratio=max(item.memory_peak_ratio for item in resources),
        memory_growth_ratio=max(item.memory_growth_ratio for item in resources),
        database_connection_peak_ratio=max(
            item.database_connection_peak_ratio for item in resources
        ),
        blocked_over_two_seconds=max(item.blocked_over_two_seconds for item in resources),
        pool_timeouts=max(item.pool_timeouts for item in resources),
        server_5xx=max(item.server_5xx for item in resources),
        sampling_gap_seconds=max(item.sampling_gap_seconds for item in resources),
    )


def build_queue_run_summary(
    results: Sequence[QueueTypeResult],
) -> QueueRunSummary:
    expected_kinds = tuple(kind.value for kind in QueueKind)
    actual_kinds = tuple(result.kind for result in results)
    if actual_kinds != expected_kinds:
        raise ValueError(f"队列结果顺序无效：期望 {expected_kinds}，实际 {actual_kinds}")
    violations = tuple(
        f"{result.kind}: {violation}" for result in results for violation in result.violations
    )
    return QueueRunSummary(
        users=QUEUE_USERS,
        backlog_per_type=QUEUE_BACKLOG_SIZE,
        drain_timeout_seconds=QUEUE_DRAIN_TIMEOUT_SECONDS,
        minimum_observation_seconds=QUEUE_MINIMUM_OBSERVATION_SECONDS,
        passed=not violations,
        violations=violations,
        requests=_aggregate_requests(results),
        resources=_aggregate_resources(results),
        queues=tuple(results),
    )
