import argparse
import asyncio
import csv
import json
import os
import re
import signal
import subprocess
import time
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

COMPOSE_PROJECT = re.compile(r"^itime-perf-[0-9a-f]{8,32}$")
SIZE_PATTERN = re.compile(r"^(?P<value>\d+(?:\.\d+)?)(?P<unit>[kmgt]?i?b)$", re.IGNORECASE)
SIZE_FACTORS = {
    "b": 1,
    "kb": 1000,
    "mb": 1000**2,
    "gb": 1000**3,
    "tb": 1000**4,
    "kib": 1024,
    "mib": 1024**2,
    "gib": 1024**3,
    "tib": 1024**4,
}

QUEUE_QUERIES = {
    "ai": """
        SELECT
          count(*) FILTER (WHERE status='queued') AS queued,
          count(*) FILTER (WHERE status='running') AS running,
          min(available_at) FILTER (WHERE status='queued') AS oldest,
          count(*) FILTER (
            WHERE status IN ('succeeded','failed','timed_out','cancelled')
          ) AS terminal
        FROM ai_jobs
    """,
    "subscriptions": """
        SELECT
          count(*) FILTER (WHERE refresh_status='queued') AS queued,
          count(*) FILTER (WHERE refresh_status='running') AS running,
          min(next_refresh_at) FILTER (WHERE refresh_status='queued') AS oldest,
          count(*) FILTER (WHERE refresh_status IN ('idle','error')) AS terminal
        FROM calendar_subscriptions
    """,
    "public_links": """
        SELECT
          count(*) FILTER (WHERE fetch_status='queued') AS queued,
          count(*) FILTER (WHERE fetch_status='running') AS running,
          min(available_at) FILTER (WHERE fetch_status='queued') AS oldest,
          count(*) FILTER (WHERE fetch_status IN ('ready','failed')) AS terminal
        FROM task_links
    """,
    "exports": """
        SELECT
          count(*) FILTER (WHERE status='queued') AS queued,
          count(*) FILTER (WHERE status='running') AS running,
          min(available_at) FILTER (WHERE status='queued') AS oldest,
          count(*) FILTER (WHERE status IN ('succeeded','failed','expired')) AS terminal
        FROM data_export_jobs
    """,
    "mail": """
        SELECT
          count(*) FILTER (
            WHERE finished_at IS NULL
              AND (lease_until IS NULL OR lease_until <= now())
          ) AS queued,
          count(*) FILTER (
            WHERE finished_at IS NULL AND lease_until > now()
          ) AS running,
          min(available_at) FILTER (WHERE finished_at IS NULL) AS oldest,
          count(*) FILTER (WHERE finished_at IS NOT NULL) AS terminal
        FROM mail_outbox
    """,
    "account_deletions": """
        SELECT
          count(*) FILTER (WHERE status='pending') AS queued,
          count(*) FILTER (WHERE status='processing') AS running,
          min(available_at) FILTER (WHERE status='pending') AS oldest,
          count(*) FILTER (WHERE status IN ('cancelled','completed')) AS terminal
        FROM account_deletions
    """,
}


class SamplerError(RuntimeError):
    """性能采样无法生成可信样本。"""


@dataclass(frozen=True)
class DockerStat:
    at: datetime
    name: str
    cpu_percent: float
    memory_bytes: int
    memory_limit_bytes: int
    network_rx_bytes: int
    network_tx_bytes: int
    block_read_bytes: int
    block_write_bytes: int
    pids: int


@dataclass(frozen=True)
class PostgresSample:
    at: datetime
    connections_total: int
    connections_active: int
    connections_idle: int
    connections_idle_in_transaction: int
    connections_waiting: int
    blocked_over_two_seconds: int
    queues: dict[str, dict[str, int | str | None]]
    slow_queries: tuple[dict[str, int | float | str | None], ...]


@dataclass(frozen=True)
class SamplerConfig:
    docker_context: str
    compose_project: str
    database_url: str
    output_dir: Path
    cpu_count: int = 2
    memory_limit_bytes: int = 4 * 1024**3
    database_connection_limit: int = 50
    interval_seconds: float = 1.0
    container_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.docker_context != "colima-itime-perf":
            raise ValueError("采样只允许专用 colima-itime-perf context")
        if not COMPOSE_PROJECT.fullmatch(self.compose_project):
            raise ValueError("Compose 项目名必须包含有效运行 ID")
        if self.cpu_count < 1 or self.memory_limit_bytes < 1:
            raise ValueError("CPU 和内存预算必须为正数")
        if self.database_connection_limit < 1 or self.interval_seconds <= 0:
            raise ValueError("连接和采样间隔必须为正数")


def parse_bytes(value: str) -> int:
    matched = SIZE_PATTERN.fullmatch(value.strip())
    if matched is None:
        raise ValueError(f"无法识别 Docker 字节单位：{value!r}")
    amount = Decimal(matched.group("value"))
    unit = matched.group("unit").casefold()
    return int(amount * SIZE_FACTORS[unit])


def _parse_pair(value: str) -> tuple[int, int]:
    parts = [part.strip() for part in value.split("/")]
    if len(parts) != 2:
        raise ValueError(f"Docker 统计值必须包含读写两部分：{value!r}")
    return parse_bytes(parts[0]), parse_bytes(parts[1])


def parse_docker_stat(value: dict[str, str], *, at: datetime) -> DockerStat:
    memory, memory_limit = _parse_pair(value["MemUsage"])
    network_rx, network_tx = _parse_pair(value["NetIO"])
    block_read, block_write = _parse_pair(value["BlockIO"])
    cpu = value["CPUPerc"].strip()
    if not cpu.endswith("%"):
        raise ValueError("Docker CPU 百分比格式无效")
    return DockerStat(
        at=at,
        name=value["Name"],
        cpu_percent=float(cpu.removesuffix("%")),
        memory_bytes=memory,
        memory_limit_bytes=memory_limit,
        network_rx_bytes=network_rx,
        network_tx_bytes=network_tx,
        block_read_bytes=block_read,
        block_write_bytes=block_write,
        pids=int(value["PIDs"]),
    )


def _run(command: list[str], *, timeout: float = 5) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # noqa: S603
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise SamplerError(f"采样命令失败：{command[0]}") from exc


async def sample_docker(
    docker_context: str,
    compose_project: str,
    *,
    at: datetime,
    container_ids: tuple[str, ...] = (),
) -> tuple[DockerStat, ...]:
    if docker_context != "colima-itime-perf":
        raise SamplerError("Docker 采样只允许专用 context")
    if not COMPOSE_PROJECT.fullmatch(compose_project):
        raise SamplerError("Docker 采样项目名无效")
    active_container_ids = container_ids or await list_container_ids(
        docker_context,
        compose_project,
    )
    if not active_container_ids:
        raise SamplerError("被测 Compose 项目没有运行中的容器")
    result = await asyncio.to_thread(
        _run,
        [
            "docker",
            "--context",
            docker_context,
            "stats",
            "--no-stream",
            "--format",
            "{{json .}}",
            *active_container_ids,
        ],
    )
    try:
        return tuple(
            parse_docker_stat(json.loads(line), at=at)
            for line in result.stdout.splitlines()
            if line.strip()
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SamplerError("Docker stats 输出格式无效") from exc


async def list_container_ids(
    docker_context: str,
    compose_project: str,
) -> tuple[str, ...]:
    if docker_context != "colima-itime-perf":
        raise SamplerError("Docker 容器枚举只允许专用 context")
    if not COMPOSE_PROJECT.fullmatch(compose_project):
        raise SamplerError("Docker 容器枚举项目名无效")
    listed = await asyncio.to_thread(
        _run,
        [
            "docker",
            "--context",
            docker_context,
            "ps",
            "--filter",
            f"label=com.docker.compose.project={compose_project}",
            "--format",
            "{{.ID}}",
        ],
    )
    return tuple(listed.stdout.split())


async def inspect_containers(
    docker_context: str,
    compose_project: str,
) -> tuple[dict[str, Any], ...]:
    if docker_context != "colima-itime-perf":
        raise SamplerError("Docker 检查只允许专用 context")
    if not COMPOSE_PROJECT.fullmatch(compose_project):
        raise SamplerError("Docker 检查项目名无效")
    listed = await asyncio.to_thread(
        _run,
        [
            "docker",
            "--context",
            docker_context,
            "ps",
            "--filter",
            f"label=com.docker.compose.project={compose_project}",
            "--format",
            "{{.ID}}",
        ],
    )
    container_ids = listed.stdout.split()
    if not container_ids:
        return ()
    inspected = await asyncio.to_thread(
        _run,
        ["docker", "--context", docker_context, "inspect", *container_ids],
    )
    payload = json.loads(inspected.stdout)
    return tuple(
        {
            "name": item["Name"].removeprefix("/"),
            "image_id": item["Image"],
            "memory_limit_bytes": item["HostConfig"]["Memory"],
            "nano_cpus": item["HostConfig"]["NanoCpus"],
            "pids_limit": item["HostConfig"]["PidsLimit"],
            "health": item["State"].get("Health", {}).get("Status"),
        }
        for item in payload
    )


def _psycopg_url(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


async def sample_postgres(
    database_url: str,
    *,
    at: datetime | None = None,
    include_statements: bool = True,
) -> PostgresSample:
    sampled_at = at or datetime.now(UTC)
    try:
        connection = await psycopg.AsyncConnection.connect(
            _psycopg_url(database_url),
            autocommit=True,
            connect_timeout=3,
            application_name="itime-performance-sampler",
            row_factory=dict_row,
        )
        async with connection:
            activity_cursor = await connection.execute(
                """
                SELECT state, wait_event_type, wait_event, count(*) AS count
                FROM pg_stat_activity
                WHERE datname = current_database()
                GROUP BY state, wait_event_type, wait_event
                """
            )
            activity = await activity_cursor.fetchall()
            blocked_cursor = await connection.execute(
                """
                SELECT count(*) AS count
                FROM pg_stat_activity
                WHERE datname = current_database()
                  AND wait_event_type = 'Lock'
                  AND now() - query_start > interval '2 seconds'
                """
            )
            blocked = int((await blocked_cursor.fetchone())["count"])
            slow_rows = []
            if include_statements:
                slow_cursor = await connection.execute(
                    """
                    SELECT queryid, calls, total_exec_time, mean_exec_time, rows,
                           shared_blks_hit, shared_blks_read, temp_blks_written
                    FROM pg_stat_statements
                    WHERE dbid = (
                        SELECT oid FROM pg_database WHERE datname=current_database()
                    )
                    ORDER BY total_exec_time DESC
                    LIMIT 20
                    """
                )
                slow_rows = await slow_cursor.fetchall()
            queues: dict[str, dict[str, int | str | None]] = {}
            for name, query in QUEUE_QUERIES.items():
                cursor = await connection.execute(query)
                row = await cursor.fetchone()
                if row is None:
                    raise SamplerError(f"队列采样未返回数据：{name}")
                oldest = row["oldest"]
                queues[name] = {
                    "queued": int(row["queued"]),
                    "running": int(row["running"]),
                    "oldest": oldest.isoformat() if oldest is not None else None,
                    "terminal": int(row["terminal"]),
                }
    except (psycopg.Error, KeyError, TypeError, ValueError) as exc:
        raise SamplerError("PostgreSQL 性能采样失败") from exc

    connections_total = sum(int(row["count"]) for row in activity)
    return PostgresSample(
        at=sampled_at,
        connections_total=connections_total,
        connections_active=sum(
            int(row["count"]) for row in activity if row["state"] == "active"
        ),
        connections_idle=sum(
            int(row["count"]) for row in activity if row["state"] == "idle"
        ),
        connections_idle_in_transaction=sum(
            int(row["count"])
            for row in activity
            if row["state"] == "idle in transaction"
        ),
        connections_waiting=sum(
            int(row["count"]) for row in activity if row["wait_event_type"] is not None
        ),
        blocked_over_two_seconds=blocked,
        queues=queues,
        slow_queries=tuple(
            {
                "queryid": str(row["queryid"]) if row["queryid"] is not None else None,
                "calls": int(row["calls"]),
                "total_exec_time": float(row["total_exec_time"]),
                "mean_exec_time": float(row["mean_exec_time"]),
                "rows": int(row["rows"]),
                "shared_blks_hit": int(row["shared_blks_hit"]),
                "shared_blks_read": int(row["shared_blks_read"]),
                "temp_blks_written": int(row["temp_blks_written"]),
            }
            for row in slow_rows
        ),
    )


def _append_csv(path: Path, fieldnames: tuple[str, ...], rows: list[dict[str, object]]) -> None:
    exists = path.exists() and path.stat().st_size > 0
    with path.open("a", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()
        writer.writerows(rows)
        output.flush()
        os.fsync(output.fileno())


async def write_sample(
    config: SamplerConfig,
    *,
    sampling_gap_seconds: int = 0,
) -> None:
    sampled_at = datetime.now(UTC)
    docker_stats, postgres = await asyncio.gather(
        sample_docker(
            config.docker_context,
            config.compose_project,
            at=sampled_at,
            container_ids=config.container_ids,
        ),
        sample_postgres(config.database_url, at=sampled_at),
    )
    if not docker_stats:
        raise SamplerError("Docker 采样为空")
    config.output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)

    total_cpu_ratio = sum(item.cpu_percent for item in docker_stats) / (
        config.cpu_count * 100
    )
    total_memory_ratio = sum(item.memory_bytes for item in docker_stats) / (
        config.memory_limit_bytes
    )
    _append_csv(
        config.output_dir / "resources.csv",
        (
            "at",
            "cpu_ratio",
            "memory_ratio",
            "database_connection_ratio",
            "blocked_over_two_seconds",
            "pool_timeouts",
            "server_5xx",
            "sampling_gap_seconds",
        ),
        [
            {
                "at": sampled_at.isoformat(),
                "cpu_ratio": total_cpu_ratio,
                "memory_ratio": total_memory_ratio,
                "database_connection_ratio": (
                    postgres.connections_total / config.database_connection_limit
                ),
                "blocked_over_two_seconds": postgres.blocked_over_two_seconds,
                "pool_timeouts": 0,
                "server_5xx": 0,
                "sampling_gap_seconds": sampling_gap_seconds,
            }
        ],
    )
    _append_csv(
        config.output_dir / "containers.csv",
        tuple(asdict(docker_stats[0])),
        [
            {
                **asdict(item),
                "at": item.at.isoformat(),
            }
            for item in docker_stats
        ],
    )
    _append_csv(
        config.output_dir / "postgres.csv",
        (
            "at",
            "connections_total",
            "connections_active",
            "connections_idle",
            "connections_idle_in_transaction",
            "connections_waiting",
            "blocked_over_two_seconds",
            "slow_queries",
        ),
        [
            {
                "at": postgres.at.isoformat(),
                "connections_total": postgres.connections_total,
                "connections_active": postgres.connections_active,
                "connections_idle": postgres.connections_idle,
                "connections_idle_in_transaction": (
                    postgres.connections_idle_in_transaction
                ),
                "connections_waiting": postgres.connections_waiting,
                "blocked_over_two_seconds": postgres.blocked_over_two_seconds,
                "slow_queries": json.dumps(postgres.slow_queries, separators=(",", ":")),
            }
        ],
    )
    queue_rows = [
        {
            "at": postgres.at.isoformat(),
            "queue": name,
            **values,
        }
        for name, values in sorted(postgres.queues.items())
    ]
    _append_csv(
        config.output_dir / "queues.csv",
        ("at", "queue", "queued", "running", "oldest", "terminal"),
        queue_rows,
    )


async def sample_until_stopped(
    config: SamplerConfig,
    stop: asyncio.Event,
) -> None:
    last_started: float | None = None
    while not stop.is_set():
        started = time.monotonic()
        sampling_gap = (
            0
            if last_started is None
            else max(
                0,
                int(started - last_started - config.interval_seconds),
            )
        )
        await write_sample(config, sampling_gap_seconds=sampling_gap)
        last_started = started
        try:
            await asyncio.wait_for(
                stop.wait(),
                timeout=max(
                    0.0,
                    config.interval_seconds - (time.monotonic() - started),
                ),
            )
        except TimeoutError:
            continue


async def _main_async(config: SamplerConfig) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_number in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signal_number, stop.set)
    container_ids = await list_container_ids(
        config.docker_context,
        config.compose_project,
    )
    if not container_ids:
        raise SamplerError("被测 Compose 项目没有运行中的容器")
    await sample_until_stopped(
        replace(config, container_ids=container_ids),
        stop,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="采集 iTime 性能资源指标")
    parser.add_argument("--docker-context", required=True)
    parser.add_argument("--compose-project", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--interval", type=float, default=1.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    database_url = os.environ.get("ITIME_PERF_DATABASE_URL")
    if not database_url:
        raise RuntimeError("缺少 ITIME_PERF_DATABASE_URL")
    asyncio.run(
        _main_async(
            SamplerConfig(
                docker_context=args.docker_context,
                compose_project=args.compose_project,
                database_url=database_url,
                output_dir=args.output_dir,
                interval_seconds=args.interval,
            )
        )
    )


if __name__ == "__main__":
    main()
