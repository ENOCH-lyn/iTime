import json
import os
from pathlib import Path

import pytest

import performance.run as performance_run
from performance.contracts import GateResult, RunConfig


def state(tmp_path: Path) -> performance_run.RunState:
    return performance_run.RunState(
        config=RunConfig(
            run_id="abc12345",
            environment="test",
            http_origin="http://127.0.0.1:18000",
            database_name="itime_perf_abc12345",
            database_host="127.0.0.1",
            guard_value="itime-performance:abc12345",
            docker_context="colima-itime-perf",
            output_dir=tmp_path,
        ),
        scenario="smoke",
        env={},
    )


def test_lifecycle_runs_every_stage_in_order(tmp_path: Path):
    calls: list[str] = []

    def execute(run_state, stage):
        calls.append(stage)
        if stage == "build-report":
            return GateResult("smoke", True, (), ())
        if stage == "verify-clean":
            return ()
        return None

    result = performance_run.execute_lifecycle(state(tmp_path), execute=execute)

    assert result.passed
    assert result.remaining_resources == ()
    assert calls == [
        "check-runtime",
        "start-postgres",
        "migrate",
        "create-extension-and-guard",
        "seed",
        "start-services",
        "wait-ready",
        "warmup",
        "reset-stats",
        "start-sampler",
        "run-locust",
        "verify-invariants",
        "build-report",
        "cleanup",
        "verify-clean",
    ]


@pytest.mark.parametrize(
    ("failing_stage", "error"),
    [
        ("migrate", RuntimeError("migration failed")),
        ("seed", RuntimeError("seed failed")),
        ("run-locust", RuntimeError("locust failed")),
        ("build-report", RuntimeError("report failed")),
        ("run-locust", KeyboardInterrupt()),
    ],
)
def test_lifecycle_always_cleans_and_preserves_the_primary_error(
    tmp_path: Path,
    failing_stage: str,
    error: BaseException,
):
    calls: list[str] = []

    def execute(run_state, stage):
        calls.append(stage)
        if stage == failing_stage:
            raise error
        if stage == "verify-clean":
            return ()
        return None

    result = performance_run.execute_lifecycle(state(tmp_path), execute=execute)

    assert not result.passed
    assert calls[-2:] == ["cleanup", "verify-clean"]
    assert calls.count("cleanup") == 1
    assert calls.count("verify-clean") == 1
    partial = json.loads((tmp_path / "partial-report.json").read_text())
    assert partial["failed_stage"] == failing_stage
    assert partial["error_type"] == type(error).__name__
    assert partial["error"] == str(error)


def test_cli_contract_accepts_all_planned_commands():
    parser = performance_run.build_parser()
    assert parser.parse_args(["smoke"]).command == "smoke"
    assert parser.parse_args(["baseline"]).command == "baseline"
    assert parser.parse_args(["verify-clean"]).command == "verify-clean"
    for scenario in (
        "steady",
        "peak",
        "history",
        "ai-isolation",
        "queues",
        "maintenance",
        "soak",
        "saturation",
    ):
        parsed = parser.parse_args(["scenario", scenario])
        assert parsed.command == "scenario"
        assert parsed.scenario == scenario


@pytest.mark.performance
@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("ITIME_RUN_PERFORMANCE") != "1",
    reason="设置 ITIME_RUN_PERFORMANCE=1 执行容器性能烟测",
)
def test_real_performance_smoke():
    result = performance_run.run_performance_scenario("smoke")
    assert result.passed, result.error
    assert result.remaining_resources == ()
