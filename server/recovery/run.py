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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import psycopg
from cryptography.fernet import Fernet
from psycopg import sql

from itime import __version__
from itime.account_deletion.service import (
    ClaimedDeletion,
    purge_claimed_account,
)
from itime.calendar.lifecycle_service import cleanup_expired_calendar_items
from itime.config import Settings
from itime.database import Database
from itime.execution.difficulty_service import cleanup_expired_difficulties
from itime.review.memory_service import cleanup_expired_memories
from itime.tasks.lifecycle_service import cleanup_expired_tasks
from performance.contracts import SeedManifest, SeedProfile
from performance.seed import seed_dataset
from recovery.contracts import (
    RecoveryConfig,
    RecoveryLedger,
    RecoveryScenario,
)
from recovery.finalize import FinalizationSummary, finalize_recovered_database
from recovery.ledger import (
    LedgerError,
    build_ledger_snapshot,
    ledger_digest,
    read_encrypted_snapshot,
    write_encrypted_snapshot,
)
from recovery.pgbackrest import PgBackRest, PgBackRestBackup, PgBackRestError
from recovery.replay import RecoveryReplayError, ReplaySummary, replay_ledger
from recovery.report import (
    RecoveryEvidence,
    RecoveryGateResult,
    SensitiveArtifactError,
    assert_no_sensitive_recovery_artifacts,
    write_recovery_report,
)
from recovery.safety import (
    DOCKER_CONTEXT,
    OUTPUT_ROOT,
    exclusive_run_lock,
    expected_cleanup_manifest,
    require_safe_cleanup,
    require_safe_run,
)
from recovery.verify import (
    EncryptionVerification,
    RecoveryVerification,
    verify_encryption_keys,
    verify_recovered_database,
)

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"
WEB = ROOT / "web"
COMPOSE_FILE = ROOT / "compose.recovery.yaml"
CONTAINER_TMP = Path("/", "tmp")
HOST_ENVIRONMENT_KEYS = (
    "CI",
    "FORCE_COLOR",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "NO_COLOR",
    "PATH",
    "SHELL",
    "TEMP",
    "TMP",
    "TMPDIR",
)
STAGES = (
    "check-runtime",
    "prepare-secrets",
    "start-source",
    "migrate-source",
    "seed-source",
    "create-stanza",
    "ledger-before",
    "backup",
    "write-after-backup",
    "archive-wal",
    "ledger-after",
    "stop-source",
    "restore",
    "replay-ledger",
    "finalize",
    "verify",
    "start-app",
    "build-report",
)
CLEANUP_STAGES = ("cleanup", "verify-clean")
BASELINE_SCENARIOS = ("latest", "pitr")
PROBE_TITLES = (
    "恢复点前探针",
    "恢复点后探针",
)


@dataclass
class ManagedProcess:
    name: str
    process: subprocess.Popen[str]
    log_handle: Any


@dataclass
class RunState:
    config: RecoveryConfig
    scenario: str
    env: dict[str, str]
    private_dir: Path | None = None
    repo_cipher_pass: str = ""
    ledger_key: bytes = b""
    source_database_url: str | None = None
    restore_database_url: str | None = None
    seed_manifest: SeedManifest | None = None
    ledger_before: RecoveryLedger | None = None
    ledger_after: RecoveryLedger | None = None
    backups: tuple[PgBackRestBackup, ...] = ()
    processes: list[ManagedProcess] = field(default_factory=list)
    registered_paths: list[Path] = field(default_factory=list)
    source_api: ManagedProcess | None = None
    probe_ids: list[str] = field(default_factory=list)
    probe_committed_at: list[datetime] = field(default_factory=list)
    deletion_expectations: dict[str, str] = field(default_factory=dict)
    deletion_labels: dict[str, str] = field(default_factory=dict)
    deleted_account_email: str | None = None
    expected_counts: dict[str, int] = field(default_factory=dict)
    archived_wal: str | None = None
    target_at: datetime | None = None
    failure_at: datetime | None = None
    restore_started_at: datetime | None = None
    service_ready_at: datetime | None = None
    replay_summary: ReplaySummary | None = None
    finalization_summary: FinalizationSummary | None = None
    verification: RecoveryVerification | None = None
    encryption_verification: EncryptionVerification | None = None
    fault_results: dict[str, bool] = field(default_factory=dict)
    ui_passed: bool = False
    gate_result: RecoveryGateResult | None = None
    failed_stage: str | None = None

    def compose(self, *arguments: str) -> list[str]:
        return [
            "docker",
            "--context",
            self.config.docker_context,
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "--project-name",
            self.config.compose_project,
            "--profile",
            "restore",
            "--profile",
            "app",
            *arguments,
        ]


@dataclass(frozen=True)
class RecoveryRunResult:
    passed: bool
    run_dir: Path
    remaining_resources: tuple[str, ...]
    error: str | None = None


def _host_environment() -> dict[str, str]:
    return {
        key: os.environ[key]
        for key in HOST_ENVIRONMENT_KEYS
        if key in os.environ
    }


def _safe_error(error: BaseException) -> str:
    value = str(error)
    try:
        assert_no_sensitive_recovery_artifacts(value)
    except SensitiveArtifactError:
        return "错误详情包含敏感材料，已拒绝写入。"
    return value


def _write_json_atomic(path: Path, payload: object) -> None:
    assert_no_sensitive_recovery_artifacts(payload)
    if path.is_symlink():
        raise ValueError(f"恢复输出目标不能是符号链接：{path.name}")
    temporary = path.with_name(f".{path.name}.tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            descriptor = -1
            json.dump(payload, output, ensure_ascii=False, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise


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
) -> RecoveryRunResult:
    state.config.output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    primary_error: BaseException | None = None
    cleanup_errors: list[str] = []
    remaining: tuple[str, ...] = ()
    try:
        for stage in STAGES:
            state.failed_stage = stage
            value = execute(state, stage)
            if stage == "build-report" and isinstance(
                value,
                RecoveryGateResult,
            ):
                state.gate_result = value
    except BaseException as error:
        primary_error = error
    finally:
        for stage in CLEANUP_STAGES:
            try:
                value = execute(state, stage)
                if stage == "verify-clean" and value is not None:
                    remaining = tuple(str(item) for item in value)
            except BaseException as error:
                cleanup_errors.append(f"{stage}: {_safe_error(error)}")

    if primary_error is not None:
        _write_partial_report(
            state,
            stage=state.failed_stage or "unknown",
            error=primary_error,
            cleanup_errors=cleanup_errors,
        )
        return RecoveryRunResult(
            False,
            state.config.output_dir,
            remaining,
            _safe_error(primary_error),
        )
    if cleanup_errors or remaining:
        error = "; ".join((*cleanup_errors, *remaining))
        _write_partial_report(
            state,
            stage="cleanup",
            error=RuntimeError(error),
            cleanup_errors=cleanup_errors,
        )
        return RecoveryRunResult(
            False,
            state.config.output_dir,
            remaining,
            error,
        )
    if state.gate_result is None:
        error = "报告阶段未返回恢复门禁结果"
        _write_partial_report(
            state,
            stage="build-report",
            error=RuntimeError(error),
            cleanup_errors=(),
        )
        return RecoveryRunResult(False, state.config.output_dir, (), error)
    return RecoveryRunResult(
        state.gate_result.passed,
        state.config.output_dir,
        (),
        (
            None
            if state.gate_result.passed
            else "; ".join(state.gate_result.failures)
        ),
    )


def checked(
    command: list[str],
    *,
    env: Mapping[str, str],
    timeout: int,
    cwd: Path = ROOT,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # noqa: S603
            command,
            cwd=cwd,
            env=dict(env),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=True,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"命令执行超时：{command[0]}") from error
    except subprocess.CalledProcessError as error:
        raw_detail = (
            error.stderr
            if isinstance(error.stderr, str) and error.stderr.strip()
            else error.stdout if isinstance(error.stdout, str) else ""
        ).strip()
        detail = ""
        if raw_detail:
            try:
                assert_no_sensitive_recovery_artifacts(raw_detail)
            except SensitiveArtifactError:
                detail = "；详细输出包含敏感材料，已隐藏"
            else:
                compact = " ".join(raw_detail.split())
                detail = f"；{compact[-600:]}"
        raise RuntimeError(
            f"命令执行失败：{command[0]}，退出码 {error.returncode}{detail}"
        ) from error


def _write_secret(path: Path, content: str) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            descriptor = -1
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        path.unlink(missing_ok=True)
        raise


def _write_browser_session_state(path: Path, session_cookie: str) -> None:
    _write_secret(
        path,
        json.dumps(
            {
                "cookies": [
                    {
                        "name": "itime_session",
                        "value": session_cookie,
                        "domain": "localhost",
                        "path": "/api",
                        "expires": int(time.time()) + 86400,
                        "httpOnly": True,
                        "secure": False,
                        "sameSite": "Lax",
                    }
                ],
                "origins": [],
            },
            separators=(",", ":"),
        ),
    )


def _available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _new_state(
    scenario: str,
    *,
    run_id: str | None = None,
) -> RunState:
    selected = RecoveryScenario(scenario)
    active_run_id = run_id or secrets.token_hex(4)
    output_dir = OUTPUT_ROOT / active_run_id
    private_dir = ROOT / ".local" / "recovery" / active_run_id
    source_api_port = _available_port()
    web_port = _available_port()
    source_database_port = _available_port()
    restore_database_port = _available_port()
    restore_api_port = _available_port()
    keys = [Fernet.generate_key().decode() for _ in range(5)]
    config = RecoveryConfig(
        run_id=active_run_id,
        environment="test",
        docker_context=DOCKER_CONTEXT,
        source_database=f"itime_backup_{active_run_id}",
        restore_database=f"itime_restore_{active_run_id}",
        http_origin=f"http://127.0.0.1:{source_api_port}",
        guard_value=f"itime-recovery:{active_run_id}",
        output_dir=output_dir,
    )
    env = {
        **_host_environment(),
        "ITIME_RECOVERY_RUN_ID": active_run_id,
        "ITIME_RECOVERY_SOURCE_DATABASE": config.source_database,
        "ITIME_RECOVERY_RESTORE_DATABASE": config.restore_database,
        "ITIME_RECOVERY_POSTGRES_PASSWORD": secrets.token_hex(24),
        "ITIME_RECOVERY_SOURCE_CONFIG": str(
            (private_dir / "source.conf").resolve()
        ),
        "ITIME_RECOVERY_RESTORE_CONFIG": str(
            (private_dir / "restore.conf").resolve()
        ),
        "ITIME_RECOVERY_ENCRYPTION_KEY": keys[0],
        "ITIME_RECOVERY_AI_ENCRYPTION_KEY": keys[1],
        "ITIME_RECOVERY_CALENDAR_KEY": keys[2],
        "ITIME_RECOVERY_EXPORT_KEY": keys[3],
        "ITIME_RECOVERY_IMAGE_TAG": "local",
        "ITIME_RECOVERY_APP_IMAGE_TAG": "local",
        "ITIME_RECOVERY_WEB_PORT": str(web_port),
        "ITIME_RECOVERY_SOURCE_DB_PORT": str(source_database_port),
        "ITIME_RECOVERY_RESTORE_DB_PORT": str(restore_database_port),
        "ITIME_RECOVERY_API_PORT": str(restore_api_port),
    }
    return RunState(
        config=config,
        scenario=selected.value,
        env=env,
        private_dir=private_dir,
        repo_cipher_pass=secrets.token_urlsafe(48),
        ledger_key=keys[4].encode(),
    )


def _settings(state: RunState, *, restored: bool) -> Settings:
    database_url = (
        state.restore_database_url if restored else state.source_database_url
    )
    if database_url is None:
        raise RuntimeError("恢复数据库连接尚未初始化")
    return Settings(
        environment="test",
        database_url=database_url,
        public_origin=state.config.http_origin,
        encryption_key=state.env["ITIME_RECOVERY_ENCRYPTION_KEY"],
        ai_encryption_key=state.env["ITIME_RECOVERY_AI_ENCRYPTION_KEY"],
        calendar_subscription_encryption_key=state.env[
            "ITIME_RECOVERY_CALENDAR_KEY"
        ],
        export_encryption_key=state.env["ITIME_RECOVERY_EXPORT_KEY"],
        db_pool_size=2,
        db_max_overflow=0,
    )


def _app_environment(state: RunState, database_url: str) -> dict[str, str]:
    return {
        **_host_environment(),
        "ITIME_ENVIRONMENT": "test",
        "ITIME_DATABASE_URL": database_url,
        "ITIME_PUBLIC_ORIGIN": state.config.http_origin,
        "ITIME_ALLOWED_HOSTS": '["localhost","127.0.0.1"]',
        "ITIME_ENCRYPTION_KEY": state.env["ITIME_RECOVERY_ENCRYPTION_KEY"],
        "ITIME_AI_ENCRYPTION_KEY": state.env[
            "ITIME_RECOVERY_AI_ENCRYPTION_KEY"
        ],
        "ITIME_CALENDAR_SUBSCRIPTION_ENCRYPTION_KEY": state.env[
            "ITIME_RECOVERY_CALENDAR_KEY"
        ],
        "ITIME_EXPORT_ENCRYPTION_KEY": state.env[
            "ITIME_RECOVERY_EXPORT_KEY"
        ],
        "ITIME_DB_POOL_SIZE": "2",
        "ITIME_DB_MAX_OVERFLOW": "0",
        "ITIME_SMTP_HOST": "127.0.0.1",
        "ITIME_SMTP_STARTTLS": "false",
    }


def _compose_port(state: RunState, service: str, container_port: int) -> int:
    result = checked(
        state.compose("port", service, str(container_port)),
        env=state.env,
        timeout=10,
    )
    try:
        return int(result.stdout.strip().rsplit(":", 1)[1])
    except (IndexError, ValueError) as error:
        raise RuntimeError(f"无法读取 {service} 映射端口") from error


def _database_url(state: RunState, port: int, database: str) -> str:
    password = state.env["ITIME_RECOVERY_POSTGRES_PASSWORD"]
    return (
        f"postgresql+psycopg://itime:{password}@127.0.0.1:"
        f"{port}/{database}"
    )


def _psycopg_url(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def _start_process(
    state: RunState,
    name: str,
    command: list[str],
    *,
    env: Mapping[str, str],
    cwd: Path = SERVER,
) -> ManagedProcess:
    log_dir = state.config.output_dir / "logs"
    log_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    log_path = log_dir / f"{name}.log"
    log_handle = log_path.open("w")
    os.chmod(log_path, 0o600)
    process = subprocess.Popen(  # noqa: S603
        command,
        cwd=cwd,
        env=dict(env),
        text=True,
        stdout=log_handle,
        stderr=log_handle,
        start_new_session=True,
    )
    managed = ManagedProcess(name, process, log_handle)
    state.processes.append(managed)
    return managed


def _stop_process(process: ManagedProcess) -> None:
    if process.process.poll() is None:
        try:
            os.killpg(os.getpgid(process.process.pid), signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(process.process.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.process.wait(timeout=5)
    process.log_handle.close()


def _wait_http(
    origin: str,
    process: ManagedProcess | None = None,
    *,
    timeout: float = 60,
) -> None:
    deadline = time.monotonic() + timeout
    with httpx.Client(base_url=origin, timeout=2, trust_env=False) as client:
        while time.monotonic() < deadline:
            if process is not None and process.process.poll() is not None:
                raise RuntimeError(f"{process.name} 在就绪前退出")
            try:
                if client.get("/api/ready").status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(0.1)
    raise TimeoutError(f"API 未在 {timeout:g} 秒内就绪")


def _pgbackrest(
    state: RunState,
    *,
    service: str,
    one_off: bool = False,
) -> PgBackRest:
    compose_arguments = (
        ("run", "--rm", "--no-deps", service)
        if one_off
        else ("exec", "-T", service)
    )
    return PgBackRest(
        binary="pgbackrest",
        stanza="itime",
        config=Path("/etc/pgbackrest/pgbackrest.conf"),
        command_prefix=tuple(state.compose(*compose_arguments)),
    )


def _stage_check_runtime(state: RunState) -> None:
    require_safe_run(state.config)
    details = json.loads(
        checked(
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
        ).stdout
    )
    cpus = int(details["NCPU"])
    memory = int(details["MemTotal"])
    if cpus != 2 or not int(3.75 * 1024**3) <= memory <= int(4.25 * 1024**3):
        raise RuntimeError(
            "恢复 Docker 资源不匹配："
            f"期望 2 vCPU/4 GiB，实际 {cpus} vCPU/{memory} bytes"
        )
    checked(
        [
            "docker",
            "--context",
            state.config.docker_context,
            "build",
            "-f",
            str(SERVER / "Dockerfile.recovery-postgres"),
            "-t",
            "itime-recovery-postgres:local",
            str(SERVER),
        ],
        env=state.env,
        timeout=900,
    )
    checked(
        [
            "docker",
            "--context",
            state.config.docker_context,
            "build",
            "-f",
            str(SERVER / "Dockerfile.performance"),
            "-t",
            "itime-performance:local",
            str(SERVER),
        ],
        env=state.env,
        timeout=900,
    )


def _stage_prepare_secrets(state: RunState) -> None:
    if state.private_dir is None:
        raise RuntimeError("恢复私有目录未配置")
    private_dir = state.private_dir
    private_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    for name, spool_path in (
        ("source.conf", "/var/spool/pgbackrest"),
        ("restore.conf", "/var/spool/pgbackrest"),
    ):
        path = private_dir / name
        _write_secret(
            path,
            "\n".join(
                (
                    "[global]",
                    "repo1-path=/var/lib/pgbackrest",
                    "repo1-retention-full=4",
                    "repo1-cipher-type=aes-256-cbc",
                    f"repo1-cipher-pass={state.repo_cipher_pass}",
                    f"spool-path={spool_path}",
                    "archive-async=n",
                    "process-max=2",
                    "log-level-console=warn",
                    "log-level-file=off",
                    "",
                    "[itime]",
                    "pg1-path=/var/lib/postgresql/18/docker",
                    "",
                )
            ),
        )
        state.registered_paths.append(path)
    state.registered_paths.append(private_dir / "accounts.json")
    state.registered_paths.extend(
        (
            private_dir / "ledger-before.bin",
            private_dir / "ledger-after.bin",
            private_dir / "old-session.json",
            private_dir / "primary-session.json",
            private_dir / "ui-run.json",
        )
    )


def _stage_start_source(state: RunState) -> None:
    checked(
        state.compose("up", "-d", "--wait", "source-postgres"),
        env=state.env,
        timeout=180,
    )
    port = _compose_port(state, "source-postgres", 5432)
    state.source_database_url = _database_url(
        state,
        port,
        state.config.source_database,
    )


def _stage_migrate_source(state: RunState) -> None:
    if state.source_database_url is None:
        raise RuntimeError("源数据库尚未启动")
    checked(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env=_app_environment(state, state.source_database_url),
        timeout=180,
        cwd=SERVER,
    )
    with psycopg.connect(
        _psycopg_url(state.source_database_url),
        autocommit=True,
        connect_timeout=3,
    ) as connection:
        connection.execute(
            sql.SQL("COMMENT ON DATABASE {} IS {}").format(
                sql.Identifier(state.config.source_database),
                sql.Literal(state.config.guard_value),
            )
        )


def _seed_encryption_probes(state: RunState) -> None:
    if state.source_database_url is None or state.seed_manifest is None:
        raise RuntimeError("加密探针缺少源数据库或账号")
    owner_email = state.seed_manifest.credentials[0].email
    base_cipher = Fernet(state.env["ITIME_RECOVERY_ENCRYPTION_KEY"])
    calendar_cipher = Fernet(state.env["ITIME_RECOVERY_CALENDAR_KEY"])
    export_cipher = Fernet(state.env["ITIME_RECOVERY_EXPORT_KEY"])
    with psycopg.connect(
        _psycopg_url(state.source_database_url),
        connect_timeout=3,
    ) as connection:
        owner = connection.execute(
            "SELECT id FROM users WHERE email=%s",
            (owner_email,),
        ).fetchone()
        if owner is None:
            raise RuntimeError("加密探针账号不存在")
        owner_id = owner[0]
        token_id = uuid4()
        source_id = uuid4()
        now_value = datetime.now(UTC)
        connection.execute(
            """
            INSERT INTO email_tokens (
                id, user_id, purpose, token_hash, created_at, expires_at
            ) VALUES (
                %s, %s, 'reset', %s, %s, %s
            )
            """,
            (
                token_id,
                owner_id,
                secrets.token_hex(32),
                now_value,
                now_value + timedelta(hours=1),
            ),
        )
        connection.execute(
            """
            INSERT INTO mail_outbox (
                id, token_id, encrypted_message, available_at
            ) VALUES (%s, %s, %s, %s)
            """,
            (
                uuid4(),
                token_id,
                base_cipher.encrypt(b"recovery-key-probe"),
                now_value,
            ),
        )
        connection.execute(
            """
            INSERT INTO calendar_sources (
                id, owner_id, kind, name
            ) VALUES (%s, %s, 'ics_subscription', '恢复密钥探针')
            """,
            (source_id, owner_id),
        )
        connection.execute(
            """
            INSERT INTO calendar_subscriptions (
                source_id, owner_id, encrypted_url, url_hmac,
                display_url, display_host, default_time_zone,
                lifecycle_status, refresh_status, refresh_interval_minutes,
                next_refresh_at, fetch_revision, generation,
                attempt_count, consecutive_failures
            ) VALUES (
                %s, %s, %s, %s,
                'https://recovery.invalid/…', 'recovery.invalid', 'UTC',
                'active', 'idle', 360,
                %s, 1, 0, 0, 0
            )
            """,
            (
                source_id,
                owner_id,
                calendar_cipher.encrypt(b"https://recovery.invalid/calendar"),
                secrets.token_hex(32),
                now_value + timedelta(hours=1),
            ),
        )
        connection.execute(
            """
            INSERT INTO data_export_jobs (
                id, owner_id, status, schema_version, attempt_count,
                max_attempts, available_at, snapshot_at,
                artifact_ciphertext, artifact_sha256, artifact_byte_size,
                filename, expires_at, finished_at
            ) VALUES (
                %s, %s, 'succeeded', 1, 1,
                3, %s, %s,
                %s, %s, 18,
                'recovery-probe.zip', %s, %s
            )
            """,
            (
                uuid4(),
                owner_id,
                now_value,
                now_value,
                export_cipher.encrypt(b"recovery-key-probe"),
                "9" * 64,
                now_value + timedelta(hours=1),
                now_value,
            ),
        )
        connection.commit()


def _stage_seed_source(state: RunState) -> None:
    if state.private_dir is None:
        raise RuntimeError("恢复私有目录未配置")
    credentials = state.private_dir / "accounts.json"
    profile = SeedProfile.standard(
        accounts=2,
        active_accounts=2,
        tasks_per_active=5,
        sync_events_per_active=20,
    )
    state.seed_manifest = asyncio.run(
        seed_dataset(
            _settings(state, restored=False),
            profile,
            seed=20261005,
            at=datetime.now(UTC),
            credentials_path=credentials,
            upstream_origin="http://127.0.0.1:18081",
        )
    )
    _seed_encryption_probes(state)


def _stage_create_stanza(state: RunState) -> None:
    runner = _pgbackrest(state, service="source-postgres")
    runner.run("stanza-create", timeout=60, env=state.env, cwd=ROOT)
    runner.run("check", timeout=60, env=state.env, cwd=ROOT)


def _stage_ledger_before(state: RunState) -> None:
    ledger = asyncio.run(
        build_ledger_snapshot(
            _settings(state, restored=False),
            generated_at=datetime.now(UTC),
        )
    )
    if state.private_dir is None:
        raise RuntimeError("恢复私有目录未配置")
    path = state.private_dir / "ledger-before.bin"
    write_encrypted_snapshot(ledger, path, state.ledger_key)
    state.ledger_before = ledger


def _stage_backup(state: RunState) -> None:
    runner = _pgbackrest(state, service="source-postgres")
    runner.run(
        "backup",
        "--type=full",
        timeout=900,
        env=state.env,
        cwd=ROOT,
    )
    state.backups = runner.info(timeout=60, env=state.env, cwd=ROOT)
    if not state.backups or state.backups[-1].backup_type != "full":
        raise RuntimeError("pgBackRest 未返回刚完成的 full 备份")


def _start_source_api(state: RunState) -> ManagedProcess:
    if state.source_database_url is None:
        raise RuntimeError("源数据库尚未启动")
    process = _start_process(
        state,
        "source-api",
        [
            sys.executable,
            "-m",
            "uvicorn",
            "itime.main:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            state.config.http_origin.rsplit(":", 1)[1],
            "--no-access-log",
        ],
        env=_app_environment(state, state.source_database_url),
    )
    state.source_api = process
    _wait_http(state.config.http_origin, process)
    return process


def _write_http_probe(
    client: httpx.Client,
    *,
    title: str,
) -> str:
    response = client.post(
        "/api/v1/tasks",
        json={
            "operation_id": str(uuid4()),
            "title": title,
            "description": "M8 recovery probe",
            "project_id": None,
            "estimated_minutes": 15,
            "priority": 1,
            "labels": [],
            "links": [],
            "steps": [],
        },
    )
    if response.status_code != 201:
        raise RuntimeError(f"恢复探针写入失败：HTTP {response.status_code}")
    return str(response.json()["task"]["id"])


def _probe_time(state: RunState, probe_id: str) -> datetime:
    if state.source_database_url is None:
        raise RuntimeError("源数据库尚未启动")
    with psycopg.connect(
        _psycopg_url(state.source_database_url),
        connect_timeout=3,
    ) as connection:
        value = connection.execute(
            "SELECT created_at FROM tasks WHERE id=%s",
            (probe_id,),
        ).fetchone()
    if value is None:
        raise RuntimeError("恢复探针提交后不可读取")
    return value[0].astimezone(UTC)


def _database_clock(state: RunState) -> datetime:
    if state.source_database_url is None:
        raise RuntimeError("源数据库尚未启动")
    with psycopg.connect(
        _psycopg_url(state.source_database_url),
        connect_timeout=3,
    ) as connection:
        value = connection.execute("SELECT clock_timestamp()").fetchone()
    assert value is not None
    return value[0].astimezone(UTC)


def _delete_json(
    client: httpx.Client,
    path: str,
    *,
    expected_version: int,
) -> None:
    response = client.request(
        "DELETE",
        path,
        json={
            "operation_id": str(uuid4()),
            "expected_version": expected_version,
        },
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"恢复删除场景请求失败：{path} HTTP {response.status_code}"
        )


def _lifecycle_targets(
    state: RunState,
    owner_id: str,
) -> dict[str, tuple[str, int]]:
    if state.source_database_url is None:
        raise RuntimeError("源数据库尚未启动")
    queries = {
        "task": """
            SELECT id::text, version FROM tasks
            WHERE owner_id=%s
              AND deleted_at IS NULL
              AND NOT (id = ANY(%s::uuid[]))
            ORDER BY id LIMIT 1
        """,
        "difficulty": """
            SELECT id::text, version FROM difficulty_records
            WHERE owner_id=%s AND deleted_at IS NULL
            ORDER BY id LIMIT 1
        """,
        "memory": """
            SELECT id::text, version FROM memories
            WHERE owner_id=%s AND deleted_at IS NULL
            ORDER BY id LIMIT 1
        """,
        "calendar-series": """
            SELECT id::text, version FROM calendar_series
            WHERE owner_id=%s AND deleted_at IS NULL
            ORDER BY id LIMIT 1
        """,
    }
    with psycopg.connect(
        _psycopg_url(state.source_database_url),
        connect_timeout=3,
    ) as connection:
        targets = {
            kind: connection.execute(
                query,
                (
                    (owner_id, state.probe_ids)
                    if kind == "task"
                    else (owner_id,)
                ),
            ).fetchone()
            for kind, query in queries.items()
        }
    if any(value is None for value in targets.values()):
        raise RuntimeError("删除重放场景缺少代表业务对象")
    return {
        kind: (str(value[0]), int(value[1]))
        for kind, value in targets.items()
        if value is not None
    }


def _expire_deleted_objects(
    state: RunState,
    targets: Mapping[str, tuple[str, int]],
    *,
    at: datetime,
) -> None:
    if state.source_database_url is None:
        raise RuntimeError("源数据库尚未启动")
    table_names = {
        "task": "tasks",
        "difficulty": "difficulty_records",
        "memory": "memories",
        "calendar-series": "calendar_series",
    }
    with psycopg.connect(
        _psycopg_url(state.source_database_url),
        connect_timeout=3,
    ) as connection:
        for kind, (object_id, _) in targets.items():
            connection.execute(
                sql.SQL(
                    "UPDATE {} SET purge_after=GREATEST(deleted_at, %s) "
                    "WHERE id=%s"
                ).format(sql.Identifier(table_names[kind])),
                (at, object_id),
            )
        connection.commit()

    async def cleanup() -> None:
        database = Database(_settings(state, restored=False))
        try:
            await cleanup_expired_tasks(database)
            await cleanup_expired_difficulties(database)
            await cleanup_expired_memories(database)
            await cleanup_expired_calendar_items(database)
        finally:
            await database.close()

    asyncio.run(cleanup())


def _complete_account_deletion(
    state: RunState,
    credential,
) -> str:
    if state.source_database_url is None:
        raise RuntimeError("源数据库尚未启动")
    with psycopg.connect(
        _psycopg_url(state.source_database_url),
        connect_timeout=3,
    ) as connection:
        owner = connection.execute(
            "SELECT id::text FROM users WHERE email=%s",
            (credential.email,),
        ).fetchone()
    if owner is None:
        raise RuntimeError("注销场景账号不存在")
    owner_id = owner[0]

    with httpx.Client(
        base_url=state.config.http_origin,
        timeout=10,
        trust_env=False,
    ) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={
                "email": credential.email,
                "password": credential.password,
                "device_name": "M8 deletion replay",
            },
        )
        if login.status_code != 200:
            raise RuntimeError(
                f"注销场景登录失败：HTTP {login.status_code}"
            )
        client.headers["x-csrf-token"] = login.json()["csrf_token"]
        requested = client.post(
            "/api/v1/account-deletion",
            json={
                "operation_id": str(uuid4()),
                "email_confirmation": credential.email,
                "current_password": credential.password,
            },
        )
        if requested.status_code != 202:
            raise RuntimeError(
                f"注销场景申请失败：HTTP {requested.status_code}"
            )

    with psycopg.connect(
        _psycopg_url(state.source_database_url),
        connect_timeout=3,
    ) as connection:
        claimed = connection.execute(
            """
            UPDATE account_deletions
            SET status='processing',
                purge_after=requested_at + interval '1 microsecond',
                available_at=requested_at + interval '1 microsecond',
                lease_id=%s,
                lease_until=clock_timestamp() + interval '1 day',
                attempt_count=attempt_count + 1,
                started_at=COALESCE(started_at, clock_timestamp()),
                last_error_code=NULL,
                last_error_message=NULL,
                updated_at=clock_timestamp()
            WHERE user_id=%s AND status='pending'
            RETURNING id, lease_id, attempt_count
            """,
            (uuid4(), owner_id),
        ).fetchone()
        if claimed is None:
            raise RuntimeError("注销 worker 未找到目标账号")
        connection.commit()
    claim = ClaimedDeletion(
        id=claimed[0],
        user_id=UUID(owner_id),
        lease_id=claimed[1],
        attempt_number=claimed[2],
    )

    async def purge() -> None:
        database = Database(_settings(state, restored=False))
        try:
            if not await purge_claimed_account(database, claim):
                raise RuntimeError("注销 worker 未完成目标账号")
        finally:
            await database.close()

    asyncio.run(purge())
    return owner_id


def _perform_deletion_replay_changes(
    state: RunState,
    client: httpx.Client,
) -> None:
    if state.seed_manifest is None or state.source_database_url is None:
        raise RuntimeError("删除重放场景尚未完成数据播种")
    owner_email = state.seed_manifest.credentials[0].email
    with psycopg.connect(
        _psycopg_url(state.source_database_url),
        connect_timeout=3,
    ) as connection:
        owner = connection.execute(
            "SELECT id::text FROM users WHERE email=%s",
            (owner_email,),
        ).fetchone()
    if owner is None:
        raise RuntimeError("删除重放控制账号不存在")
    targets = _lifecycle_targets(state, owner[0])
    with psycopg.connect(
        _psycopg_url(state.source_database_url),
        connect_timeout=3,
    ) as connection:
        state.deletion_labels = {
            "task": connection.execute(
                "SELECT title FROM tasks WHERE id=%s",
                (targets["task"][0],),
            ).fetchone()[0],
            "difficulty": connection.execute(
                "SELECT content FROM difficulty_records WHERE id=%s",
                (targets["difficulty"][0],),
            ).fetchone()[0],
            "memory": connection.execute(
                "SELECT title FROM memories WHERE id=%s",
                (targets["memory"][0],),
            ).fetchone()[0],
            "calendar-series": connection.execute(
                "SELECT name FROM calendar_series WHERE id=%s",
                (targets["calendar-series"][0],),
            ).fetchone()[0],
        }
    endpoints = {
        "task": "/api/v1/tasks/{id}",
        "difficulty": "/api/v1/difficulties/{id}",
        "memory": "/api/v1/memories/{id}",
        "calendar-series": "/api/v1/calendar/series/{id}",
    }
    for kind, (object_id, version) in targets.items():
        _delete_json(
            client,
            endpoints[kind].format(id=object_id),
            expected_version=version,
        )
    _expire_deleted_objects(state, targets, at=datetime.now(UTC))
    deleted_account_id = _complete_account_deletion(
        state,
        state.seed_manifest.credentials[1],
    )
    state.deleted_account_email = state.seed_manifest.credentials[1].email
    state.deletion_expectations = {
        **{kind: value[0] for kind, value in targets.items()},
        "account": deleted_account_id,
    }
    with psycopg.connect(
        _psycopg_url(state.source_database_url),
        connect_timeout=3,
    ) as connection:
        state.expected_counts = {
            "users": int(
                connection.execute("SELECT count(*) FROM users").fetchone()[0]
            ),
            "tasks": int(
                connection.execute("SELECT count(*) FROM tasks").fetchone()[0]
            )
            - 1,
        }


def _stage_write_after_backup(state: RunState) -> None:
    if state.seed_manifest is None:
        raise RuntimeError("恢复数据尚未播种")
    _start_source_api(state)
    credential = state.seed_manifest.credentials[0]
    with httpx.Client(
        base_url=state.config.http_origin,
        timeout=10,
        trust_env=False,
    ) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={
                "email": credential.email,
                "password": credential.password,
                "device_name": "M8 recovery",
            },
        )
        if login.status_code != 200:
            raise RuntimeError(f"恢复探针登录失败：HTTP {login.status_code}")
        client.headers["x-csrf-token"] = login.json()["csrf_token"]
        if state.scenario == RecoveryScenario.UI:
            if state.private_dir is None:
                raise RuntimeError("恢复 UI 私有目录未配置")
            session_cookie = client.cookies.get("itime_session")
            if not session_cookie:
                raise RuntimeError("恢复 UI 场景未取得旧会话")
            _write_browser_session_state(
                state.private_dir / "old-session.json",
                session_cookie,
            )
        before_id = _write_http_probe(client, title=PROBE_TITLES[0])
        state.probe_ids.append(before_id)
        state.probe_committed_at.append(_probe_time(state, before_id))
        state.target_at = _database_clock(state)

        runner = _pgbackrest(state, service="source-postgres")
        runner.run(
            "backup",
            "--type=diff",
            timeout=900,
            env=state.env,
            cwd=ROOT,
        )
        time.sleep(0.02)
        after_id = _write_http_probe(client, title=PROBE_TITLES[1])
        state.probe_ids.append(after_id)
        state.probe_committed_at.append(_probe_time(state, after_id))
        if state.scenario in {
            RecoveryScenario.DELETION_REPLAY,
            RecoveryScenario.UI,
        }:
            _perform_deletion_replay_changes(state, client)
    state.failure_at = _database_clock(state)


def _stage_archive_wal(state: RunState) -> None:
    if state.source_database_url is None:
        raise RuntimeError("源数据库尚未启动")
    with psycopg.connect(
        _psycopg_url(state.source_database_url),
        autocommit=True,
        connect_timeout=3,
    ) as connection:
        switched = connection.execute(
            "SELECT pg_walfile_name(pg_switch_wal())"
        ).fetchone()
    if switched is None:
        raise RuntimeError("无法取得待归档 WAL")
    target_wal = switched[0]
    state.archived_wal = target_wal
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        with psycopg.connect(
            _psycopg_url(state.source_database_url),
            connect_timeout=3,
        ) as connection:
            archived = connection.execute(
                "SELECT last_archived_wal FROM pg_stat_archiver"
            ).fetchone()
        if archived is not None and archived[0] == target_wal:
            break
        time.sleep(0.25)
    else:
        raise TimeoutError("最新恢复探针 WAL 未在 60 秒内归档")

    runner = _pgbackrest(state, service="source-postgres")
    runner.run("check", timeout=60, env=state.env, cwd=ROOT)
    state.backups = runner.info(timeout=60, env=state.env, cwd=ROOT)
    backup_types = {backup.backup_type for backup in state.backups}
    if not {"full", "diff"} <= backup_types:
        raise RuntimeError("pgBackRest 缺少 full 或 differential 备份")


def _stage_ledger_after(state: RunState) -> None:
    previous_digest = (
        ledger_digest(state.ledger_before)
        if state.ledger_before is not None
        else None
    )
    ledger = asyncio.run(
        build_ledger_snapshot(
            _settings(state, restored=False),
            generated_at=datetime.now(UTC),
            previous_digest=previous_digest,
        )
    )
    if state.private_dir is None:
        raise RuntimeError("恢复私有目录未配置")
    path = state.private_dir / "ledger-after.bin"
    write_encrypted_snapshot(ledger, path, state.ledger_key)
    state.ledger_after = ledger


def _stage_stop_source(state: RunState) -> None:
    if state.source_api is not None:
        _stop_process(state.source_api)
        state.processes.remove(state.source_api)
        state.source_api = None
    checked(
        state.compose("stop", "--timeout", "30", "source-postgres"),
        env=state.env,
        timeout=60,
    )


def _restore_arguments(state: RunState) -> tuple[str, ...]:
    arguments = ["--archive-mode=off"]
    if state.scenario in {
        RecoveryScenario.PITR,
        RecoveryScenario.DELETION_REPLAY,
        RecoveryScenario.UI,
    }:
        if state.target_at is None:
            raise RuntimeError("PITR 恢复目标尚未记录")
        arguments.extend(
            (
                "--type=time",
                "--target="
                + state.target_at.astimezone(UTC).strftime(
                    "%Y-%m-%d %H:%M:%S.%f+00"
                ),
                "--target-action=promote",
            )
        )
    return tuple(arguments)


def _reset_fault_volume(state: RunState, name: str) -> None:
    existing = _resource_names(state, "volume")
    if name in existing:
        checked(
            [
                "docker",
                "--context",
                state.config.docker_context,
                "volume",
                "rm",
                name,
            ],
            env=state.env,
            timeout=30,
        )
    checked(
        [
            "docker",
            "--context",
            state.config.docker_context,
            "volume",
            "create",
            "--label",
            f"com.docker.compose.project={state.config.compose_project}",
            name,
        ],
        env=state.env,
        timeout=30,
    )


def _copy_repository(
    state: RunState,
    *,
    destination: str,
) -> None:
    manifest = expected_cleanup_manifest(state.config)
    checked(
        [
            "docker",
            "--context",
            state.config.docker_context,
            "run",
            "--rm",
            "--network",
            "none",
            "--user",
            "0",
            "--mount",
            (
                "type=volume,"
                f"src={manifest.repository_volume},"
                "dst=/source,readonly"
            ),
            "--mount",
            f"type=volume,src={destination},dst=/destination",
            "--entrypoint",
            "cp",
            "itime-recovery-postgres:local",
            "-a",
            "/source/.",
            "/destination/",
        ],
        env=state.env,
        timeout=120,
    )


def _fault_container_prefix(
    state: RunState,
    *,
    config_path: Path,
    repository_volume: str,
    restore_volume: str | None = None,
    tiny_restore_tmpfs: bool = False,
) -> tuple[str, ...]:
    prefix = [
        "docker",
        "--context",
        state.config.docker_context,
        "run",
        "--rm",
        "--network",
        "none",
        "--user",
        "postgres",
        "--mount",
        (
            "type=volume,"
            f"src={repository_volume},"
            "dst=/var/lib/pgbackrest,readonly"
        ),
        "--mount",
        (
            "type=bind,"
            f"src={config_path.resolve()},"
            "dst=/etc/pgbackrest/pgbackrest.conf,readonly"
        ),
        "--tmpfs",
        f"{CONTAINER_TMP}:size=32m,mode=1777",
        "--tmpfs",
        "/var/run/postgresql:size=16m,mode=0770,uid=70,gid=70",
    ]
    if restore_volume is not None:
        prefix.extend(
            (
                "--mount",
                (
                    "type=volume,"
                    f"src={restore_volume},"
                    "dst=/var/lib/postgresql"
                ),
            )
        )
    elif tiny_restore_tmpfs:
        prefix.extend(
            (
                "--tmpfs",
                "/var/lib/postgresql:size=1m,mode=0700,uid=70,gid=70",
            )
        )
    prefix.append("itime-recovery-postgres:local")
    return tuple(prefix)


def _expect_pgbackrest_failure(
    runner: PgBackRest,
    operation: str,
    *arguments: str,
    state: RunState,
    label: str,
) -> None:
    try:
        runner.run(
            operation,
            *arguments,
            timeout=180,
            env=state.env,
            cwd=ROOT,
        )
    except PgBackRestError:
        state.fault_results[label] = True
        return
    raise RuntimeError(f"恢复故障未被拒绝：{label}")


def _run_repository_faults(state: RunState) -> None:
    if state.private_dir is None or state.archived_wal is None:
        raise RuntimeError("恢复故障场景缺少仓库或 WAL 证据")
    manifest = expected_cleanup_manifest(state.config)
    wrong_config = state.private_dir / "wrong-repository.conf"
    _write_secret(
        wrong_config,
        "\n".join(
            (
                "[global]",
                "repo1-path=/var/lib/pgbackrest",
                "repo1-cipher-type=aes-256-cbc",
                f"repo1-cipher-pass={secrets.token_urlsafe(48)}",
                "log-level-console=warn",
                "log-level-file=off",
                "",
                "[itime]",
                "pg1-path=/var/lib/postgresql/18/docker",
                "",
            )
        ),
    )
    state.registered_paths.append(wrong_config)
    _reset_fault_volume(state, manifest.fault_restore_data_volume)
    wrong_key = PgBackRest(
        binary="pgbackrest",
        stanza="itime",
        config=Path("/etc/pgbackrest/pgbackrest.conf"),
        command_prefix=_fault_container_prefix(
            state,
            config_path=wrong_config,
            repository_volume=manifest.repository_volume,
            restore_volume=manifest.fault_restore_data_volume,
        ),
    )
    _expect_pgbackrest_failure(
        wrong_key,
        "restore",
        "--archive-mode=off",
        state=state,
        label="wrong_repository_key",
    )

    _reset_fault_volume(state, manifest.fault_restore_data_volume)
    checked(
        [
            "docker",
            "--context",
            state.config.docker_context,
            "run",
            "--rm",
            "--network",
            "none",
            "--user",
            "0",
            "--mount",
            (
                "type=volume,"
                f"src={manifest.fault_restore_data_volume},"
                "dst=/var/lib/postgresql"
            ),
            "--entrypoint",
            "mkdir",
            "itime-recovery-postgres:local",
            "-p",
            "/var/lib/postgresql/18/docker",
        ],
        env=state.env,
        timeout=30,
    )
    checked(
        [
            "docker",
            "--context",
            state.config.docker_context,
            "run",
            "--rm",
            "--network",
            "none",
            "--user",
            "0",
            "--mount",
            (
                "type=volume,"
                f"src={manifest.fault_restore_data_volume},"
                "dst=/var/lib/postgresql"
            ),
            "--entrypoint",
            "touch",
            "itime-recovery-postgres:local",
            "/var/lib/postgresql/18/docker/occupied",
        ],
        env=state.env,
        timeout=30,
    )
    occupied = PgBackRest(
        binary="pgbackrest",
        stanza="itime",
        config=Path("/etc/pgbackrest/pgbackrest.conf"),
        command_prefix=_fault_container_prefix(
            state,
            config_path=Path(state.env["ITIME_RECOVERY_RESTORE_CONFIG"]),
            repository_volume=manifest.repository_volume,
            restore_volume=manifest.fault_restore_data_volume,
        ),
    )
    _expect_pgbackrest_failure(
        occupied,
        "restore",
        "--archive-mode=off",
        state=state,
        label="occupied_restore_volume",
    )

    tiny = PgBackRest(
        binary="pgbackrest",
        stanza="itime",
        config=Path("/etc/pgbackrest/pgbackrest.conf"),
        command_prefix=_fault_container_prefix(
            state,
            config_path=Path(state.env["ITIME_RECOVERY_RESTORE_CONFIG"]),
            repository_volume=manifest.repository_volume,
            tiny_restore_tmpfs=True,
        ),
    )
    _expect_pgbackrest_failure(
        tiny,
        "restore",
        "--archive-mode=off",
        state=state,
        label="disk_full",
    )

    _reset_fault_volume(state, manifest.fault_repository_volume)
    _copy_repository(
        state,
        destination=manifest.fault_repository_volume,
    )
    corrupt_script = (
        "from pathlib import Path;"
        "files=[p for p in Path('/repo/backup').rglob('*') "
        "if p.is_file() and 'manifest' not in p.name];"
        "p=max(files,key=lambda item:item.stat().st_size);"
        "data=bytearray(p.read_bytes());"
        "data[len(data)//2]^=255;"
        "p.write_bytes(data)"
    )
    checked(
        [
            "docker",
            "--context",
            state.config.docker_context,
            "run",
            "--rm",
            "--network",
            "none",
            "--user",
            "0",
            "--mount",
            (
                "type=volume,"
                f"src={manifest.fault_repository_volume},dst=/repo"
            ),
            "itime-performance:local",
            "python",
            "-c",
            corrupt_script,
        ],
        env=state.env,
        timeout=60,
    )
    _reset_fault_volume(state, manifest.fault_restore_data_volume)
    corrupted = PgBackRest(
        binary="pgbackrest",
        stanza="itime",
        config=Path("/etc/pgbackrest/pgbackrest.conf"),
        command_prefix=_fault_container_prefix(
            state,
            config_path=Path(state.env["ITIME_RECOVERY_RESTORE_CONFIG"]),
            repository_volume=manifest.fault_repository_volume,
            restore_volume=manifest.fault_restore_data_volume,
        ),
    )
    _expect_pgbackrest_failure(
        corrupted,
        "restore",
        "--archive-mode=off",
        state=state,
        label="corrupted_backup",
    )

    _reset_fault_volume(state, manifest.fault_repository_volume)
    _copy_repository(
        state,
        destination=manifest.fault_repository_volume,
    )
    remove_wal_script = (
        "from pathlib import Path;"
        f"name={state.archived_wal!r};"
        "matches=[p for p in Path('/repo/archive').rglob(name+'*') if p.is_file()];"
        "[p.unlink() for p in matches];"
        "assert matches"
    )
    checked(
        [
            "docker",
            "--context",
            state.config.docker_context,
            "run",
            "--rm",
            "--network",
            "none",
            "--user",
            "0",
            "--mount",
            (
                "type=volume,"
                f"src={manifest.fault_repository_volume},dst=/repo"
            ),
            "itime-performance:local",
            "python",
            "-c",
            remove_wal_script,
        ],
        env=state.env,
        timeout=60,
    )
    missing_wal = PgBackRest(
        binary="pgbackrest",
        stanza="itime",
        config=Path("/etc/pgbackrest/pgbackrest.conf"),
        command_prefix=_fault_container_prefix(
            state,
            config_path=Path(state.env["ITIME_RECOVERY_RESTORE_CONFIG"]),
            repository_volume=manifest.fault_repository_volume,
        ),
    )
    _expect_pgbackrest_failure(
        missing_wal,
        "archive-get",
        state.archived_wal,
        str(CONTAINER_TMP / "recovered-wal"),
        state=state,
        label="missing_wal",
    )

    ledger_path = state.private_dir / "ledger-after.bin"
    try:
        read_encrypted_snapshot(
            ledger_path,
            Fernet.generate_key(),
            expected_previous_digest=(
                ledger_digest(state.ledger_before)
                if state.ledger_before is not None
                else None
            ),
        )
    except LedgerError:
        state.fault_results["wrong_ledger_key"] = True
    else:
        raise RuntimeError("错误删除台账密钥未被拒绝")

    truncated_path = state.private_dir / "ledger-truncated.bin"
    truncated = ledger_path.read_bytes()[:-8]
    _write_secret(truncated_path, truncated.decode("ascii"))
    state.registered_paths.append(truncated_path)
    try:
        read_encrypted_snapshot(
            truncated_path,
            state.ledger_key,
            expected_previous_digest=(
                ledger_digest(state.ledger_before)
                if state.ledger_before is not None
                else None
            ),
        )
    except LedgerError:
        state.fault_results["truncated_ledger"] = True
    else:
        raise RuntimeError("截断删除台账未被拒绝")

    assert state.ledger_after is not None
    stale_ledger = replace(
        state.ledger_after,
        generated_at=(
            datetime.now(UTC) - timedelta(hours=2)
        ).isoformat(timespec="microseconds").replace("+00:00", "Z"),
    )
    try:
        asyncio.run(
            replay_ledger(
                _settings(state, restored=False),
                stale_ledger,
                at=datetime.now(UTC),
            )
        )
    except RecoveryReplayError:
        state.fault_results["stale_ledger"] = True
    else:
        raise RuntimeError("过期删除台账未被拒绝")


def _stage_restore(state: RunState) -> None:
    state.restore_started_at = datetime.now(UTC)
    if state.scenario == RecoveryScenario.FAULTS:
        _run_repository_faults(state)
    empty = checked(
        state.compose(
            "run",
            "--rm",
            "--no-deps",
            "restore-postgres",
            "find",
            "/var/lib/postgresql",
            "-type",
            "f",
            "-print",
            "-quit",
        ),
        env=state.env,
        timeout=30,
    )
    if empty.stdout.strip():
        raise RuntimeError("恢复数据卷不是空卷")
    runner = _pgbackrest(
        state,
        service="restore-postgres",
        one_off=True,
    )
    runner.run(
        "restore",
        *_restore_arguments(state),
        timeout=900,
        env=state.env,
        cwd=ROOT,
    )
    checked(
        state.compose("up", "-d", "--wait", "restore-postgres"),
        env=state.env,
        timeout=180,
    )
    port = _compose_port(state, "restore-postgres", 5432)
    admin_url = _database_url(state, port, "postgres")
    with psycopg.connect(
        _psycopg_url(admin_url),
        autocommit=True,
        connect_timeout=5,
    ) as connection:
        recovering = connection.execute(
            "SELECT pg_is_in_recovery()"
        ).fetchone()
        if recovering is None or recovering[0]:
            raise RuntimeError("恢复 PostgreSQL 尚未完成恢复或提升")
        source_exists = connection.execute(
            "SELECT 1 FROM pg_database WHERE datname=%s",
            (state.config.source_database,),
        ).fetchone()
        restore_exists = connection.execute(
            "SELECT 1 FROM pg_database WHERE datname=%s",
            (state.config.restore_database,),
        ).fetchone()
        if source_exists is None or restore_exists is not None:
            raise RuntimeError("恢复数据库名称状态不符合隔离改名条件")
        connection.execute(
            sql.SQL("ALTER DATABASE {} RENAME TO {}").format(
                sql.Identifier(state.config.source_database),
                sql.Identifier(state.config.restore_database),
            )
        )
    state.restore_database_url = _database_url(
        state,
        port,
        state.config.restore_database,
    )


def _stage_replay_ledger(state: RunState) -> None:
    if state.private_dir is None:
        raise RuntimeError("恢复私有目录未配置")
    ledger_path = state.private_dir / "ledger-after.bin"
    ledger = read_encrypted_snapshot(
        ledger_path,
        state.ledger_key,
        expected_previous_digest=(
            ledger_digest(state.ledger_before)
            if state.ledger_before is not None
            else None
        ),
    )
    state.replay_summary = asyncio.run(
        replay_ledger(
            _settings(state, restored=True),
            ledger,
            at=datetime.now(UTC),
        )
    )
    settings = _settings(state, restored=True)
    state.encryption_verification = asyncio.run(
        verify_encryption_keys(settings)
    )
    if not state.encryption_verification.passed:
        raise RuntimeError(
            "恢复数据库密钥验证失败："
            + "; ".join(state.encryption_verification.failures)
        )
    if state.scenario == RecoveryScenario.FAULTS:
        for field, expected_failure in (
            ("encryption_key", "base encryption key mismatch"),
            ("ai_encryption_key", "ai encryption key mismatch"),
            (
                "calendar_subscription_encryption_key",
                "calendar encryption key mismatch",
            ),
            ("export_encryption_key", "export encryption key mismatch"),
        ):
            values = settings.model_dump()
            values[field] = Fernet.generate_key().decode()
            result = asyncio.run(verify_encryption_keys(Settings(**values)))
            if result.passed or expected_failure not in result.failures:
                raise RuntimeError(
                    f"错误 {field} 未被恢复密钥门禁拒绝"
                )
            state.fault_results[f"wrong_{field}"] = True


def _stage_finalize(state: RunState) -> None:
    state.finalization_summary = asyncio.run(
        finalize_recovered_database(
            _settings(state, restored=True),
            at=datetime.now(UTC),
        )
    )


def _probe_rows(state: RunState) -> list[tuple[str, datetime]]:
    if state.restore_database_url is None:
        raise RuntimeError("恢复数据库尚未启动")
    with psycopg.connect(
        _psycopg_url(state.restore_database_url),
        connect_timeout=3,
    ) as connection:
        rows = connection.execute(
            """
            SELECT id::text, created_at
            FROM tasks
            WHERE id = ANY(%s)
            ORDER BY created_at, id
            """,
            (state.probe_ids,),
        ).fetchall()
    return [(row[0], row[1].astimezone(UTC)) for row in rows]


def _verify_deletion_expectations(state: RunState) -> None:
    if state.restore_database_url is None:
        raise RuntimeError("恢复数据库尚未启动")
    table_names = {
        "task": "tasks",
        "difficulty": "difficulty_records",
        "memory": "memories",
        "calendar-series": "calendar_series",
    }
    with psycopg.connect(
        _psycopg_url(state.restore_database_url),
        connect_timeout=3,
    ) as connection:
        account_id = state.deletion_expectations.get("account")
        if account_id is None or connection.execute(
            "SELECT 1 FROM users WHERE id=%s",
            (account_id,),
        ).fetchone() is not None:
            raise RuntimeError("删除台账未保持已注销账号的删除状态")
        for kind, table_name in table_names.items():
            object_id = state.deletion_expectations.get(kind)
            if object_id is None:
                raise RuntimeError(f"删除台账缺少 {kind} 验证目标")
            row = connection.execute(
                sql.SQL(
                    "SELECT purged_at IS NOT NULL FROM {} WHERE id=%s"
                ).format(sql.Identifier(table_name)),
                (object_id,),
            ).fetchone()
            if row != (True,):
                raise RuntimeError(f"删除台账未保持 {kind} 清理状态")


def _stage_verify(state: RunState) -> None:
    if (
        state.seed_manifest is None
        or state.ledger_after is None
        or state.replay_summary is None
        or len(state.probe_ids) != 2
    ):
        raise RuntimeError("恢复验证缺少源数据、台账或探针")
    rows = _probe_rows(state)
    expected_probe_ids = (
        state.probe_ids[:1]
        if state.scenario
        in {
            RecoveryScenario.PITR,
            RecoveryScenario.DELETION_REPLAY,
            RecoveryScenario.UI,
        }
        else state.probe_ids
    )
    if [row[0] for row in rows] != expected_probe_ids:
        raise RuntimeError("恢复探针范围与恢复目标不一致")
    state.probe_committed_at = [row[1] for row in rows]
    if state.scenario in {
        RecoveryScenario.DELETION_REPLAY,
        RecoveryScenario.UI,
    }:
        _verify_deletion_expectations(state)
    expected_counts = state.expected_counts or {
        "users": state.seed_manifest.counts["users"],
        "tasks": state.seed_manifest.counts["tasks"] + len(expected_probe_ids),
    }
    replayed = (
        state.replay_summary.applied + state.replay_summary.skipped
    )
    state.verification = asyncio.run(
        verify_recovered_database(
            _settings(state, restored=True),
            expected_counts=expected_counts,
            ledger_entries_total=len(state.ledger_after.entries),
            ledger_entries_applied=replayed,
        )
    )
    if not state.verification.passed:
        raise RuntimeError(
            "恢复数据库业务门禁失败："
            + "; ".join(state.verification.failures)
        )


def _edge_executable_path() -> str | None:
    configured = os.environ.get("ITIME_E2E_BROWSER_EXECUTABLE_PATH", "")
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


def _write_ui_run_input(state: RunState) -> Path:
    if (
        state.private_dir is None
        or state.deleted_account_email is None
        or not state.deletion_expectations
        or not state.deletion_labels
    ):
        raise RuntimeError("恢复 UI 场景缺少删除与账号证据")
    path = state.private_dir / "ui-run.json"
    objects = {
        kind: {
            "id": object_id,
            "label": state.deletion_labels[kind],
        }
        for kind, object_id in state.deletion_expectations.items()
        if kind != "account"
    }
    _write_secret(
        path,
        json.dumps(
            {
                "run_id": state.config.run_id,
                "scenario": state.scenario,
                "service_version": __version__,
                "probes": {
                    "before": PROBE_TITLES[0],
                    "after": PROBE_TITLES[1],
                },
                "deletions": {
                    "account_email": state.deleted_account_email,
                    "objects": objects,
                },
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    )
    return path


def _write_restored_browser_session(state: RunState) -> Path:
    if state.private_dir is None or state.seed_manifest is None:
        raise RuntimeError("恢复 UI 场景缺少主账号")
    credential = state.seed_manifest.credentials[0]
    with httpx.Client(
        base_url=state.config.http_origin,
        timeout=10,
        trust_env=False,
    ) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={
                "email": credential.email,
                "password": credential.password,
                "device_name": "M8 restored browser",
            },
        )
        if login.status_code != 200:
            raise RuntimeError(
                f"恢复 UI 主账号会话创建失败：HTTP {login.status_code}"
            )
        session_cookie = client.cookies.get("itime_session")
        if not session_cookie:
            raise RuntimeError("恢复 UI 主账号会话缺少 Cookie")
    path = state.private_dir / "primary-session.json"
    _write_browser_session_state(path, session_cookie)
    return path


def _run_recovery_ui(state: RunState) -> None:
    if state.private_dir is None:
        raise RuntimeError("恢复 UI 私有目录未配置")
    web_port = int(state.env["ITIME_RECOVERY_WEB_PORT"])
    web_origin = f"http://localhost:{web_port}"
    preview_environment = {
        **_host_environment(),
        "ITIME_API_ORIGIN": state.config.http_origin,
    }
    checked(
        ["pnpm", "build"],
        env=preview_environment,
        timeout=300,
        cwd=WEB,
    )
    preview = _start_process(
        state,
        "web-preview",
        ["pnpm", "preview", "--port", str(web_port)],
        env=preview_environment,
        cwd=WEB,
    )
    _wait_http(web_origin, preview)
    run_file = _write_ui_run_input(state)
    primary_state_file = _write_restored_browser_session(state)
    browser_environment = {
        **_host_environment(),
        "ITIME_RECOVERY_WEB_ORIGIN": web_origin,
        "ITIME_RECOVERY_ACCOUNT_FILE": str(
            state.private_dir / "accounts.json"
        ),
        "ITIME_RECOVERY_RUN_FILE": str(run_file),
        "ITIME_RECOVERY_OLD_STORAGE_STATE": str(
            state.private_dir / "old-session.json"
        ),
        "ITIME_RECOVERY_PRIMARY_STORAGE_STATE": str(primary_state_file),
        "ITIME_RECOVERY_RUN_ID": state.config.run_id,
        "ITIME_RECOVERY_BROWSER_LABEL": "chromium",
    }
    checked(
        ["pnpm", "test:e2e:recovery"],
        env=browser_environment,
        timeout=20 * 60,
        cwd=WEB,
    )
    edge_environment = {
        **browser_environment,
        "ITIME_RECOVERY_BROWSER_LABEL": "edge",
    }
    edge_path = _edge_executable_path()
    if edge_path is None:
        edge_environment["ITIME_E2E_BROWSER_CHANNEL"] = "msedge"
    else:
        edge_environment["ITIME_E2E_BROWSER_EXECUTABLE_PATH"] = edge_path
    checked(
        [
            "pnpm",
            "exec",
            "playwright",
            "test",
            "--config",
            "playwright.recovery.config.ts",
            "--project=desktop",
        ],
        env=edge_environment,
        timeout=20 * 60,
        cwd=WEB,
    )
    state.ui_passed = True


def _stage_start_app(state: RunState) -> None:
    checked(
        state.compose("up", "-d", "--wait", "restore-api"),
        env=state.env,
        timeout=180,
    )
    port = _compose_port(state, "restore-api", 8000)
    state.config = replace(
        state.config,
        http_origin=f"http://127.0.0.1:{port}",
    )
    require_safe_run(state.config)
    _wait_http(state.config.http_origin)
    if state.scenario == RecoveryScenario.UI:
        _run_recovery_ui(state)
    state.service_ready_at = datetime.now(UTC)


def _stage_build_report(state: RunState) -> RecoveryGateResult:
    if (
        state.seed_manifest is None
        or state.ledger_after is None
        or state.replay_summary is None
        or state.finalization_summary is None
        or state.verification is None
        or state.encryption_verification is None
        or state.target_at is None
        or state.failure_at is None
        or state.restore_started_at is None
        or state.service_ready_at is None
        or not state.probe_committed_at
    ):
        raise RuntimeError("恢复报告证据不完整")
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
    manifest = {
        "run_id": state.config.run_id,
        "scenario": state.scenario,
        "service_version": __version__,
        "environment": "test",
        "docker_context": state.config.docker_context,
        "source_database": state.config.source_database,
        "restore_database": state.config.restore_database,
        "commit": commit,
        "dirty_worktree": dirty,
        "postgres_version": "18.6",
        "pgbackrest_version": "2.59.3",
    }
    backup = {
        "backups": [item.to_public_dict() for item in state.backups],
        "ledger": {
            "entries": len(state.ledger_after.entries),
            "digest": ledger_digest(state.ledger_after),
            "generated_at": state.ledger_after.generated_at,
        },
    }
    restore = {
        "target": state.target_at,
        "probe_ids": list(state.probe_ids),
        "replay": asdict(state.replay_summary),
        "finalizer": asdict(state.finalization_summary),
        "verification": state.verification.to_public_dict(),
        "encryption_verification": (
            state.encryption_verification.to_public_dict()
        ),
        "fault_results": dict(sorted(state.fault_results.items())),
    }
    evidence = RecoveryEvidence(
        run_id=state.config.run_id,
        scenario=state.scenario,
        recovery_target_at=state.target_at,
        failure_at=state.failure_at,
        latest_recovered_at=max(state.probe_committed_at),
        restore_started_at=state.restore_started_at,
        service_ready_at=state.service_ready_at,
        migration_passed=(
            state.verification.current_revision
            == state.verification.expected_revision
        ),
        ledger_passed=(
            state.verification.ledger_entries_applied
            == state.verification.ledger_entries_total
        ),
        finalizer_passed=not state.finalization_summary.inconsistencies,
        invariants_passed=state.verification.passed,
        ui_required=state.scenario == RecoveryScenario.UI,
        ui_passed=state.ui_passed,
        remaining_resources=(),
    )
    state.gate_result = write_recovery_report(
        state.config.output_dir,
        manifest=manifest,
        backup=backup,
        restore=restore,
        evidence=evidence,
    )
    return state.gate_result


def _resource_names(state: RunState, kind: str) -> tuple[str, ...]:
    list_arguments = ["ls"]
    if kind == "container":
        list_arguments.append("--all")
    result = checked(
        [
            "docker",
            "--context",
            state.config.docker_context,
            kind,
            *list_arguments,
            "--filter",
            f"label=com.docker.compose.project={state.config.compose_project}",
            "--format",
            "{{.ID}}" if kind == "container" else "{{.Name}}",
        ],
        env=state.env,
        timeout=15,
    )
    return tuple(line for line in result.stdout.splitlines() if line)


def _remove_resources(state: RunState) -> None:
    manifest = expected_cleanup_manifest(state.config)
    require_safe_cleanup(state.config, manifest)
    containers = _resource_names(state, "container")
    if containers:
        checked(
            [
                "docker",
                "--context",
                state.config.docker_context,
                "container",
                "rm",
                "--force",
                *containers,
            ],
            env=state.env,
            timeout=60,
        )
    volumes = _resource_names(state, "volume")
    expected_volumes = {
        manifest.source_data_volume,
        manifest.restore_data_volume,
        manifest.repository_volume,
        manifest.source_spool_volume,
        manifest.restore_spool_volume,
        manifest.fault_repository_volume,
        manifest.fault_restore_data_volume,
    }
    if not set(volumes) <= expected_volumes:
        raise RuntimeError("恢复清理发现清单外数据卷")
    if volumes:
        checked(
            [
                "docker",
                "--context",
                state.config.docker_context,
                "volume",
                "rm",
                *volumes,
            ],
            env=state.env,
            timeout=60,
        )
    networks = _resource_names(state, "network")
    if not set(networks) <= {manifest.network, manifest.host_network}:
        raise RuntimeError("恢复清理发现清单外网络")
    if networks:
        checked(
            [
                "docker",
                "--context",
                state.config.docker_context,
                "network",
                "rm",
                *networks,
            ],
            env=state.env,
            timeout=60,
        )


def _cleanup(state: RunState) -> None:
    for process in reversed(state.processes):
        _stop_process(process)
    state.processes.clear()
    state.source_api = None
    _remove_resources(state)
    for path in state.registered_paths:
        path.unlink(missing_ok=True)
    private_dir = state.private_dir
    if private_dir is not None and private_dir.exists():
        private_dir.rmdir()
    state.repo_cipher_pass = ""
    state.ledger_key = b""


def _verify_clean(state: RunState) -> tuple[str, ...]:
    remaining: list[str] = []
    for kind in ("container", "volume", "network"):
        remaining.extend(
            f"{kind}:{item}" for item in _resource_names(state, kind)
        )
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
    "prepare-secrets": _stage_prepare_secrets,
    "start-source": _stage_start_source,
    "migrate-source": _stage_migrate_source,
    "seed-source": _stage_seed_source,
    "create-stanza": _stage_create_stanza,
    "ledger-before": _stage_ledger_before,
    "backup": _stage_backup,
    "write-after-backup": _stage_write_after_backup,
    "archive-wal": _stage_archive_wal,
    "ledger-after": _stage_ledger_after,
    "stop-source": _stage_stop_source,
    "restore": _stage_restore,
    "replay-ledger": _stage_replay_ledger,
    "finalize": _stage_finalize,
    "verify": _stage_verify,
    "start-app": _stage_start_app,
    "build-report": _stage_build_report,
    "cleanup": _cleanup,
    "verify-clean": _verify_clean,
}


def execute_stage(state: RunState, stage: str) -> object:
    return STAGE_HANDLERS[stage](state)


def run_recovery_scenario(
    scenario: str,
    *,
    run_id: str | None = None,
) -> RecoveryRunResult:
    state = _new_state(scenario, run_id=run_id)
    OUTPUT_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = OUTPUT_ROOT / ".run.lock"
    try:
        with exclusive_run_lock(lock_path):
            return execute_lifecycle(state, execute=execute_stage)
    except FileExistsError:
        return RecoveryRunResult(
            False,
            state.config.output_dir,
            (),
            "已有备份恢复演练正在运行",
        )


def find_recovery_resources() -> tuple[str, ...]:
    state = _new_state("smoke")
    resources: list[str] = []
    for kind in ("container", "volume", "network"):
        result = checked(
            [
                "docker",
                "--context",
                DOCKER_CONTEXT,
                kind,
                "ls",
                "--filter",
                "name=itime-recovery-",
                "--format",
                "{{.ID}}" if kind == "container" else "{{.Name}}",
            ],
            env=state.env,
            timeout=15,
        )
        resources.extend(
            f"{kind}:{line}"
            for line in result.stdout.splitlines()
            if line
        )
    return tuple(resources)


def cleanup_run(run_id: str) -> tuple[str, ...]:
    state = _new_state("smoke", run_id=run_id)
    require_safe_run(state.config)
    _remove_resources(state)
    private_dir = state.private_dir
    if (
        private_dir is not None
        and private_dir.exists()
        and not private_dir.is_symlink()
    ):
        for name in (
            "source.conf",
            "restore.conf",
            "accounts.json",
            "ledger-before.bin",
            "ledger-after.bin",
            "old-session.json",
            "primary-session.json",
            "ui-run.json",
        ):
            (private_dir / name).unlink(missing_ok=True)
        private_dir.rmdir()
    return _verify_clean(state)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="运行 iTime 隔离 PostgreSQL 备份恢复演练"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    scenario = subparsers.add_parser("scenario")
    scenario.add_argument(
        "scenario",
        choices=tuple(item.value for item in RecoveryScenario),
    )
    subparsers.add_parser("baseline")
    cleanup = subparsers.add_parser("cleanup")
    cleanup.add_argument("--run-id", required=True)
    subparsers.add_parser("verify-clean")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "scenario":
        result = run_recovery_scenario(args.scenario)
        print(
            json.dumps(
                {
                    "passed": result.passed,
                    "run_dir": str(result.run_dir),
                    "remaining_resources": list(result.remaining_resources),
                    "error": result.error,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        raise SystemExit(0 if result.passed else 1)
    if args.command == "baseline":
        results = [
            run_recovery_scenario(scenario)
            for scenario in BASELINE_SCENARIOS
        ]
        print(
            json.dumps(
                [
                    {
                        "scenario": scenario,
                        "passed": result.passed,
                        "run_dir": str(result.run_dir),
                        "error": result.error,
                    }
                    for scenario, result in zip(
                        BASELINE_SCENARIOS,
                        results,
                        strict=True,
                    )
                ],
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        raise SystemExit(0 if all(result.passed for result in results) else 1)
    if args.command == "cleanup":
        remaining = cleanup_run(args.run_id)
        print(json.dumps({"remaining_resources": list(remaining)}))
        raise SystemExit(0 if not remaining else 1)
    remaining = find_recovery_resources()
    print(json.dumps({"remaining_resources": list(remaining)}))
    raise SystemExit(0 if not remaining else 1)


if __name__ == "__main__":
    main()
