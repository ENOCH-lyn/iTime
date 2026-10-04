import csv
import json
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from performance.contracts import (
    GateResult,
    RequestFailure,
    RequestStat,
    ResourceSummary,
)

LIMITS = {
    "smoke": {
        "read": {"p95_ms": 1000, "p99_ms": 2000},
        "write": {"p95_ms": 1000, "p99_ms": 2000},
        "sync": {"p95_ms": 3000, "p99_ms": 5000},
    },
    "steady": {
        "read": {"p95_ms": 500, "p99_ms": 1000},
        "write": {"p95_ms": 800, "p99_ms": 1500},
    },
    "peak": {
        "read": {"p95_ms": 1000, "p99_ms": 2000},
        "write": {"p95_ms": 1000, "p99_ms": 2000},
    },
    "history": {
        "history": {"p95_ms": 1500, "p99_ms": 3000},
    },
    "saturation": {
        "read": {"p95_ms": 1000, "p99_ms": 2000},
        "write": {"p95_ms": 1000, "p99_ms": 2000},
    },
}
SENSITIVE_KEYS = frozenset(
    {
        "password",
        "cookie",
        "authorization",
        "csrf",
        "csrf_token",
        "api_key",
    }
)
FERNET_PATTERN = re.compile(r"\bgAAAAA[A-Za-z0-9_-]{35,}={0,2}\b")
JWT_PATTERN = re.compile(
    r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"
)


class SensitiveArtifactError(ValueError):
    """报告对象包含凭据或认证材料。"""


def _number(value: float) -> str:
    return f"{value:g}"


def _request_category(name: str) -> str:
    return name.partition("::")[0]


def evaluate_gates(
    *,
    scenario: str,
    users: int,
    requests: Iterable[RequestStat],
    resources: ResourceSummary,
    invariants: Mapping[str, int],
    failures: Iterable[RequestFailure] = (),
    expected_errors: Mapping[str, frozenset[tuple[int, str]]] | None = None,
) -> GateResult:
    request_rows = tuple(requests)
    failure_rows = tuple(failures)
    expected_errors = expected_errors or {}
    hard_failures: list[str] = []
    warnings: list[str] = []
    limits = LIMITS.get(scenario, LIMITS["steady"])

    categories = {_request_category(item.name) for item in request_rows if item.count}
    for required in limits:
        if required not in categories:
            hard_failures.append(f"required request category has no samples: {required}")

    latency_failures: list[str] = []
    for item in request_rows:
        category = _request_category(item.name)
        thresholds = limits.get(category)
        if item.count <= 0 or thresholds is None:
            continue
        for percentile in ("p95_ms", "p99_ms"):
            actual = getattr(item, percentile)
            limit = thresholds[percentile]
            if actual > limit:
                label = percentile.removesuffix("_ms")
                latency_failures.append(
                    f"{item.name} {label} {_number(actual)}ms > {limit}ms"
                )

    if scenario == "saturation" and users > 50:
        warnings.extend(latency_failures)
    else:
        hard_failures.extend(latency_failures)

    expected_failure_count = 0
    unexpected_failure_count = 0
    for failure in failure_rows:
        allowed = expected_errors.get(failure.name, frozenset())
        if (failure.status_code, failure.error_code) in allowed:
            expected_failure_count += failure.count
            continue
        unexpected_failure_count += failure.count
        hard_failures.append(
            "unexpected failure "
            f"{failure.name}: {failure.status_code}/{failure.error_code} "
            f"x{failure.count}"
        )

    reported_failure_count = sum(item.failures for item in request_rows)
    unclassified_failures = max(
        0,
        reported_failure_count - expected_failure_count - unexpected_failure_count,
    )
    if unclassified_failures:
        unexpected_failure_count += unclassified_failures
        hard_failures.append(f"unclassified request failures x{unclassified_failures}")
    request_count = sum(item.count for item in request_rows)
    failure_rate = (
        unexpected_failure_count / request_count
        if request_count
        else 1.0
    )
    if failure_rate > 0.001:
        hard_failures.append(
            f"unexpected request failure rate {failure_rate:.3%} > 0.100%"
        )

    resource_checks = (
        (
            scenario in {"steady", "soak"} and resources.cpu_mean_ratio > 0.70,
            f"mean CPU ratio {_number(resources.cpu_mean_ratio)} > 0.7",
        ),
        (
            resources.cpu_peak_over_90_seconds >= 60,
            "CPU stayed above 90% for "
            f"{resources.cpu_peak_over_90_seconds}s",
        ),
        (
            resources.memory_peak_ratio > 0.85,
            f"peak memory ratio {_number(resources.memory_peak_ratio)} > 0.85",
        ),
        (
            scenario == "soak" and resources.memory_growth_ratio > 0.05,
            "memory growth ratio "
            f"{_number(resources.memory_growth_ratio)} > 0.05",
        ),
        (
            resources.database_connection_peak_ratio > 0.80,
            "database connection peak ratio "
            f"{_number(resources.database_connection_peak_ratio)} > 0.8",
        ),
        (
            resources.blocked_over_two_seconds > 0,
            "database locks over two seconds "
            f"{resources.blocked_over_two_seconds} > 0",
        ),
        (
            resources.pool_timeouts > 0,
            f"database pool timeout count {resources.pool_timeouts} > 0",
        ),
        (
            resources.server_5xx > 0,
            f"server 5xx count {resources.server_5xx} > 0",
        ),
        (
            resources.sampling_gap_seconds > 5,
            f"sampling gap {resources.sampling_gap_seconds}s > 5s",
        ),
    )
    hard_failures.extend(message for failed, message in resource_checks if failed)
    hard_failures.extend(
        f"invariant {name}={value}"
        for name, value in sorted(invariants.items())
        if value != 0
    )

    return GateResult(
        scenario=scenario,
        passed=not hard_failures,
        failures=tuple(hard_failures),
        warnings=tuple(warnings),
    )


def assert_no_sensitive_artifacts(value: object, *, path: str = "root") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).casefold().replace("-", "_")
            if any(sensitive in normalized for sensitive in SENSITIVE_KEYS):
                raise SensitiveArtifactError(f"敏感字段禁止写入报告：{path}.{key}")
            assert_no_sensitive_artifacts(nested, path=f"{path}.{key}")
        return
    if isinstance(value, (list, tuple, set, frozenset)):
        for index, nested in enumerate(value):
            assert_no_sensitive_artifacts(nested, path=f"{path}[{index}]")
        return
    if isinstance(value, str) and (
        FERNET_PATTERN.search(value)
        or JWT_PATTERN.search(value)
        or value.casefold().startswith("bearer ")
    ):
        raise SensitiveArtifactError(f"敏感令牌禁止写入报告：{path}")


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} 必须包含 JSON object")
    return payload


def read_locust_csv(path: Path) -> tuple[RequestStat, ...]:
    rows: list[RequestStat] = []
    with path.open(newline="") as source:
        for row in csv.DictReader(source):
            name = row.get("Name", "")
            if not name or name == "Aggregated":
                continue
            rows.append(
                RequestStat(
                    name=name,
                    count=int(row["Request Count"]),
                    failures=int(row["Failure Count"]),
                    p50_ms=float(row["50%"]),
                    p95_ms=float(row["95%"]),
                    p99_ms=float(row["99%"]),
                )
            )
    return tuple(rows)


def read_resource_samples(
    path: Path,
    *,
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
) -> ResourceSummary:
    with path.open(newline="") as source:
        rows = list(csv.DictReader(source))
    if started_at is not None or ended_at is not None:
        selected: list[dict[str, str]] = []
        for row in rows:
            raw_at = row.get("at")
            if not raw_at:
                raise ValueError("resources.csv 缺少窗口采样时间")
            try:
                sampled_at = datetime.fromisoformat(raw_at)
            except ValueError as exc:
                raise ValueError("resources.csv 包含无效采样时间") from exc
            if started_at is not None and sampled_at < started_at:
                continue
            if ended_at is not None and sampled_at > ended_at:
                continue
            selected.append(row)
        rows = selected
    if not rows:
        raise ValueError("resources.csv 没有采样数据")

    def values(name: str) -> list[float]:
        return [float(row[name]) for row in rows]

    cpu = values("cpu_ratio")
    memory = values("memory_ratio")
    connections = values("database_connection_ratio")
    leading_window = memory[: max(1, len(memory) // 3)]
    trailing_window = memory[-max(1, len(memory) // 3) :]
    initial_memory = sum(leading_window) / len(leading_window)
    final_memory = sum(trailing_window) / len(trailing_window)
    memory_growth = max(
        0.0,
        (final_memory - initial_memory) / max(initial_memory, 0.000001),
    )
    high_cpu_streak = 0
    current_streak = 0
    for value in cpu:
        if value > 0.90:
            current_streak += 1
            high_cpu_streak = max(high_cpu_streak, current_streak)
        else:
            current_streak = 0
    return ResourceSummary(
        cpu_mean_ratio=sum(cpu) / len(cpu),
        cpu_peak_over_90_seconds=high_cpu_streak,
        memory_peak_ratio=max(memory),
        memory_growth_ratio=memory_growth,
        database_connection_peak_ratio=max(connections),
        blocked_over_two_seconds=max(
            int(float(row["blocked_over_two_seconds"])) for row in rows
        ),
        pool_timeouts=max(int(float(row["pool_timeouts"])) for row in rows),
        server_5xx=max(int(float(row["server_5xx"])) for row in rows),
        sampling_gap_seconds=int(max(values("sampling_gap_seconds"))),
    )


def _write_atomic(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content)
    os.replace(temporary, path)


def _resource_summary(value: object) -> ResourceSummary:
    if not isinstance(value, Mapping):
        raise ValueError("饱和代表档位缺少资源摘要")
    try:
        return ResourceSummary(
            cpu_mean_ratio=float(value["cpu_mean_ratio"]),
            cpu_peak_over_90_seconds=int(value["cpu_peak_over_90_seconds"]),
            memory_peak_ratio=float(value["memory_peak_ratio"]),
            memory_growth_ratio=float(value["memory_growth_ratio"]),
            database_connection_peak_ratio=float(
                value["database_connection_peak_ratio"]
            ),
            blocked_over_two_seconds=int(value["blocked_over_two_seconds"]),
            pool_timeouts=int(value["pool_timeouts"]),
            server_5xx=int(value["server_5xx"]),
            sampling_gap_seconds=int(value["sampling_gap_seconds"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("饱和代表档位资源摘要格式无效") from exc


def _request_stats(value: object) -> tuple[RequestStat, ...]:
    if not isinstance(value, list):
        raise ValueError("饱和代表档位缺少请求统计")
    requests: list[RequestStat] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("饱和代表档位请求统计格式无效")
        try:
            requests.append(
                RequestStat(
                    name=str(item["name"]),
                    count=int(item["count"]),
                    failures=int(item["failures"]),
                    p50_ms=float(item["p50_ms"]),
                    p95_ms=float(item["p95_ms"]),
                    p99_ms=float(item["p99_ms"]),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("饱和代表档位请求统计格式无效") from exc
    return tuple(requests)


def _saturation_inputs(
    run_dir: Path,
) -> tuple[tuple[RequestStat, ...], ResourceSummary, int, dict[str, Any]]:
    payload = read_json(run_dir / "saturation.json")
    representative = payload.get("representative")
    if payload.get("schema_version") != 1 or not isinstance(
        representative,
        Mapping,
    ):
        raise ValueError("saturation.json 版本或代表档位无效")
    hard_gate_violations = payload.get("hard_gate_violations")
    if not isinstance(hard_gate_violations, list) or not all(
        isinstance(item, str) for item in hard_gate_violations
    ):
        raise ValueError("saturation.json 最低容量违规格式无效")
    try:
        users = int(representative["users"])
        maximum_stable_users = int(payload["maximum_stable_users"])
        minimum_required_users = int(payload["minimum_required_users"])
        minimum_capacity_passed = payload["minimum_capacity_passed"]
        first_unstable_users = payload["first_unstable_users"]
        capacity_boundary_users = payload["capacity_boundary_users"]
        stop_reason = str(payload["stop_reason"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("saturation.json 摘要字段无效") from exc
    if not isinstance(minimum_capacity_passed, bool):
        raise ValueError("saturation.json 最低容量结果必须为布尔值")
    for name, value in (
        ("first_unstable_users", first_unstable_users),
        ("capacity_boundary_users", capacity_boundary_users),
    ):
        if value is not None and not isinstance(value, int):
            raise ValueError(f"saturation.json {name} 必须为整数或 null")
    if users <= 0 or maximum_stable_users < 0 or minimum_required_users <= 0:
        raise ValueError("saturation.json 用户档位必须为正数")
    metadata = {
        "representative_users": users,
        "maximum_stable_users": maximum_stable_users,
        "minimum_required_users": minimum_required_users,
        "minimum_capacity_passed": minimum_capacity_passed,
        "first_unstable_users": first_unstable_users,
        "capacity_boundary_users": capacity_boundary_users,
        "stop_reason": stop_reason,
        "hard_gate_violations": hard_gate_violations,
    }
    return (
        _request_stats(representative.get("requests")),
        _resource_summary(representative.get("resources")),
        users,
        metadata,
    )


def _queue_inputs(
    run_dir: Path,
) -> tuple[tuple[RequestStat, ...], ResourceSummary, int, dict[str, Any]]:
    payload = read_json(run_dir / "queue-results.json")
    queue_results = payload.get("queues")
    violations = payload.get("violations")
    if (
        payload.get("schema_version") != 1
        or not isinstance(queue_results, list)
        or not isinstance(violations, list)
        or not all(isinstance(item, str) for item in violations)
    ):
        raise ValueError("queue-results.json 格式无效")
    kinds = [
        item.get("kind")
        for item in queue_results
        if isinstance(item, Mapping)
    ]
    if kinds != ["subscriptions", "public_links", "exports"]:
        raise ValueError("queue-results.json 队列类型或顺序无效")
    try:
        users = int(payload["users"])
        backlog_per_type = int(payload["backlog_per_type"])
        drain_timeout_seconds = int(payload["drain_timeout_seconds"])
        minimum_observation_seconds = int(
            payload["minimum_observation_seconds"]
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("queue-results.json 摘要字段无效") from exc
    if users <= 0 or backlog_per_type <= 0 or drain_timeout_seconds <= 0:
        raise ValueError("queue-results.json 数量与时限必须为正数")
    details = [
        {
            "kind": item["kind"],
            "expected": int(item["expected"]),
            "drained": bool(item["drained"]),
            "drain_seconds": float(item["drain_seconds"]),
            "throughput_per_second": float(item["throughput_per_second"]),
            "oldest_remaining_age_seconds": item[
                "oldest_remaining_age_seconds"
            ],
            "final": item["final"],
        }
        for item in queue_results
        if isinstance(item, Mapping)
    ]
    metadata = {
        "backlog_per_type": backlog_per_type,
        "drain_timeout_seconds": drain_timeout_seconds,
        "minimum_observation_seconds": minimum_observation_seconds,
        "passed": payload.get("passed") is True,
        "violations": violations,
        "results": details,
    }
    return (
        _request_stats(payload.get("requests")),
        _resource_summary(payload.get("resources")),
        users,
        metadata,
    )


def _maintenance_inputs(
    run_dir: Path,
) -> tuple[tuple[RequestStat, ...], ResourceSummary, int, dict[str, Any]]:
    payload = read_json(run_dir / "maintenance.json")
    violations = payload.get("violations")
    cleanup = payload.get("cleanup")
    degradation = payload.get("degradation_ratios")
    initial = payload.get("initial")
    final = payload.get("final")
    if (
        payload.get("schema_version") != 1
        or not isinstance(violations, list)
        or not all(isinstance(item, str) for item in violations)
        or not isinstance(cleanup, Mapping)
        or not isinstance(degradation, Mapping)
        or not isinstance(initial, Mapping)
        or not isinstance(final, Mapping)
    ):
        raise ValueError("maintenance.json 格式无效")
    try:
        users = int(payload["users"])
        baseline_seconds = int(payload["baseline_seconds"])
        maintenance_seconds = int(payload["maintenance_seconds"])
        maximum_degradation_ratio = float(
            payload["maximum_degradation_ratio"]
        )
        cleanup_counts = {
            str(key): int(value)
            for key, value in cleanup["counts"].items()
        }
        cleanup_durations = {
            str(key): float(value)
            for key, value in cleanup["durations_seconds"].items()
        }
        total_seconds = float(cleanup["total_seconds"])
        degradation_ratios = {
            str(key): float(value)
            for key, value in degradation.items()
        }
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("maintenance.json 摘要字段无效") from exc
    metadata = {
        "baseline_seconds": baseline_seconds,
        "maintenance_seconds": maintenance_seconds,
        "maximum_degradation_ratio": maximum_degradation_ratio,
        "passed": payload.get("passed") is True,
        "violations": violations,
        "degradation_ratios": degradation_ratios,
        "cleanup": {
            "counts": cleanup_counts,
            "durations_seconds": cleanup_durations,
            "total_seconds": total_seconds,
        },
        "initial": dict(initial),
        "final": dict(final),
    }
    return (
        _request_stats(payload.get("maintenance_requests")),
        _resource_summary(payload.get("maintenance_resources")),
        users,
        metadata,
    )


def _markdown_report(
    manifest: Mapping[str, Any],
    requests: tuple[RequestStat, ...],
    resources: ResourceSummary,
    result: GateResult,
    *,
    saturation: Mapping[str, Any] | None = None,
    queues: Mapping[str, Any] | None = None,
    maintenance: Mapping[str, Any] | None = None,
) -> str:
    lines = [
        f"# iTime performance report: {result.scenario}",
        "",
        f"- Result: {'PASS' if result.passed else 'FAIL'}",
        f"- Load: {manifest.get('users', 'unknown')} users",
        f"- Environment: {manifest.get('environment', 'unknown')}",
        f"- Commit: {manifest.get('commit', 'unknown')}",
        "",
    ]
    if saturation is not None:
        lines.extend(
            [
                "## Saturation",
                "",
                "- Maximum stable load: "
                f"{saturation['maximum_stable_users']} users",
                "- Minimum required load: "
                f"{saturation['minimum_required_users']} users "
                f"({'PASS' if saturation['minimum_capacity_passed'] else 'FAIL'})",
                "- First unstable load: "
                f"{saturation['first_unstable_users'] or 'not observed'}",
                "- Capacity boundary: "
                f"{saturation['capacity_boundary_users'] or 'not observed'}",
                f"- Stop reason: {saturation['stop_reason']}",
                "",
            ]
        )
    if queues is not None:
        lines.extend(
            [
                "## Queues",
                "",
                f"- Backlog per type: {queues['backlog_per_type']}",
                f"- Drain timeout: {queues['drain_timeout_seconds']}s",
                "",
                "| Queue | Drained | Drain | Throughput | Remaining oldest |",
                "| --- | --- | ---: | ---: | ---: |",
            ]
        )
        for item in queues["results"]:
            oldest_age = item["oldest_remaining_age_seconds"]
            oldest_label = (
                f"{_number(oldest_age)}s"
                if oldest_age is not None
                else "-"
            )
            lines.append(
                "| "
                f"{item['kind']} | {'yes' if item['drained'] else 'no'} | "
                f"{_number(item['drain_seconds'])}s | "
                f"{_number(item['throughput_per_second'])}/s | "
                f"{oldest_label} |"
            )
        lines.append("")
    if maintenance is not None:
        lines.extend(
            [
                "## Maintenance",
                "",
                "- Cleanup duration: "
                f"{_number(maintenance['cleanup']['total_seconds'])}s",
                "- Maximum allowed P95 degradation: "
                f"{_number(maintenance['maximum_degradation_ratio'])}x",
                "",
                "| Category | P95 degradation |",
                "| --- | ---: |",
            ]
        )
        lines.extend(
            f"| {category} | {_number(ratio)}x |"
            for category, ratio in sorted(
                maintenance["degradation_ratios"].items()
            )
        )
        lines.extend(["", "### Cleaned rows", ""])
        lines.extend(
            f"- {name}: {count}"
            for name, count in sorted(
                maintenance["cleanup"]["counts"].items()
            )
        )
        lines.append("")
    lines.extend(
        [
            "## Requests",
            "",
            "| Name | Count | Failures | P50 | P95 | P99 |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    lines.extend(
        "| "
        f"{row.name} | {row.count} | {row.failures} | "
        f"{_number(row.p50_ms)}ms | {_number(row.p95_ms)}ms | "
        f"{_number(row.p99_ms)}ms |"
        for row in requests
    )
    lines.extend(
        [
            "",
            "## Resources",
            "",
            f"- Mean CPU ratio: {_number(resources.cpu_mean_ratio)}",
            f"- Peak memory ratio: {_number(resources.memory_peak_ratio)}",
            "- Peak database connection ratio: "
            f"{_number(resources.database_connection_peak_ratio)}",
            "",
            "## Failures",
            "",
        ]
    )
    lines.extend(f"- {failure}" for failure in result.failures)
    if not result.failures:
        lines.append("- None")
    lines.extend(["", "## Warnings", ""])
    lines.extend(f"- {warning}" for warning in result.warnings)
    if not result.warnings:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Scope",
            "",
            "This local baseline excludes public-network latency and real provider latency.",
            "",
        ]
    )
    return "\n".join(lines)


def build_report(run_dir: Path) -> GateResult:
    manifest = read_json(run_dir / "manifest.json")
    scenario = str(manifest["scenario"])
    saturation: dict[str, Any] | None = None
    queues: dict[str, Any] | None = None
    maintenance: dict[str, Any] | None = None
    if scenario == "saturation":
        requests, resources, users, saturation = _saturation_inputs(run_dir)
        report_manifest = {**manifest, "users": users}
    elif scenario == "queues":
        requests, resources, users, queues = _queue_inputs(run_dir)
        report_manifest = {**manifest, "users": users}
    elif scenario == "maintenance":
        requests, resources, users, maintenance = _maintenance_inputs(run_dir)
        report_manifest = {**manifest, "users": users}
    else:
        requests = read_locust_csv(run_dir / "requests_stats.csv")
        resources = read_resource_samples(run_dir / "resources.csv")
        users = int(manifest["users"])
        report_manifest = manifest
    invariants = read_json(run_dir / "invariants.json")
    evaluated = evaluate_gates(
        scenario=scenario,
        users=users,
        requests=requests,
        resources=resources,
        invariants={str(key): int(value) for key, value in invariants.items()},
    )
    failures = list(evaluated.failures)
    warnings = list(evaluated.warnings)
    if saturation is not None:
        if not saturation["minimum_capacity_passed"]:
            failures.extend(saturation["hard_gate_violations"])
            if not saturation["hard_gate_violations"]:
                failures.append("minimum saturation capacity was not established")
        boundary = saturation["capacity_boundary_users"]
        first_unstable = saturation["first_unstable_users"]
        if boundary is not None and boundary > saturation["minimum_required_users"]:
            warnings.append(
                f"capacity boundary observed at {boundary} users after "
                "three consecutive violating windows"
            )
        elif (
            first_unstable is not None
            and first_unstable > saturation["minimum_required_users"]
        ):
            warnings.append(
                f"first unstable saturation level observed at "
                f"{first_unstable} users"
            )
    if queues is not None:
        failures.extend(queues["violations"])
    if maintenance is not None:
        failures.extend(maintenance["violations"])
    result = GateResult(
        scenario=scenario,
        passed=not failures,
        failures=tuple(dict.fromkeys(failures)),
        warnings=tuple(dict.fromkeys(warnings)),
    )
    summary = asdict(result)
    if saturation is not None:
        summary["saturation"] = saturation
    if queues is not None:
        summary["queues"] = queues
    if maintenance is not None:
        summary["maintenance"] = maintenance
    artifact = {
        "manifest": manifest,
        "requests": [asdict(item) for item in requests],
        "resources": asdict(resources),
        "result": summary,
    }
    assert_no_sensitive_artifacts(artifact)
    _write_atomic(
        run_dir / "summary.json",
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    _write_atomic(
        run_dir / "report.md",
        _markdown_report(
            report_manifest,
            requests,
            resources,
            result,
            saturation=saturation,
            queues=queues,
            maintenance=maintenance,
        ),
    )
    return result
