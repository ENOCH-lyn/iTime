import csv
import json
from pathlib import Path

import pytest

from performance.contracts import RequestFailure, RequestStat, ResourceSummary
from performance.report import (
    SensitiveArtifactError,
    assert_no_sensitive_artifacts,
    build_report,
    evaluate_gates,
)


def healthy_resources(**changes: object) -> ResourceSummary:
    values: dict[str, object] = {
        "cpu_mean_ratio": 0.60,
        "cpu_peak_over_90_seconds": 0,
        "memory_peak_ratio": 0.70,
        "memory_growth_ratio": 0.02,
        "database_connection_peak_ratio": 0.50,
        "blocked_over_two_seconds": 0,
        "pool_timeouts": 0,
        "server_5xx": 0,
        "sampling_gap_seconds": 0,
    }
    values.update(changes)
    return ResourceSummary(**values)  # type: ignore[arg-type]


def test_steady_threshold_is_inclusive_and_one_millisecond_over_fails():
    passed = evaluate_gates(
        scenario="steady",
        users=20,
        requests=[
            RequestStat("read::tasks.list", 1000, 120, 500, 1000, 0),
            RequestStat("write::tasks.create", 100, 250, 800, 1500, 0),
        ],
        resources=healthy_resources(),
        invariants={"orphan_tasks": 0},
    )
    assert passed.passed

    failed = evaluate_gates(
        scenario="steady",
        users=20,
        requests=[
            RequestStat("read::tasks.list", 1000, 120, 501, 900, 0),
            RequestStat("write::tasks.create", 100, 250, 800, 1500, 0),
        ],
        resources=healthy_resources(),
        invariants={"orphan_tasks": 0},
    )
    assert failed.failures == ("read::tasks.list p95 501ms > 500ms",)


def test_empty_categories_5xx_resource_and_invariant_failures_are_hard_gates():
    result = evaluate_gates(
        scenario="steady",
        users=20,
        requests=[RequestStat("read::tasks.list", 100, 50, 100, 200, 0)],
        resources=healthy_resources(
            server_5xx=1,
            pool_timeouts=2,
            sampling_gap_seconds=6,
        ),
        invariants={"orphan_tasks": 1},
    )
    assert not result.passed
    assert "required request category has no samples: write" in result.failures
    assert "server 5xx count 1 > 0" in result.failures
    assert "database pool timeout count 2 > 0" in result.failures
    assert "sampling gap 6s > 5s" in result.failures
    assert "invariant orphan_tasks=1" in result.failures


def test_expected_errors_require_exact_status_and_error_code():
    request = RequestStat("write::tasks.create", 1000, 100, 400, 700, 3)
    accepted = evaluate_gates(
        scenario="steady",
        users=20,
        requests=[
            RequestStat("read::tasks.list", 1000, 100, 300, 500, 0),
            request,
        ],
        resources=healthy_resources(),
        invariants={},
        failures=[
            RequestFailure("write::tasks.create", 429, "rate_limited", 3),
        ],
        expected_errors={
            "write::tasks.create": frozenset({(429, "rate_limited")}),
        },
    )
    assert accepted.passed

    rejected = evaluate_gates(
        scenario="steady",
        users=20,
        requests=[
            RequestStat("read::tasks.list", 1000, 100, 300, 500, 0),
            request,
        ],
        resources=healthy_resources(),
        invariants={},
        failures=[
            RequestFailure("write::tasks.create", 429, "wrong_code", 3),
        ],
        expected_errors={
            "write::tasks.create": frozenset({(429, "rate_limited")}),
        },
    )
    assert rejected.failures == (
        "unexpected failure write::tasks.create: 429/wrong_code x3",
        "unexpected request failure rate 0.150% > 0.100%",
    )


def test_saturation_above_minimum_turns_latency_failure_into_capacity_warning():
    result = evaluate_gates(
        scenario="saturation",
        users=60,
        requests=[
            RequestStat("read::tasks.list", 1000, 200, 1001, 1900, 0),
            RequestStat("write::tasks.create", 100, 300, 900, 1800, 0),
        ],
        resources=healthy_resources(),
        invariants={},
    )
    assert result.passed
    assert result.failures == ()
    assert result.warnings == ("read::tasks.list p95 1001ms > 1000ms",)

    minimum = evaluate_gates(
        scenario="saturation",
        users=50,
        requests=[
            RequestStat("read::tasks.list", 1000, 200, 1001, 1900, 0),
            RequestStat("write::tasks.create", 100, 300, 900, 1800, 0),
        ],
        resources=healthy_resources(),
        invariants={},
    )
    assert not minimum.passed


@pytest.mark.parametrize(
    "payload",
    [
        {"password": "value"},
        {"nested": {"Cookie": "value"}},
        {"authorization": "Bearer value"},
        {"csrf_token": "value"},
        {"api_key": "value"},
        {"value": "gAAAAABmQ2RkAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"},
        {"value": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature"},
    ],
)
def test_sensitive_artifacts_are_rejected(payload: object):
    with pytest.raises(SensitiveArtifactError):
        assert_no_sensitive_artifacts(payload)


def test_build_report_reads_csv_and_writes_atomic_public_outputs(tmp_path: Path):
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "scenario": "steady",
                "users": 20,
                "environment": "test",
                "commit": "abc1234",
            }
        )
    )
    with (tmp_path / "requests_stats.csv").open("w", newline="") as output:
        writer = csv.DictWriter(
            output,
            fieldnames=[
                "Type",
                "Name",
                "Request Count",
                "Failure Count",
                "50%",
                "95%",
                "99%",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "Type": "GET",
                "Name": "read::tasks.list",
                "Request Count": 1000,
                "Failure Count": 0,
                "50%": 120,
                "95%": 480,
                "99%": 900,
            }
        )
        writer.writerow(
            {
                "Type": "POST",
                "Name": "write::tasks.create",
                "Request Count": 100,
                "Failure Count": 0,
                "50%": 250,
                "95%": 790,
                "99%": 1400,
            }
        )
        writer.writerow(
            {
                "Type": "",
                "Name": "Aggregated",
                "Request Count": 1100,
                "Failure Count": 0,
                "50%": 130,
                "95%": 500,
                "99%": 1000,
            }
        )
    with (tmp_path / "resources.csv").open("w", newline="") as output:
        writer = csv.DictWriter(
            output,
            fieldnames=[
                "cpu_ratio",
                "memory_ratio",
                "database_connection_ratio",
                "blocked_over_two_seconds",
                "pool_timeouts",
                "server_5xx",
                "sampling_gap_seconds",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "cpu_ratio": 0.5,
                "memory_ratio": 0.6,
                "database_connection_ratio": 0.4,
                "blocked_over_two_seconds": 0,
                "pool_timeouts": 0,
                "server_5xx": 0,
                "sampling_gap_seconds": 0,
            }
        )
        writer.writerow(
            {
                "cpu_ratio": 0.7,
                "memory_ratio": 0.7,
                "database_connection_ratio": 0.5,
                "blocked_over_two_seconds": 0,
                "pool_timeouts": 0,
                "server_5xx": 0,
                "sampling_gap_seconds": 0,
            }
        )
    (tmp_path / "invariants.json").write_text(json.dumps({"orphan_tasks": 0}))

    result = build_report(tmp_path)

    assert result.passed
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["passed"] is True
    assert summary["scenario"] == "steady"
    report = (tmp_path / "report.md").read_text()
    assert "20 users" in report
    assert "read::tasks.list" in report
    assert not list(tmp_path.glob("*.tmp"))
