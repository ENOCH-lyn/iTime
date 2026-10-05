# iTime M8 性能与容量基线实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在固定 2 vCPU / 4 GiB 隔离环境中建立可重复的性能与容量测试系统，完成普通 API、同步、异步队列、长历史、耐久和负载下真实浏览器验证，并形成可审计门禁报告。

**Architecture:** 使用性能专用容器镜像和 Compose 项目运行 PostgreSQL、双 API 进程及固定数量 worker；Locust、Playwright、数据编排和采样器在资源边界外运行。每次执行创建随机 `itime_perf_*` 数据库、固定种子数据和临时凭据，通过真实 HTTP/worker 测量请求、队列和资源，最后验证业务不变量并精确清理。

**Tech Stack:** Python 3.13、FastAPI、SQLAlchemy 2、psycopg 3、PostgreSQL 18、Locust 2、Docker Compose、Vue 3、Vite、Playwright Chromium/Edge、pytest、Ruff。

**状态：** 2026-10-05 已实施并完成 M8-A。最终一键基线、全量回归、负载下 Chromium/Edge 和
零残留检查均通过；精确数据见[Web 实施与验证](../验收/iTime-Web实施与验证.md)。本计划不包含
M8-B 备份恢复或 M8-C 生产发布。

---

## 文件结构

新增文件：

- `server/Dockerfile.performance`：只用于性能基线的 Linux 服务镜像。
- `server/.dockerignore`：排除本地虚拟环境、缓存、测试产物和凭据。
- `compose.performance.yaml`：固定 PostgreSQL、API 和 worker 资源/进程拓扑。
- `server/performance/__init__.py`：性能包边界。
- `server/performance/contracts.py`：运行配置、数据清单、指标和门禁类型。
- `server/performance/safety.py`：回环地址、数据库名、环境、运行锁和清理清单校验。
- `server/performance/seed.py`：数据播种 CLI 和事务入口。
- `server/performance/seed_core.py`：100 账号标准数据。
- `server/performance/seed_history.py`：5 个重历史账号数据。
- `server/performance/seed_verify.py`：播种后及负载后的数量与业务不变量核对。
- `server/performance/locustfile.py`：普通、重历史、同步和 AI 用户行为。
- `server/performance/upstream.py`：受控 OpenAI 兼容、SMTP、ICS 和公开网页行为。
- `server/performance/synthetic_workers.py`：为受限抓取 worker 注入可控 fetcher 的性能入口。
- `server/performance/sampler.py`：容器、进程、PostgreSQL、锁、SQL 和队列采样。
- `server/performance/report.py`：读取原始统计、执行门禁并生成报告。
- `server/performance/run.py`：创建、预热、执行、停止、报告和清理总编排。
- `server/tests/test_performance_runtime.py`：镜像、Compose 和资源/连接预算合同。
- `server/tests/test_performance_safety.py`：生产/外部环境拒绝及精确清理边界。
- `server/tests/test_performance_seed.py`：数据确定性、数量、关系和重历史边界。
- `server/tests/test_performance_report.py`：分位数、阈值、预期错误和敏感字段拒绝。
- `server/tests/test_performance_sampler.py`：Docker/PostgreSQL/队列采样解析。
- `server/tests/test_performance_smoke.py`：真实容器、迁移、2 用户负载、报告和零残留。
- `server/tests/test_performance_workloads.py`：Locust 场景、认证和请求分类。
- `server/tests/test_performance_async.py`：协议桩、异步作业和可注入 worker。
- `web/playwright.performance.config.ts`：连接已运行性能栈，不自行创建第二套服务。
- `web/e2e/performance-under-load.spec.ts`：背景负载下的用户端流程。

修改文件：

- `server/pyproject.toml`、`server/uv.lock`：锁定 Locust 开发依赖。
- `web/package.json`：增加性能 UI 验证命令。
- `.gitignore`：忽略仓库内误生成的性能原始产物。
- `README.md`、`cloud.md`、`doc/README.md`、`doc/测试/iTime-测试方案与测试用例.md`、
  `doc/验收/iTime-产品验收清单.md`、`doc/验收/iTime-Web实施与验证.md`：记录实际命令、环境、
  结果和仍未完成边界。

不修改生产业务路由以方便压测，不把测试专用播种、时间推进或协议桩挂到 `itime.main:create_app`。

### Task 1: 锁定负载依赖和性能运行镜像

**Files:**
- Modify: `server/pyproject.toml`
- Modify: `server/uv.lock`
- Create: `server/Dockerfile.performance`
- Create: `server/.dockerignore`
- Create: `server/tests/test_performance_runtime.py`
- Modify: `.gitignore`

- [x] **Step 1: 写镜像与依赖合同失败测试**

在 `server/tests/test_performance_runtime.py` 写入：

```python
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"


def test_performance_runtime_is_pinned_and_excludes_local_state():
    project = tomllib.loads((SERVER / "pyproject.toml").read_text())
    assert any(
        value.startswith("locust>=") and value.endswith(",<3")
        for value in project["dependency-groups"]["dev"]
    )

    dockerfile = (SERVER / "Dockerfile.performance").read_text()
    assert (
        "python:3.13.9-slim-bookworm@"
        "sha256:b685a4fa58bb19d1814d78a1ec0f0208f351452724f78b20212c984d6e124a34"
    ) in dockerfile
    assert "uv==0.11.29" in dockerfile
    assert "USER itime" in dockerfile

    ignored = set((SERVER / ".dockerignore").read_text().splitlines())
    assert {".venv", ".pytest_cache", "__pycache__", "dist", "*.log"} <= ignored
```

- [x] **Step 2: 运行测试确认失败**

Run:

```bash
cd server
uv run pytest -q tests/test_performance_runtime.py
```

Expected: FAIL，Locust、镜像和 `.dockerignore` 尚不存在。

- [x] **Step 3: 添加 Locust 并生成锁文件**

Run:

```bash
cd server
uv add --dev 'locust>=2.41,<3'
uv lock --check
```

Expected: `pyproject.toml` 的 dev 组出现 Locust，`uv.lock` 更新且检查通过。

- [x] **Step 4: 创建最小非 root 性能镜像**

`server/Dockerfile.performance`：

```dockerfile
FROM python:3.13.9-slim-bookworm@sha256:b685a4fa58bb19d1814d78a1ec0f0208f351452724f78b20212c984d6e124a34

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PATH=/app/.venv/bin:$PATH

WORKDIR /app
RUN pip install --no-cache-dir uv==0.11.29 \
    && groupadd --system itime \
    && useradd --system --gid itime --home-dir /app itime

COPY pyproject.toml uv.lock alembic.ini ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY performance ./performance
RUN uv sync --frozen --no-dev

USER itime
CMD ["uvicorn", "itime.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
```

`server/.dockerignore`：

```text
.venv
.pytest_cache
.ruff_cache
__pycache__
dist
*.egg-info
*.log
tests
```

先创建空的 `server/performance/__init__.py`，保证镜像 COPY 路径稳定。
在根 `.gitignore` 增加 `performance-results/`，防止手工覆盖输出目录时提交大体积原始数据；
标准入口仍固定写入 `/tmp/itime-performance/`。

- [x] **Step 5: 运行静态合同、构建并验证运行身份**

Run:

```bash
cd server
uv run pytest -q tests/test_performance_runtime.py
colima start --profile itime-perf --cpu 2 --memory 4 --disk 20
docker --context colima-itime-perf build -f Dockerfile.performance -t itime-performance:local .
docker --context colima-itime-perf run --rm itime-performance:local id -u
```

Expected: pytest PASS；镜像构建成功；容器 UID 非 `0`。

- [x] **Step 6: 检查并提交**

```bash
git diff --check
git add server/pyproject.toml server/uv.lock server/Dockerfile.performance \
  server/.dockerignore server/performance/__init__.py \
  server/tests/test_performance_runtime.py .gitignore
git commit -m "build: 添加性能测试运行镜像"
```

### Task 2: 建立运行合同与危险环境拒绝

**Files:**
- Create: `server/performance/contracts.py`
- Create: `server/performance/safety.py`
- Create: `server/tests/test_performance_safety.py`

- [x] **Step 1: 写配置与安全拒绝测试**

`server/tests/test_performance_safety.py` 覆盖：

```python
from pathlib import Path

import pytest

from performance.contracts import RunConfig
from performance.safety import SafetyError, require_safe_run


def config(**changes) -> RunConfig:
    values = {
        "run_id": "abc12345",
        "environment": "test",
        "http_origin": "http://127.0.0.1:18000",
        "database_name": "itime_perf_abc12345",
        "database_host": "127.0.0.1",
        "guard_value": "itime-performance:abc12345",
        "docker_context": "colima-itime-perf",
        "output_dir": Path("/tmp/itime-performance/abc12345"),
    }
    values.update(changes)
    return RunConfig(**values)


def test_safe_run_requires_loopback_test_database_and_matching_ids():
    require_safe_run(config())
    with pytest.raises(SafetyError):
        require_safe_run(config(environment="production"))
    with pytest.raises(SafetyError):
        require_safe_run(config(http_origin="https://itime.example"))
    with pytest.raises(SafetyError):
        require_safe_run(config(database_name="itime"))
    with pytest.raises(SafetyError):
        require_safe_run(config(database_host="db.example"))
    with pytest.raises(SafetyError):
        require_safe_run(config(guard_value="itime-performance:other"))
    with pytest.raises(SafetyError):
        require_safe_run(config(docker_context="default"))
```

另测：

- `run_id` 只能为 8—32 位小写十六进制；
- 输出目录必须位于 `/tmp/itime-performance/<run_id>`；
- 已存在运行锁时拒绝第二次执行；
- 清理清单中的 Compose 项目、数据库和目录必须全部包含精确 run ID。

- [x] **Step 2: 运行测试确认失败**

Run:

```bash
cd server
uv run pytest -q tests/test_performance_safety.py
```

Expected: FAIL，`performance.contracts` 尚不存在。

- [x] **Step 3: 实现不可变运行合同**

`server/performance/contracts.py` 定义：

```python
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class Scenario(StrEnum):
    SMOKE = "smoke"
    STEADY = "steady"
    PEAK = "peak"
    HISTORY = "history"
    AI_ISOLATION = "ai-isolation"
    QUEUES = "queues"
    MAINTENANCE = "maintenance"
    SOAK = "soak"
    SATURATION = "saturation"
    UI_LOAD = "ui-load"


@dataclass(frozen=True)
class RunConfig:
    run_id: str
    environment: str
    http_origin: str
    database_name: str
    database_host: str
    guard_value: str
    docker_context: str
    output_dir: Path

    @property
    def compose_project(self) -> str:
        return f"itime-perf-{self.run_id}"


@dataclass(frozen=True)
class ScenarioConfig:
    name: Scenario
    users: int
    spawn_rate: float
    duration_seconds: int
    dataset: str


SCENARIOS = {
    Scenario.SMOKE: ScenarioConfig(Scenario.SMOKE, 2, 2, 30, "standard"),
    Scenario.STEADY: ScenarioConfig(Scenario.STEADY, 20, 4, 900, "standard"),
    Scenario.PEAK: ScenarioConfig(Scenario.PEAK, 50, 10, 300, "standard"),
    Scenario.HISTORY: ScenarioConfig(Scenario.HISTORY, 10, 2, 600, "history"),
    Scenario.SOAK: ScenarioConfig(Scenario.SOAK, 20, 4, 3600, "standard"),
}
```

- [x] **Step 4: 实现纯函数安全校验和运行锁**

`server/performance/safety.py`：

```python
import os
import re
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from performance.contracts import RunConfig

RUN_ID = re.compile(r"^[0-9a-f]{8,32}$")


class SafetyError(RuntimeError):
    """性能测试运行边界校验失败。"""


def require_safe_run(config: RunConfig) -> None:
    parsed = urlsplit(config.http_origin)
    if config.environment != "test":
        raise SafetyError("性能测试只允许 ITIME_ENVIRONMENT=test")
    if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise SafetyError("性能 HTTP origin 必须为回环地址")
    if config.database_host not in {"localhost", "127.0.0.1", "::1"}:
        raise SafetyError("性能数据库只允许回环地址")
    if not RUN_ID.fullmatch(config.run_id):
        raise SafetyError("运行 ID 必须为 8—32 位小写十六进制")
    if config.database_name != f"itime_perf_{config.run_id}":
        raise SafetyError("数据库名与运行 ID 不匹配")
    if config.guard_value != f"itime-performance:{config.run_id}":
        raise SafetyError("数据库安全标记与运行 ID 不匹配")
    if config.docker_context != "colima-itime-perf":
        raise SafetyError("性能测试只允许专用 colima-itime-perf context")
    expected = Path("/tmp/itime-performance") / config.run_id
    if config.output_dir.resolve() != expected:
        raise SafetyError("输出目录不在本次运行边界内")


@contextmanager
def exclusive_run_lock(path: Path):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, f"{os.getpid()}\n".encode())
        yield
    finally:
        os.close(descriptor)
        path.unlink(missing_ok=True)
```

测试断言锁文件权限为 `0600`，第二次进入抛 `FileExistsError`，退出后锁文件不存在。

- [x] **Step 5: 运行测试和静态检查**

Run:

```bash
cd server
uv run pytest -q tests/test_performance_safety.py
uv run ruff check performance tests/test_performance_safety.py
```

Expected: PASS。

- [x] **Step 6: 提交**

```bash
git add server/performance/contracts.py server/performance/safety.py \
  server/tests/test_performance_safety.py
git commit -m "test: 隔离性能测试运行边界"
```

### Task 3: 创建固定资源 Compose 拓扑

**Files:**
- Create: `compose.performance.yaml`
- Modify: `server/tests/test_performance_runtime.py`

- [x] **Step 1: 扩展失败测试**

解析 `docker compose config --format json`，断言：

```python
required = {
    "postgres",
    "migrate",
    "api",
    "ai-worker",
    "mail-worker",
    "subscription-worker",
    "public-link-worker",
    "export-worker",
}
assert required <= set(config["services"])
assert config["services"]["postgres"]["mem_limit"] == 1024 * 1024 * 1024
command = config["services"]["api"]["command"]
assert command[command.index("--workers") + 1] == "2"
assert config["services"]["api"]["environment"]["ITIME_DB_POOL_SIZE"] == "4"
assert config["services"]["ai-worker"]["environment"]["ITIME_DB_POOL_SIZE"] == "1"
```

测试命令通过临时环境提供确定值：

```python
import os

key = Fernet.generate_key().decode()
env = {
    **os.environ,
    "ITIME_PERF_RUN_ID": "abc12345",
    "ITIME_PERF_DATABASE": "itime_perf_abc12345",
    "ITIME_PERF_POSTGRES_PASSWORD": "synthetic-only",
    "ITIME_PERF_ENCRYPTION_KEY": key,
    "ITIME_PERF_AI_ENCRYPTION_KEY": key,
    "ITIME_PERF_CALENDAR_KEY": key,
    "ITIME_PERF_EXPORT_KEY": key,
    "ITIME_PERF_UPSTREAM_SMTP_PORT": "19025",
}
```

- [x] **Step 2: 运行测试确认失败**

Run:

```bash
cd server
uv run pytest -q tests/test_performance_runtime.py
```

Expected: FAIL，性能 Compose 尚不存在。

- [x] **Step 3: 创建 Compose 公共服务模板**

`compose.performance.yaml` 使用以下公共环境：

```yaml
name: itime-perf-${ITIME_PERF_RUN_ID:?required}

x-app: &app
  image: itime-performance:${ITIME_PERF_IMAGE_TAG:-local}
  build:
    context: ./server
    dockerfile: Dockerfile.performance
  environment: &app-env
    ITIME_ENVIRONMENT: test
    ITIME_DATABASE_URL: postgresql+psycopg://itime:${ITIME_PERF_POSTGRES_PASSWORD}@postgres:5432/${ITIME_PERF_DATABASE}
    ITIME_PUBLIC_ORIGIN: http://localhost:${ITIME_PERF_WEB_PORT:-4174}
    ITIME_ALLOWED_HOSTS: '["localhost","127.0.0.1"]'
    ITIME_ENCRYPTION_KEY: ${ITIME_PERF_ENCRYPTION_KEY}
    ITIME_AI_ENCRYPTION_KEY: ${ITIME_PERF_AI_ENCRYPTION_KEY}
    ITIME_CALENDAR_SUBSCRIPTION_ENCRYPTION_KEY: ${ITIME_PERF_CALENDAR_KEY}
    ITIME_EXPORT_ENCRYPTION_KEY: ${ITIME_PERF_EXPORT_KEY}
    ITIME_AUTH_IP_LIMIT: "1000"
    ITIME_AUTH_IDENTITY_LIMIT: "100"
    ITIME_MAIL_COOLDOWN_SECONDS: "1"
    ITIME_DB_MAX_OVERFLOW: "0"
    ITIME_AI_PRIVATE_ENDPOINT_HOSTS: '["host.docker.internal"]'
    ITIME_SMTP_HOST: host.docker.internal
    ITIME_SMTP_PORT: ${ITIME_PERF_UPSTREAM_SMTP_PORT:?required}
  extra_hosts:
    - host.docker.internal:host-gateway
  depends_on:
    postgres:
      condition: service_healthy
  restart: "no"

services:
  postgres:
    image: postgres:18-alpine@sha256:77f585114c32fbca283dc835b0596f4e52b51b4c6662d7810b2f4084f60a1873
    command:
      - postgres
      - -c
      - max_connections=50
      - -c
      - shared_preload_libraries=pg_stat_statements
    environment:
      POSTGRES_USER: itime
      POSTGRES_PASSWORD: ${ITIME_PERF_POSTGRES_PASSWORD:?required}
      POSTGRES_DB: ${ITIME_PERF_DATABASE:?required}
    ports:
      - 127.0.0.1::5432
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U itime -d ${ITIME_PERF_DATABASE}"]
      interval: 1s
      timeout: 1s
      retries: 30
    mem_limit: 1g
    shm_size: 128mb
    volumes:
      - perf_postgres:/var/lib/postgresql

  migrate:
    <<: *app
    command: ["alembic", "upgrade", "head"]
    environment:
      <<: *app-env
      ITIME_DB_POOL_SIZE: "1"

  api:
    <<: *app
    command:
      - uvicorn
      - itime.main:create_app
      - --factory
      - --host
      - 0.0.0.0
      - --port
      - "8000"
      - --workers
      - "2"
      - --no-access-log
    environment:
      <<: *app-env
      ITIME_DB_POOL_SIZE: "4"
      ITIME_DB_MAX_OVERFLOW: "1"
    ports:
      - 127.0.0.1::8000
    mem_limit: 1280m

  ai-worker:
    <<: *app
    command: ["python", "-m", "itime.ai.worker"]
    environment:
      <<: *app-env
      ITIME_DB_POOL_SIZE: "1"
      ITIME_AI_PRIVATE_ENDPOINT_HOSTS: '["host.docker.internal"]'
    mem_limit: 384m

  mail-worker:
    <<: *app
    command: ["python", "-m", "itime.mail_worker"]
    environment:
      <<: *app-env
      ITIME_DB_POOL_SIZE: "1"
    mem_limit: 160m

  subscription-worker:
    <<: *app
    command: ["python", "-m", "performance.synthetic_workers", "subscription"]
    environment:
      <<: *app-env
      ITIME_DB_POOL_SIZE: "1"
    mem_limit: 160m

  public-link-worker:
    <<: *app
    command: ["python", "-m", "performance.synthetic_workers", "public-link"]
    environment:
      <<: *app-env
      ITIME_DB_POOL_SIZE: "1"
    mem_limit: 160m

  export-worker:
    <<: *app
    command: ["python", "-m", "itime.data_export.worker"]
    environment:
      <<: *app-env
      ITIME_DB_POOL_SIZE: "1"
    mem_limit: 320m

volumes:
  perf_postgres:
```

`migrate` 必须先成功完成，API/worker 才能启动。编排器使用
`docker compose up --wait postgres`、`docker compose run --rm migrate`、再
`docker compose up --wait api ai-worker mail-worker subscription-worker public-link-worker
export-worker`，不依赖 Compose 对一次性迁移完成的隐式判断。

- [x] **Step 4: 验证配置与总内存预算**

Run:

```bash
cd server
uv run pytest -q tests/test_performance_runtime.py
docker --context colima-itime-perf compose -f ../compose.performance.yaml \
  --env-file ../.env.local config --quiet
```

Expected: PASS；Compose 配置可解析。测试另断言 API、双 AI worker 及四个串行 worker 的内存
上限总和不超过 4 GiB。

- [x] **Step 5: 提交**

```bash
git add compose.performance.yaml server/tests/test_performance_runtime.py
git commit -m "build: 固定性能基线服务拓扑"
```

### Task 4: 实现标准数据生成与清单校验

**Files:**
- Create: `server/performance/seed.py`
- Create: `server/performance/seed_core.py`
- Create: `server/performance/seed_verify.py`
- Create: `server/tests/test_performance_seed.py`

- [x] **Step 1: 写确定性标准数据失败测试**

测试在随机迁移数据库中执行两次独立播种：

```python
profile = SeedProfile.standard(
    accounts=100,
    active_accounts=50,
    tasks_per_active=300,
    sync_events_per_active=3000,
)
first = asyncio.run(seed_dataset(
    settings,
    profile,
    seed=20261005,
    at=BASE_TIME,
    credentials_path=tmp_path / "standard-accounts.json",
))
assert first.accounts == 100
assert first.active_accounts == 50
assert first.counts["tasks"] == 15_000
assert first.counts["change_events"] >= 150_000
assert len(first.credentials) == 50
assert all(item.email.endswith("@performance.invalid") for item in first.credentials)
assert asyncio.run(verify_dataset(settings, first)).ok
```

第二个随机数据库使用相同参数，断言除运行 ID、UUID 和数据库时间外的分类计数完全一致。另测
事务中途失败不会留下半个账号，凭据文件权限为 `0600`，清单不含密码哈希、Cookie、Key 或正文。

- [x] **Step 2: 运行测试确认失败**

Run:

```bash
cd server
uv run pytest -q tests/test_performance_seed.py -k standard
```

Expected: FAIL，播种模块尚不存在。

- [x] **Step 3: 定义数据配置和公开清单**

在 `server/performance/contracts.py` 增加：

```python
@dataclass(frozen=True)
class SeedProfile:
    name: str
    accounts: int
    active_accounts: int
    tasks_per_active: int
    sync_events_per_active: int
    history_accounts: int = 0
    calendar_series_per_account: int = 0
    subscription_events_per_account: int = 0
    conversation_messages_per_account: int = 0

    def __post_init__(self) -> None:
        values = (
            self.accounts,
            self.active_accounts,
            self.tasks_per_active,
            self.sync_events_per_active,
            self.history_accounts,
            self.calendar_series_per_account,
            self.subscription_events_per_account,
            self.conversation_messages_per_account,
        )
        if any(value < 0 for value in values):
            raise ValueError("性能数据数量不能为负数")
        if self.accounts < 1 or self.active_accounts > self.accounts:
            raise ValueError("活动账号数量必须位于总账号范围内")
        if self.tasks_per_active > 1000:
            raise ValueError("单账号任务日程不能超过当前 1000 条边界")
        if self.subscription_events_per_account > 5000:
            raise ValueError("单账号订阅事件不能超过当前 5000 条边界")

    @classmethod
    def standard(
        cls,
        *,
        accounts: int = 100,
        active_accounts: int = 50,
        tasks_per_active: int = 300,
        sync_events_per_active: int = 3000,
    ) -> "SeedProfile":
        return cls(
            "standard",
            accounts,
            active_accounts,
            tasks_per_active,
            sync_events_per_active,
        )

    @classmethod
    def history(
        cls,
        *,
        accounts: int = 5,
        tasks_per_account: int = 1000,
        calendar_series_per_account: int = 1000,
        subscription_events_per_account: int = 5000,
        conversation_messages_per_account: int = 5000,
        sync_events_per_account: int = 10000,
    ) -> "SeedProfile":
        return cls(
            "history",
            accounts,
            accounts,
            tasks_per_account,
            sync_events_per_account,
            history_accounts=accounts,
            calendar_series_per_account=calendar_series_per_account,
            subscription_events_per_account=subscription_events_per_account,
            conversation_messages_per_account=conversation_messages_per_account,
        )


@dataclass(frozen=True)
class AccountCredential:
    email: str
    password: str
    dataset: str


@dataclass(frozen=True)
class SeedManifest:
    profile: str
    seed: int
    generated_at: str
    accounts: int
    active_accounts: int
    counts: dict[str, int]
    credentials: tuple[AccountCredential, ...]
```

最终实现让 `SeedProfile.standard()` 接受关键字覆盖，便于小型测试构造，同时拒绝负数、活动账号
超过总账号和超过领域硬上限的数据。

- [x] **Step 4: 实现账号和标准业务数据**

`seed.py` 只负责事务和文件：

```python
async def seed_dataset(
    settings: Settings,
    profile: SeedProfile,
    *,
    seed: int,
    at: datetime,
    credentials_path: Path,
) -> SeedManifest:
    database = Database(settings)
    try:
        if profile.name == "standard":
            manifest = await seed_standard(
                database,
                settings,
                profile,
                seed=seed,
                at=at,
            )
        elif profile.name == "history":
            manifest = await seed_history_accounts(
                database,
                settings,
                profile,
                seed=seed,
                at=at,
            )
        else:
            raise ValueError(f"不支持的数据档位：{profile.name}")
        await verify_dataset(settings, manifest, database=database)
        write_private_credentials(credentials_path, manifest.credentials)
        return manifest
    finally:
        await database.close()
```

`seed_core.py`：

- 只计算一次固定测试密码的 Argon2 哈希；
- 使用 UUID5 和固定时间偏移生成稳定关系；
- 每个用户建立 `User`、`UserTimeSettings`、`SyncState`、隐私设置和已发布额度日；
- 创建指向受控上游的五类已发布 AI 配置、价格和额度策略；
- 使用分批 INSERT 创建项目、标签、任务、步骤、任务日程、日历、计划、专注、困难、复盘、
  记忆、对话和变更事件；
- 每批不超过 1000 行，每个账号在单一事务中完成；
- 生命周期比例固定为活动 80%、归档 10%、回收 8%、已清理墓碑 2%；
- 不创建真实外部 URL、媒体字节、邮件或未结算金额。

凭据文件使用：

```python
descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(descriptor, "w", encoding="utf-8") as output:
    json.dump(
        [{"email": item.email, "password": item.password, "dataset": item.dataset}
         for item in credentials],
        output,
    )
```

- [x] **Step 5: 实现播种后不变量核对**

`seed_verify.py` 定义：

```python
@dataclass(frozen=True)
class VerificationResult:
    ok: bool
    counts: dict[str, int]
    violations: tuple[str, ...]


async def verify_dataset(
    settings: Settings,
    manifest: SeedManifest,
    *,
    database: Database | None = None,
) -> VerificationResult:
    owned_database = database is None
    active_database = database or Database(settings)
    try:
        return await verify_counts_and_invariants(active_database, manifest)
    finally:
        if owned_database:
            await active_database.close()
```

传入现有连接时不重复建池；未传入时自行创建并在结束时关闭。至少检查：

```python
CHECKS = {
    "orphan_tasks": """
        SELECT count(*) FROM tasks t
        LEFT JOIN users u ON u.id=t.owner_id
        WHERE u.id IS NULL
    """,
    "duplicate_active_focus": """
        SELECT count(*) FROM (
          SELECT owner_id FROM focus_sessions
          WHERE status IN ('running','paused')
          GROUP BY owner_id HAVING count(*) > 1
        ) value
    """,
    "orphan_change_events": """
        SELECT count(*) FROM change_events e
        LEFT JOIN users u ON u.id=e.owner_id
        WHERE u.id IS NULL
    """,
    "active_quota_reservation_without_job": """
        SELECT count(*) FROM ai_quota_reservations r
        LEFT JOIN ai_jobs j ON j.id=r.job_id
        WHERE r.status='reserved' AND j.id IS NULL
    """,
}
```

每项必须为 0；另核对清单中的对象计数、账号隔离和生命周期分类计数。

- [x] **Step 6: 运行专项测试并提交**

Run:

```bash
cd server
uv run pytest -q tests/test_performance_seed.py -k standard
uv run ruff check performance tests/test_performance_seed.py
```

Expected: PASS。

```bash
git add server/performance/contracts.py server/performance/seed.py \
  server/performance/seed_core.py server/performance/seed_verify.py \
  server/tests/test_performance_seed.py
git commit -m "test: 生成标准性能数据集"
```

### Task 5: 扩展重历史数据并验证查询边界

**Files:**
- Create: `server/performance/seed_history.py`
- Modify: `server/performance/seed.py`
- Modify: `server/performance/seed_verify.py`
- Modify: `server/tests/test_performance_seed.py`

- [x] **Step 1: 写重历史边界失败测试**

使用缩小参数验证关系，再用真实参数标记为 integration：

```python
profile = SeedProfile.history(
    accounts=1,
    tasks_per_account=100,
    calendar_series_per_account=100,
    subscription_events_per_account=200,
    conversation_messages_per_account=200,
    sync_events_per_account=500,
)
manifest = asyncio.run(seed_dataset(
    settings,
    profile,
    seed=20261005,
    at=BASE_TIME,
    credentials_path=tmp_path / "accounts.json",
))
assert manifest.counts["tasks"] == 100
assert manifest.counts["calendar_subscription_events"] == 200
assert manifest.counts["conversation_messages"] == 200
assert manifest.counts["change_events"] >= 500
assert asyncio.run(verify_dataset(settings, manifest)).ok
```

真实档位断言 5 个账号分别达到 1000 任务/日程、5000 订阅事件、5000 对话消息和 10000 同步
事件。该用例使用以下门禁，默认全量 pytest 会明确 skip，只有显式设置环境变量时执行：

```python
@pytest.mark.performance
@pytest.mark.skipif(
    os.environ.get("ITIME_RUN_PERFORMANCE") != "1",
    reason="设置 ITIME_RUN_PERFORMANCE=1 执行完整性能数据测试",
)
def test_full_history_profile(settings: Settings, tmp_path: Path):
    profile = SeedProfile.history()
    manifest = asyncio.run(seed_dataset(
        settings,
        profile,
        seed=20261005,
        at=BASE_TIME,
        credentials_path=tmp_path / "history-accounts.json",
    ))
    assert manifest.accounts == 5
    assert manifest.counts["tasks"] == 5000
    assert manifest.counts["calendar_series"] == 5000
    assert manifest.counts["calendar_subscription_events"] == 25_000
    assert manifest.counts["conversation_messages"] == 25_000
    assert manifest.counts["change_events"] >= 50_000
    assert asyncio.run(verify_dataset(settings, manifest)).ok
```

该完整数据用例单独执行，不进入日常快速回归。

- [x] **Step 2: 注册性能测试 marker 并确认失败**

在 `server/pyproject.toml` 的 markers 增加：

```toml
"performance: 仅在隔离性能环境显式执行的高数据量测试",
```

Run:

```bash
cd server
uv run pytest -q tests/test_performance_seed.py -k history
```

Expected: FAIL，重历史生成器尚不存在。

- [x] **Step 3: 实现重历史生成**

`seed_history.py` 提供：

```python
from collections import Counter
from dataclasses import dataclass


@dataclass(frozen=True)
class HistoryAccountResult:
    counts: dict[str, int]
    credential: AccountCredential


async def seed_history_accounts(
    database: Database,
    settings: Settings,
    profile: SeedProfile,
    *,
    seed: int,
    at: datetime,
) -> SeedManifest:
    counts: Counter[str] = Counter()
    credentials: list[AccountCredential] = []
    for account_index in range(profile.history_accounts):
        async with database.transaction() as session:
            result = await seed_one_history_account(
                session,
                settings,
                profile,
                account_index=account_index,
                seed=seed,
                at=at,
            )
        counts.update(result.counts)
        credentials.append(result.credential)
    return SeedManifest(
        profile="history",
        seed=seed,
        generated_at=at.isoformat(),
        accounts=profile.accounts,
        active_accounts=profile.active_accounts,
        counts=dict(counts),
        credentials=tuple(credentials),
    )
```

按账号分事务、按 1000 行分批写入；日历和任务遵守当前 `MAX_TASK_SCHEDULES=1000`、
`MAX_SUBSCRIPTION_EVENTS_PER_OWNER=5000`，消息按会话 sequence 连续，变更事件 sequence
由数据库生成。`seed_one_history_account()` 与标准数据生成器共用 UUID5、密码哈希、批量
INSERT 和计数辅助函数，只扩展数量，不复制账号初始化。生成结束执行 `ANALYZE`，不执行
`VACUUM FULL` 或改写生产查询规划参数。

- [x] **Step 4: 用真实 HTTP 读取缩小重历史数据**

测试登录播种账号并请求：

```python
assert client.get("/api/v1/tasks", params={"limit": 100}).status_code == 200
assert client.get(
    "/api/v1/calendar/occurrences",
    params={"start": "2026-01-01", "end": "2026-04-04"},
).status_code == 200
assert client.get(
    "/api/v1/statistics",
    params={"scope": "month", "anchor_date": "2026-10-05"},
).status_code == 200
initial = client.get("/api/v1/sync/changes", params={"limit": 200}).json()
assert initial["reset_required"] is True
```

- [x] **Step 5: 运行测试并提交**

Run:

```bash
cd server
uv run pytest -q tests/test_performance_seed.py
uv run ruff check performance tests/test_performance_seed.py
```

Expected: PASS，默认不执行标记的完整 5 账号大数据用例。

```bash
git add server/pyproject.toml server/performance/seed.py \
  server/performance/seed_history.py server/performance/seed_verify.py \
  server/tests/test_performance_seed.py
git commit -m "test: 生成重历史性能数据集"
```

### Task 6: 实现普通、重历史与同步 Locust 场景

**Files:**
- Create: `server/performance/locustfile.py`
- Create: `server/tests/test_performance_workloads.py`

- [x] **Step 1: 写场景注册、认证和操作名失败测试**

使用 Locust 环境直接实例化用户，断言：

```python
assert set(WORKLOADS) == {"standard", "history", "sync"}
assert request_name("GET", "/api/v1/tasks") == "read::tasks.list"
assert request_name("POST", "/api/v1/tasks") == "write::tasks.create"
assert request_name(
    "GET",
    "/api/v1/statistics",
    category="history",
) == "history::statistics.read"
```

用 `httpx.MockTransport` 或 Locust `HttpSession` 的测试 adapter 验证 `on_start()` 登录后保存
`csrf_token`，所有写请求包含 `X-CSRF-Token` 和新的 UUID operation ID，响应错误会调用
`response.failure()`。

- [x] **Step 2: 运行测试确认失败**

Run:

```bash
cd server
uv run pytest -q tests/test_performance_workloads.py
```

Expected: FAIL，Locust 场景尚不存在。

- [x] **Step 3: 实现账号分配与认证基类**

`locustfile.py` 核心：

```python
from collections import deque
from locust import HttpUser, between, events, task

_credentials = deque(load_credentials())


class AuthenticatedUser(HttpUser):
    abstract = True
    wait_time = between(1, 3)

    def on_start(self):
        if not _credentials:
            raise RuntimeError("性能账号数量不足，拒绝复用账号")
        self.credential = _credentials.popleft()
        self.dataset = load_account_dataset(self.credential["email"])
        with self.client.post(
            "/api/v1/auth/login",
            json={
                "email": self.credential["email"],
                "password": self.credential["password"],
                "device_name": "M8 性能测试",
            },
            name="auth::login",
            catch_response=True,
        ) as response:
            require_status(response, 200)
            self.csrf = response.json()["csrf_token"]
        initial = self.client.get(
            "/api/v1/sync/changes",
            params={"limit": 200},
            name="read::sync.initial",
        )
        self.sync_cursor = initial.json()["next_cursor"]

    def write_headers(self) -> dict[str, str]:
        return {"X-CSRF-Token": self.csrf}
```

凭据按 Locust worker ID 和序号分片；账号不足时启动失败，不能让多个虚拟用户共享一个账号而
制造非目标版本冲突。

- [x] **Step 4: 实现标准混合行为**

`StandardUser` 的权重：

```python
class StandardUser(AuthenticatedUser):
    weight = 8

    @task(5)
    def task_list(self):
        self.client.get(
            "/api/v1/tasks",
            params={"status": "open", "limit": 50},
            name="read::tasks.list",
        )

    @task(4)
    def calendar_week(self):
        self.client.get(
            "/api/v1/calendar/occurrences",
            params=self.dataset.week_range,
            name="read::calendar.week",
        )

    @task(4)
    def sync_changes(self):
        response = self.client.get(
            "/api/v1/sync/changes",
            params={"cursor": self.sync_cursor, "limit": 200},
            name="read::sync.changes",
        )
        if response.ok:
            self.sync_cursor = response.json()["next_cursor"]

    @task(2)
    def statistics(self):
        self.client.get(
            "/api/v1/statistics",
            params={"scope": "week", "anchor_date": self.dataset.anchor_date},
            name="read::statistics.week",
        )

    @task(1)
    def create_task(self):
        self.client.post(
            "/api/v1/tasks",
            headers=self.write_headers(),
            json=self.dataset.task_payload(),
            name="write::tasks.create",
        )
```

补齐项目/任务详情、计划块、当前专注、困难、复盘、额度和同步状态，使分类权重符合设计中的
25/20/20/20/10/5。每项响应校验状态和最小 JSON 结构，不能只看 TCP 成功。

- [x] **Step 5: 实现重历史和同步端到端事件**

`HistoryUser` 使用重历史凭据，固定查询最大合法分页和 93 天日历；`SyncUser` 每轮：

1. 记录 monotonic 起点；
2. 用设备 A 创建任务；
3. 用同账号独立设备 B 的游标每 250 ms 拉取；
4. 读取到目标 change event 后发出 `SYNC_E2E` 自定义 Locust request event；
5. 超过 5 秒调用 failure，不删除已创建任务。

自定义事件：

```python
events.request.fire(
    request_type="SYNC",
    name="sync::cross-device-visible",
    response_time=elapsed_ms,
    response_length=0,
    exception=None,
    context={},
)
```

- [x] **Step 6: 运行场景单测和 Locust 发现检查**

Run:

```bash
cd server
uv run pytest -q tests/test_performance_workloads.py
uv run locust -f performance/locustfile.py --list
uv run ruff check performance tests/test_performance_workloads.py
```

Expected: PASS；列表包含标准、重历史和同步用户类。

- [x] **Step 7: 提交**

```bash
git add server/performance/locustfile.py \
  server/tests/test_performance_workloads.py
git commit -m "test: 建立真实业务负载场景"
```

### Task 7: 实现分位数门禁与脱敏报告

**Files:**
- Create: `server/performance/report.py`
- Create: `server/tests/test_performance_report.py`

- [x] **Step 1: 写阈值、预期错误与敏感数据失败测试**

构造固定统计：

```python
def healthy_resources() -> ResourceSummary:
    return ResourceSummary(
        cpu_mean_ratio=0.60,
        cpu_peak_over_90_seconds=0,
        memory_peak_ratio=0.70,
        memory_growth_ratio=0.02,
        database_connection_peak_ratio=0.50,
        blocked_over_two_seconds=0,
        pool_timeouts=0,
        server_5xx=0,
        sampling_gap_seconds=0,
    )


summary = evaluate_gates(
    scenario="steady",
    requests=[
        RequestStat("read::tasks.list", 1000, 120, 480, 900, 0),
        RequestStat("write::tasks.create", 100, 250, 790, 1400, 0),
    ],
    resources=healthy_resources(),
    invariants={"orphan_tasks": 0},
)
assert summary.passed

failed = evaluate_gates(
    scenario="steady",
    requests=[RequestStat("read::tasks.list", 1000, 120, 501, 900, 0)],
    resources=healthy_resources(),
    invariants={"orphan_tasks": 0},
)
assert failed.failures == ("read::tasks.list p95 501ms > 500ms",)
```

另测：

- P95 恰好等于门限通过，超过 1 ms 失败；
- 服务端 5xx 任意一个即失败；
- 声明场景中的指定 409/429/503 单独计数，错误码不匹配仍失败；
- 50 用户档位失败使总报告失败，饱和档位在 50 以上失败只结束加压；
- 报告对象出现 `password`、`cookie`、`authorization`、`csrf`、`api_key` 或 Fernet/JWT
  形态时抛 `SensitiveArtifactError`。

- [x] **Step 2: 运行测试确认失败**

Run:

```bash
cd server
uv run pytest -q tests/test_performance_report.py
```

Expected: FAIL，报告模块尚不存在。

- [x] **Step 3: 实现类型和门禁**

在 `contracts.py` 定义：

```python
@dataclass(frozen=True)
class RequestStat:
    name: str
    count: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    failures: int


@dataclass(frozen=True)
class ResourceSummary:
    cpu_mean_ratio: float
    cpu_peak_over_90_seconds: int
    memory_peak_ratio: float
    memory_growth_ratio: float
    database_connection_peak_ratio: float
    blocked_over_two_seconds: int
    pool_timeouts: int
    server_5xx: int
    sampling_gap_seconds: int


@dataclass(frozen=True)
class GateResult:
    scenario: str
    passed: bool
    failures: tuple[str, ...]
    warnings: tuple[str, ...]
```

`report.py` 使用显式门限映射：

```python
LIMITS = {
    "steady": {
        "read": {"p95_ms": 500, "p99_ms": 1000},
        "write": {"p95_ms": 800, "p99_ms": 1500},
    },
    "peak": {
        "read": {"p95_ms": 1000, "p99_ms": 2000},
        "write": {"p95_ms": 1000, "p99_ms": 2000},
    },
    "history": {
        "history": {"p95_ms": 1500, "p99_ms": 3000},
    },
}
```

请求名按 `category::operation` 分类；缺失必需分类、请求数为 0、采样中断或 Locust 退出非零均
失败，不能通过空样本。

- [x] **Step 4: 实现 JSON/CSV/Markdown 输出**

公开入口：

```python
def build_report(run_dir: Path) -> GateResult:
    manifest = read_json(run_dir / "manifest.json")
    requests = read_locust_csv(run_dir / "requests_stats.csv")
    resources = read_resource_samples(run_dir / "resources.csv")
    invariants = read_json(run_dir / "invariants.json")
    result = evaluate_gates(
        scenario=manifest["scenario"],
        requests=requests,
        resources=resources,
        invariants=invariants,
    )
    assert_no_sensitive_artifacts(result)
    write_json_atomic(run_dir / "summary.json", asdict(result))
    write_markdown_atomic(run_dir / "report.md", result)
    return result
```

使用临时文件加 `os.replace` 原子写入；Markdown 明确环境、样本量、每项门限、实际值、失败请求
编号、饱和点和不适用范围。

- [x] **Step 5: 运行测试并提交**

Run:

```bash
cd server
uv run pytest -q tests/test_performance_report.py
uv run ruff check performance tests/test_performance_report.py
```

Expected: PASS。

```bash
git add server/performance/contracts.py server/performance/report.py \
  server/tests/test_performance_report.py
git commit -m "test: 实现性能门禁与报告"
```

### Task 8: 采集容器、PostgreSQL 和队列资源

**Files:**
- Create: `server/performance/sampler.py`
- Create: `server/tests/test_performance_sampler.py`

- [x] **Step 1: 写 Docker 与 PostgreSQL 解析失败测试**

固定输入：

```python
DOCKER_SAMPLE = {
    "Name": "itime-perf-abc-api-1",
    "CPUPerc": "37.50%",
    "MemUsage": "512MiB / 1.25GiB",
    "NetIO": "1.2MB / 820kB",
    "BlockIO": "4.1MB / 2.0MB",
    "PIDs": "3",
}

sample = parse_docker_stat(DOCKER_SAMPLE, at=BASE_TIME)
assert sample.cpu_percent == 37.5
assert sample.memory_bytes == 512 * 1024 * 1024
assert sample.memory_limit_bytes == int(1.25 * 1024**3)
assert sample.pids == 3
```

数据库测试创建活动/空闲连接、短锁等待和队列记录，断言 `sample_postgres()` 返回：

```python
assert sample.connections_total >= 3
assert sample.connections_idle_in_transaction == 0
assert sample.blocked_over_two_seconds == 0
assert sample.queues["ai"]["queued"] >= 1
```

- [x] **Step 2: 运行测试确认失败**

Run:

```bash
cd server
uv run pytest -q tests/test_performance_sampler.py
```

Expected: FAIL，采样模块尚不存在。

- [x] **Step 3: 实现 Docker/进程采样**

每秒运行一次：

```bash
docker --context colima-itime-perf stats --no-stream --format '{{json .}}'
```

`sampler.py` 解析 IEC/SI 单位，按容器保存 CPU、内存、网络、块 I/O 和 PID。另通过
`docker inspect` 在开始时记录镜像 ID、CPU/内存上限和健康状态，不把 `docker stats` 的宿主
百分比误当总资源百分比。

- [x] **Step 4: 实现 PostgreSQL 与队列采样**

使用一个短连接和单次只读事务执行：

```sql
SELECT state, wait_event_type, wait_event, count(*)
FROM pg_stat_activity
WHERE datname = current_database()
GROUP BY state, wait_event_type, wait_event;

SELECT count(*)
FROM pg_stat_activity
WHERE datname = current_database()
  AND wait_event_type = 'Lock'
  AND now() - query_start > interval '2 seconds';

SELECT queryid, calls, total_exec_time, mean_exec_time, rows,
       shared_blks_hit, shared_blks_read, temp_blks_written
FROM pg_stat_statements
WHERE dbid = (SELECT oid FROM pg_database WHERE datname=current_database())
ORDER BY total_exec_time DESC
LIMIT 20;
```

队列使用显式白名单 SQL，分别统计 `ai_jobs`、`calendar_subscriptions`、`task_links`、
`data_export_jobs`、`mail_outbox` 和 `account_deletions` 的 queued/running、最旧时间及终态速率。
表不存在视为运行配置错误，不静默忽略。

- [x] **Step 5: 实现有界采样循环**

```python
@dataclass(frozen=True)
class SamplerConfig:
    docker_context: str
    compose_project: str
    database_url: str
    output_dir: Path


async def sample_until_stopped(
    config: SamplerConfig,
    stop: asyncio.Event,
) -> None:
    next_tick = time.monotonic()
    while not stop.is_set():
        await write_sample(config)
        next_tick += 1.0
        try:
            await asyncio.wait_for(stop.wait(), timeout=max(0, next_tick - time.monotonic()))
        except TimeoutError:
            continue
```

单次采样超过 1 秒记缺口但不并发堆叠；连续 5 秒缺口使报告无效。CSV 每行 flush，进程中断仍
保留已完成样本。

- [x] **Step 6: 运行测试并提交**

Run:

```bash
cd server
uv run pytest -q tests/test_performance_sampler.py
uv run ruff check performance tests/test_performance_sampler.py
```

Expected: PASS。

```bash
git add server/performance/sampler.py server/tests/test_performance_sampler.py
git commit -m "test: 采集性能资源与队列指标"
```

### Task 9: 实现可控上游和异步队列场景

**Files:**
- Create: `server/performance/upstream.py`
- Create: `server/performance/synthetic_workers.py`
- Modify: `server/performance/locustfile.py`
- Create: `server/tests/test_performance_async.py`

- [x] **Step 1: 写协议桩与可注入 worker 失败测试**

使用真实本地 HTTP 服务验证：

```python
response = httpx.post(
    f"{slow_stream_origin}/v1/chat/completions",
    json={"stream": True, "messages": [{"role": "user", "content": "容量测试"}]},
    headers={"Authorization": "Bearer synthetic"},
)
assert response.status_code == 200
assert "data:" in response.text
assert "[DONE]" in response.text
```

另测 `rate-limit` 返回 429、`unavailable` 返回 503、`disconnect` 在首段后断开；响应和日志不
回显 Authorization。对 `synthetic_workers` 使用随机数据库，断言订阅和公开链接各处理一条，
租约、终态及上一成功快照语义与正式 worker 相同。

- [x] **Step 2: 运行测试确认失败**

Run:

```bash
cd server
uv run pytest -q tests/test_performance_async.py
```

Expected: FAIL，可控上游和性能 worker 尚不存在。

- [x] **Step 3: 实现受控 OpenAI 兼容上游**

`upstream.py` 使用 FastAPI，模式和延迟只从启动参数读取，不接受请求正文或查询参数动态改变：

```python
@app.post("/v1/chat/completions")
async def chat(request: Request):
    if settings.mode == "rate-limit":
        return JSONResponse({"error": {"message": "synthetic"}}, status_code=429)
    if settings.mode == "unavailable":
        return JSONResponse({"error": {"message": "synthetic"}}, status_code=503)

    async def stream():
        await asyncio.sleep(settings.initial_delay_seconds)
        for part in ("性能", "测试", "完成"):
            yield sse_delta(part)
            await asyncio.sleep(settings.chunk_delay_seconds)
        yield "data: [DONE]\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")
```

同时提供转写 multipart、固定 ICS、固定公开 HTML 和只接收不外发的 SMTP server。所有正文由
固定合成字符串生成，日志只记模式、状态、字节数和耗时。

- [x] **Step 4: 实现受限抓取的可注入性能 worker**

`synthetic_workers.py` 不复制领域写回逻辑：

```python
async def run_subscription(
    database: Database,
    settings: Settings,
    *,
    delay_seconds: float,
) -> bool:
    return await subscription_worker.process_one(
        database,
        settings,
        fetcher=SyntheticCalendarFetcher(delay_seconds=delay_seconds),
    )


async def run_public_link(
    database: Database,
    settings: Settings,
    *,
    delay_seconds: float,
) -> bool:
    return await public_link_worker.process_one(
        database,
        settings,
        fetcher=SyntheticPublicLinkFetcher(delay_seconds=delay_seconds),
    )
```

fetcher 返回与正式 parser/fetcher 相同的结果类型；仅替换网络边界，领取、租约、解析、写回、
版本和失败恢复继续走生产服务函数。CLI 只接受 `subscription` 或 `public-link`，环境不是
`test` 时拒绝启动。

- [x] **Step 5: 实现 AI Locust 场景和端到端事件**

`AIUser` 创建会话、提交消息，并按 250 ms 读取作业或 SSE：

```python
events.request.fire(
    request_type="AI",
    name="ai::accepted-to-claimed",
    response_time=claimed_ms,
    response_length=0,
    exception=None,
    context={},
)
events.request.fire(
    request_type="AI",
    name="ai::end-to-end",
    response_time=completed_ms,
    response_length=len(content),
    exception=None,
    context={},
)
```

20 个账号各提交一条，不因轮询失败重复接单。完成后读取额度和作业，保存 job ID 供
`seed_verify.py` 核对唯一终态、成本和预留。

在 `WORKLOADS` 注册 `"ai": AIUser`，并增加测试断言
`set(WORKLOADS) == {"standard", "history", "sync", "ai"}`。

- [x] **Step 6: 运行测试并提交**

Run:

```bash
cd server
uv run pytest -q tests/test_performance_async.py \
  tests/test_performance_workloads.py
uv run ruff check performance tests/test_performance_async.py
```

Expected: PASS。

```bash
git add server/performance/upstream.py \
  server/performance/synthetic_workers.py \
  server/performance/locustfile.py \
  server/tests/test_performance_async.py
git commit -m "test: 覆盖异步队列性能与隔离"
```

### Task 10: 实现完整编排和真实烟测

**Files:**
- Create: `server/performance/run.py`
- Create: `server/tests/test_performance_smoke.py`
- Modify: `server/performance/report.py`
- Modify: `server/performance/seed_verify.py`

- [x] **Step 1: 写命令合同与生命周期失败测试**

CLI 应支持：

```text
python -m performance.run smoke
python -m performance.run scenario steady
python -m performance.run scenario peak
python -m performance.run scenario history
python -m performance.run scenario ai-isolation
python -m performance.run scenario queues
python -m performance.run scenario maintenance
python -m performance.run scenario soak
python -m performance.run baseline
python -m performance.run verify-clean
```

`test_performance_smoke.py` 使用：

```python
pytestmark = pytest.mark.integration


@pytest.mark.performance
@pytest.mark.skipif(
    os.environ.get("ITIME_RUN_PERFORMANCE") != "1",
    reason="设置 ITIME_RUN_PERFORMANCE=1 执行容器性能烟测",
)
def test_real_performance_smoke():
    result = run_performance_scenario("smoke")
    assert result.passed
    assert result.remaining_resources == ()
```

测试 monkeypatch 子进程执行器，断言顺序严格为：

```python
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
```

分别在 migrate、seed、Locust、report 和 SIGINT 位置抛错，均断言执行 cleanup 与
verify-clean，原始异常保留在部分报告中。

- [x] **Step 2: 运行测试确认失败**

Run:

```bash
cd server
uv run pytest -q tests/test_performance_smoke.py -k lifecycle
```

Expected: FAIL，编排器尚不存在。

- [x] **Step 3: 实现子进程和清理边界**

`run.py` 使用 `ExitStack` 和进程组：

```python
def checked(
    command: list[str],
    *,
    env: dict[str, str],
    timeout: int,
    cwd: Path = ROOT,
) -> CompletedProcess:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=True,
    )


@dataclass
class RunState:
    config: RunConfig
    env: dict[str, str]
    registered_paths: list[Path]
    processes: list[subprocess.Popen[str]]

    def compose(self, *args: str) -> list[str]:
        return [
            "docker", "--context", self.config.docker_context,
            "compose", "-f", str(ROOT / "compose.performance.yaml"),
            "--project-name", self.config.compose_project,
            *args,
        ]


def cleanup(state: RunState) -> None:
    checked(state.compose("down", "--volumes", "--remove-orphans"),
            env=state.env, timeout=60)
    remove_registered_temp_files(state)
    verify_no_registered_resources(state)
```

启动长进程时使用 `start_new_session=True`，停止时先向进程组发 SIGTERM，10 秒后仍存活才
SIGKILL。清理只读取本次 `manifest.json` 中登记的资源；没有清单时仅允许清理精确 compose
project，不扫描其他 `itime_*` 资源。

- [x] **Step 4: 实现运行阶段**

关键命令：

```python
compose = [
    "docker", "--context", config.docker_context,
    "compose", "-f", str(ROOT / "compose.performance.yaml"),
    "--project-name", config.compose_project,
]
checked([*compose, "up", "-d", "--wait", "postgres"], env=env, timeout=120)
checked([*compose, "run", "--rm", "migrate"], env=env, timeout=120)
checked(
    [*compose, "up", "-d", "--wait", "--scale", "ai-worker=2",
     "api", "ai-worker", "mail-worker", "subscription-worker",
     "public-link-worker", "export-worker"],
    env=env,
    timeout=180,
)
```

通过 `docker compose port` 获取随机 PostgreSQL/API 端口；创建扩展和数据库 COMMENT 安全标记
后才播种。Locust 命令：

```python
[
    sys.executable, "-m", "locust",
    "-f", "performance/locustfile.py",
    "--headless",
    "--host", api_origin,
    "--users", str(scenario.users),
    "--spawn-rate", str(scenario.spawn_rate),
    "--run-time", f"{scenario.duration_seconds}s",
    "--csv", str(run_dir / "requests"),
    "--only-summary",
]
```

预热单独执行且不复用统计文件。每个正式场景前调用
`SELECT pg_stat_statements_reset()`，后调用 `verify_dataset(settings, manifest)`。

- [x] **Step 5: 实现饱和、队列与维护调度**

- 饱和按 10、20、30……100 用户分别运行 3 分钟；
- 连续三个 60 秒窗口违反延迟或错误门禁即停止；
- 50 用户以下失败使 baseline 失败，50 以上失败记录为饱和边界；
- 队列场景按类型分别播种，避免无法归因的混合结果；
- 维护场景先记录正常 API 基线，再执行一次清理批次并测退化；
- 场景结束最多等待 180 秒排空，超时记录最旧队列年龄和剩余数。

- [x] **Step 6: 执行真实 2 用户烟测**

Run:

```bash
cd server
ITIME_RUN_PERFORMANCE=1 uv run pytest -q \
  tests/test_performance_smoke.py -m performance
uv run python -m performance.run smoke
uv run python -m performance.run verify-clean
```

Expected:

- 迁移、播种、API、worker、Locust、采样、报告全部真实运行；
- `summary.json` 为 `passed`；
- 运行后容器、卷、网络、子进程和 `itime_perf_*` 数据库残留均为 0。

- [x] **Step 7: 提交**

```bash
git add server/performance/run.py server/performance/report.py \
  server/performance/seed_verify.py server/tests/test_performance_smoke.py
git commit -m "test: 编排隔离性能基线"
```

### Task 11: 增加负载下真实浏览器验证

**Files:**
- Create: `web/playwright.performance.config.ts`
- Create: `web/e2e/performance-under-load.spec.ts`
- Modify: `web/package.json`
- Modify: `server/performance/run.py`
- Modify: `server/performance/contracts.py`

- [x] **Step 1: 写连接外部性能栈的 Playwright 配置**

`playwright.performance.config.ts`：

```typescript
import base from './playwright.config'
import { defineConfig } from '@playwright/test'

const baseURL = process.env.ITIME_PERF_WEB_ORIGIN
if (!baseURL?.startsWith('http://localhost:')) {
  throw new Error('ITIME_PERF_WEB_ORIGIN 必须是 localhost 性能预览地址')
}

export default defineConfig({
  ...base,
  testMatch: 'performance-under-load.spec.ts',
  timeout: 60_000,
  use: { ...base.use, baseURL },
  webServer: undefined,
})
```

`package.json`：

```json
"test:e2e:performance": "playwright test --config playwright.performance.config.ts"
```

- [x] **Step 2: 写三视口负载下 UI 失败测试**

`performance-under-load.spec.ts` 从权限 `0600` 的
`ITIME_PERF_BROWSER_ACCOUNT_FILE` 读取一个已播种账号。测试必须：

1. 登录并打开任务列表/详情；
2. 打开日历并查询当前周；
3. 打开计划、专注、复盘和统计；
4. 创建一条唯一任务，在第二 browser context 中等待 5 秒内同步可见；
5. 检查响应时间、页面错误、API 5xx、错误覆盖层、axe 和根级横向溢出；
6. 保存 `/tmp/itime-ui-evidence/<project>-performance-under-load.png`。

关键计时：

```typescript
const started = performance.now()
await page.getByRole('link', { name: '统计', exact: true }).click()
await expect(page.getByRole('heading', { name: '统计', exact: true }))
  .toBeVisible()
expect(performance.now() - started).toBeLessThanOrEqual(5_000)
```

运行前确认 `/api/ready` 的 `version` 与 manifest 一致；测试结束断言后台 Locust 进程仍存活，
防止在负载已经停止后得到假通过。

- [x] **Step 3: 让编排器并行运行稳态负载和浏览器**

在 `SCENARIOS` 增加：

```python
Scenario.UI_LOAD: ScenarioConfig(
    Scenario.UI_LOAD,
    users=20,
    spawn_rate=4,
    duration_seconds=900,
    dataset="standard",
),
```

并让 CLI 接受 `python -m performance.run scenario ui-load`。`run.py` 的 browser 阶段：

```python
locust = start_locust(steady_config)
wait_for_active_users(locust_stats, expected=20, timeout=30)
try:
    checked(
        ["pnpm", "test:e2e:performance"],
        cwd=WEB,
        env=browser_env,
        timeout=20 * 60,
    )
finally:
    stop_process_group(locust)
```

三视口使用默认 Chromium。真实 Edge desktop 另执行：

```bash
ITIME_E2E_BROWSER_CHANNEL=msedge \
pnpm exec playwright test --config playwright.performance.config.ts \
  --project=desktop
```

Edge 不可用时明确失败，不自动回退 Chromium。

- [x] **Step 4: 构建并执行负载下浏览器验证**

Run:

```bash
cd web
pnpm build
cd ../server
uv run python -m performance.run scenario ui-load
```

`ui-load` 由编排器先运行 Chromium desktop/mobile/tablet，再设置
`ITIME_E2E_BROWSER_CHANNEL=msedge` 运行 desktop。Expected: 四组流程全部通过；截图非空，
无错误覆盖层或横向溢出。

- [x] **Step 5: 提交**

```bash
git add web/playwright.performance.config.ts \
  web/e2e/performance-under-load.spec.ts web/package.json \
  server/performance/run.py server/performance/contracts.py
git commit -m "test: 验证负载下真实 Web 流程"
```

### Task 12: 执行完整基线并处理实测瓶颈

**Files:**
- Read: `/tmp/itime-performance/$RUN_ID/summary.json`
- Read: `/tmp/itime-performance/$RUN_ID/resources.csv`
- Read: `/tmp/itime-performance/$RUN_ID/postgres.csv`
- Read: `/tmp/itime-performance/$RUN_ID/queues.csv`
- Modify: 本 Task 不预选业务文件；每个实测瓶颈先追加一个带精确路径、失败测试和命令的
  子任务，再按该子任务修改并单独提交

- [x] **Step 1: 核验 2 vCPU / 4 GiB 运行环境**

Run:

```bash
colima status --profile itime-perf --json
```

Expected: JSON 中 `cpu` 为 2、`memory` 为 4294967296。Docker 会为虚拟机自身保留少量内存，
因此不使用 `docker info` 的可分配字节数冒充 profile 配置值。context 不存在时执行：

```bash
colima start --profile itime-perf --cpu 2 --memory 4 --disk 20
```

该专用 profile 不停止或修改当前 `colima-itime` 开发环境。

- [x] **Step 2: 执行标准、峰值、重历史与饱和场景**

Run:

```bash
cd server
uv run python -m performance.run scenario steady
uv run python -m performance.run scenario peak
uv run python -m performance.run scenario history
uv run python -m performance.run scenario saturation
```

Expected: 50 用户峰值通过；饱和场景输出最大稳定档位。每次均独立数据库并零残留。

- [x] **Step 3: 执行异步、维护与耐久场景**

Run:

```bash
cd server
uv run python -m performance.run scenario ai-isolation
uv run python -m performance.run scenario queues
uv run python -m performance.run scenario maintenance
uv run python -m performance.run scenario soak
```

Expected: 普通 API 隔离、队列终态、金额、资源和 60 分钟趋势全部通过。

- [x] **Step 4: 按证据修复瓶颈**

每个失败按以下固定流程处理：

1. 从 `summary.json` 定位失败门禁；
2. 用同一 run ID 的 `postgres.csv`、`resources.csv` 和结构化日志确定瓶颈；
3. SQL 问题保存 `EXPLAIN (ANALYZE, BUFFERS, WAL, FORMAT JSON)` 到临时证据；
4. 先增加能在标准/重历史数据上稳定复现的 pytest；
5. 只修改对应查询、索引、连接预算或有界 worker 并发；
6. 运行专项测试、原失败场景和受影响回归；
7. 每个独立优化单独提交，例如统计查询优化使用 `perf: 优化重历史统计查询`。

禁止通过提高设计门限、缩小数据、减少业务请求、跳过失败样本、关闭所有权/版本校验或扩大
数据库超时来获得通过。若必须提高资源档位，保留 2 vCPU / 4 GiB 失败报告，并新增独立资源
profile 后重新走设计确认。

- [x] **Step 5: 执行最终一键基线**

Run:

```bash
cd server
uv run python -m performance.run baseline
uv run python -m performance.run verify-clean
```

Expected: 全部硬门禁通过，报告包含实际饱和点、慢查询、资源峰值、队列吞吐和限制。

### Task 13: 全量回归、证据同步与文档提交

**Files:**
- Modify: `README.md`
- Modify: `cloud.md`
- Modify: `doc/README.md`
- Modify: `doc/测试/iTime-测试方案与测试用例.md`
- Modify: `doc/验收/iTime-产品验收清单.md`
- Modify: `doc/验收/iTime-Web实施与验证.md`
- Modify: `doc/需求/iTime-需求追踪矩阵.md`
- Modify: `doc/计划/iTime-开发总计划.md`
- Modify: `doc/计划/iTime-M8性能与容量基线实施计划.md`

- [x] **Step 1: 执行服务端静态、全量测试与构建**

Run:

```bash
cd server
uv run ruff check .
uv run pytest -q
uv build --no-sources
uv run alembic current
uv run alembic check
```

Expected: 全部通过；Alembic 在当前 head 且无模型漂移。

- [x] **Step 2: 执行前端构建与受影响回归**

Run:

```bash
cd web
pnpm build
pnpm test:e2e
cd ../server
uv run python -m performance.run scenario ui-load
```

Expected: 性能 UI 专项及 Chromium 全量通过。若性能实现未改变业务 UI，真实 Edge 只需重新执行
`performance-under-load.spec.ts` desktop；若修改共享业务或样式，则执行受影响页面的 Edge
desktop/mobile/tablet。

- [x] **Step 3: 核验运行资源零残留**

Run:

```bash
cd server
uv run python -m performance.run verify-clean
```

再查询 PostgreSQL：

```sql
SELECT datname
FROM pg_database
WHERE datname LIKE 'itime_perf_%'
ORDER BY datname;
```

Expected: 0 行；无 `itime-perf-*` 容器、卷、网络、Locust、性能 Vite 或协议桩进程。

- [x] **Step 4: 同步真实证据**

文档必须写入：

- 测试日期和 Git SHA；
- macOS、Colima、Docker、Python、PostgreSQL、Locust、Playwright、Chrome/Edge 版本；
- 2 vCPU / 4 GiB、进程数、连接预算和数据对象数量；
- 每个场景实际时长、请求数、吞吐、P50/P95/P99、错误率；
- CPU、内存、连接、锁、慢查询、队列等待/排空和饱和点；
- Chromium 三视口和 Edge desktop 的真实命令、通过数及截图；
- 所有失败、修复、复跑和仍不适用的公网/正式模型边界；
- 清理后数据库、容器、卷、网络和进程数量。

`PERF-01`、`PERF-02` 和 `NFR-PERF-01/NFR-CAP-01` 只能按实际覆盖范围更新；M8-B
备份恢复、M8-C 生产部署、正式模型质量和实体设备继续标为未完成。

- [x] **Step 5: 检查并提交证据**

Run:

```bash
git diff --check
git status --short
git diff -- README.md cloud.md doc/README.md \
  doc/测试/iTime-测试方案与测试用例.md \
  doc/验收/iTime-产品验收清单.md \
  doc/验收/iTime-Web实施与验证.md \
  doc/需求/iTime-需求追踪矩阵.md \
  doc/计划/iTime-开发总计划.md \
  doc/计划/iTime-M8性能与容量基线实施计划.md
```

确认未包含 `.env.local`、Cookie、CSRF、密码、Key、完整 URL、用户正文或原始大文件后提交：

```bash
git add README.md cloud.md doc/README.md \
  doc/测试/iTime-测试方案与测试用例.md \
  doc/验收/iTime-产品验收清单.md \
  doc/验收/iTime-Web实施与验证.md \
  doc/需求/iTime-需求追踪矩阵.md \
  doc/计划/iTime-开发总计划.md \
  doc/计划/iTime-M8性能与容量基线实施计划.md
git commit -m "docs: 记录 M8 性能容量基线证据"
```

## 实施结果

- 最终一键基线提交 `cef08e1`：steady `c4d6f805`、peak `0065f99a`、history `d3ced98f`、
  saturation `51b71e51`、ai-isolation `edb93be4`、queues `30e69fb1`、maintenance
  `15f4538b`、soak `f644da29`，八个场景全部 PASS。
- 最终负载 UI 提交 `2a9a1aa`、run `4402349a`：Chromium desktop/mobile/tablet 3 passed，
  真实 Edge 154 desktop 1 passed，背景负载 452 请求、0 失败。
- 50 用户最低容量门禁通过；10—100 用户 30 个窗口全部稳定，100 为测试上限，未观察到真实
  容量边界。60 分钟耐久 39,932 请求、0 失败，内存增长 0.603%。
- 服务端性能专项 89 passed、2 skipped；全量 670 passed、2 skipped；Ruff、构建、迁移 head
  与漂移检查通过；前端构建和 Chromium 全量 277 passed、20 skipped。
- 最终 `verify-clean` 返回空清单；独立数据库、Docker 和进程扫描清理了一个脱离精确运行清单
  的旧协议桩后，`itime_perf_%`、`itime_test_%` 数据库及性能容器、卷、网络、Locust、Vite、
  协议桩进程均为 0。
- 公网、正式 AI/邮件供应商、生产候选拓扑、月度成本、备份恢复和实体设备不在本次通过范围。

## 实施纪律

- 每个 Task 在对应测试通过后独立提交；实测发现的每个独立优化再单独提交。
- 性能测试仅针对随机隔离数据库；不使用开发库，更不连接生产或外部环境。
- 原始性能结果保存在 `/tmp/itime-performance/`，仓库只保存精简、可复核且无凭据的摘要。
- 任何失败都保留原因、请求编号、是否保存和恢复方式；不得只报告“未达标”。
- M8-A 完成后再进入 M8-B 备份恢复设计，不在本计划中混入 RPO、保留期或异地位置决定。
