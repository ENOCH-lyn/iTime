import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from recovery.contracts import CleanupManifest, RecoveryConfig

RUN_ID = re.compile(r"^[0-9a-f]{8,32}$")
OUTPUT_ROOT = Path("/", "tmp", "itime-recovery")
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
DOCKER_CONTEXT = "colima-itime-recovery"


class RecoverySafetyError(RuntimeError):
    """恢复演练运行边界校验失败。"""


def _require_loopback_origin(origin: str) -> None:
    parsed = urlsplit(origin)
    try:
        port = parsed.port
    except ValueError as error:
        raise RecoverySafetyError("恢复 HTTP origin 端口无效") from error

    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in LOOPBACK_HOSTS
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise RecoverySafetyError("恢复 HTTP origin 必须为无凭据、无路径的回环 HTTP(S) 地址")


def _require_output_path(config: RecoveryConfig) -> None:
    expected_output = OUTPUT_ROOT / config.run_id
    expected_resolved = OUTPUT_ROOT.resolve() / config.run_id
    if (
        OUTPUT_ROOT.is_symlink()
        or config.output_dir != expected_output
        or config.output_dir.is_symlink()
        or config.output_dir.resolve() != expected_resolved
    ):
        raise RecoverySafetyError("输出目录不在本次恢复运行边界内")


def require_safe_run(config: RecoveryConfig) -> None:
    if config.environment != "test":
        raise RecoverySafetyError("恢复演练只允许 ITIME_ENVIRONMENT=test")
    if not RUN_ID.fullmatch(config.run_id):
        raise RecoverySafetyError("运行 ID 必须为 8-32 位小写十六进制")
    if config.docker_context != DOCKER_CONTEXT:
        raise RecoverySafetyError(f"恢复演练只允许专用 {DOCKER_CONTEXT} context")
    if config.source_database != f"itime_backup_{config.run_id}":
        raise RecoverySafetyError("源数据库名与运行 ID 不匹配")
    if config.restore_database != f"itime_restore_{config.run_id}":
        raise RecoverySafetyError("恢复数据库名与运行 ID 不匹配")
    if config.source_database == config.restore_database:
        raise RecoverySafetyError("源数据库和恢复数据库必须不同")
    if config.guard_value != f"itime-recovery:{config.run_id}":
        raise RecoverySafetyError("恢复安全标记与运行 ID 不匹配")

    _require_loopback_origin(config.http_origin)
    _require_output_path(config)


def expected_cleanup_manifest(config: RecoveryConfig) -> CleanupManifest:
    project = config.compose_project
    return CleanupManifest(
        run_id=config.run_id,
        compose_project=project,
        source_database=config.source_database,
        restore_database=config.restore_database,
        source_data_volume=f"{project}_source_data",
        restore_data_volume=f"{project}_restore_data",
        repository_volume=f"{project}_repository",
        source_spool_volume=f"{project}_spool",
        restore_spool_volume=f"{project}_restore_spool",
        fault_repository_volume=f"{project}_fault_repository",
        fault_restore_data_volume=f"{project}_fault_restore_data",
        network=f"{project}_default",
        host_network=f"{project}_host_access",
        output_dir=config.output_dir,
    )


def require_safe_cleanup(
    config: RecoveryConfig,
    manifest: CleanupManifest,
) -> None:
    require_safe_run(config)
    if manifest != expected_cleanup_manifest(config):
        raise RecoverySafetyError("清理清单包含不属于本次恢复运行的资源")


@contextmanager
def exclusive_run_lock(path: Path) -> Iterator[None]:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, f"{os.getpid()}\n".encode())
        yield
    finally:
        os.close(descriptor)
        path.unlink(missing_ok=True)
