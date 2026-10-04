import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from performance.contracts import RunConfig

RUN_ID = re.compile(r"^[0-9a-f]{8,32}$")
OUTPUT_ROOT = Path("/", "tmp", "itime-performance")
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class SafetyError(RuntimeError):
    """性能测试运行边界校验失败。"""


@dataclass(frozen=True)
class CleanupManifest:
    run_id: str
    compose_project: str
    database_name: str
    output_dir: Path


def require_safe_run(config: RunConfig) -> None:
    parsed = urlsplit(config.http_origin)
    expected_output = OUTPUT_ROOT / config.run_id

    if config.environment != "test":
        raise SafetyError("性能测试只允许 ITIME_ENVIRONMENT=test")
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in LOOPBACK_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise SafetyError("性能 HTTP origin 必须为无凭据、无路径的回环 HTTP(S) 地址")
    if config.database_host not in LOOPBACK_HOSTS:
        raise SafetyError("性能数据库只允许回环地址")
    if not RUN_ID.fullmatch(config.run_id):
        raise SafetyError("运行 ID 必须为 8-32 位小写十六进制")
    if config.database_name != f"itime_perf_{config.run_id}":
        raise SafetyError("数据库名与运行 ID 不匹配")
    if config.guard_value != f"itime-performance:{config.run_id}":
        raise SafetyError("数据库安全标记与运行 ID 不匹配")
    if config.docker_context != "colima-itime-perf":
        raise SafetyError("性能测试只允许专用 colima-itime-perf context")
    if (
        config.output_dir != expected_output
        or config.output_dir.resolve() != expected_output.resolve()
    ):
        raise SafetyError("输出目录不在本次运行边界内")


def require_safe_cleanup(config: RunConfig, manifest: CleanupManifest) -> None:
    require_safe_run(config)
    expected = CleanupManifest(
        run_id=config.run_id,
        compose_project=config.compose_project,
        database_name=config.database_name,
        output_dir=config.output_dir,
    )
    if manifest != expected:
        raise SafetyError("清理清单包含不属于本次运行的资源")


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
