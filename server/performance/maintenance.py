import argparse
import asyncio
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text

from itime.calendar.lifecycle_service import cleanup_expired_calendar_items
from itime.config import Settings, get_settings
from itime.database import Database
from itime.execution.difficulty_service import cleanup_expired_difficulties
from itime.review.memory_service import cleanup_expired_memories
from itime.sync.service import cleanup_expired_events
from itime.tasks.lifecycle_service import cleanup_expired_tasks
from performance.contracts import RequestStat, ResourceSummary
from performance.report import evaluate_gates
from performance.seed import require_safe_seed_settings

MAINTENANCE_BATCH_SIZE = 100
MAINTENANCE_SYNC_BATCH_SIZE = 1000
MAINTENANCE_USERS = 20
MAINTENANCE_SPAWN_RATE = 4.0
MAINTENANCE_WINDOW_SECONDS = 60
MAINTENANCE_MAX_DEGRADATION_RATIO = 2.0


@dataclass(frozen=True)
class MaintenanceState:
    tasks_due: int
    tasks_purged: int
    calendar_series_due: int
    calendar_series_purged: int
    difficulties_due: int
    difficulties_purged: int
    memories_due: int
    memories_purged: int
    expired_sync_events: int
    sync_events: int


@dataclass(frozen=True)
class CleanupBatchResult:
    counts: dict[str, int]
    durations_seconds: dict[str, float]
    total_seconds: float


@dataclass(frozen=True)
class MaintenanceRunSummary:
    users: int
    baseline_seconds: int
    maintenance_seconds: int
    maximum_degradation_ratio: float
    passed: bool
    violations: tuple[str, ...]
    degradation_ratios: dict[str, float]
    initial: MaintenanceState
    final: MaintenanceState
    cleanup: CleanupBatchResult
    baseline_requests: tuple[RequestStat, ...]
    maintenance_requests: tuple[RequestStat, ...]
    baseline_resources: ResourceSummary
    maintenance_resources: ResourceSummary


def parse_cleanup_batch_result(value: object) -> CleanupBatchResult:
    if not isinstance(value, Mapping):
        raise ValueError("维护批次结果必须是 JSON object")
    counts = value.get("counts")
    durations = value.get("durations_seconds")
    if not isinstance(counts, Mapping) or not isinstance(durations, Mapping):
        raise ValueError("维护批次结果缺少计数或耗时")
    try:
        return CleanupBatchResult(
            counts={str(key): int(item) for key, item in counts.items()},
            durations_seconds={str(key): float(item) for key, item in durations.items()},
            total_seconds=float(value["total_seconds"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("维护批次结果字段无效") from exc


STATE_QUERY = """
    SELECT
      (SELECT count(*) FROM tasks
       WHERE deleted_at IS NOT NULL
         AND purge_after <= now()
         AND purged_at IS NULL) AS tasks_due,
      (SELECT count(*) FROM tasks
       WHERE purged_at IS NOT NULL) AS tasks_purged,
      (SELECT count(*) FROM calendar_series
       WHERE deleted_at IS NOT NULL
         AND purge_after <= now()
         AND purged_at IS NULL) AS calendar_series_due,
      (SELECT count(*) FROM calendar_series
       WHERE purged_at IS NOT NULL) AS calendar_series_purged,
      (SELECT count(*) FROM difficulty_records
       WHERE deleted_at IS NOT NULL
         AND purge_after <= now()
         AND purged_at IS NULL) AS difficulties_due,
      (SELECT count(*) FROM difficulty_records
       WHERE purged_at IS NOT NULL) AS difficulties_purged,
      (SELECT count(*) FROM memories
       WHERE deleted_at IS NOT NULL
         AND purge_after <= now()
         AND purged_at IS NULL) AS memories_due,
      (SELECT count(*) FROM memories
       WHERE purged_at IS NOT NULL) AS memories_purged,
      (SELECT count(*) FROM change_events
       WHERE changed_at <= now() - interval '30 days') AS expired_sync_events,
      (SELECT count(*) FROM change_events) AS sync_events
"""


def _maintenance_state(row: object) -> MaintenanceState:
    if row is None:
        raise RuntimeError("维护状态查询未返回数据")
    mapping = row._mapping  # type: ignore[attr-defined]
    return MaintenanceState(
        **{name: int(mapping[name]) for name in MaintenanceState.__dataclass_fields__}
    )


async def read_maintenance_state(database: Database) -> MaintenanceState:
    async with database.sessions() as session:
        row = (await session.execute(text(STATE_QUERY))).one_or_none()
    return _maintenance_state(row)


async def _mark_due(
    database: Database,
    *,
    statement: str,
    count: int,
    current: datetime,
) -> int:
    async with database.transaction() as session:
        rows = (
            await session.execute(
                text(statement),
                {
                    "count": count,
                    "deleted_at": current - timedelta(days=31),
                    "purge_after": current - timedelta(seconds=1),
                    "changed_at": current - timedelta(days=31),
                },
            )
        ).all()
    return len(rows)


async def prepare_maintenance_batch(
    database: Database,
    settings: Settings,
    *,
    count: int = MAINTENANCE_BATCH_SIZE,
    sync_count: int = MAINTENANCE_SYNC_BATCH_SIZE,
) -> MaintenanceState:
    require_safe_seed_settings(settings)
    if count < 1 or count > MAINTENANCE_BATCH_SIZE:
        raise ValueError("维护清理数量必须位于 1 到 100")
    if sync_count < 1 or sync_count > MAINTENANCE_SYNC_BATCH_SIZE:
        raise ValueError("同步清理数量必须位于 1 到 1000")
    current = datetime.now(UTC)
    statements = (
        """
        WITH candidates AS (
          SELECT id FROM tasks
          WHERE deleted_at IS NOT NULL AND purged_at IS NULL
          ORDER BY id LIMIT :count
        )
        UPDATE tasks
        SET purge_after=:purge_after, updated_at=:purge_after
        WHERE id IN (SELECT id FROM candidates)
        RETURNING id
        """,
        """
        WITH candidates AS (
          SELECT id FROM calendar_series
          WHERE deleted_at IS NULL AND purged_at IS NULL
          ORDER BY id LIMIT :count
        )
        UPDATE calendar_series
        SET deleted_at=:deleted_at, purge_after=:purge_after,
            updated_at=:purge_after
        WHERE id IN (SELECT id FROM candidates)
        RETURNING id
        """,
        """
        WITH candidates AS (
          SELECT id FROM difficulty_records
          WHERE deleted_at IS NOT NULL AND purged_at IS NULL
          ORDER BY id LIMIT :count
        )
        UPDATE difficulty_records
        SET purge_after=:purge_after, updated_at=:purge_after
        WHERE id IN (SELECT id FROM candidates)
        RETURNING id
        """,
        """
        WITH candidates AS (
          SELECT id FROM memories
          WHERE deleted_at IS NOT NULL AND purged_at IS NULL
          ORDER BY id LIMIT :count
        )
        UPDATE memories
        SET purge_after=:purge_after, updated_at=:purge_after
        WHERE id IN (SELECT id FROM candidates)
        RETURNING id
        """,
    )
    prepared = [
        await _mark_due(
            database,
            statement=statement,
            count=count,
            current=current,
        )
        for statement in statements
    ]
    sync_prepared = await _mark_due(
        database,
        statement="""
            WITH candidates AS (
              SELECT sequence FROM change_events
              ORDER BY sequence LIMIT :count
            )
            UPDATE change_events
            SET changed_at=:changed_at
            WHERE sequence IN (SELECT sequence FROM candidates)
            RETURNING sequence
        """,
        count=sync_count,
        current=current,
    )
    if prepared != [count] * len(statements) or sync_prepared != sync_count:
        raise RuntimeError(f"维护候选数量不足：批次={prepared}，同步={sync_prepared}")
    initial = await read_maintenance_state(database)
    if (
        initial.tasks_due != count
        or initial.calendar_series_due != count
        or initial.difficulties_due != count
        or initial.memories_due != count
        or initial.expired_sync_events != sync_count
    ):
        raise RuntimeError("维护候选状态与预期不一致")
    return initial


async def run_cleanup_batch(
    database: Database,
    settings: Settings,
    *,
    count: int = MAINTENANCE_BATCH_SIZE,
    sync_count: int = MAINTENANCE_SYNC_BATCH_SIZE,
) -> CleanupBatchResult:
    require_safe_seed_settings(settings)
    counts: dict[str, int] = {}
    durations: dict[str, float] = {}

    async def measured(name: str, operation: Any) -> Any:
        started = time.perf_counter()
        value = await operation
        durations[name] = time.perf_counter() - started
        return value

    total_started = time.perf_counter()
    counts["tasks"] = int(
        await measured(
            "tasks",
            cleanup_expired_tasks(database, limit=count),
        )
    )
    calendar = await measured(
        "calendar",
        cleanup_expired_calendar_items(database, limit=count),
    )
    counts["calendar_series"] = int(calendar["series"])
    counts["calendar_sources"] = int(calendar["sources"])
    counts["difficulties"] = int(
        await measured(
            "difficulties",
            cleanup_expired_difficulties(database, limit=count),
        )
    )
    counts["memories"] = int(
        await measured(
            "memories",
            cleanup_expired_memories(database, limit=count),
        )
    )
    counts["sync_events"] = int(
        await measured(
            "sync_events",
            cleanup_expired_events(
                database,
                retention_days=settings.sync_event_retention_days,
                limit=sync_count,
            ),
        )
    )
    return CleanupBatchResult(
        counts=counts,
        durations_seconds=durations,
        total_seconds=time.perf_counter() - total_started,
    )


def _category_p95(
    requests: Sequence[RequestStat],
) -> dict[str, float]:
    categories: dict[str, float] = {}
    for request in requests:
        if request.count <= 0:
            continue
        category = request.name.partition("::")[0]
        if category not in {"read", "write"}:
            continue
        categories[category] = max(
            categories.get(category, 0),
            request.p95_ms,
        )
    return categories


def build_maintenance_summary(
    *,
    initial: MaintenanceState,
    final: MaintenanceState,
    cleanup: CleanupBatchResult,
    baseline_requests: tuple[RequestStat, ...],
    maintenance_requests: tuple[RequestStat, ...],
    baseline_resources: ResourceSummary,
    maintenance_resources: ResourceSummary,
    count: int = MAINTENANCE_BATCH_SIZE,
    sync_count: int = MAINTENANCE_SYNC_BATCH_SIZE,
) -> MaintenanceRunSummary:
    violations: list[str] = []
    expected_counts = {
        "tasks": count,
        "calendar_series": count,
        "calendar_sources": 0,
        "difficulties": count,
        "memories": count,
        "sync_events": sync_count,
    }
    for name, expected in expected_counts.items():
        actual = cleanup.counts.get(name)
        if actual != expected:
            violations.append(f"{name} cleanup count {actual}, expected {expected}")
    remaining = {
        "tasks": final.tasks_due,
        "calendar_series": final.calendar_series_due,
        "difficulties": final.difficulties_due,
        "memories": final.memories_due,
        "sync_events": final.expired_sync_events,
    }
    violations.extend(
        f"{name} expired rows remaining {value}" for name, value in remaining.items() if value
    )
    deltas = {
        "tasks": final.tasks_purged - initial.tasks_purged,
        "calendar_series": (final.calendar_series_purged - initial.calendar_series_purged),
        "difficulties": (final.difficulties_purged - initial.difficulties_purged),
        "memories": final.memories_purged - initial.memories_purged,
    }
    violations.extend(
        f"{name} terminal delta {value}, expected {count}"
        for name, value in deltas.items()
        if value != count
    )

    baseline_gate = evaluate_gates(
        scenario="steady",
        users=MAINTENANCE_USERS,
        requests=baseline_requests,
        resources=baseline_resources,
        invariants={},
    )
    maintenance_gate = evaluate_gates(
        scenario="maintenance",
        users=MAINTENANCE_USERS,
        requests=maintenance_requests,
        resources=maintenance_resources,
        invariants={},
    )
    violations.extend(f"baseline: {item}" for item in baseline_gate.failures)
    violations.extend(f"maintenance: {item}" for item in maintenance_gate.failures)

    baseline_p95 = _category_p95(baseline_requests)
    maintenance_p95 = _category_p95(maintenance_requests)
    ratios: dict[str, float] = {}
    for category in ("read", "write"):
        baseline_value = baseline_p95.get(category)
        maintenance_value = maintenance_p95.get(category)
        if baseline_value is None or maintenance_value is None:
            violations.append(f"{category} category missing from degradation comparison")
            continue
        ratio = maintenance_value / max(baseline_value, 0.000001)
        ratios[category] = ratio
        if ratio > MAINTENANCE_MAX_DEGRADATION_RATIO:
            violations.append(
                f"{category} p95 degradation {ratio:.3f}x > "
                f"{MAINTENANCE_MAX_DEGRADATION_RATIO:.1f}x"
            )

    return MaintenanceRunSummary(
        users=MAINTENANCE_USERS,
        baseline_seconds=MAINTENANCE_WINDOW_SECONDS,
        maintenance_seconds=MAINTENANCE_WINDOW_SECONDS,
        maximum_degradation_ratio=MAINTENANCE_MAX_DEGRADATION_RATIO,
        passed=not violations,
        violations=tuple(dict.fromkeys(violations)),
        degradation_ratios=ratios,
        initial=initial,
        final=final,
        cleanup=cleanup,
        baseline_requests=baseline_requests,
        maintenance_requests=maintenance_requests,
        baseline_resources=baseline_resources,
        maintenance_resources=maintenance_resources,
    )


async def _main() -> CleanupBatchResult:
    settings = get_settings()
    database = Database(settings)
    try:
        return await run_cleanup_batch(database, settings)
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="执行 iTime 性能测试维护批次")
    parser.parse_args()
    result = asyncio.run(_main())
    print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
