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

from itime.config import Settings
from itime.database import Database
from performance.contracts import (
    SCENARIOS,
    GateResult,
    RunConfig,
    Scenario,
    ScenarioConfig,
    SeedManifest,
    SeedProfile,
)
from performance.report import (
    SensitiveArtifactError,
    assert_no_sensitive_artifacts,
    build_report,
)
from performance.safety import (
    OUTPUT_ROOT,
    CleanupManifest,
    exclusive_run_lock,
    require_safe_cleanup,
    require_safe_run,
)
from performance.seed import seed_dataset
from performance.seed_verify import collect_invariants, verify_dataset

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"
COMPOSE_FILE = ROOT / "compose.performance.yaml"
HOST_GATEWAY_BIND = "0.0.0.0"  # noqa: S104
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
        "ITIME_PERF_IMAGE_TAG": "local",
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


def _start_process(
    state: RunState,
    name: str,
    command: list[str],
    *,
    cwd: Path = SERVER,
) -> ManagedProcess:
    logs = state.config.output_dir / "logs"
    logs.mkdir(mode=0o700, parents=True, exist_ok=True)
    log_path = logs / f"{name}.log"
    log_handle = log_path.open("w")
    os.chmod(log_path, 0o600)
    process = subprocess.Popen(  # noqa: S603
        command,
        cwd=cwd,
        env=state.env,
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
        managed.process.send_signal(signal.SIGTERM)
        try:
            managed.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(managed.process.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
            managed.process.wait(timeout=5)
    managed.log_handle.close()


def _wait_http(origin: str, process: ManagedProcess, *, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    with httpx.Client(base_url=origin, timeout=2, trust_env=False) as client:
        while time.monotonic() < deadline:
            if process.process.poll() is not None:
                raise RuntimeError(f"{process.name} 在就绪前退出")
            try:
                response = client.get("/health")
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
) -> list[str]:
    return [
        sys.executable,
        "-m",
        "locust",
        "-f",
        "performance/locustfile.py",
        "--headless",
        "--host",
        state.config.http_origin,
        "--users",
        str(users),
        "--spawn-rate",
        str(spawn_rate),
        "--run-time",
        f"{duration_seconds}s",
        "--csv",
        str(state.config.output_dir / prefix),
        "--only-summary",
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
    state.manifest = asyncio.run(
        seed_dataset(
            _settings(state),
            profile,
            seed=20261005,
            at=datetime.now(UTC),
            credentials_path=credentials,
            upstream_origin=upstream_origin,
        )
    )
    state.env["ITIME_PERF_CREDENTIALS"] = str(credentials)
    state.env["ITIME_PERF_ANCHOR_DATE"] = datetime.now(UTC).date().isoformat()
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
    result = checked(
        state.compose(
            "up",
            "-d",
            "--wait",
            "--scale",
            "ai-worker=2",
            "api",
            "ai-worker",
            "mail-worker",
            "subscription-worker",
            "public-link-worker",
            "export-worker",
        ),
        env=state.env,
        timeout=180,
    )
    _write_command_output(state, "start-services", result)


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
    if state.scenario == "ai-isolation":
        return ("StandardUser", "AIUser")
    if state.scenario == "smoke":
        return ("SyncUser",)
    return ("StandardUser",)


def _stage_run_locust(state: RunState) -> None:
    assert state.scenario_config is not None
    try:
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


def _stage_verify_invariants(state: RunState) -> None:
    if state.manifest is None:
        raise RuntimeError("数据清单尚未创建")
    settings = _settings(state)
    verification = asyncio.run(
        verify_dataset(
            settings,
            state.manifest,
            allow_growth=True,
        )
    )
    database = Database(settings)
    try:
        invariants = asyncio.run(collect_invariants(database))
    finally:
        asyncio.run(database.close())
    _write_json_atomic(state.config.output_dir / "invariants.json", invariants)
    if not verification.ok:
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
        ("steady", "peak", "history", "ai-isolation", "queues", "maintenance", "soak")
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
