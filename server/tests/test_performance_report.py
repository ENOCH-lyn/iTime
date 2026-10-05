import csv
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import pytest

from performance.contracts import RequestFailure, RequestStat, ResourceSummary
from performance.report import (
    SensitiveArtifactError,
    assert_no_sensitive_artifacts,
    build_report,
    evaluate_gates,
    read_resource_samples,
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


def test_cpu_peak_requires_a_full_60_second_streak():
    below = evaluate_gates(
        scenario="peak",
        users=50,
        requests=[
            RequestStat("read::tasks.list", 100, 50, 100, 200, 0),
            RequestStat("write::tasks.create", 10, 50, 100, 200, 0),
        ],
        resources=healthy_resources(cpu_peak_over_90_seconds=59),
        invariants={},
    )
    assert below.passed

    threshold = evaluate_gates(
        scenario="peak",
        users=50,
        requests=[
            RequestStat("read::tasks.list", 100, 50, 100, 200, 0),
            RequestStat("write::tasks.create", 10, 50, 100, 200, 0),
        ],
        resources=healthy_resources(cpu_peak_over_90_seconds=60),
        invariants={},
    )
    assert threshold.failures == ("CPU stayed above 90% for 60s",)


def test_resource_summary_can_be_bounded_to_a_saturation_window(tmp_path: Path):
    path = tmp_path / "resources.csv"
    with path.open("w", newline="") as output:
        writer = csv.DictWriter(
            output,
            fieldnames=[
                "at",
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
        for at, cpu in (
            ("2026-10-05T00:00:00+00:00", 0.99),
            ("2026-10-05T00:01:00+00:00", 0.40),
            ("2026-10-05T00:01:01+00:00", 0.60),
            ("2026-10-05T00:02:00+00:00", 0.95),
        ):
            writer.writerow(
                {
                    "at": at,
                    "cpu_ratio": cpu,
                    "memory_ratio": 0.5,
                    "database_connection_ratio": 0.3,
                    "blocked_over_two_seconds": 0,
                    "pool_timeouts": 0,
                    "server_5xx": 0,
                    "sampling_gap_seconds": 0,
                }
            )

    summary = read_resource_samples(
        path,
        started_at=datetime.fromisoformat("2026-10-05T00:00:30+00:00"),
        ended_at=datetime.fromisoformat("2026-10-05T00:01:30+00:00"),
    )

    assert summary.cpu_mean_ratio == pytest.approx(0.5)
    assert summary.memory_peak_ratio == 0.5
    assert summary.cpu_peak_over_90_seconds == 0


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


def test_saturation_report_uses_representative_level_and_records_capacity(
    tmp_path: Path,
):
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "scenario": "saturation",
                "users": 100,
                "environment": "test",
                "commit": "abc1234",
            }
        )
    )
    representative_requests = [
        asdict(RequestStat("read::tasks.list", 300, 120, 800, 1500, 0)),
        asdict(RequestStat("write::tasks.create", 30, 250, 700, 1400, 0)),
    ]
    (tmp_path / "saturation.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "minimum_required_users": 50,
                "minimum_capacity_passed": True,
                "maximum_stable_users": 50,
                "first_unstable_users": 60,
                "capacity_boundary_users": 60,
                "stop_reason": "three_consecutive_request_gate_violations",
                "hard_gate_violations": [],
                "representative": {
                    "users": 50,
                    "requests": representative_requests,
                    "resources": asdict(healthy_resources()),
                },
            }
        )
    )
    (tmp_path / "invariants.json").write_text(
        json.dumps({"orphan_tasks": 0})
    )

    result = build_report(tmp_path)

    assert result.passed
    assert result.warnings == (
        "capacity boundary observed at 60 users after "
        "three consecutive violating windows",
    )
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["saturation"]["maximum_stable_users"] == 50
    assert summary["saturation"]["representative_users"] == 50
    report = (tmp_path / "report.md").read_text()
    assert "- Load: 50 users" in report
    assert "- Maximum stable load: 50 users" in report
    assert "- Capacity boundary: 60" in report
    assert not (tmp_path / "requests_stats.csv").exists()


def test_queue_report_uses_combined_workload_and_drain_evidence(tmp_path: Path):
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "scenario": "queues",
                "users": 20,
                "environment": "test",
                "commit": "abc1234",
            }
        )
    )
    queue_results = [
        {
            "kind": kind,
            "expected": 50,
            "drained": True,
            "drain_seconds": 25,
            "throughput_per_second": 2,
            "oldest_remaining_age_seconds": None,
            "final": {
                "queued": 0,
                "running": 0,
                "succeeded": 50,
                "failed": 0,
                "oldest": None,
            },
        }
        for kind in ("subscriptions", "public_links", "exports")
    ]
    (tmp_path / "queue-results.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "users": 20,
                "backlog_per_type": 50,
                "drain_timeout_seconds": 180,
                "minimum_observation_seconds": 30,
                "passed": True,
                "violations": [],
                "requests": [
                    asdict(
                        RequestStat(
                            "read::tasks.list",
                            300,
                            120,
                            450,
                            900,
                            0,
                        )
                    ),
                    asdict(
                        RequestStat(
                            "write::tasks.create",
                            30,
                            250,
                            700,
                            1400,
                            0,
                        )
                    ),
                ],
                "resources": asdict(healthy_resources()),
                "queues": queue_results,
            }
        )
    )
    (tmp_path / "invariants.json").write_text(
        json.dumps({"orphan_tasks": 0})
    )

    result = build_report(tmp_path)

    assert result.passed
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["queues"]["backlog_per_type"] == 50
    assert [item["kind"] for item in summary["queues"]["results"]] == [
        "subscriptions",
        "public_links",
        "exports",
    ]
    report = (tmp_path / "report.md").read_text()
    assert "- Backlog per type: 50" in report
    assert "| subscriptions | yes | 25s | 2/s | - |" in report
    assert not (tmp_path / "requests_stats.csv").exists()


def test_maintenance_report_uses_measured_cleanup_window(tmp_path: Path):
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "scenario": "maintenance",
                "users": 20,
                "environment": "test",
                "commit": "abc1234",
            }
        )
    )
    maintenance_requests = [
        asdict(RequestStat("read::tasks.list", 300, 120, 300, 600, 0)),
        asdict(RequestStat("write::tasks.create", 30, 250, 500, 900, 0)),
    ]
    maintenance_state = {
        "tasks_due": 0,
        "tasks_purged": 100,
        "calendar_series_due": 0,
        "calendar_series_purged": 100,
        "difficulties_due": 0,
        "difficulties_purged": 100,
        "memories_due": 0,
        "memories_purged": 100,
        "expired_sync_events": 0,
        "sync_events": 1000,
    }
    (tmp_path / "maintenance.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "users": 20,
                "baseline_seconds": 60,
                "maintenance_seconds": 60,
                "maximum_degradation_ratio": 2,
                "passed": True,
                "violations": [],
                "degradation_ratios": {"read": 1.2, "write": 1.1},
                "initial": maintenance_state,
                "final": maintenance_state,
                "cleanup": {
                    "counts": {
                        "tasks": 100,
                        "calendar_series": 100,
                        "calendar_sources": 0,
                        "difficulties": 100,
                        "memories": 100,
                        "sync_events": 1000,
                    },
                    "durations_seconds": {"tasks": 0.5},
                    "total_seconds": 2.5,
                },
                "baseline_requests": maintenance_requests,
                "maintenance_requests": maintenance_requests,
                "baseline_resources": asdict(healthy_resources()),
                "maintenance_resources": asdict(healthy_resources()),
            }
        )
    )
    (tmp_path / "invariants.json").write_text(
        json.dumps({"orphan_tasks": 0})
    )

    result = build_report(tmp_path)

    assert result.passed
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["maintenance"]["cleanup"]["counts"]["sync_events"] == 1000
    assert summary["maintenance"]["degradation_ratios"]["read"] == 1.2
    report = (tmp_path / "report.md").read_text()
    assert "- Cleanup duration: 2.5s" in report
    assert "| read | 1.2x |" in report
    assert "- sync_events: 1000" in report
    assert not (tmp_path / "requests_stats.csv").exists()


def test_ai_isolation_report_uses_ordinary_load_and_job_evidence(
    tmp_path: Path,
):
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "scenario": "ai-isolation",
                "users": 40,
                "environment": "test",
                "commit": "abc1234",
            }
        )
    )
    ordinary_requests = [
        asdict(RequestStat("read::tasks.list", 300, 120, 300, 600, 0)),
        asdict(RequestStat("write::tasks.create", 30, 250, 500, 900, 0)),
    ]
    ai_state = {
        "jobs": 20,
        "distinct_owners": 20,
        "queued": 0,
        "running": 0,
        "succeeded": 20,
        "failed": 0,
        "timed_out": 0,
        "cancelled": 0,
        "attempts": 20,
        "succeeded_attempts": 20,
        "provider_costs": 20,
        "reservations_reserved": 0,
        "reservations_settled": 20,
        "reservations_released": 0,
        "usage_ledger_entries": 20,
        "inconsistent_jobs": 0,
        "claimed_p95_ms": 500,
        "end_to_end_p99_ms": 1500,
    }
    (tmp_path / "ai-isolation.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "standard_users": 20,
                "ai_users": 20,
                "duration_seconds": 300,
                "passed": True,
                "violations": [],
                "degradation_ratios": {"read": 1.2, "write": 1.1},
                "initial": {**ai_state, "jobs": 0, "distinct_owners": 0},
                "final": ai_state,
                "ordinary_baseline_requests": ordinary_requests,
                "ordinary_active_requests": ordinary_requests,
                "ai_requests": [
                    asdict(
                        RequestStat(
                            "ai::messages.accept",
                            20,
                            100,
                            500,
                            800,
                            0,
                        )
                    )
                ],
                "baseline_resources": asdict(healthy_resources()),
                "active_resources": asdict(healthy_resources()),
            }
        )
    )
    (tmp_path / "invariants.json").write_text(
        json.dumps({"orphan_tasks": 0})
    )

    result = build_report(tmp_path)

    assert result.passed
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["ai_isolation"]["standard_users"] == 20
    assert summary["ai_isolation"]["ai_users"] == 20
    assert summary["ai_isolation"]["final"]["inconsistent_jobs"] == 0
    report = (tmp_path / "report.md").read_text()
    assert "- Ordinary users: 20" in report
    assert "- AI users/jobs: 20" in report
    assert "| write | 1.1x |" in report
