from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import text

from itime.database import Database
from performance.contracts import RequestStat, ResourceSummary
from performance.report import evaluate_gates

AI_STANDARD_USERS = 20
AI_JOB_USERS = 20
AI_BASELINE_SECONDS = 60
AI_ACCEPT_P95_LIMIT_MS = 1000
AI_CLAIM_P95_LIMIT_MS = 30_000
AI_SSE_VISIBLE_P95_LIMIT_MS = 2000
AI_END_TO_END_P99_LIMIT_MS = 60_000
AI_MAX_DEGRADATION_RATIO = 2.0


@dataclass(frozen=True)
class AIIsolationState:
    jobs: int
    distinct_owners: int
    queued: int
    running: int
    succeeded: int
    failed: int
    timed_out: int
    cancelled: int
    attempts: int
    succeeded_attempts: int
    provider_costs: int
    reservations_reserved: int
    reservations_settled: int
    reservations_released: int
    usage_ledger_entries: int
    inconsistent_jobs: int
    claimed_p95_ms: float
    end_to_end_p99_ms: float


@dataclass(frozen=True)
class AIIsolationSummary:
    standard_users: int
    ai_users: int
    duration_seconds: int
    passed: bool
    violations: tuple[str, ...]
    degradation_ratios: dict[str, float]
    initial: AIIsolationState
    final: AIIsolationState
    ordinary_baseline_requests: tuple[RequestStat, ...]
    ordinary_active_requests: tuple[RequestStat, ...]
    ai_requests: tuple[RequestStat, ...]
    baseline_resources: ResourceSummary
    active_resources: ResourceSummary


STATE_QUERY = """
    WITH jobs AS (
      SELECT * FROM ai_jobs WHERE capability='conversation'
    ),
    job_checks AS (
      SELECT
        job.id,
        job.status,
        count(DISTINCT attempt.id) AS attempts,
        count(DISTINCT cost.id) AS costs,
        count(DISTINCT reservation.id) AS reservations,
        count(DISTINCT ledger.id) AS ledgers,
        bool_and(
          reservation.status='settled'
          AND cost.attempt_status='succeeded'
          AND ledger.kind='usage_charge'
          AND reservation.actual_cost_nanos=cost.total_cost_nanos
          AND reservation.charged_nanos=-ledger.delta_nanos
          AND reservation.provider_cost_id=cost.id
          AND ledger.provider_cost_id=cost.id
          AND ledger.reservation_id=reservation.id
        ) AS amounts_match
      FROM jobs AS job
      LEFT JOIN ai_job_attempts AS attempt ON attempt.job_id=job.id
      LEFT JOIN ai_provider_costs AS cost ON cost.job_id=job.id
      LEFT JOIN ai_quota_reservations AS reservation
        ON reservation.job_id=job.id
      LEFT JOIN ai_quota_ledger_entries AS ledger
        ON ledger.job_id=job.id AND ledger.kind='usage_charge'
      GROUP BY job.id, job.status
    )
    SELECT
      (SELECT count(*) FROM jobs) AS jobs,
      (SELECT count(DISTINCT owner_id) FROM jobs) AS distinct_owners,
      (SELECT count(*) FROM jobs WHERE status='queued') AS queued,
      (SELECT count(*) FROM jobs WHERE status='running') AS running,
      (SELECT count(*) FROM jobs WHERE status='succeeded') AS succeeded,
      (SELECT count(*) FROM jobs WHERE status='failed') AS failed,
      (SELECT count(*) FROM jobs WHERE status='timed_out') AS timed_out,
      (SELECT count(*) FROM jobs WHERE status='cancelled') AS cancelled,
      (SELECT count(*) FROM ai_job_attempts AS attempt
       JOIN jobs AS job ON job.id=attempt.job_id) AS attempts,
      (SELECT count(*) FROM ai_job_attempts AS attempt
       JOIN jobs AS job ON job.id=attempt.job_id
       WHERE attempt.status='succeeded') AS succeeded_attempts,
      (SELECT count(*) FROM ai_provider_costs AS cost
       JOIN jobs AS job ON job.id=cost.job_id) AS provider_costs,
      (SELECT count(*) FROM ai_quota_reservations AS reservation
       JOIN jobs AS job ON job.id=reservation.job_id
       WHERE reservation.status='reserved') AS reservations_reserved,
      (SELECT count(*) FROM ai_quota_reservations AS reservation
       JOIN jobs AS job ON job.id=reservation.job_id
       WHERE reservation.status='settled') AS reservations_settled,
      (SELECT count(*) FROM ai_quota_reservations AS reservation
       JOIN jobs AS job ON job.id=reservation.job_id
       WHERE reservation.status='released') AS reservations_released,
      (SELECT count(*) FROM ai_quota_ledger_entries AS ledger
       JOIN jobs AS job ON job.id=ledger.job_id
       WHERE ledger.kind='usage_charge') AS usage_ledger_entries,
      (SELECT count(*) FROM job_checks
       WHERE status <> 'succeeded'
          OR attempts <> 1
          OR costs <> 1
          OR reservations <> 1
          OR ledgers <> 1
          OR amounts_match IS NOT TRUE) AS inconsistent_jobs,
      COALESCE((
        SELECT percentile_cont(0.95) WITHIN GROUP (
          ORDER BY EXTRACT(EPOCH FROM (attempt.started_at-job.created_at))*1000
        )
        FROM jobs AS job
        JOIN ai_job_attempts AS attempt
          ON attempt.job_id=job.id AND attempt.attempt_number=1
      ), 0) AS claimed_p95_ms,
      COALESCE((
        SELECT percentile_cont(0.99) WITHIN GROUP (
          ORDER BY EXTRACT(EPOCH FROM (job.finished_at-job.created_at))*1000
        )
        FROM jobs AS job
        WHERE job.finished_at IS NOT NULL
      ), 0) AS end_to_end_p99_ms
"""


def _state_from_mapping(value: Mapping[str, object]) -> AIIsolationState:
    integer_fields = (
        "jobs",
        "distinct_owners",
        "queued",
        "running",
        "succeeded",
        "failed",
        "timed_out",
        "cancelled",
        "attempts",
        "succeeded_attempts",
        "provider_costs",
        "reservations_reserved",
        "reservations_settled",
        "reservations_released",
        "usage_ledger_entries",
        "inconsistent_jobs",
    )
    try:
        values = {name: int(value[name]) for name in integer_fields}
        return AIIsolationState(
            **values,
            claimed_p95_ms=float(value["claimed_p95_ms"]),
            end_to_end_p99_ms=float(value["end_to_end_p99_ms"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("AI 隔离状态字段无效") from exc


async def read_ai_isolation_state(database: Database) -> AIIsolationState:
    async with database.sessions() as session:
        row = (await session.execute(text(STATE_QUERY))).one_or_none()
    if row is None:
        raise RuntimeError("AI 隔离状态查询未返回数据")
    return _state_from_mapping(row._mapping)


def parse_ai_isolation_state(value: object) -> AIIsolationState:
    if not isinstance(value, Mapping):
        raise ValueError("AI 隔离状态必须是 JSON object")
    return _state_from_mapping(value)


def _request_by_name(
    requests: Sequence[RequestStat],
    name: str,
) -> RequestStat | None:
    return next((item for item in requests if item.name == name), None)


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


def _delta(
    initial: AIIsolationState,
    final: AIIsolationState,
    field: str,
) -> int:
    return int(getattr(final, field)) - int(getattr(initial, field))


def build_ai_isolation_summary(
    *,
    initial: AIIsolationState,
    final: AIIsolationState,
    ordinary_baseline_requests: tuple[RequestStat, ...],
    ordinary_active_requests: tuple[RequestStat, ...],
    ai_requests: tuple[RequestStat, ...],
    baseline_resources: ResourceSummary,
    active_resources: ResourceSummary,
    duration_seconds: int,
) -> AIIsolationSummary:
    violations: list[str] = []
    expected_deltas = {
        "jobs": AI_JOB_USERS,
        "distinct_owners": AI_JOB_USERS,
        "succeeded": AI_JOB_USERS,
        "attempts": AI_JOB_USERS,
        "succeeded_attempts": AI_JOB_USERS,
        "provider_costs": AI_JOB_USERS,
        "reservations_settled": AI_JOB_USERS,
        "usage_ledger_entries": AI_JOB_USERS,
    }
    for field, expected in expected_deltas.items():
        actual = _delta(initial, final, field)
        if actual != expected:
            violations.append(f"{field} delta {actual}, expected {expected}")
    for field in (
        "queued",
        "running",
        "failed",
        "timed_out",
        "cancelled",
        "reservations_reserved",
        "reservations_released",
        "inconsistent_jobs",
    ):
        actual = _delta(initial, final, field)
        if actual:
            violations.append(f"{field} delta {actual}, expected 0")

    ordinary_baseline_gate = evaluate_gates(
        scenario="peak",
        users=AI_STANDARD_USERS,
        requests=ordinary_baseline_requests,
        resources=baseline_resources,
        invariants={},
    )
    ordinary_active_gate = evaluate_gates(
        scenario="peak",
        users=AI_STANDARD_USERS,
        requests=ordinary_active_requests,
        resources=active_resources,
        invariants={},
    )
    violations.extend(f"ordinary baseline: {item}" for item in ordinary_baseline_gate.failures)
    violations.extend(f"ordinary with AI: {item}" for item in ordinary_active_gate.failures)
    baseline_p95 = _category_p95(ordinary_baseline_requests)
    active_p95 = _category_p95(ordinary_active_requests)
    ratios: dict[str, float] = {}
    for category in ("read", "write"):
        baseline_value = baseline_p95.get(category)
        active_value = active_p95.get(category)
        if baseline_value is None or active_value is None:
            violations.append(f"{category} category missing from AI isolation comparison")
            continue
        ratio = active_value / max(baseline_value, 0.000001)
        ratios[category] = ratio
        if ratio > AI_MAX_DEGRADATION_RATIO:
            violations.append(
                f"{category} p95 degradation {ratio:.3f}x > {AI_MAX_DEGRADATION_RATIO:.1f}x"
            )

    metric_limits = (
        ("ai::messages.accept", "p95_ms", AI_ACCEPT_P95_LIMIT_MS),
        (
            "ai::accepted-to-claimed",
            "p95_ms",
            AI_CLAIM_P95_LIMIT_MS,
        ),
        (
            "ai::sse-visible-lag",
            "p95_ms",
            AI_SSE_VISIBLE_P95_LIMIT_MS,
        ),
        ("ai::end-to-end", "p99_ms", AI_END_TO_END_P99_LIMIT_MS),
    )
    for name, percentile, limit in metric_limits:
        request = _request_by_name(ai_requests, name)
        if request is None or request.count != AI_JOB_USERS:
            actual = 0 if request is None else request.count
            violations.append(f"{name} sample count {actual}, expected {AI_JOB_USERS}")
            continue
        value = float(getattr(request, percentile))
        if value > limit:
            violations.append(f"{name} {percentile} {value:g}ms > {limit}ms")
        if request.failures:
            violations.append(f"{name} failures {request.failures}")

    if final.claimed_p95_ms > AI_CLAIM_P95_LIMIT_MS:
        violations.append(
            f"database claimed p95 {final.claimed_p95_ms:g}ms > {AI_CLAIM_P95_LIMIT_MS}ms"
        )
    if final.end_to_end_p99_ms > AI_END_TO_END_P99_LIMIT_MS:
        violations.append(
            f"database end-to-end p99 {final.end_to_end_p99_ms:g}ms > "
            f"{AI_END_TO_END_P99_LIMIT_MS}ms"
        )

    return AIIsolationSummary(
        standard_users=AI_STANDARD_USERS,
        ai_users=AI_JOB_USERS,
        duration_seconds=duration_seconds,
        passed=not violations,
        violations=tuple(dict.fromkeys(violations)),
        degradation_ratios=ratios,
        initial=initial,
        final=final,
        ordinary_baseline_requests=ordinary_baseline_requests,
        ordinary_active_requests=ordinary_active_requests,
        ai_requests=ai_requests,
        baseline_resources=baseline_resources,
        active_resources=active_resources,
    )
