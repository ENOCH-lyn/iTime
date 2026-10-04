import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from performance.contracts import RequestStat, ResourceSummary
from performance.report import evaluate_gates

SATURATION_LEVELS = tuple(range(10, 101, 10))
SATURATION_WINDOWS_PER_LEVEL = 3
SATURATION_WINDOW_SECONDS = 60
SATURATION_MINIMUM_USERS = 50
SATURATION_SPAWN_RATE = 10.0


@dataclass(frozen=True)
class SaturationWindowResult:
    users: int
    window: int
    started_at: str
    ended_at: str
    requests: tuple[RequestStat, ...]
    resources: ResourceSummary
    request_violations: tuple[str, ...]
    violations: tuple[str, ...]


@dataclass(frozen=True)
class SaturationLevelResult:
    users: int
    completed: bool
    started_at: str
    ended_at: str
    requests: tuple[RequestStat, ...]
    resources: ResourceSummary
    request_violations: tuple[str, ...]
    violations: tuple[str, ...]


@dataclass(frozen=True)
class SaturationRunSummary:
    levels: tuple[int, ...]
    windows_per_level: int
    window_seconds: int
    minimum_required_users: int
    completed_levels: tuple[int, ...]
    maximum_stable_users: int
    first_unstable_users: int | None
    capacity_boundary_users: int | None
    minimum_capacity_passed: bool
    hard_gate_violations: tuple[str, ...]
    stop_reason: str
    representative: SaturationLevelResult
    level_results: tuple[SaturationLevelResult, ...]
    windows: tuple[SaturationWindowResult, ...]


def healthy_resources() -> ResourceSummary:
    return ResourceSummary(
        cpu_mean_ratio=0,
        cpu_peak_over_90_seconds=0,
        memory_peak_ratio=0,
        memory_growth_ratio=0,
        database_connection_peak_ratio=0,
        blocked_over_two_seconds=0,
        pool_timeouts=0,
        server_5xx=0,
        sampling_gap_seconds=0,
    )


def merge_violations(*groups: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item for group in groups for item in group))


def locust_request_stats(payload: object) -> tuple[RequestStat, ...]:
    if not isinstance(payload, dict) or not isinstance(payload.get("stats"), list):
        raise RuntimeError("Locust 统计响应格式无效")
    requests: list[RequestStat] = []
    for raw in payload["stats"]:
        if not isinstance(raw, dict):
            raise RuntimeError("Locust 请求统计项格式无效")
        name = raw.get("name")
        if name == "Aggregated":
            continue
        if not isinstance(name, str) or not name:
            raise RuntimeError("Locust 请求统计缺少名称")
        try:
            count = int(raw["num_requests"])
            failures = int(raw["num_failures"])
            if count <= 0 and failures <= 0:
                continue
            request = RequestStat(
                name=name,
                count=count,
                failures=failures,
                p50_ms=float(raw["median_response_time"]),
                p95_ms=float(raw["response_time_percentile_0.95"]),
                p99_ms=float(raw["response_time_percentile_0.99"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"Locust 请求统计字段无效：{name}") from exc
        requests.append(request)
    return tuple(sorted(requests, key=lambda item: item.name))


def request_gate_violations(
    users: int,
    requests: Sequence[RequestStat],
) -> tuple[str, ...]:
    result = evaluate_gates(
        scenario="saturation",
        users=users,
        requests=requests,
        resources=healthy_resources(),
        invariants={},
    )
    return merge_violations(result.failures, result.warnings)


def build_saturation_window(
    *,
    users: int,
    window: int,
    started_at: datetime,
    ended_at: datetime,
    requests: tuple[RequestStat, ...],
    resources: ResourceSummary,
) -> SaturationWindowResult:
    request_violations = request_gate_violations(users, requests)
    gate = evaluate_gates(
        scenario="saturation",
        users=users,
        requests=requests,
        resources=resources,
        invariants={},
    )
    return SaturationWindowResult(
        users=users,
        window=window,
        started_at=started_at.isoformat(),
        ended_at=ended_at.isoformat(),
        requests=requests,
        resources=resources,
        request_violations=request_violations,
        violations=merge_violations(gate.failures, gate.warnings),
    )


def aggregate_request_stats(
    windows: Sequence[SaturationWindowResult],
) -> tuple[RequestStat, ...]:
    grouped: dict[str, list[RequestStat]] = {}
    for window in windows:
        for request in window.requests:
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


def build_saturation_level(
    users: int,
    windows: Sequence[SaturationWindowResult],
    resources: ResourceSummary,
) -> SaturationLevelResult:
    if not windows:
        raise RuntimeError(f"{users} 用户档位没有窗口结果")
    requests = aggregate_request_stats(windows)
    gate = evaluate_gates(
        scenario="saturation",
        users=users,
        requests=requests,
        resources=resources,
        invariants={},
    )
    return SaturationLevelResult(
        users=users,
        completed=len(windows) == SATURATION_WINDOWS_PER_LEVEL,
        started_at=windows[0].started_at,
        ended_at=windows[-1].ended_at,
        requests=requests,
        resources=resources,
        request_violations=merge_violations(*(window.request_violations for window in windows)),
        violations=merge_violations(
            *(window.violations for window in windows),
            gate.failures,
            gate.warnings,
        ),
    )


def execute_saturation_schedule(
    *,
    set_users: Callable[[int], None],
    reset_stats: Callable[[], None],
    capture_window: Callable[
        [int, int, datetime, datetime],
        SaturationWindowResult,
    ],
    summarize_level: Callable[
        [int, Sequence[SaturationWindowResult]],
        SaturationLevelResult,
    ],
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] | None = None,
) -> SaturationRunSummary:
    clock = now or (lambda: datetime.now(UTC))
    windows: list[SaturationWindowResult] = []
    level_results: list[SaturationLevelResult] = []
    consecutive_request_violations = 0
    capacity_boundary_users: int | None = None
    stop_reason = "maximum_level_reached"

    for users in SATURATION_LEVELS:
        set_users(users)
        current_level: list[SaturationWindowResult] = []
        for window_number in range(1, SATURATION_WINDOWS_PER_LEVEL + 1):
            reset_stats()
            started_at = clock()
            sleep(SATURATION_WINDOW_SECONDS)
            ended_at = clock()
            window = capture_window(
                users,
                window_number,
                started_at,
                ended_at,
            )
            if window.users != users or window.window != window_number:
                raise RuntimeError("饱和窗口结果与当前调度档位不一致")
            windows.append(window)
            current_level.append(window)
            if window.request_violations:
                consecutive_request_violations += 1
            else:
                consecutive_request_violations = 0
            if consecutive_request_violations >= 3:
                capacity_boundary_users = users
                stop_reason = "three_consecutive_request_gate_violations"
                break

        level_results.append(summarize_level(users, current_level))
        if capacity_boundary_users is not None:
            break

    completed_levels = tuple(level.users for level in level_results if level.completed)
    stable_levels = tuple(
        level.users for level in level_results if level.completed and not level.violations
    )
    maximum_stable_users = max(stable_levels, default=0)
    first_unstable = next(
        (level.users for level in level_results if not level.completed or level.violations),
        None,
    )
    required_levels = set(range(SATURATION_LEVELS[0], SATURATION_MINIMUM_USERS + 1, 10))
    passed_required_levels = {
        level.users
        for level in level_results
        if (level.users <= SATURATION_MINIMUM_USERS and level.completed and not level.violations)
    }
    minimum_capacity_passed = required_levels <= passed_required_levels
    hard_gate_violations: list[str] = []
    for level in level_results:
        if level.users > SATURATION_MINIMUM_USERS:
            continue
        if not level.completed:
            hard_gate_violations.append(
                f"{level.users} users: level stopped before three windows completed"
            )
        hard_gate_violations.extend(
            f"{level.users} users: {violation}" for violation in level.violations
        )
    missing_levels = sorted(required_levels - set(completed_levels))
    if missing_levels:
        hard_gate_violations.append(
            "minimum capacity levels not completed: "
            + ", ".join(str(level) for level in missing_levels)
        )

    if not level_results:
        raise RuntimeError("饱和调度未生成任何档位结果")
    if minimum_capacity_passed:
        representative = next(
            level for level in reversed(level_results) if level.users == maximum_stable_users
        )
    else:
        representative = next(
            (level for level in level_results if level.users == first_unstable),
            level_results[-1],
        )
    return SaturationRunSummary(
        levels=SATURATION_LEVELS,
        windows_per_level=SATURATION_WINDOWS_PER_LEVEL,
        window_seconds=SATURATION_WINDOW_SECONDS,
        minimum_required_users=SATURATION_MINIMUM_USERS,
        completed_levels=completed_levels,
        maximum_stable_users=maximum_stable_users,
        first_unstable_users=first_unstable,
        capacity_boundary_users=capacity_boundary_users,
        minimum_capacity_passed=minimum_capacity_passed,
        hard_gate_violations=tuple(hard_gate_violations),
        stop_reason=stop_reason,
        representative=representative,
        level_results=tuple(level_results),
        windows=tuple(windows),
    )
