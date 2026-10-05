# iTime M8 备份与恢复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在隔离环境实现 PostgreSQL 18 加密物理备份、连续 WAL、指定时间 PITR、删除台账重放和恢复后真实 Web 验证，证明 RPO 不超过 1 小时、RTO 不超过 24 小时。

**Architecture:** 使用固定 pgBackRest 2.59.3 的恢复专用 PostgreSQL 镜像和独立 Compose 项目。恢复编排复用 M8-A 的数据生成、安全边界、报告脱敏和 UI 连接模式；恢复始终写入新集群，先重放由现有生命周期墓碑生成的加密台账，再撤销会话、收敛在途状态、执行到期清理和业务不变量验证。

**Tech Stack:** PostgreSQL 18.6、pgBackRest 2.59.3、Python 3.13、psycopg 3、SQLAlchemy 2、Docker Compose、pytest、Vue 3、Playwright Chromium/Edge。

---

## 文件结构

新增：

- `server/Dockerfile.recovery-postgres`：固定 PostgreSQL 和 pgBackRest 的多阶段构建镜像。
- `compose.recovery.yaml`：隔离源库、恢复库、应用 API、仓库及数据卷。
- `server/recovery/__init__.py`：恢复模块边界。
- `server/recovery/contracts.py`：配置、阶段、台账、备份、恢复和报告类型。
- `server/recovery/safety.py`：回环环境、资源命名、目录、锁和精确清理。
- `server/recovery/ledger.py`：生命周期快照、Fernet 加密、完整性和链校验。
- `server/recovery/replay.py`：账号及单项删除状态的幂等重放。
- `server/recovery/finalize.py`：会话撤销、租约/作业/专注收敛和到期清理。
- `server/recovery/pgbackrest.py`：无 shell 的 pgBackRest 命令和 JSON 结果解析。
- `server/recovery/verify.py`：迁移、数据摘要、所有权、金额和删除不变量。
- `server/recovery/report.py`：RPO/RTO、阶段、错误和敏感字段门禁。
- `server/recovery/run.py`：完整场景、清理和 CLI 编排。
- `server/tests/test_recovery_runtime.py`：镜像、Compose、工具版本和资源合同。
- `server/tests/test_recovery_safety.py`：危险环境、路径、清理和锁拒绝。
- `server/tests/test_recovery_ledger.py`：台账确定性、加密、签名、链和敏感字段。
- `server/tests/test_recovery_replay.py`：真实 PostgreSQL 删除状态重放。
- `server/tests/test_recovery_finalize.py`：会话、租约、作业、金额和清理收尾。
- `server/tests/test_recovery_report.py`：RPO/RTO、报告和凭据拒绝。
- `server/tests/test_recovery_integration.py`：真实 full/diff/WAL/PITR、故障注入和零残留。
- `web/playwright.recovery.config.ts`：连接已恢复的隔离栈。
- `web/e2e/recovery-after-restore.spec.ts`：恢复后核心页面、删除不可见和新写同步。

修改：

- `.gitignore`：忽略误写入仓库的恢复产物。
- `web/package.json`：增加恢复后 UI 命令。
- `README.md`、`cloud.md`、`doc/README.md`、需求追踪、测试、验收、实施和本计划：记录实际证据。

不新增生产业务路由、管理员恢复按钮或用户整包恢复入口。

### Task 1: 固定恢复 PostgreSQL 与 pgBackRest 运行镜像

**Files:**
- Create: `server/Dockerfile.recovery-postgres`
- Create: `server/recovery/__init__.py`
- Create: `server/tests/test_recovery_runtime.py`
- Modify: `.gitignore`

- [x] **Step 1: 写工具版本与镜像合同失败测试**

在 `server/tests/test_recovery_runtime.py` 解析 Dockerfile 并断言：

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "server" / "Dockerfile.recovery-postgres"


def test_recovery_image_pins_postgres_and_safe_pgbackrest():
    content = DOCKERFILE.read_text()
    assert (
        "postgres:18-alpine@"
        "sha256:77f585114c32fbca283dc835b0596f4e52b51b4c6662d7810b2f4084f60a1873"
    ) in content
    assert "PGBACKREST_VERSION=2.59.3" in content
    assert (
        "14037901db002e5536a948bf9f0fc0ff6cde31f4e675d3e9b46f129071bf2e5f"
        in content
    )
    assert "USER postgres" in content
```

同时断言最终镜像没有 `build-base`、`meson`、`ninja`，并存在 `/usr/local/bin/pgbackrest`。

- [x] **Step 2: 运行测试确认失败**

Run:

```bash
cd server
uv run pytest -q tests/test_recovery_runtime.py
```

Expected: FAIL，恢复镜像尚不存在。

- [x] **Step 3: 创建多阶段恢复镜像**

`server/Dockerfile.recovery-postgres`：

```dockerfile
FROM postgres:18-alpine@sha256:77f585114c32fbca283dc835b0596f4e52b51b4c6662d7810b2f4084f60a1873 AS build

ARG PGBACKREST_VERSION=2.59.3
ARG PGBACKREST_SHA256=14037901db002e5536a948bf9f0fc0ff6cde31f4e675d3e9b46f129071bf2e5f

RUN apk add --no-cache \
      build-base bzip2-dev curl curl-dev libxml2-dev lz4-dev meson ninja \
      openssl-dev pkgconf postgresql-dev zlib-dev zstd-dev \
    && curl -fL --retry 3 \
      -o /tmp/pgbackrest.tar.gz \
      "https://github.com/pgbackrest/pgbackrest/releases/download/release/${PGBACKREST_VERSION}/pgbackrest-${PGBACKREST_VERSION}.tar.gz" \
    && echo "${PGBACKREST_SHA256}  /tmp/pgbackrest.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/pgbackrest.tar.gz -C /tmp \
    && meson setup /tmp/build "/tmp/pgbackrest-${PGBACKREST_VERSION}" \
      --buildtype=release -Dlibssh2=disabled -Dlibsystemd=disabled \
    && ninja -C /tmp/build \
    && install -m 0755 /tmp/build/src/pgbackrest /usr/local/bin/pgbackrest

RUN install -m 0755 \
    "$(readlink -f /usr/lib/libbz2.so.1)" \
    /usr/local/lib/libbz2.so.1

FROM postgres:18-alpine@sha256:77f585114c32fbca283dc835b0596f4e52b51b4c6662d7810b2f4084f60a1873

RUN mkdir -p /etc/pgbackrest /var/log/pgbackrest /var/spool/pgbackrest \
    && chown -R postgres:postgres /etc/pgbackrest /var/log/pgbackrest /var/spool/pgbackrest

COPY --from=build /usr/local/bin/pgbackrest /usr/local/bin/pgbackrest
COPY --from=build /usr/local/lib/libbz2.so.1 /usr/lib/libbz2.so.1

USER postgres
```

若实际 Meson 输出路径不同，只按官方 tarball 的构建结果修正 COPY 路径，并把路径合同加入测试；
不得改用 Alpine 仓库中的修复前版本。

在根 `.gitignore` 增加 `recovery-results/`，防止手工覆盖输出目录时提交仓库或恢复数据；标准
编排仍只写 `/tmp/itime-recovery/`。

- [x] **Step 4: 构建并验证工具、身份和动态链接**

Run:

```bash
docker --context colima-itime-perf build \
  -f server/Dockerfile.recovery-postgres \
  -t itime-recovery-postgres:local server
docker --context colima-itime-perf run --rm \
  itime-recovery-postgres:local pgbackrest version
docker --context colima-itime-perf run --rm \
  itime-recovery-postgres:local id -u
```

Expected: `pgBackRest 2.59.3`；UID 为 PostgreSQL 非 root 用户；无缺失动态库。

- [x] **Step 5: 完成测试和提交**

```bash
cd server
uv run pytest -q tests/test_recovery_runtime.py
uv run ruff check tests/test_recovery_runtime.py recovery
cd ..
git diff --check
git add server/Dockerfile.recovery-postgres server/recovery/__init__.py \
  server/tests/test_recovery_runtime.py .gitignore
git commit -m "build: 添加备份恢复运行镜像"
```

### Task 2: 建立恢复运行合同与危险环境拒绝

**Files:**
- Create: `server/recovery/contracts.py`
- Create: `server/recovery/safety.py`
- Create: `server/tests/test_recovery_safety.py`

- [x] **Step 1: 写配置、路径、锁和清理失败测试**

`server/tests/test_recovery_safety.py` 使用：

```python
from pathlib import Path

import pytest

from recovery.contracts import RecoveryConfig
from recovery.safety import RecoverySafetyError, require_safe_run


def config(**changes: object) -> RecoveryConfig:
    values = {
        "run_id": "abc12345",
        "environment": "test",
        "docker_context": "colima-itime-recovery",
        "source_database": "itime_backup_abc12345",
        "restore_database": "itime_restore_abc12345",
        "http_origin": "http://127.0.0.1:18100",
        "guard_value": "itime-recovery:abc12345",
        "output_dir": Path("/tmp/itime-recovery/abc12345"),
    }
    values.update(changes)
    return RecoveryConfig(**values)


def test_safe_recovery_accepts_only_exact_isolated_boundary():
    require_safe_run(config())
    for field, value in (
        ("environment", "production"),
        ("docker_context", "default"),
        ("source_database", "itime"),
        ("restore_database", "itime_backup_abc12345"),
        ("http_origin", "https://itime.example"),
        ("guard_value", "itime-recovery:other"),
        ("output_dir", Path("/tmp/itime-recovery/abc12345/extra")),
    ):
        with pytest.raises(RecoverySafetyError):
            require_safe_run(config(**{field: value}))
```

另测 8—32 位小写十六进制 run ID、源/目标不同、回环 URL、符号链接逃逸、`0600` 运行锁和
manifest 中所有资源精确匹配。

- [x] **Step 2: 运行测试确认失败**

```bash
cd server
uv run pytest -q tests/test_recovery_safety.py
```

Expected: FAIL，`recovery.contracts` 尚不存在。

- [x] **Step 3: 实现不可变运行合同**

`server/recovery/contracts.py` 至少定义：

```python
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class RecoveryScenario(StrEnum):
    SMOKE = "smoke"
    LATEST = "latest"
    PITR = "pitr"
    DELETION_REPLAY = "deletion-replay"
    FAULTS = "faults"
    UI = "ui"


@dataclass(frozen=True)
class RecoveryConfig:
    run_id: str
    environment: str
    docker_context: str
    source_database: str
    restore_database: str
    http_origin: str
    guard_value: str
    output_dir: Path

    @property
    def compose_project(self) -> str:
        return f"itime-recovery-{self.run_id}"
```

再定义 `CleanupManifest`，明确 source/restore 数据卷、仓库卷、网络、输出目录和 Compose 项目。

- [x] **Step 4: 实现安全校验与私有运行锁**

复用 M8-A 的正则、路径 resolve 和 `O_EXCL` 模式，但使用独立
`/tmp/itime-recovery`、`itime_backup_`、`itime_restore_` 和 `colima-itime-recovery`。
`require_safe_cleanup` 对 manifest 做完整 dataclass 相等检查，不接受前缀或模糊匹配。

- [x] **Step 5: 验证并提交**

```bash
cd server
uv run pytest -q tests/test_recovery_safety.py
uv run ruff check recovery tests/test_recovery_safety.py
cd ..
git diff --check
git add server/recovery/contracts.py server/recovery/safety.py \
  server/tests/test_recovery_safety.py
git commit -m "test: 隔离备份恢复运行边界"
```

### Task 3: 建立源库、恢复库和独立仓库拓扑

**Files:**
- Create: `compose.recovery.yaml`
- Modify: `server/tests/test_recovery_runtime.py`

- [x] **Step 1: 写 Compose 合同失败测试**

测试使用最小无敏感占位环境执行
`docker compose -f compose.recovery.yaml config --format json`，断言：

- 源库、恢复库均使用 `itime-recovery-postgres:local`；
- 源库开启 `archive_mode=on`、`archive_timeout=60s` 和 pgBackRest `archive-push`；
- 源、恢复、仓库、spool 是四个不同命名卷；
- 恢复库默认 profile 为 `restore`，不随源库启动；
- 恢复 API 只连接恢复库且只绑定 `127.0.0.1`；
- 没有 AI、邮件、订阅、公开链接或导出 worker；
- 仓库配置和密钥文件只读挂载，PostgreSQL 数据目录不可跨服务复用；
- 服务内存总额不超过恢复 profile 的 4 GiB。

- [x] **Step 2: 运行测试确认失败**

```bash
cd server
uv run pytest -q tests/test_recovery_runtime.py -k compose
```

Expected: FAIL，Compose 文件尚不存在。

- [x] **Step 3: 创建恢复 Compose**

`compose.recovery.yaml` 使用以下服务边界：

```yaml
name: itime-recovery-${ITIME_RECOVERY_RUN_ID:?required}

services:
  source-postgres:
    image: itime-recovery-postgres:${ITIME_RECOVERY_IMAGE_TAG:-local}
    command:
      - postgres
      - -c
      - archive_mode=on
      - -c
      - archive_timeout=60s
      - -c
      - wal_level=replica
      - -c
      - archive_command=pgbackrest --stanza=itime archive-push %p
    volumes:
      - source_data:/var/lib/postgresql
      - repository:/var/lib/pgbackrest
      - spool:/var/spool/pgbackrest
      - ${ITIME_RECOVERY_SOURCE_CONFIG:?required}:/etc/pgbackrest/pgbackrest.conf:ro

  restore-postgres:
    profiles: [restore]
    image: itime-recovery-postgres:${ITIME_RECOVERY_IMAGE_TAG:-local}
    volumes:
      - restore_data:/var/lib/postgresql
      - repository:/var/lib/pgbackrest:ro
      - restore_spool:/var/spool/pgbackrest
      - ${ITIME_RECOVERY_RESTORE_CONFIG:?required}:/etc/pgbackrest/pgbackrest.conf:ro

  restore-api:
    profiles: [app]
    image: itime-performance:${ITIME_RECOVERY_APP_IMAGE_TAG:-local}
    command:
      - uvicorn
      - itime.main:create_app
      - --factory
      - --host
      - 0.0.0.0
      - --port
      - "8000"
      - --no-access-log
    ports:
      - "127.0.0.1::8000"

volumes:
  source_data:
  restore_data:
  repository:
  spool:
  restore_spool:
```

完整文件补 healthcheck、`read_only`/tmpfs、`no-new-privileges`、内存、PID、停止期限和所需应用
环境。源和恢复 PostgreSQL 数据卷挂载父目录，以兼容官方镜像 18 的版本目录布局。

- [x] **Step 4: 验证静态配置和真实启动**

```bash
colima start --profile itime-recovery --cpu 2 --memory 4 --disk 30
cd server
uv run pytest -q tests/test_recovery_runtime.py
cd ..
docker --context colima-itime-recovery compose \
  -f compose.recovery.yaml config --format json >/tmp/itime-recovery-compose.json
```

Expected: pytest PASS；Compose 可解析；只启动源库时恢复库/API 不出现。

- [x] **Step 5: 提交**

```bash
git diff --check
git add compose.recovery.yaml server/tests/test_recovery_runtime.py
git commit -m "build: 固定备份恢复服务拓扑"
```

### Task 4: 生成加密且确定性的删除台账快照

**Files:**
- Create: `server/recovery/ledger.py`
- Create: `server/tests/test_recovery_ledger.py`

- [x] **Step 1: 写快照结构和敏感字段失败测试**

测试在随机迁移数据库写入：

- pending 和 completed 账号注销各一条，cancelled 一条；
- active、trashed、purged 的任务、困难、记忆、日历系列和来源；
- 含明显哨兵正文、邮箱和 URL 的关联数据。

调用：

```python
snapshot = asyncio.run(build_ledger_snapshot(settings, generated_at=at))
path = write_encrypted_snapshot(
    snapshot,
    output_dir / "ledger.bin",
    Fernet.generate_key(),
)
```

断言：

- cancelled 和 active 不产生恢复动作；
- 条目按 `(kind, subject)` 稳定排序；
- 明文不含邮箱、正文、URL、密码、Cookie、Key；
- 文件权限 `0600`，原始字节不含 UUID/哨兵文本；
- 同输入产生相同 canonical payload 和 digest；
- 错误 Fernet key、篡改 token、链摘要不匹配全部失败。

- [x] **Step 2: 运行测试确认失败**

```bash
cd server
uv run pytest -q tests/test_recovery_ledger.py
```

Expected: FAIL，台账模块不存在。

- [x] **Step 3: 定义台账类型**

在 `contracts.py` 增加：

```python
@dataclass(frozen=True)
class LedgerEntry:
    kind: str
    subject: str
    owner_id: str | None
    state: str
    deleted_at: str
    purge_after: str
    purged_at: str | None
    version: int


@dataclass(frozen=True)
class RecoveryLedger:
    schema_version: int
    generated_at: str
    source_system_identifier: str
    previous_digest: str | None
    entries: tuple[LedgerEntry, ...]
```

账号条目的 `subject` 只保存现有 `subject_digest`、`owner_id=None`；单项对象保存 UUID 和 owner
UUID，但整个 payload 必须加密。

- [x] **Step 4: 实现查询、规范化、加密和链校验**

`ledger.py` 提供：

```python
async def build_ledger_snapshot(
    settings: Settings,
    *,
    generated_at: datetime,
    previous_digest: str | None = None,
) -> RecoveryLedger: ...

def canonical_payload(ledger: RecoveryLedger) -> bytes: ...
def ledger_digest(ledger: RecoveryLedger) -> str: ...
def write_encrypted_snapshot(
    ledger: RecoveryLedger,
    path: Path,
    key: bytes,
) -> Path: ...
def read_encrypted_snapshot(
    path: Path,
    key: bytes,
    *,
    expected_previous_digest: str | None,
) -> RecoveryLedger: ...
```

使用现有 `Settings`/`Database`、`cryptography.fernet.Fernet`、原子 rename、`fsync` 和 `0600`
文件权限。异常消息只包含阶段和文件名，不包含 token 或解密错误原文。

- [x] **Step 5: 运行真实 PostgreSQL 测试并提交**

```bash
cd server
uv run pytest -q tests/test_recovery_ledger.py
uv run ruff check recovery tests/test_recovery_ledger.py
cd ..
git diff --check
git add server/recovery/contracts.py server/recovery/ledger.py \
  server/tests/test_recovery_ledger.py
git commit -m "feat: 生成加密恢复删除台账"
```

### Task 5: 幂等重放账号和单项删除状态

**Files:**
- Create: `server/recovery/replay.py`
- Create: `server/tests/test_recovery_replay.py`

- [x] **Step 1: 写目标早于删除状态的失败测试**

在源测试库生成台账，在另一随机恢复库保留删除前数据。覆盖：

- completed 账号在恢复库重新出现；
- pending 账号在备份后申请注销；
- task/difficulty/memory/calendar series/source 分别为 trashed 和 purged；
- 一个完全无关控制账号及其相同名称内容。

调用两次：

```python
first = asyncio.run(replay_ledger(settings, ledger, at=restore_now))
second = asyncio.run(replay_ledger(settings, ledger, at=restore_now))
assert first.applied > 0
assert second.applied == 0
```

断言 completed 账号及本人数据消失，pending 账号冻结且可按原期限处理，purged 正文/URL/直接
明细为空，trashed 保持回收状态，控制账号不变。

- [x] **Step 2: 写冲突与签名失败测试**

同对象 ID 属于不同 owner、目标状态比台账更新、版本倒退、未知 kind、错误系统标识和过期台账
分别测试。所有者冲突、未知类型和签名错误阻断恢复；目标更新时只跳过，不倒退状态。

- [x] **Step 3: 实现账号 digest 匹配和重放**

`replay.py` 定义：

```python
@dataclass(frozen=True)
class ReplaySummary:
    applied: int
    skipped: int
    accounts_pending: int
    accounts_purged: int
    objects_trashed: int
    objects_purged: int


async def replay_ledger(
    settings: Settings,
    ledger: RecoveryLedger,
    *,
    at: datetime,
) -> ReplaySummary: ...
```

账号匹配使用 `Security(settings).keyed_digest(f"account-deletion:{user.id}")`；pending 复用
`quiesce_account` 和 `revoke_all_sessions`，completed 复用 `scrub_account_audit`、现有外键级联
及最小完成回执。操作在恢复数据库单事务中按 `users → account_deletions → aggregate` 锁顺序
执行。

- [x] **Step 4: 复用生命周期清理实现单项重放**

trashed 只恢复服务端删除/到期时间并使旧操作/租约失效。purged 先设置到期状态，再调用现有：

- `cleanup_expired_tasks`
- `cleanup_expired_calendar_items`
- `cleanup_expired_difficulties`
- `cleanup_expired_memories`

每次用真实 `at` 和有界批量循环到无待处理项。不得在恢复模块复制正文清空字段列表。

- [x] **Step 5: 验证并提交**

```bash
cd server
uv run pytest -q tests/test_recovery_replay.py \
  tests/test_task_lifecycle.py \
  tests/test_calendar_lifecycle_cleanup.py \
  tests/test_execution_cleanup.py \
  tests/test_review_cleanup.py
uv run ruff check recovery tests/test_recovery_replay.py
cd ..
git diff --check
git add server/recovery/replay.py server/tests/test_recovery_replay.py
git commit -m "feat: 重放恢复删除台账"
```

### Task 6: 收敛恢复出的会话、租约、作业和金额

**Files:**
- Create: `server/recovery/finalize.py`
- Create: `server/tests/test_recovery_finalize.py`

- [x] **Step 1: 写恢复收尾失败测试**

真实数据库预置：

- 未撤销用户/管理员会话和邮箱令牌；
- running AI job/attempt、活动预留、AI 正文和流事件；
- running 导出、订阅、公开链接及 processing 注销；
- running/paused 专注；
- 已成功且已结算终态作为控制项；
- 到期 AI 日志/样本、媒体、导出产物、同步事件和四类回收对象。

执行：

```python
summary = asyncio.run(finalize_recovered_database(settings, at=recovery_time))
```

断言全部会话撤销、旧令牌失效、活动 lease 清空、running 进入各领域允许的恢复状态、开放专注
abandoned、到期数据已清理、控制终态及金额不变。重复执行第二次变化数为 0。

- [x] **Step 2: 写金额和不可恢复状态阻断测试**

制造：

- settled reservation 无 provider cost；
- provider cost 与 ledger 金额不一致；
- succeeded job 同时有活动 lease；
- completed 注销仍有关联用户。

`finalize_recovered_database` 必须抛 `RecoveryFinalizationError` 并回滚本轮，不通过修改金额自动
“修复”审计事实。

- [x] **Step 3: 实现 finalizer**

定义：

```python
@dataclass(frozen=True)
class FinalizationSummary:
    sessions_revoked: int
    tokens_expired: int
    jobs_recovered: dict[str, int]
    focus_abandoned: int
    retention_cleaned: dict[str, int]
    inconsistencies: tuple[str, ...]


async def finalize_recovered_database(
    settings: Settings,
    *,
    at: datetime,
) -> FinalizationSummary: ...
```

收尾顺序：

1. 撤销会话并递增用户 `auth_epoch`；
2. 失效邮箱 token、限流桶和未发送旧邮件；
3. 复用各 worker 的 lease recovery/失败合同收敛活动作业；
4. 结束开放专注；
5. 运行 AI/媒体/导出/同步和回收站到期清理；
6. 执行金额与终态只读核对。

任何外部网络函数在恢复模式中禁止调用。

- [x] **Step 4: 验证并提交**

```bash
cd server
uv run pytest -q tests/test_recovery_finalize.py \
  tests/test_ai_worker.py tests/test_data_export_worker.py \
  tests/test_calendar_subscriptions.py tests/test_public_link_service.py \
  tests/test_account_deletion_worker.py
uv run ruff check recovery tests/test_recovery_finalize.py
cd ..
git diff --check
git add server/recovery/finalize.py server/tests/test_recovery_finalize.py
git commit -m "feat: 收敛恢复数据库运行状态"
```

### Task 7: 封装 pgBackRest、RPO/RTO 和恢复报告

**Files:**
- Create: `server/recovery/pgbackrest.py`
- Create: `server/recovery/verify.py`
- Create: `server/recovery/report.py`
- Create: `server/tests/test_recovery_report.py`

- [x] **Step 1: 写命令构造和 JSON 解析失败测试**

断言命令始终为参数数组：

```python
runner = PgBackRest(
    binary="pgbackrest",
    stanza="itime",
    config=Path("/run/secrets/pgbackrest.conf"),
)
assert runner.command("backup", "--type=full") == [
    "pgbackrest",
    "--config=/run/secrets/pgbackrest.conf",
    "--stanza=itime",
    "backup",
    "--type=full",
]
```

测试 `info --output=json` 的 backup label、类型、开始/结束、WAL start/stop 和大小解析；缺字段、
非 JSON、超时、非零退出和输出含密钥时返回稳定脱敏错误。

- [x] **Step 2: 写报告门禁测试**

定义 `RecoveryEvidence`，测试：

- RPO `3600s` 通过、`3600.001s` 失败；
- RTO `86400s` 通过、`86400.001s` 失败；
- migration/ledger/finalizer/invariant/UI 任一失败则总失败；
- repository key、Fernet、JWT、Cookie、database URL、正文哨兵递归拒绝；
- `summary.json` 原子写入且不接受符号链接目标。

- [x] **Step 3: 实现无 shell pgBackRest 包装**

`pgbackrest.py` 使用 `subprocess.run(..., shell=False, check=True, capture_output=True,
timeout=...)`，只向日志和异常暴露阶段、退出码及安全化 stderr 分类。配置文件由 orchestrator
以 `0600` 生成，命令行不出现 cipher pass。

- [x] **Step 4: 实现业务验证和报告**

`verify.py` 复用 M8-A `collect_invariants`，再检查：

- Alembic head；
- 所有会话 revoked；
- 删除台账无未应用条目；
- 账号/对象删除不倒退；
- AI 终态、预留、provider cost、ledger 一致；
- 无开放专注和过期租约；
- 代表数据计数与摘要匹配目标点。

`report.py` 输出 `manifest.json`、`backup.json`、`restore.json`、`summary.json` 和 `report.md`，
并复用 `performance.report.assert_no_sensitive_artifacts`。

- [x] **Step 5: 验证并提交**

```bash
cd server
uv run pytest -q tests/test_recovery_report.py
uv run ruff check recovery tests/test_recovery_report.py
cd ..
git diff --check
git add server/recovery/pgbackrest.py server/recovery/verify.py \
  server/recovery/report.py server/tests/test_recovery_report.py
git commit -m "feat: 生成备份恢复门禁报告"
```

### Task 8: 编排 full、differential、连续 WAL 和 PITR

**Files:**
- Create: `server/recovery/run.py`
- Create: `server/tests/test_recovery_integration.py`
- Modify: `server/pyproject.toml`

- [x] **Step 1: 写真实恢复烟测并默认跳过**

```python
@pytest.mark.recovery
@pytest.mark.skipif(
    os.environ.get("ITIME_RUN_RECOVERY") != "1",
    reason="设置 ITIME_RUN_RECOVERY=1 执行真实备份恢复",
)
def test_real_full_backup_and_latest_restore():
    result = run_recovery_scenario("smoke")
    assert result.passed
    assert result.remaining_resources == ()
```

在 `server/pyproject.toml` 注册 `recovery` marker。普通 `pytest` 只执行静态/纯数据库测试，不启动
Colima；显式运行才执行容器恢复。

- [x] **Step 2: 定义阶段和 CLI**

`run.py` 固定：

```python
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
```

CLI：

```text
python -m recovery.run scenario smoke|latest|pitr|deletion-replay|faults|ui
python -m recovery.run baseline
python -m recovery.run cleanup --run-id <id>
python -m recovery.run verify-clean
```

- [x] **Step 3: 实现源库、迁移、播种与备份**

复用：

- `performance.seed.seed_dataset`
- `performance.seed.write_private_credentials`
- 当前 Alembic migration
- M8-A 的镜像构建、端口分配、进程注册和安全 JSON 写入辅助

流程创建 stanza，执行 `check`、full 或 differential backup，再通过真实 HTTP 写入恢复探针并
调用 `pg_switch_wal()`；轮询 pgBackRest info，直到目标 WAL 到达或超时。每个阶段写安全日志。
生成的配置固定 `repo1-retention-full=4`、客户端加密、压缩和 archive timeout；场景先执行 full，
再写入增量并执行 differential，断言 info 中两个备份集及其 WAL 范围完整。

- [x] **Step 4: 实现恢复到最新和指定时间**

停止源库后：

1. 验证 restore volume 为空；
2. 以仓库只读挂载执行 pgBackRest restore；
3. latest 场景恢复所有可用 WAL；
4. PITR 场景传入明确 UTC `--type=time --target=<timestamp> --target-action=promote`；
5. 启动 restore PostgreSQL 并等待 `pg_is_in_recovery()=false`；
6. 执行台账、finalizer 和 verify；
7. 只有全部通过才启动 restore API。

- [x] **Step 5: 实现异常清理和断点报告**

捕获 timeout、Docker、pgBackRest、数据库、台账和门禁错误，写 `partial-report.json`，finally
按 manifest 停止进程、Compose down `--volumes --remove-orphans`、删除临时密钥和输出中注册的
私有文件。`cleanup --run-id` 只接收已存在且校验通过的 manifest。

- [x] **Step 6: 执行真实 smoke**

```bash
cd server
ITIME_RUN_RECOVERY=1 uv run pytest -q \
  tests/test_recovery_integration.py -k full_backup_and_latest_restore -s
uv run python -m recovery.run verify-clean
```

Expected: full backup、WAL、latest restore、迁移、finalizer、不变量和零残留全部通过。

- [x] **Step 7: 提交**

```bash
git diff --check
git add server/pyproject.toml server/recovery/run.py \
  server/tests/test_recovery_integration.py
git commit -m "feat: 编排 PostgreSQL 时间点恢复"
```

### Task 9: 验证删除重放和恢复故障拒绝

**Files:**
- Modify: `server/tests/test_recovery_integration.py`
- Modify: `server/recovery/run.py`

- [x] **Step 1: 增加删除重放真实场景**

在 full backup 后通过真实 API/worker：

- 删除一个任务并让其到期清理；
- 删除一个日历来源、困难和记忆；
- 申请并完成一个账号注销；
- 保留控制账号和控制对象；
- 生成新台账并强制归档 WAL；
- PITR 到所有删除之前。

恢复后断言台账重新应用上述状态，控制数据完整。

- [x] **Step 2: 增加故障注入场景**

复制仓库到本次临时故障卷后分别：

- 翻转一个 backup 文件字节；
- 删除恢复目标所需 WAL；
- 使用错误 pgBackRest cipher pass；
- 使用错误 ledger Fernet key；
- 使用错误应用基础/AI/订阅/导出解密根密钥；
- 截断 ledger token；
- 删除最新台账，只保留超过 1 小时的快照；
- 预占 restore volume。
- 将故障仓库放入受限 tmpfs 并耗尽可用空间。

每种场景必须在启动 API 前失败，源库与原仓库保持可用，错误不含密钥或正文。

- [x] **Step 3: 执行专项**

```bash
cd server
ITIME_RUN_RECOVERY=1 uv run pytest -q \
  tests/test_recovery_integration.py \
  -k 'pitr or deletion_replay or corruption or wrong_key or missing_wal' -s
uv run python -m recovery.run verify-clean
```

Expected: 正常场景 PASS；故障场景被预期拒绝；零残留。

- [x] **Step 4: 提交**

```bash
git diff --check
git add server/recovery/run.py server/tests/test_recovery_integration.py
git commit -m "test: 验证恢复删除重放与故障拒绝"
```

### Task 10: 恢复后真实 Web 验证

**Files:**
- Create: `web/playwright.recovery.config.ts`
- Create: `web/e2e/recovery-after-restore.spec.ts`
- Modify: `web/package.json`
- Modify: `server/recovery/run.py`

- [x] **Step 1: 写连接恢复栈的 Playwright 配置**

`web/playwright.recovery.config.ts` 复用普通配置，但要求：

```typescript
const baseURL = process.env.ITIME_RECOVERY_WEB_ORIGIN
if (!baseURL?.startsWith('http://localhost:')) {
  throw new Error('ITIME_RECOVERY_WEB_ORIGIN 必须是 localhost 恢复预览地址')
}
```

设置 `testMatch: 'recovery-after-restore.spec.ts'`、`webServer: undefined`、独立 outputDir 和
60 秒用例时限。`package.json` 增加：

```json
"test:e2e:recovery": "playwright test --config playwright.recovery.config.ts"
```

- [x] **Step 2: 实现旧会话拒绝和恢复后核心流程**

测试读取权限 `0600` 的账号、旧 storage state 和 run manifest，覆盖：

```typescript
test('恢复后撤销旧会话并保持数据与同步可用', async ({ browser }) => {
  // 旧 Cookie 访问 /tasks 必须转到 /login。
  // 重新登录后依次打开任务、规划、日历、专注、复盘、统计、同步。
  // 核对目标恢复点的代表任务存在，目标后的探针任务不存在。
  // 核对删除账号不能登录，删除对象不在列表、搜索和日历。
  // 新建恢复后任务，第二 context 在 5 秒内读取到。
})
```

每页执行 axe、控制台、API 5xx、Vite overlay 和根级横向溢出检查；截图按视口写
`/tmp/itime-ui-evidence/<project>-recovery-after-restore.png`。

- [x] **Step 3: 将 UI 接入恢复 orchestrator**

`scenario ui` 在 finalizer/verify 后：

1. 启动恢复 API；
2. 启动当前前端生产构建预览；
3. 写只含测试账号的 `0600` 文件和旧 session storage state；
4. 设置 `ITIME_RECOVERY_*` 环境；
5. 运行 Chromium desktop/mobile/tablet；
6. 收集退出码和截图后再清理。

浏览器失败不能仍生成 PASS 报告。

- [x] **Step 4: 执行 Chromium 和 Edge**

```bash
cd web
pnpm build
cd ../server
uv run python -m recovery.run scenario ui
cd ../web
ITIME_E2E_BROWSER_EXECUTABLE_PATH="$HOME/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge" \
  pnpm exec playwright test \
  --config playwright.recovery.config.ts \
  --project=desktop
```

Run `15687668`: Chromium 10 passed、2 skipped（仅 desktop 重复执行会话恢复语义）；
Edge desktop 4 passed；截图人工检查无空白、重叠、裁切或错误状态。

- [x] **Step 5: 提交**

```bash
git diff --check
git add web/playwright.recovery.config.ts \
  web/e2e/recovery-after-restore.spec.ts web/package.json \
  server/recovery/run.py
git commit -m "test: 验证恢复后真实 Web 流程"
```

### Task 11: 最终一键恢复基线、回归与证据

**Files:**
- Modify: `README.md`
- Modify: `cloud.md`
- Modify: `doc/README.md`
- Modify: `doc/需求/iTime-需求追踪矩阵.md`
- Modify: `doc/测试/iTime-测试方案与测试用例.md`
- Modify: `doc/验收/iTime-产品验收清单.md`
- Modify: `doc/验收/iTime-Web实施与验证.md`
- Modify: `doc/计划/iTime-开发总计划.md`
- Modify: `doc/计划/iTime-M8备份与恢复实施计划.md`

- [ ] **Step 1: 执行恢复专项与一键基线**

```bash
cd server
uv run pytest -q tests/test_recovery_*.py
ITIME_RUN_RECOVERY=1 uv run python -m recovery.run baseline
uv run python -m recovery.run verify-clean
```

Expected: latest、PITR、删除重放、故障拒绝和 UI 场景按各自预期通过；报告给出实际 RPO/RTO、
备份大小、WAL、台账、finalizer 和不变量；零残留。

- [ ] **Step 2: 执行服务端全量门禁**

```bash
cd server
uv run ruff check .
uv run pytest -q
uv build --no-sources
uv run alembic current
uv run alembic check
```

Expected: 全部通过；Alembic 在 head，无模型漂移。

- [ ] **Step 3: 执行前端构建和受影响回归**

```bash
cd web
pnpm build
pnpm test:e2e
```

Expected: 构建及 Chromium 全量通过。恢复专项再执行真实 Edge desktop；恢复实现不修改共享业务
UI 时无需重复 Edge 全量三视口，否则执行受影响项目。

- [ ] **Step 4: 核验最终资源和凭据零残留**

执行 `verify-clean`，查询：

```sql
SELECT datname
FROM pg_database
WHERE datname LIKE 'itime_backup_%'
   OR datname LIKE 'itime_restore_%'
   OR datname LIKE 'itime_test_%'
ORDER BY datname;
```

再核对无 `itime-recovery-*` 容器、卷、网络、pgBackRest、恢复 API/Vite/Playwright 进程，无仓库
口令、数据库 URL、Fernet、Cookie、用户正文或原始仓库进入 Git。

- [ ] **Step 5: 同步真实证据**

文档记录：

- 测试日期、Git SHA、macOS/Colima/Docker/PostgreSQL/pgBackRest/Python/Playwright/浏览器版本；
- full/differential/WAL 标签、时间、大小、加密和完整性结果；
- latest/PITR 目标、实际 RPO/RTO、恢复对象计数和摘要；
- 删除台账时间、摘要、重放/清理数量及控制账号结果；
- 会话、租约、作业、专注、额度和解密抽样；
- 故障注入的失败阶段、是否改写、恢复方式；
- Chromium 三视口、Edge desktop、截图和人工检查；
- 数据库、容器、卷、网络、进程和临时密钥清理结果。

只有实际范围可更新 `REL-02/NFR-REL-01`；正式主机外仓库、生产调度/告警、域名/TLS、供应商和
发布切换继续标为 M8-C 未完成。

- [ ] **Step 6: 提交文档证据**

```bash
git diff --check
git add README.md cloud.md doc/README.md \
  doc/需求/iTime-需求追踪矩阵.md \
  doc/测试/iTime-测试方案与测试用例.md \
  doc/验收/iTime-产品验收清单.md \
  doc/验收/iTime-Web实施与验证.md \
  doc/计划/iTime-开发总计划.md \
  doc/计划/iTime-M8备份与恢复实施计划.md
git commit -m "docs: 记录 M8 备份恢复验证证据"
```

## 实施纪律

- 每个 Task 的专项动态测试通过后独立提交；发现的每个独立修复另行提交。
- 所有恢复仅操作随机隔离资源，不读取或覆盖开发、生产或外部数据库。
- 不通过缩短恢复数据、跳过删除重放、关闭加密、放宽 RPO/RTO 或减少业务验证获得通过。
- M8-B 完成后再进入 M8-C；没有正式主机外仓库和生产候选演练时不宣称已部署备份。
