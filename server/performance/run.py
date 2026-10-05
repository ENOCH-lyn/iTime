import argparse
import asyncio
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from cryptography.fernet import Fernet

from itime import __version__
from itime.config import Settings
from itime.database import Database
from itime.statistics.time_math import activity_date_at
from performance.ai_isolation import (
    AI_BASELINE_SECONDS,
    AI_JOB_USERS,
    AI_STANDARD_USERS,
    AIIsolationState,
    build_ai_isolation_summary,
    read_ai_isolation_state,
)
from performance.contracts import (
    SCENARIOS,
    AccountCredential,
    GateResult,
    RequestStat,
    ResourceSummary,
    RunConfig,
    Scenario,
    ScenarioConfig,
    SeedManifest,
    SeedProfile,
)
from performance.maintenance import (
    MAINTENANCE_SPAWN_RATE,
    MAINTENANCE_USERS,
    MAINTENANCE_WINDOW_SECONDS,
    CleanupBatchResult,
    MaintenanceState,
    build_maintenance_summary,
    parse_cleanup_batch_result,
    prepare_maintenance_batch,
    read_maintenance_state,
)
from performance.queues import (
    QUEUE_BACKLOG_SIZE,
    QUEUE_DRAIN_TIMEOUT_SECONDS,
    QUEUE_MINIMUM_OBSERVATION_SECONDS,
    QUEUE_SERVICES,
    QUEUE_SPAWN_RATE,
    QUEUE_USERS,
    QueueDrainObservation,
    QueueKind,
    QueueState,
    QueueTypeResult,
    build_queue_run_summary,
    build_queue_type_result,
    prepare_queue_backlog,
    wait_for_queue_drain,
)
from performance.report import (
    SensitiveArtifactError,
    assert_no_sensitive_artifacts,
    build_report,
    read_resource_samples,
)
from performance.safety import (
    OUTPUT_ROOT,
    CleanupManifest,
    exclusive_run_lock,
    require_safe_cleanup,
    require_safe_run,
)
from performance.saturation import (
    SATURATION_LEVELS,
    SATURATION_SPAWN_RATE,
    SATURATION_WINDOW_SECONDS,
    SATURATION_WINDOWS_PER_LEVEL,
    SaturationLevelResult,
    SaturationWindowResult,
    build_saturation_level,
    build_saturation_window,
    execute_saturation_schedule,
    locust_request_stats,
)
from performance.seed import seed_dataset, write_private_credentials
from performance.seed_core import SYNTHETIC_LOGIN_SECRET
from performance.seed_verify import collect_invariants, verify_dataset

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"
WEB = ROOT / "web"
COMPOSE_FILE = ROOT / "compose.performance.yaml"
HOST_GATEWAY_BIND = "0.0.0.0"  # noqa: S104
HOST_ENVIRONMENT_KEYS = (
    "CI",
    "FORCE_COLOR",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "NO_COLOR",
    "NODE_OPTIONS",
    "PATH",
    "PLAYWRIGHT_BROWSERS_PATH",
    "SHELL",
    "TEMP",
    "TMP",
    "TMPDIR",
)
LIFECYCLE_STAGES = (
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
)
CLEANUP_STAGES = ("cleanup", "verify-clean")
BASELINE_SCENARIOS = (
    "steady",
    "peak",
    "history",
    "saturation",
    "ai-isolation",
    "queues",
    "maintenance",
    "soak",
)


@dataclass
class ManagedProcess:
    name: str
    process: subprocess.Popen[str]
    log_handle: Any


@dataclass
class RunState:
    config: RunConfig
    scenario: str
    env: dict[str, str]
    scenario_config: ScenarioConfig | None = None
    database_url: str | None = None
    manifest: SeedManifest | None = None
    processes: list[ManagedProcess] = field(default_factory=list)
    registered_paths: list[Path] = field(default_factory=list)
    gate_result: GateResult | None = None
    failed_stage: str | None = None

    def compose(self, *args: str) -> list[str]:
        return [
            "docker",
            "--context",
            self.config.docker_context,
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "--project-name",
            self.config.compose_project,
            *args,
        ]


@dataclass(frozen=True)
class PerformanceRunResult:
    passed: bool
    run_dir: Path
    remaining_resources: tuple[str, ...]
    error: str | None = None


def checked(
    command: list[str],
    *,
    env: dict[str, str],
    timeout: int,
    cwd: Path = ROOT,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        command,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=True,
    )


def _write_json_atomic(path: Path, payload: object) -> None:
    assert_no_sensitive_artifacts(payload)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    os.replace(temporary, path)


def _safe_error(error: BaseException) -> str:
    value = str(error)
    try:
        assert_no_sensitive_artifacts(value)
    except SensitiveArtifactError:
        return "错误详情包含敏感材料，已拒绝写入。"
    return value


def _write_partial_report(
    state: RunState,
    *,
    stage: str,
    error: BaseException,
    cleanup_errors: Sequence[str],
) -> None:
    state.config.output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    _write_json_atomic(
        state.config.output_dir / "partial-report.json",
        {
            "scenario": state.scenario,
            "run_id": state.config.run_id,
            "failed_stage": stage,
            "error_type": type(error).__name__,
            "error": _safe_error(error),
            "cleanup_errors": list(cleanup_errors),
        },
    )


def execute_lifecycle(
    state: RunState,
    *,
    execute: Callable[[RunState, str], object],
) -> PerformanceRunResult:
    state.config.output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    primary_error: BaseException | None = None
    cleanup_errors: list[str] = []
    remaining: tuple[str, ...] = ()
    try:
        for stage in LIFECYCLE_STAGES:
            state.failed_stage = stage
            value = execute(state, stage)
            if stage == "build-report" and isinstance(value, GateResult):
                state.gate_result = value
    except BaseException as exc:
        primary_error = exc
    finally:
        for stage in CLEANUP_STAGES:
            try:
                value = execute(state, stage)
                if stage == "verify-clean" and value is not None:
                    remaining = tuple(str(item) for item in value)
            except BaseException as exc:
                cleanup_errors.append(f"{stage}: {_safe_error(exc)}")

    if primary_error is not None:
        _write_partial_report(
            state,
            stage=state.failed_stage or "unknown",
            error=primary_error,
            cleanup_errors=cleanup_errors,
        )
        return PerformanceRunResult(
            passed=False,
            run_dir=state.config.output_dir,
            remaining_resources=remaining,
            error=_safe_error(primary_error),
        )
    if cleanup_errors or remaining:
        error = "; ".join((*cleanup_errors, *remaining))
        _write_partial_report(
            state,
            stage="cleanup",
            error=RuntimeError(error),
            cleanup_errors=cleanup_errors,
        )
        return PerformanceRunResult(
            passed=False,
            run_dir=state.config.output_dir,
            remaining_resources=remaining,
            error=error,
        )
    if state.gate_result is None:
        error = "报告阶段未返回门禁结果"
        _write_partial_report(
            state,
            stage="build-report",
            error=RuntimeError(error),
            cleanup_errors=(),
        )
        return PerformanceRunResult(False, state.config.output_dir, (), error)
    return PerformanceRunResult(
        passed=state.gate_result.passed,
        run_dir=state.config.output_dir,
        remaining_resources=(),
        error=(
            None
            if state.gate_result.passed
            else "; ".join(state.gate_result.failures)
        ),
    )


def _available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _new_state(scenario_name: str, *, run_id: str | None = None) -> RunState:
    scenario = Scenario(scenario_name)
    scenario_config = SCENARIOS[scenario]
    active_run_id = run_id or secrets.token_hex(4)
    output_dir = OUTPUT_ROOT / active_run_id
    smtp_port = _available_port()
    upstream_port = _available_port()
    web_port = _available_port()
    encryption_keys = [Fernet.generate_key().decode() for _ in range(4)]
    env = {
        **os.environ,
        "ITIME_PERF_RUN_ID": active_run_id,
        "ITIME_PERF_DATABASE": f"itime_perf_{active_run_id}",
        "ITIME_PERF_POSTGRES_PASSWORD": secrets.token_hex(24),
        "ITIME_PERF_ENCRYPTION_KEY": encryption_keys[0],
        "ITIME_PERF_AI_ENCRYPTION_KEY": encryption_keys[1],
        "ITIME_PERF_CALENDAR_KEY": encryption_keys[2],
        "ITIME_PERF_EXPORT_KEY": encryption_keys[3],
        "ITIME_PERF_UPSTREAM_SMTP_PORT": str(smtp_port),
        "ITIME_PERF_UPSTREAM_HTTP_PORT": str(upstream_port),
        "ITIME_PERF_WEB_PORT": str(web_port),
        "ITIME_PERF_IMAGE_TAG": "local",
        "ITIME_PERF_FETCH_DELAY_SECONDS": "0.25",
    }
    config = RunConfig(
        run_id=active_run_id,
        environment="test",
        http_origin="http://127.0.0.1:1",
        database_name=f"itime_perf_{active_run_id}",
        database_host="127.0.0.1",
        guard_value=f"itime-performance:{active_run_id}",
        docker_context="colima-itime-perf",
        output_dir=output_dir,
    )
    return RunState(
        config=config,
        scenario=scenario_name,
        env=env,
        scenario_config=scenario_config,
    )


def _compose_port(state: RunState, service: str, container_port: int) -> int:
    result = checked(
        state.compose("port", service, str(container_port)),
        env=state.env,
        timeout=10,
    )
    try:
        return int(result.stdout.strip().rsplit(":", 1)[1])
    except (IndexError, ValueError) as exc:
        raise RuntimeError(f"无法读取 {service} 映射端口") from exc


def _settings(state: RunState) -> Settings:
    if state.database_url is None:
        raise RuntimeError("性能数据库地址尚未初始化")
    return Settings(
        environment="test",
        database_url=state.database_url,
        public_origin=state.config.http_origin,
        encryption_key=state.env["ITIME_PERF_ENCRYPTION_KEY"],
        ai_encryption_key=state.env["ITIME_PERF_AI_ENCRYPTION_KEY"],
        calendar_subscription_encryption_key=state.env["ITIME_PERF_CALENDAR_KEY"],
        export_encryption_key=state.env["ITIME_PERF_EXPORT_KEY"],
        db_pool_size=2,
        db_max_overflow=0,
    )


def _performance_anchor_date(at: datetime) -> str:
    return activity_date_at(at, "UTC").isoformat()


def _start_process(
    state: RunState,
    name: str,
    command: list[str],
    *,
    cwd: Path = SERVER,
    env: dict[str, str] | None = None,
) -> ManagedProcess:
    logs = state.config.output_dir / "logs"
    logs.mkdir(mode=0o700, parents=True, exist_ok=True)
    log_path = logs / f"{name}.log"
    log_handle = log_path.open("w")
    os.chmod(log_path, 0o600)
    process = subprocess.Popen(  # noqa: S603
        command,
        cwd=cwd,
        env=env or state.env,
        text=True,
        stdout=log_handle,
        stderr=log_handle,
        start_new_session=True,
    )
    managed = ManagedProcess(name, process, log_handle)
    state.processes.append(managed)
    return managed


def _stop_process(managed: ManagedProcess) -> None:
    if managed.process.poll() is None:
        try:
            os.killpg(os.getpgid(managed.process.pid), signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            managed.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(managed.process.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
            managed.process.wait(timeout=5)
    managed.log_handle.close()


def _wait_http(
    origin: str,
    process: ManagedProcess,
    *,
    path: str = "/health",
    timeout: float = 30,
) -> None:
    deadline = time.monotonic() + timeout
    with httpx.Client(base_url=origin, timeout=2, trust_env=False) as client:
        while time.monotonic() < deadline:
            if process.process.poll() is not None:
                raise RuntimeError(f"{process.name} 在就绪前退出")
            try:
                response = client.get(path)
                if response.status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(0.1)
    raise TimeoutError(f"{process.name} 未在 {timeout:g} 秒内就绪")


def _wait_api(state: RunState, *, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    with httpx.Client(
        base_url=state.config.http_origin,
        timeout=2,
        trust_env=False,
    ) as client:
        while time.monotonic() < deadline:
            try:
                response = client.get("/api/ready")
                if response.status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(0.1)
    raise TimeoutError(f"API 未在 {timeout:g} 秒内就绪")


def _locust_command(
    state: RunState,
    *,
    users: int,
    spawn_rate: float,
    duration_seconds: int,
    prefix: str,
    user_classes: tuple[str, ...],
    web_port: int | None = None,
    autostart: bool = True,
) -> list[str]:
    if web_port is None:
        run_mode = ["--headless", "--only-summary"]
    else:
        run_mode = [
            *(
                ["--autostart", "--autoquit", "5"]
                if autostart
                else []
            ),
            "--web-host",
            "127.0.0.1",
            "--web-port",
            str(web_port),
        ]
    load_options = (
        [
            "--users",
            str(users),
            "--spawn-rate",
            str(spawn_rate),
            "--run-time",
            f"{duration_seconds}s",
        ]
        if web_port is None or autostart
        else []
    )
    return [
        sys.executable,
        "-m",
        "locust",
        "-f",
        "performance/locustfile.py",
        *run_mode,
        "--host",
        state.config.http_origin,
        *load_options,
        "--csv",
        str(state.config.output_dir / prefix),
        *user_classes,
    ]


def _write_command_output(
    state: RunState,
    name: str,
    result: subprocess.CompletedProcess[str],
) -> None:
    path = state.config.output_dir / "logs" / f"{name}.log"
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(result.stdout + result.stderr)
    os.chmod(path, 0o600)


def _run_logged(
    state: RunState,
    name: str,
    command: list[str],
    *,
    env: dict[str, str],
    timeout: int,
    cwd: Path,
) -> subprocess.CompletedProcess[str]:
    try:
        result = checked(command, env=env, timeout=timeout, cwd=cwd)
    except subprocess.CalledProcessError as exc:
        result = subprocess.CompletedProcess(
            command,
            exc.returncode,
            exc.stdout or "",
            exc.stderr or "",
        )
        _write_command_output(state, name, result)
        raise RuntimeError(
            f"{name} 失败，退出码 {exc.returncode}；详情见 logs/{name}.log"
        ) from exc
    _write_command_output(state, name, result)
    return result


def _host_environment() -> dict[str, str]:
    return {
        key: os.environ[key]
        for key in HOST_ENVIRONMENT_KEYS
        if key in os.environ
    }


def _browser_environment(
    state: RunState,
    *,
    web_origin: str,
    locust_pid: int,
) -> dict[str, str]:
    credentials = state.env.get(
        "ITIME_PERF_BROWSER_CREDENTIALS",
        state.env.get("ITIME_PERF_CREDENTIALS"),
    )
    if not credentials:
        raise RuntimeError("浏览器性能验证缺少账号文件")
    return {
        **_host_environment(),
        "ITIME_API_ORIGIN": state.config.http_origin,
        "ITIME_PERF_WEB_ORIGIN": web_origin,
        "ITIME_PERF_BROWSER_ACCOUNT_FILE": credentials,
        "ITIME_PERF_RUN_MANIFEST_FILE": str(
            state.config.output_dir / "manifest.json"
        ),
        "ITIME_PERF_LOCUST_PID": str(locust_pid),
        "ITIME_PERF_BROWSER_LABEL": "chromium",
        "ITIME_PERF_RUN_ID": state.config.run_id,
    }


def _edge_executable_path() -> str | None:
    configured = os.environ.get(
        "ITIME_PERF_EDGE_EXECUTABLE_PATH",
        os.environ.get("ITIME_E2E_BROWSER_EXECUTABLE_PATH", ""),
    )
    if configured:
        path = Path(configured).expanduser()
        if not path.is_file():
            raise RuntimeError(f"指定的 Microsoft Edge 不存在：{path}")
        return str(path)
    candidates = (
        Path.home()
        / "Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
    )
    return next((str(path) for path in candidates if path.is_file()), None)


def _require_process_alive(process: ManagedProcess, purpose: str) -> None:
    return_code = process.process.poll()
    if return_code is not None:
        raise RuntimeError(f"{purpose} 提前退出，退出码 {return_code}")


def _wait_for_locust_users(
    process: ManagedProcess,
    stats_origin: str,
    *,
    expected: int,
    timeout: float = 30,
) -> None:
    deadline = time.monotonic() + timeout
    with httpx.Client(base_url=stats_origin, timeout=2, trust_env=False) as client:
        while time.monotonic() < deadline:
            _require_process_alive(process, "UI 背景负载")
            try:
                response = client.get("/stats/requests")
                if (
                    response.status_code == 200
                    and int(response.json().get("user_count", -1)) == expected
                ):
                    return
            except (httpx.TransportError, ValueError):
                pass
            time.sleep(0.1)
    raise TimeoutError(f"UI 背景负载未在 {timeout:g} 秒内达到 {expected} 用户")


def _profile_for(state: RunState) -> SeedProfile:
    assert state.scenario_config is not None
    if state.scenario_config.dataset == "history":
        return SeedProfile.history()
    if state.scenario == "smoke":
        return SeedProfile.standard(
            accounts=2,
            active_accounts=2,
            tasks_per_active=30,
            sync_events_per_active=200,
        )
    active_accounts = max(50, state.scenario_config.users)
    return SeedProfile.standard(
        accounts=max(100, active_accounts),
        active_accounts=active_accounts,
    )


def _stage_check_runtime(state: RunState) -> None:
    require_safe_run(state.config)
    info = checked(
        [
            "docker",
            "--context",
            state.config.docker_context,
            "info",
            "--format",
            "{{json .}}",
        ],
        env=state.env,
        timeout=30,
    )
    details = json.loads(info.stdout)
    cpus = int(details["NCPU"])
    memory = int(details["MemTotal"])
    if cpus != 2 or not int(3.75 * 1024**3) <= memory <= int(4.25 * 1024**3):
        raise RuntimeError(
            "性能 Docker 资源不匹配："
            f"期望 2 vCPU/4 GiB，实际 {cpus} vCPU/{memory} bytes"
        )
    built = checked(state.compose("build"), env=state.env, timeout=900)
    _write_command_output(state, "build", built)
    commit = checked(
        ["git", "rev-parse", "HEAD"],
        env=state.env,
        timeout=10,
    ).stdout.strip()
    dirty = bool(
        checked(
            ["git", "status", "--porcelain"],
            env=state.env,
            timeout=10,
        ).stdout.strip()
    )
    _write_json_atomic(
        state.config.output_dir / "manifest.json",
        {
            "run_id": state.config.run_id,
            "scenario": state.scenario,
            "service_version": __version__,
            "users": state.scenario_config.users if state.scenario_config else 0,
            "environment": "test",
            "commit": commit,
            "dirty_worktree": dirty,
            "started_at": datetime.now(UTC).isoformat(),
            "cpu_count": cpus,
            "memory_bytes": memory,
        },
    )


def _stage_start_postgres(state: RunState) -> None:
    result = checked(
        state.compose("up", "-d", "--wait", "postgres"),
        env=state.env,
        timeout=180,
    )
    _write_command_output(state, "start-postgres", result)
    database_port = _compose_port(state, "postgres", 5432)
    password = state.env["ITIME_PERF_POSTGRES_PASSWORD"]
    state.database_url = (
        f"postgresql+psycopg://itime:{password}@127.0.0.1:"
        f"{database_port}/{state.config.database_name}"
    )


def _stage_migrate(state: RunState) -> None:
    result = checked(
        state.compose("run", "--rm", "migrate"),
        env=state.env,
        timeout=180,
    )
    _write_command_output(state, "migrate", result)


def _stage_create_extension_and_guard(state: RunState) -> None:
    statement = (
        "CREATE EXTENSION IF NOT EXISTS pg_stat_statements; "
        f"COMMENT ON DATABASE {state.config.database_name} "
        f"IS '{state.config.guard_value}';"
    )
    checked(
        state.compose(
            "exec",
            "-T",
            "postgres",
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "itime",
            "-d",
            state.config.database_name,
            "-c",
            statement,
        ),
        env=state.env,
        timeout=30,
    )
    verified = checked(
        state.compose(
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "itime",
            "-d",
            state.config.database_name,
            "-Atc",
            (
                "SELECT shobj_description(oid, 'pg_database') "
                "FROM pg_database WHERE datname=current_database()"
            ),
        ),
        env=state.env,
        timeout=30,
    )
    if verified.stdout.strip() != state.config.guard_value:
        raise RuntimeError("数据库安全标记写入后校验失败")


def _stage_seed(state: RunState) -> None:
    private_dir = state.config.output_dir / "private"
    private_dir.mkdir(mode=0o700)
    credentials = private_dir / "accounts.json"
    state.registered_paths.append(credentials)
    profile = _profile_for(state)
    upstream_origin = (
        "http://host.docker.internal:"
        f"{state.env['ITIME_PERF_UPSTREAM_HTTP_PORT']}"
    )
    seed = 20261005
    state.manifest = asyncio.run(
        seed_dataset(
            _settings(state),
            profile,
            seed=seed,
            at=datetime.now(UTC),
            credentials_path=credentials,
            upstream_origin=upstream_origin,
        )
    )
    state.env["ITIME_PERF_CREDENTIALS"] = str(credentials)
    if state.scenario == Scenario.UI_LOAD:
        if profile.active_accounts >= profile.accounts:
            raise RuntimeError("UI 性能场景需要至少一个无负载历史的浏览器账号")
        browser_credentials = private_dir / "browser-account.json"
        state.registered_paths.append(browser_credentials)
        write_private_credentials(
            browser_credentials,
            (
                AccountCredential(
                    email=(
                        f"perf-{profile.name}-{seed}-"
                        f"{profile.active_accounts:03d}@performance.example.com"
                    ),
                    password=SYNTHETIC_LOGIN_SECRET,
                    dataset="browser",
                ),
            ),
        )
        state.env["ITIME_PERF_BROWSER_CREDENTIALS"] = str(browser_credentials)
    state.env["ITIME_PERF_ANCHOR_DATE"] = _performance_anchor_date(
        datetime.now(UTC)
    )
    _write_json_atomic(
        state.config.output_dir / "seed-manifest.json",
        state.manifest.to_public_dict(),
    )


def _stage_start_services(state: RunState) -> None:
    upstream_port = state.env["ITIME_PERF_UPSTREAM_HTTP_PORT"]
    smtp_port = state.env["ITIME_PERF_UPSTREAM_SMTP_PORT"]
    upstream = _start_process(
        state,
        "upstream",
        [
            sys.executable,
            "-m",
            "performance.upstream",
            "--host",
            HOST_GATEWAY_BIND,
            "--port",
            upstream_port,
            "--smtp-port",
            smtp_port,
            "--mode",
            "normal",
            "--initial-delay",
            "0.05",
            "--chunk-delay",
            "0.05",
        ],
    )
    _wait_http(
        f"http://127.0.0.1:{upstream_port}",
        upstream,
    )
    services = ["api", "ai-worker", "mail-worker"]
    if state.scenario == Scenario.MAINTENANCE:
        services.append("maintenance-runner")
    else:
        services.extend(QUEUE_SERVICES.values())
    result = checked(
        state.compose(
            "up",
            "-d",
            "--wait",
            "--scale",
            "ai-worker=2",
            *services,
        ),
        env=state.env,
        timeout=180,
    )
    _write_command_output(state, "start-services", result)
    if state.scenario == Scenario.QUEUES:
        paused = checked(
            state.compose("pause", *QUEUE_SERVICES.values()),
            env=state.env,
            timeout=30,
        )
        _write_command_output(state, "pause-queue-workers", paused)


def _stage_wait_ready(state: RunState) -> None:
    api_port = _compose_port(state, "api", 8000)
    state.config = replace(
        state.config,
        http_origin=f"http://127.0.0.1:{api_port}",
    )
    require_safe_run(state.config)
    _wait_api(state)


def _run_locust(
    state: RunState,
    *,
    prefix: str,
    users: int,
    spawn_rate: float,
    duration_seconds: int,
    user_classes: tuple[str, ...],
) -> None:
    state.env["ITIME_PERF_WORKLOAD"] = state.scenario
    result = checked(
        _locust_command(
            state,
            users=users,
            spawn_rate=spawn_rate,
            duration_seconds=duration_seconds,
            prefix=prefix,
            user_classes=user_classes,
        ),
        env=state.env,
        timeout=duration_seconds + 60,
        cwd=SERVER,
    )
    _write_command_output(state, prefix, result)


def _capture_saturation_window(
    state: RunState,
    client: httpx.Client,
    *,
    users: int,
    window: int,
    started_at: datetime,
    ended_at: datetime,
) -> SaturationWindowResult:
    response = client.get("/stats/requests")
    response.raise_for_status()
    requests = locust_request_stats(response.json())
    resources = read_resource_samples(
        state.config.output_dir / "resources.csv",
        started_at=started_at,
        ended_at=ended_at,
    )
    return build_saturation_window(
        users=users,
        window=window,
        started_at=started_at,
        ended_at=ended_at,
        requests=requests,
        resources=resources,
    )


def _summarize_saturation_level(
    state: RunState,
    users: int,
    windows: Sequence[SaturationWindowResult],
) -> SaturationLevelResult:
    if not windows:
        raise RuntimeError(f"{users} 用户档位没有窗口结果")
    started_at = datetime.fromisoformat(windows[0].started_at)
    ended_at = datetime.fromisoformat(windows[-1].ended_at)
    resources = read_resource_samples(
        state.config.output_dir / "resources.csv",
        started_at=started_at,
        ended_at=ended_at,
    )
    return build_saturation_level(
        users,
        windows,
        resources,
    )


def _set_locust_users(
    process: ManagedProcess,
    client: httpx.Client,
    stats_origin: str,
    users: int,
) -> None:
    _require_process_alive(process, "饱和负载")
    response = client.post(
        "/swarm",
        data={
            "user_count": str(users),
            "spawn_rate": str(SATURATION_SPAWN_RATE),
        },
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise RuntimeError(f"Locust 无法切换到 {users} 用户")
    _wait_for_locust_users(
        process,
        stats_origin,
        expected=users,
        timeout=max(30, users / SATURATION_SPAWN_RATE + 10),
    )


def _reset_locust_stats(
    process: ManagedProcess,
    client: httpx.Client,
) -> None:
    _require_process_alive(process, "饱和负载")
    response = client.get("/stats/reset")
    response.raise_for_status()
    if response.text.strip() != "ok":
        raise RuntimeError("Locust 统计重置未确认成功")


def _run_saturation(state: RunState) -> None:
    locust_port = _available_port()
    stats_origin = f"http://127.0.0.1:{locust_port}"
    state.env["ITIME_PERF_WORKLOAD"] = state.scenario
    load = _start_process(
        state,
        "locust-saturation",
        _locust_command(
            state,
            users=SATURATION_LEVELS[0],
            spawn_rate=SATURATION_SPAWN_RATE,
            duration_seconds=(
                len(SATURATION_LEVELS)
                * SATURATION_WINDOWS_PER_LEVEL
                * SATURATION_WINDOW_SECONDS
            ),
            prefix="requests",
            user_classes=("StandardUser",),
            web_port=locust_port,
            autostart=False,
        ),
    )
    try:
        _wait_http(stats_origin, load, path="/stats/requests")
        with httpx.Client(
            base_url=stats_origin,
            timeout=10,
            trust_env=False,
        ) as client:
            summary = execute_saturation_schedule(
                set_users=lambda users: _set_locust_users(
                    load,
                    client,
                    stats_origin,
                    users,
                ),
                reset_stats=lambda: _reset_locust_stats(load, client),
                capture_window=lambda users, window, started_at, ended_at: (
                    _capture_saturation_window(
                        state,
                        client,
                        users=users,
                        window=window,
                        started_at=started_at,
                        ended_at=ended_at,
                    )
                ),
                summarize_level=lambda users, windows: (
                    _summarize_saturation_level(state, users, windows)
                ),
            )
            response = client.get("/stop")
            response.raise_for_status()
        _write_json_atomic(
            state.config.output_dir / "saturation.json",
            {
                "schema_version": 1,
                **asdict(summary),
            },
        )
    finally:
        _stop_process(load)


async def _prepare_queue(
    state: RunState,
    kind: QueueKind,
) -> QueueState:
    settings = _settings(state)
    database = Database(settings)
    try:
        upstream_origin = (
            "http://host.docker.internal:"
            f"{state.env['ITIME_PERF_UPSTREAM_HTTP_PORT']}"
        )
        return await prepare_queue_backlog(
            database,
            settings,
            kind,
            count=QUEUE_BACKLOG_SIZE,
            upstream_origin=upstream_origin,
        )
    finally:
        await database.close()


async def _wait_for_queue(
    state: RunState,
    kind: QueueKind,
    initial: QueueState,
) -> QueueDrainObservation:
    settings = _settings(state)
    database = Database(settings)
    try:
        return await wait_for_queue_drain(
            database,
            kind,
            initial,
            expected=QUEUE_BACKLOG_SIZE,
        )
    finally:
        await database.close()


def _queue_worker_action(
    state: RunState,
    kind: QueueKind,
    action: str,
) -> None:
    service = QUEUE_SERVICES[kind]
    result = checked(
        state.compose(action, service),
        env=state.env,
        timeout=30,
    )
    _write_command_output(state, f"{action}-{service}", result)


def _unpause_queue_workers(state: RunState) -> None:
    result = subprocess.run(  # noqa: S603
        state.compose("unpause", *QUEUE_SERVICES.values()),
        cwd=ROOT,
        env=state.env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    _write_command_output(state, "unpause-queue-workers", result)


def _run_queue_type(
    state: RunState,
    load: ManagedProcess,
    client: httpx.Client,
    kind: QueueKind,
) -> QueueTypeResult:
    initial = asyncio.run(_prepare_queue(state, kind))
    _reset_locust_stats(load, client)
    started_at = datetime.now(UTC)
    _queue_worker_action(state, kind, "unpause")
    try:
        observation = asyncio.run(_wait_for_queue(state, kind, initial))
        remaining_observation = (
            QUEUE_MINIMUM_OBSERVATION_SECONDS
            - observation.drain_seconds
        )
        if remaining_observation > 0:
            time.sleep(remaining_observation)
        _require_process_alive(load, "队列场景普通 API 负载")
        ended_at = datetime.now(UTC)
        response = client.get("/stats/requests")
        response.raise_for_status()
        requests = locust_request_stats(response.json())
        resources = read_resource_samples(
            state.config.output_dir / "resources.csv",
            started_at=started_at,
            ended_at=ended_at,
        )
        return build_queue_type_result(
            kind=kind,
            initial=initial,
            observation=observation,
            started_at=started_at,
            ended_at=ended_at,
            requests=requests,
            resources=resources,
        )
    finally:
        _queue_worker_action(state, kind, "pause")


def _run_queues(state: RunState) -> None:
    locust_port = _available_port()
    stats_origin = f"http://127.0.0.1:{locust_port}"
    state.env["ITIME_PERF_WORKLOAD"] = state.scenario
    load = _start_process(
        state,
        "locust-queues",
        _locust_command(
            state,
            users=QUEUE_USERS,
            spawn_rate=QUEUE_SPAWN_RATE,
            duration_seconds=(
                len(QUEUE_SERVICES) * QUEUE_DRAIN_TIMEOUT_SECONDS
            ),
            prefix="requests",
            user_classes=("StandardUser",),
            web_port=locust_port,
            autostart=False,
        ),
    )
    try:
        _wait_http(stats_origin, load, path="/stats/requests")
        with httpx.Client(
            base_url=stats_origin,
            timeout=10,
            trust_env=False,
        ) as client:
            _set_locust_users(
                load,
                client,
                stats_origin,
                QUEUE_USERS,
            )
            results = tuple(
                _run_queue_type(state, load, client, kind)
                for kind in QueueKind
            )
            response = client.get("/stop")
            response.raise_for_status()
        _write_json_atomic(
            state.config.output_dir / "queue-results.json",
            {
                "schema_version": 1,
                **asdict(build_queue_run_summary(results)),
            },
        )
    finally:
        _unpause_queue_workers(state)
        _stop_process(load)


async def _prepare_maintenance(
    state: RunState,
) -> MaintenanceState:
    settings = _settings(state)
    database = Database(settings)
    try:
        return await prepare_maintenance_batch(database, settings)
    finally:
        await database.close()


async def _read_maintenance(
    state: RunState,
) -> MaintenanceState:
    database = Database(_settings(state))
    try:
        return await read_maintenance_state(database)
    finally:
        await database.close()


def _run_maintenance_batch(state: RunState) -> CleanupBatchResult:
    result = _run_logged(
        state,
        "maintenance-batch",
        state.compose(
            "exec",
            "-T",
            "maintenance-runner",
            "python",
            "-m",
            "performance.maintenance",
        ),
        env=state.env,
        timeout=180,
        cwd=ROOT,
    )
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("维护批次未输出结果")
    try:
        payload = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise RuntimeError("维护批次输出不是有效 JSON") from exc
    return parse_cleanup_batch_result(payload)


def _capture_locust_window(
    state: RunState,
    load: ManagedProcess,
    client: httpx.Client,
    *,
    started_at: datetime,
    ended_at: datetime,
) -> tuple[tuple[RequestStat, ...], ResourceSummary]:
    _require_process_alive(load, "维护场景普通 API 负载")
    response = client.get("/stats/requests")
    response.raise_for_status()
    return (
        locust_request_stats(response.json()),
        read_resource_samples(
            state.config.output_dir / "resources.csv",
            started_at=started_at,
            ended_at=ended_at,
        ),
    )


def _run_maintenance(state: RunState) -> None:
    initial = asyncio.run(_prepare_maintenance(state))
    locust_port = _available_port()
    stats_origin = f"http://127.0.0.1:{locust_port}"
    state.env["ITIME_PERF_WORKLOAD"] = state.scenario
    load = _start_process(
        state,
        "locust-maintenance",
        _locust_command(
            state,
            users=MAINTENANCE_USERS,
            spawn_rate=MAINTENANCE_SPAWN_RATE,
            duration_seconds=MAINTENANCE_WINDOW_SECONDS * 2,
            prefix="requests",
            user_classes=("StandardUser",),
            web_port=locust_port,
            autostart=False,
        ),
    )
    try:
        _wait_http(stats_origin, load, path="/stats/requests")
        with httpx.Client(
            base_url=stats_origin,
            timeout=10,
            trust_env=False,
        ) as client:
            _set_locust_users(
                load,
                client,
                stats_origin,
                MAINTENANCE_USERS,
            )

            _reset_locust_stats(load, client)
            baseline_started = datetime.now(UTC)
            time.sleep(MAINTENANCE_WINDOW_SECONDS)
            baseline_ended = datetime.now(UTC)
            baseline_requests, baseline_resources = _capture_locust_window(
                state,
                load,
                client,
                started_at=baseline_started,
                ended_at=baseline_ended,
            )

            _reset_locust_stats(load, client)
            maintenance_started = datetime.now(UTC)
            monotonic_started = time.monotonic()
            cleanup = _run_maintenance_batch(state)
            remaining = (
                MAINTENANCE_WINDOW_SECONDS
                - (time.monotonic() - monotonic_started)
            )
            if remaining > 0:
                time.sleep(remaining)
            maintenance_ended = datetime.now(UTC)
            maintenance_requests, maintenance_resources = (
                _capture_locust_window(
                    state,
                    load,
                    client,
                    started_at=maintenance_started,
                    ended_at=maintenance_ended,
                )
            )
            response = client.get("/stop")
            response.raise_for_status()

        final = asyncio.run(_read_maintenance(state))
        summary = build_maintenance_summary(
            initial=initial,
            final=final,
            cleanup=cleanup,
            baseline_requests=baseline_requests,
            maintenance_requests=maintenance_requests,
            baseline_resources=baseline_resources,
            maintenance_resources=maintenance_resources,
        )
        _write_json_atomic(
            state.config.output_dir / "maintenance.json",
            {
                "schema_version": 1,
                **asdict(summary),
            },
        )
    finally:
        _stop_process(load)


async def _read_ai_isolation(
    state: RunState,
) -> AIIsolationState:
    database = Database(_settings(state))
    try:
        return await read_ai_isolation_state(database)
    finally:
        await database.close()


def _locust_shard_environment(
    state: RunState,
    *,
    index: int,
    ready_dir: Path | None = None,
    start_gate: Path | None = None,
) -> dict[str, str]:
    environment = {
        **state.env,
        "ITIME_PERF_WORKER_INDEX": str(index),
        "ITIME_PERF_WORKER_COUNT": "2",
    }
    if ready_dir is not None and start_gate is not None:
        environment["ITIME_PERF_AI_READY_DIR"] = str(ready_dir)
        environment["ITIME_PERF_AI_START_GATE"] = str(start_gate)
    return environment


def _wait_for_ai_users_ready(
    process: ManagedProcess,
    ready_dir: Path,
    *,
    expected: int,
    timeout: float = 60,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _require_process_alive(process, "AI 隔离负载")
        if len(tuple(ready_dir.iterdir())) == expected:
            return
        time.sleep(0.05)
    raise TimeoutError(f"AI 用户未在 {timeout:g} 秒内全部就绪")


def _run_ai_isolation(state: RunState) -> None:
    assert state.scenario_config is not None
    ordinary_port = _available_port()
    ai_port = _available_port()
    ordinary_origin = f"http://127.0.0.1:{ordinary_port}"
    ai_origin = f"http://127.0.0.1:{ai_port}"
    ready_dir = state.config.output_dir / "ai-ready"
    ready_dir.mkdir(mode=0o700)
    start_gate = state.config.output_dir / ".ai-start"
    state.env["ITIME_PERF_WORKLOAD"] = state.scenario
    ordinary = _start_process(
        state,
        "locust-ai-ordinary",
        _locust_command(
            state,
            users=AI_STANDARD_USERS,
            spawn_rate=state.scenario_config.spawn_rate,
            duration_seconds=(
                AI_BASELINE_SECONDS + state.scenario_config.duration_seconds
            ),
            prefix="ai-ordinary",
            user_classes=("StandardUser",),
            web_port=ordinary_port,
            autostart=False,
        ),
        env=_locust_shard_environment(state, index=0),
    )
    ai_load: ManagedProcess | None = None
    try:
        _wait_http(ordinary_origin, ordinary, path="/stats/requests")
        with httpx.Client(
            base_url=ordinary_origin,
            timeout=10,
            trust_env=False,
        ) as ordinary_client:
            _set_locust_users(
                ordinary,
                ordinary_client,
                ordinary_origin,
                AI_STANDARD_USERS,
            )
            _reset_locust_stats(ordinary, ordinary_client)
            baseline_started = datetime.now(UTC)
            time.sleep(AI_BASELINE_SECONDS)
            baseline_ended = datetime.now(UTC)
            baseline_requests, baseline_resources = _capture_locust_window(
                state,
                ordinary,
                ordinary_client,
                started_at=baseline_started,
                ended_at=baseline_ended,
            )

            initial = asyncio.run(_read_ai_isolation(state))
            ai_load = _start_process(
                state,
                "locust-ai-jobs",
                _locust_command(
                    state,
                    users=AI_JOB_USERS,
                    spawn_rate=state.scenario_config.spawn_rate,
                    duration_seconds=state.scenario_config.duration_seconds,
                    prefix="ai-jobs",
                    user_classes=("AIUser",),
                    web_port=ai_port,
                    autostart=False,
                ),
                env=_locust_shard_environment(
                    state,
                    index=1,
                    ready_dir=ready_dir,
                    start_gate=start_gate,
                ),
            )
            _wait_http(ai_origin, ai_load, path="/stats/requests")
            with httpx.Client(
                base_url=ai_origin,
                timeout=20,
                trust_env=False,
            ) as ai_client:
                _set_locust_users(
                    ai_load,
                    ai_client,
                    ai_origin,
                    AI_JOB_USERS,
                )
                _wait_for_ai_users_ready(
                    ai_load,
                    ready_dir,
                    expected=AI_JOB_USERS,
                )
                _reset_locust_stats(ordinary, ordinary_client)
                _reset_locust_stats(ai_load, ai_client)
                active_started = datetime.now(UTC)
                start_gate.touch(mode=0o600)
                time.sleep(state.scenario_config.duration_seconds)
                active_ended = datetime.now(UTC)
                ordinary_requests, active_resources = (
                    _capture_locust_window(
                        state,
                        ordinary,
                        ordinary_client,
                        started_at=active_started,
                        ended_at=active_ended,
                    )
                )
                _require_process_alive(ai_load, "AI 隔离负载")
                response = ai_client.get("/stats/requests")
                response.raise_for_status()
                ai_requests = locust_request_stats(response.json())
                response = ai_client.get("/stop")
                response.raise_for_status()
            response = ordinary_client.get("/stop")
            response.raise_for_status()

        final = asyncio.run(_read_ai_isolation(state))
        summary = build_ai_isolation_summary(
            initial=initial,
            final=final,
            ordinary_baseline_requests=baseline_requests,
            ordinary_active_requests=ordinary_requests,
            ai_requests=ai_requests,
            baseline_resources=baseline_resources,
            active_resources=active_resources,
            duration_seconds=state.scenario_config.duration_seconds,
        )
        _write_json_atomic(
            state.config.output_dir / "ai-isolation.json",
            {
                "schema_version": 1,
                **asdict(summary),
            },
        )
    finally:
        if ai_load is not None:
            _stop_process(ai_load)
        _stop_process(ordinary)
        start_gate.unlink(missing_ok=True)
        for marker in ready_dir.iterdir():
            marker.unlink()
        ready_dir.rmdir()


def _stage_warmup(state: RunState) -> None:
    assert state.scenario_config is not None
    _run_locust(
        state,
        prefix="warmup",
        users=min(2, state.scenario_config.users),
        spawn_rate=2,
        duration_seconds=3,
        user_classes=("StandardUser",),
    )


def _stage_reset_stats(state: RunState) -> None:
    checked(
        state.compose(
            "exec",
            "-T",
            "postgres",
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "itime",
            "-d",
            state.config.database_name,
            "-c",
            "SELECT pg_stat_statements_reset();",
        ),
        env=state.env,
        timeout=30,
    )
    for name in ("resources.csv", "containers.csv", "postgres.csv", "queues.csv"):
        (state.config.output_dir / name).unlink(missing_ok=True)


def _stage_start_sampler(state: RunState) -> None:
    if state.database_url is None:
        raise RuntimeError("性能数据库地址尚未初始化")
    state.env["ITIME_PERF_DATABASE_URL"] = state.database_url
    _start_process(
        state,
        "sampler",
        [
            sys.executable,
            "-m",
            "performance.sampler",
            "--docker-context",
            state.config.docker_context,
            "--compose-project",
            state.config.compose_project,
            "--output-dir",
            str(state.config.output_dir),
        ],
    )
    time.sleep(1.2)
    sampler = state.processes[-1]
    if sampler.process.poll() is not None:
        raise RuntimeError("资源采样器启动后提前退出")


def _formal_user_classes(state: RunState) -> tuple[str, ...]:
    if state.scenario == "history":
        return ("HistoryUser",)
    if state.scenario == "smoke":
        return ("SyncUser",)
    return ("StandardUser",)


def _stage_run_locust(state: RunState) -> None:
    assert state.scenario_config is not None
    try:
        if state.scenario == Scenario.UI_LOAD:
            _run_ui_load(state)
        elif state.scenario == Scenario.SATURATION:
            _run_saturation(state)
        elif state.scenario == Scenario.QUEUES:
            _run_queues(state)
        elif state.scenario == Scenario.MAINTENANCE:
            _run_maintenance(state)
        elif state.scenario == Scenario.AI_ISOLATION:
            _run_ai_isolation(state)
        else:
            _run_locust(
                state,
                prefix="requests",
                users=state.scenario_config.users,
                spawn_rate=state.scenario_config.spawn_rate,
                duration_seconds=state.scenario_config.duration_seconds,
                user_classes=_formal_user_classes(state),
            )
    finally:
        for process in state.processes:
            if process.name == "sampler":
                _stop_process(process)
                break


def _run_ui_load(state: RunState) -> None:
    assert state.scenario_config is not None
    preview: ManagedProcess | None = None
    load: ManagedProcess | None = None
    try:
        web_port = int(state.env["ITIME_PERF_WEB_PORT"])
    except (KeyError, ValueError) as exc:
        raise RuntimeError("UI 性能运行缺少有效的 ITIME_PERF_WEB_PORT") from exc
    locust_port = _available_port()
    web_origin = f"http://localhost:{web_port}"
    preview_env = {
        **_host_environment(),
        "ITIME_API_ORIGIN": state.config.http_origin,
    }
    try:
        _run_logged(
            state,
            "web-build",
            ["pnpm", "build"],
            env=preview_env,
            timeout=300,
            cwd=WEB,
        )
        preview = _start_process(
            state,
            "web-preview",
            ["pnpm", "preview", "--port", str(web_port)],
            cwd=WEB,
            env=preview_env,
        )
        _wait_http(web_origin, preview, path="/api/ready")

        state.env["ITIME_PERF_WORKLOAD"] = state.scenario
        load = _start_process(
            state,
            "locust",
            _locust_command(
                state,
                users=state.scenario_config.users,
                spawn_rate=state.scenario_config.spawn_rate,
                duration_seconds=state.scenario_config.duration_seconds,
                prefix="requests",
                user_classes=_formal_user_classes(state),
                web_port=locust_port,
            ),
        )
        _wait_for_locust_users(
            load,
            f"http://127.0.0.1:{locust_port}",
            expected=state.scenario_config.users,
        )
        browser_env = _browser_environment(
            state,
            web_origin=web_origin,
            locust_pid=load.process.pid,
        )
        _run_logged(
            state,
            "playwright-chromium",
            ["pnpm", "test:e2e:performance"],
            env=browser_env,
            timeout=20 * 60,
            cwd=WEB,
        )
        _require_process_alive(load, "UI 背景负载")

        edge_env = {
            **browser_env,
            "ITIME_PERF_BROWSER_LABEL": "edge",
        }
        edge_path = _edge_executable_path()
        if edge_path is None:
            edge_env["ITIME_E2E_BROWSER_CHANNEL"] = "msedge"
        else:
            edge_env["ITIME_E2E_BROWSER_EXECUTABLE_PATH"] = edge_path
        _run_logged(
            state,
            "playwright-edge",
            [
                "pnpm",
                "exec",
                "playwright",
                "test",
                "--config",
                "playwright.performance.config.ts",
                "--project=desktop",
            ],
            env=edge_env,
            timeout=20 * 60,
            cwd=WEB,
        )
        _require_process_alive(load, "UI 背景负载")
    finally:
        if load is not None:
            _stop_process(load)
        if preview is not None:
            _stop_process(preview)


def _stage_verify_invariants(state: RunState) -> None:
    if state.manifest is None:
        raise RuntimeError("数据清单尚未创建")
    settings = _settings(state)
    verification = (
        None
        if state.scenario == Scenario.MAINTENANCE
        else asyncio.run(
            verify_dataset(
                settings,
                state.manifest,
                allow_growth=True,
            )
        )
    )
    database = Database(settings)
    try:
        invariants = asyncio.run(collect_invariants(database))
    finally:
        asyncio.run(database.close())
    _write_json_atomic(state.config.output_dir / "invariants.json", invariants)
    if verification is not None and not verification.ok:
        raise RuntimeError(
            "负载后数据不变量失败：" + "; ".join(verification.violations)
        )
    compose_logs = checked(
        state.compose("logs", "--no-color"),
        env=state.env,
        timeout=30,
    )
    _write_command_output(state, "compose", compose_logs)


def _stage_build_report(state: RunState) -> GateResult:
    state.gate_result = build_report(state.config.output_dir)
    return state.gate_result


def _cleanup(state: RunState) -> None:
    manifest = CleanupManifest(
        run_id=state.config.run_id,
        compose_project=state.config.compose_project,
        database_name=state.config.database_name,
        output_dir=state.config.output_dir,
    )
    require_safe_cleanup(state.config, manifest)
    for process in reversed(state.processes):
        _stop_process(process)
    if state.scenario == Scenario.QUEUES:
        _unpause_queue_workers(state)
    checked(
        state.compose("down", "--volumes", "--remove-orphans"),
        env=state.env,
        timeout=90,
    )
    for path in state.registered_paths:
        path.unlink(missing_ok=True)
    private_dir = state.config.output_dir / "private"
    if private_dir.exists():
        private_dir.rmdir()


def _docker_resources(state: RunState, kind: str) -> tuple[str, ...]:
    result = checked(
        [
            "docker",
            "--context",
            state.config.docker_context,
            kind,
            "ls",
            "--filter",
            f"label=com.docker.compose.project={state.config.compose_project}",
            "--format",
            "{{.ID}}",
        ],
        env=state.env,
        timeout=15,
    )
    return tuple(line for line in result.stdout.splitlines() if line)


def _verify_clean(state: RunState) -> tuple[str, ...]:
    remaining: list[str] = []
    remaining.extend(f"container:{item}" for item in _docker_resources(state, "container"))
    remaining.extend(f"volume:{item}" for item in _docker_resources(state, "volume"))
    remaining.extend(f"network:{item}" for item in _docker_resources(state, "network"))
    remaining.extend(
        f"process:{process.name}:{process.process.pid}"
        for process in state.processes
        if process.process.poll() is None
    )
    remaining.extend(
        f"path:{path}"
        for path in state.registered_paths
        if path.exists()
    )
    return tuple(remaining)


STAGE_HANDLERS: dict[str, Callable[[RunState], object]] = {
    "check-runtime": _stage_check_runtime,
    "start-postgres": _stage_start_postgres,
    "migrate": _stage_migrate,
    "create-extension-and-guard": _stage_create_extension_and_guard,
    "seed": _stage_seed,
    "start-services": _stage_start_services,
    "wait-ready": _stage_wait_ready,
    "warmup": _stage_warmup,
    "reset-stats": _stage_reset_stats,
    "start-sampler": _stage_start_sampler,
    "run-locust": _stage_run_locust,
    "verify-invariants": _stage_verify_invariants,
    "build-report": _stage_build_report,
    "cleanup": _cleanup,
    "verify-clean": _verify_clean,
}


def execute_stage(state: RunState, stage: str) -> object:
    return STAGE_HANDLERS[stage](state)


def run_performance_scenario(
    scenario: str,
    *,
    run_id: str | None = None,
) -> PerformanceRunResult:
    state = _new_state(scenario, run_id=run_id)
    OUTPUT_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = OUTPUT_ROOT / ".run.lock"
    try:
        with exclusive_run_lock(lock_path):
            return execute_lifecycle(state, execute=execute_stage)
    except FileExistsError:
        return PerformanceRunResult(
            False,
            state.config.output_dir,
            (),
            "已有性能基线正在运行",
        )


def find_performance_resources() -> tuple[str, ...]:
    resources: list[str] = []
    for kind in ("container", "volume", "network"):
        result = checked(
            [
                "docker",
                "--context",
                "colima-itime-perf",
                kind,
                "ls",
                "--filter",
                "label=com.docker.compose.project",
                "--format",
                "{{.ID}} {{.Label \"com.docker.compose.project\"}}",
            ],
            env=os.environ.copy(),
            timeout=15,
        )
        resources.extend(
            f"{kind}:{line}"
            for line in result.stdout.splitlines()
            if "itime-perf-" in line
        )
    return tuple(resources)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="运行 iTime 隔离性能基线")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("smoke")
    scenario = commands.add_parser("scenario")
    scenario.add_argument(
        "scenario",
        choices=(
            "steady",
            "peak",
            "history",
            "ai-isolation",
            "queues",
            "maintenance",
            "soak",
            "saturation",
            "ui-load",
        ),
    )
    commands.add_parser("baseline")
    commands.add_parser("verify-clean")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "verify-clean":
        resources = find_performance_resources()
        print(json.dumps({"remaining_resources": resources}, ensure_ascii=False))
        raise SystemExit(1 if resources else 0)
    scenarios = (
        BASELINE_SCENARIOS
        if args.command == "baseline"
        else ("smoke" if args.command == "smoke" else args.scenario,)
    )
    results = [run_performance_scenario(name) for name in scenarios]
    print(
        json.dumps(
            [
                {
                    **asdict(result),
                    "run_dir": str(result.run_dir),
                }
                for result in results
            ],
            ensure_ascii=False,
        )
    )
    raise SystemExit(0 if all(result.passed for result in results) else 1)


if __name__ == "__main__":
    main()
