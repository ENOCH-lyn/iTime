import csv
import json
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict
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
            resources.cpu_peak_over_90_seconds > 0,
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


def read_resource_samples(path: Path) -> ResourceSummary:
    with path.open(newline="") as source:
        rows = list(csv.DictReader(source))
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


def _markdown_report(
    manifest: Mapping[str, Any],
    requests: tuple[RequestStat, ...],
    resources: ResourceSummary,
    result: GateResult,
) -> str:
    lines = [
        f"# iTime performance report: {result.scenario}",
        "",
        f"- Result: {'PASS' if result.passed else 'FAIL'}",
        f"- Load: {manifest.get('users', 'unknown')} users",
        f"- Environment: {manifest.get('environment', 'unknown')}",
        f"- Commit: {manifest.get('commit', 'unknown')}",
        "",
        "## Requests",
        "",
        "| Name | Count | Failures | P50 | P95 | P99 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
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
    requests = read_locust_csv(run_dir / "requests_stats.csv")
    resources = read_resource_samples(run_dir / "resources.csv")
    invariants = read_json(run_dir / "invariants.json")
    result = evaluate_gates(
        scenario=str(manifest["scenario"]),
        users=int(manifest["users"]),
        requests=requests,
        resources=resources,
        invariants={str(key): int(value) for key, value in invariants.items()},
    )
    summary = asdict(result)
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
        _markdown_report(manifest, requests, resources, result),
    )
    return result
