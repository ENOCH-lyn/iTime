import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

import performance.run as performance_run
import performance.saturation as performance_saturation
from performance.contracts import (
    SCENARIOS,
    GateResult,
    RequestStat,
    ResourceSummary,
    RunConfig,
    Scenario,
)


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
    assert "saturation" in performance_run.BASELINE_SCENARIOS
    for scenario in (
        "steady",
        "peak",
        "history",
        "ai-isolation",
        "queues",
        "maintenance",
        "soak",
        "saturation",
        "ui-load",
    ):
        parsed = parser.parse_args(["scenario", scenario])
        assert parsed.command == "scenario"
        assert parsed.scenario == scenario


def test_performance_anchor_uses_the_0400_utc_activity_day_boundary():
    assert (
        performance_run._performance_anchor_date(
            datetime(2026, 10, 5, 3, 59, tzinfo=UTC)
        )
        == "2026-10-04"
    )
    assert (
        performance_run._performance_anchor_date(
            datetime(2026, 10, 5, 4, 0, tzinfo=UTC)
        )
        == "2026-10-05"
    )


def test_history_profile_has_one_distinct_account_per_virtual_user(tmp_path: Path):
    run_state = state(tmp_path)
    run_state.scenario = "history"
    run_state.scenario_config = SCENARIOS[Scenario.HISTORY]

    profile = performance_run._profile_for(run_state)

    assert profile.accounts == run_state.scenario_config.users
    assert profile.active_accounts == run_state.scenario_config.users
    assert profile.history_accounts == run_state.scenario_config.users


def test_ui_load_contract_and_browser_environment_are_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    scenario = SCENARIOS[Scenario.UI_LOAD]
    assert scenario.users == 20
    assert scenario.spawn_rate == 4
    assert scenario.duration_seconds == 900
    assert scenario.dataset == "standard"

    run_state = state(tmp_path)
    run_state.env = {
        "ITIME_PERF_CREDENTIALS": str(tmp_path / "private" / "accounts.json"),
        "ITIME_PERF_BROWSER_CREDENTIALS": str(
            tmp_path / "private" / "browser-account.json"
        ),
        "ITIME_PERF_POSTGRES_PASSWORD": "must-not-reach-browser",
        "ITIME_PERF_ENCRYPTION_KEY": "must-not-reach-browser",
    }
    monkeypatch.setenv("DATABASE_URL", "must-not-reach-browser")
    monkeypatch.setenv("LARKSUITE_CLI_USER_ACCESS_TOKEN", "must-not-reach-browser")
    browser_env = performance_run._browser_environment(
        run_state,
        web_origin="http://localhost:4174",
        locust_pid=4321,
    )

    assert browser_env["ITIME_API_ORIGIN"] == run_state.config.http_origin
    assert browser_env["ITIME_PERF_WEB_ORIGIN"] == "http://localhost:4174"
    assert browser_env["ITIME_PERF_BROWSER_ACCOUNT_FILE"].endswith(
        "/private/browser-account.json"
    )
    assert browser_env["ITIME_PERF_RUN_MANIFEST_FILE"] == str(
        tmp_path / "manifest.json"
    )
    assert browser_env["ITIME_PERF_LOCUST_PID"] == "4321"
    assert "ITIME_PERF_POSTGRES_PASSWORD" not in browser_env
    assert "ITIME_PERF_ENCRYPTION_KEY" not in browser_env
    assert "DATABASE_URL" not in browser_env
    assert "LARKSUITE_CLI_USER_ACCESS_TOKEN" not in browser_env


def test_ui_load_runs_browsers_while_background_load_is_alive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    run_state = state(tmp_path)
    run_state.scenario = "ui-load"
    run_state.scenario_config = SCENARIOS[Scenario.UI_LOAD]
    run_state.env["ITIME_PERF_CREDENTIALS"] = str(
        tmp_path / "private" / "accounts.json"
    )
    run_state.env["ITIME_PERF_WEB_PORT"] = "4174"
    events: list[tuple] = []
    ports = iter((8089,))

    class FakeProcess:
        def __init__(self, pid: int):
            self.pid = pid

        def poll(self):
            return None

    def start_process(
        run_state,
        name,
        command,
        *,
        cwd=performance_run.SERVER,
        env=None,
    ):
        events.append(("start", name, command, cwd, env))
        return performance_run.ManagedProcess(
            name,
            FakeProcess(4321 if name == "locust" else 1234),
            None,
        )

    def run_logged(run_state, name, command, *, env, timeout, cwd):
        events.append(("checked", name, command, cwd, env, timeout))
        return None

    monkeypatch.setattr(performance_run, "_available_port", lambda: next(ports))
    monkeypatch.setattr(performance_run, "_start_process", start_process)
    monkeypatch.setattr(performance_run, "_run_logged", run_logged)
    monkeypatch.setattr(
        performance_run,
        "_wait_http",
        lambda origin, process, **kwargs: events.append(
            ("wait-http", origin, process.name, kwargs)
        ),
    )
    monkeypatch.setattr(
        performance_run,
        "_wait_for_locust_users",
        lambda process, origin, **kwargs: events.append(
            ("wait-users", process.name, origin, kwargs)
        ),
    )
    monkeypatch.setattr(
        performance_run,
        "_require_process_alive",
        lambda process, purpose: events.append(("alive", process.name, purpose)),
    )
    monkeypatch.setattr(
        performance_run,
        "_edge_executable_path",
        lambda: "/Applications/Microsoft Edge",
    )
    monkeypatch.setattr(
        performance_run,
        "_stop_process",
        lambda process: events.append(("stop", process.name)),
    )

    performance_run._run_ui_load(run_state)

    names = [event[:2] for event in events]
    assert names == [
        ("checked", "web-build"),
        ("start", "web-preview"),
        ("wait-http", "http://localhost:4174"),
        ("start", "locust"),
        ("wait-users", "locust"),
        ("checked", "playwright-chromium"),
        ("alive", "locust"),
        ("checked", "playwright-edge"),
        ("alive", "locust"),
        ("stop", "locust"),
        ("stop", "web-preview"),
    ]
    locust_command = next(
        event[2]
        for event in events
        if event[:2] == ("start", "locust")
    )
    assert "--autostart" in locust_command
    assert "--headless" not in locust_command
    assert locust_command[locust_command.index("--web-host") + 1] == "127.0.0.1"
    chromium = next(
        event
        for event in events
        if event[:2] == ("checked", "playwright-chromium")
    )
    edge = next(
        event
        for event in events
        if event[:2] == ("checked", "playwright-edge")
    )
    assert chromium[2] == ["pnpm", "test:e2e:performance"]
    assert chromium[4]["ITIME_PERF_BROWSER_LABEL"] == "chromium"
    assert edge[2][-1] == "--project=desktop"
    assert edge[4]["ITIME_E2E_BROWSER_EXECUTABLE_PATH"] == (
        "/Applications/Microsoft Edge"
    )


def _healthy_resources() -> ResourceSummary:
    return ResourceSummary(
        cpu_mean_ratio=0.5,
        cpu_peak_over_90_seconds=0,
        memory_peak_ratio=0.6,
        memory_growth_ratio=0,
        database_connection_peak_ratio=0.4,
        blocked_over_two_seconds=0,
        pool_timeouts=0,
        server_5xx=0,
        sampling_gap_seconds=0,
    )


def _saturation_window(
    users: int,
    window: int,
    *,
    violated: bool,
) -> performance_saturation.SaturationWindowResult:
    violations = ("read::tasks.list p95 1001ms > 1000ms",) if violated else ()
    requests = (
        RequestStat(
            "read::tasks.list",
            100,
            100,
            1001 if violated else 900,
            1500,
            0,
        ),
        RequestStat("write::tasks.create", 10, 200, 700, 1400, 0),
    )
    return performance_saturation.SaturationWindowResult(
        users=users,
        window=window,
        started_at="2026-10-05T00:00:00+00:00",
        ended_at="2026-10-05T00:01:00+00:00",
        requests=requests,
        resources=_healthy_resources(),
        request_violations=violations,
        violations=violations,
    )


def _saturation_level(
    users: int,
    windows: list[performance_saturation.SaturationWindowResult],
) -> performance_saturation.SaturationLevelResult:
    violations = performance_saturation.merge_violations(
        *(window.violations for window in windows)
    )
    return performance_saturation.SaturationLevelResult(
        users=users,
        completed=(
            len(windows)
            == performance_saturation.SATURATION_WINDOWS_PER_LEVEL
        ),
        started_at=windows[0].started_at,
        ended_at=windows[-1].ended_at,
        requests=performance_saturation.aggregate_request_stats(windows),
        resources=_healthy_resources(),
        request_violations=violations,
        violations=violations,
    )


def test_saturation_parses_locust_window_stats_and_applies_request_gate():
    requests = performance_saturation.locust_request_stats(
        {
            "stats": [
                {
                    "name": "auth::login",
                    "num_requests": 0,
                    "num_failures": 0,
                    "median_response_time": 0,
                    "response_time_percentile_0.95": None,
                    "response_time_percentile_0.99": None,
                },
                {
                    "name": "read::tasks.list",
                    "num_requests": 100,
                    "num_failures": 0,
                    "median_response_time": 100,
                    "response_time_percentile_0.95": 1001,
                    "response_time_percentile_0.99": 1500,
                },
                {
                    "name": "write::tasks.create",
                    "num_requests": 10,
                    "num_failures": 0,
                    "median_response_time": 200,
                    "response_time_percentile_0.95": 700,
                    "response_time_percentile_0.99": 1400,
                },
                {
                    "name": "Aggregated",
                    "num_requests": 110,
                    "num_failures": 0,
                    "median_response_time": 100,
                    "response_time_percentile_0.95": 1001,
                    "response_time_percentile_0.99": 1500,
                },
            ]
        }
    )

    assert [request.name for request in requests] == [
        "read::tasks.list",
        "write::tasks.create",
    ]
    assert performance_saturation.request_gate_violations(60, requests) == (
        "read::tasks.list p95 1001ms > 1000ms",
    )


def test_saturation_uses_manual_locust_web_mode_and_stops_at_capacity_boundary(
    tmp_path: Path,
):
    run_state = state(tmp_path)
    command = performance_run._locust_command(
        run_state,
        users=10,
        spawn_rate=10,
        duration_seconds=1800,
        prefix="requests",
        user_classes=("StandardUser",),
        web_port=8089,
        autostart=False,
    )
    assert "--web-port" in command
    assert "--autostart" not in command
    assert "--users" not in command
    assert "--run-time" not in command

    user_levels: list[int] = []
    resets: list[None] = []
    sleeps: list[float] = []
    captured: list[tuple[int, int]] = []

    def capture(users, window, started_at, ended_at):
        captured.append((users, window))
        return _saturation_window(users, window, violated=users >= 60)

    result = performance_saturation.execute_saturation_schedule(
        set_users=user_levels.append,
        reset_stats=lambda: resets.append(None),
        capture_window=capture,
        summarize_level=_saturation_level,
        sleep=sleeps.append,
        now=lambda: datetime(2026, 10, 5, tzinfo=UTC),
    )

    assert user_levels == [10, 20, 30, 40, 50, 60]
    assert len(resets) == 18
    assert sleeps == [60] * 18
    assert captured[-3:] == [(60, 1), (60, 2), (60, 3)]
    assert result.stop_reason == "three_consecutive_request_gate_violations"
    assert result.capacity_boundary_users == 60
    assert result.maximum_stable_users == 50
    assert result.first_unstable_users == 60
    assert result.minimum_capacity_passed
    assert result.hard_gate_violations == ()
    assert result.representative.users == 50


def test_saturation_failure_below_50_is_a_hard_gate():
    result = performance_saturation.execute_saturation_schedule(
        set_users=lambda users: None,
        reset_stats=lambda: None,
        capture_window=lambda users, window, started_at, ended_at: (
            _saturation_window(users, window, violated=users >= 40)
        ),
        summarize_level=_saturation_level,
        sleep=lambda seconds: None,
        now=lambda: datetime(2026, 10, 5, tzinfo=UTC),
    )

    assert result.capacity_boundary_users == 40
    assert result.maximum_stable_users == 30
    assert not result.minimum_capacity_passed
    assert result.representative.users == 40
    assert any(
        violation.startswith("40 users:")
        for violation in result.hard_gate_violations
    )
    assert (
        "minimum capacity levels not completed: 50"
        in result.hard_gate_violations
    )


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
