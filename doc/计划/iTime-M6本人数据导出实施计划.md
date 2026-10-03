# iTime M6 本人数据导出实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付本人可在设置页创建、离页等待并下载的完整 JSON + 分类 CSV/ICS/Markdown 加密数据包，并通过真实 PostgreSQL、HTTP、独立 worker 与多视口 Chromium 验证。

**Architecture:** 新建 `itime.data_export` 领域模块，用 PostgreSQL 作业表保存队列、租约、终态和短期加密 ZIP。worker 在 `REPEATABLE READ READ ONLY` 事务中读取显式白名单的一致性快照，事务外生成多格式归档并加密；API 只暴露本人作业元数据和认证下载，Web `/account/data` 恢复和轮询服务端状态。

**Tech Stack:** Python 3.13、FastAPI、SQLAlchemy 2、PostgreSQL 18、Pydantic 2、cryptography/Fernet、icalendar、ZIP/CSV 标准库、Vue 3、TypeScript、Vite、Lucide、pytest、Playwright Chromium。

---

## 文件结构

新增服务端文件：

- `server/src/itime/data_export/__init__.py`：本人数据导出领域包入口。
- `server/src/itime/data_export/models.py`：持久化导出作业。
- `server/src/itime/data_export/schemas.py`：创建请求和状态类型。
- `server/src/itime/data_export/snapshot.py`：显式白名单、一致性快照和敏感字段排除。
- `server/src/itime/data_export/archive.py`：JSON、CSV、ICS、Markdown、清单和 ZIP 构建。
- `server/src/itime/data_export/service.py`：作业创建、领取、续租、终态、下载和清理。
- `server/src/itime/data_export/router.py`：本人作业列表、详情、创建和下载 API。
- `server/src/itime/data_export/worker.py`：导出生成与到期清理进程。
- `server/src/itime/migrations/versions/0027_data_exports.py`：作业表、约束和索引。

新增服务端测试：

- `server/tests/test_data_export_models.py`：迁移、约束、活动唯一和级联。
- `server/tests/test_data_export_snapshot.py`：全分类快照、所有权和敏感字段排除。
- `server/tests/test_data_export_archive.py`：格式、摘要、公式注入、ICS 和容量。
- `server/tests/test_data_export_worker.py`：双 worker、租约、重试、加密和清理。
- `server/tests/test_data_export_api.py`：真实 HTTP 创建、恢复、下载、过期和越权。

新增 Web 文件：

- `web/src/lib/data-exports.ts`：作业类型、API、认证 Blob 下载和文件名解析。
- `web/src/components/DataExportSettings.vue`：生成、轮询、历史、下载和错误恢复。
- `web/e2e/data-exports.spec.ts`：真实 worker 生成、下载解包、状态与多视口验证。

修改的共用文件：

- `server/src/itime/config.py`
- `server/src/itime/main.py`
- `scripts/bootstrap.py`
- `.env.example`
- `server/tests/test_planning_models.py`
- `server/tests/web_environment.py`
- `web/src/main.ts`
- `web/src/pages/AccountPage.vue`
- `web/src/style.css`
- `README.md`
- `doc/README.md`
- `designer.md`
- `doc/需求/iTime-需求追踪矩阵.md`
- `doc/设计/iTime-详细设计文档.md`
- `doc/计划/iTime-开发总计划.md`
- `doc/测试/iTime-测试方案与测试用例.md`
- `doc/验收/iTime-产品验收清单.md`
- `doc/验收/iTime-Web实施与验证.md`

### Task 1: 持久化导出作业与独立密钥

**Files:**
- Create: `server/src/itime/data_export/__init__.py`
- Create: `server/src/itime/data_export/models.py`
- Create: `server/src/itime/data_export/schemas.py`
- Create: `server/src/itime/migrations/versions/0027_data_exports.py`
- Modify: `server/src/itime/config.py`
- Modify: `scripts/bootstrap.py`
- Modify: `.env.example`
- Modify: `server/tests/test_planning_models.py`
- Test: `server/tests/test_data_export_models.py`

- [ ] **Step 1: 写迁移和配置失败测试**

测试迁移 head、升级/回退、活动作业唯一、状态字段一致性、用户删除级联，以及生产环境拒绝复用任一已有密钥：

```python
def test_export_key_must_be_distinct_in_production(settings_kwargs):
    with pytest.raises(ValidationError):
        Settings(
            **settings_kwargs,
            environment="production",
            public_origin="https://itime.example",
            ai_encryption_key=settings_kwargs["encryption_key"],
            calendar_subscription_encryption_key=settings_kwargs["calendar_key"],
            export_encryption_key=settings_kwargs["encryption_key"],
        )
```

Run: `cd server && uv run pytest -q tests/test_data_export_models.py tests/test_planning_models.py`

Expected: FAIL，迁移和模型尚不存在，迁移 head 仍是 `0026_review_memory`。

- [ ] **Step 2: 定义配置**

在 `Settings` 增加：

```python
export_encryption_key: SecretStr | None = None
export_job_lease_seconds: int = Field(default=120, ge=30, le=900)
export_artifact_retention_hours: int = Field(default=24, ge=1, le=168)
export_job_retention_days: int = Field(default=30, ge=1, le=90)
export_max_uncompressed_bytes: int = Field(
    default=100 * 1024 * 1024,
    ge=1024 * 1024,
    le=500 * 1024 * 1024,
)
export_max_archive_bytes: int = Field(
    default=50 * 1024 * 1024,
    ge=1024 * 1024,
    le=250 * 1024 * 1024,
)
```

把 `export_encryption_key` 加入 Fernet 校验；生产环境要求它与基础、AI 和订阅密钥均不同。
`bootstrap.py` 生成独立值，`.env.example` 仅记录配置名和边界，不写真实密钥。

- [ ] **Step 3: 定义模型和迁移**

`DataExportJob` 必须包含：

```python
class DataExportJob(Base):
    __tablename__ = "data_export_jobs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    status: Mapped[str] = mapped_column(String(12), default="queued")
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_id: Mapped[UUID | None]
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    snapshot_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    artifact_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    artifact_sha256: Mapped[str | None] = mapped_column(String(64))
    artifact_byte_size: Mapped[int | None] = mapped_column(BigInteger)
    filename: Mapped[str | None] = mapped_column(String(100))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

迁移增加：

- `status`、租约、终态、产物和错误配对检查。
- `UNIQUE (id, owner_id)`。
- `owner_id WHERE status IN ('queued','running')` 部分唯一索引。
- `available_at/status` 领取索引、`expires_at` 产物清理索引和 `finished_at` 元数据清理索引。

- [ ] **Step 4: 运行迁移、模型和静态检查**

Run:

```bash
cd server
uv run pytest -q tests/test_data_export_models.py tests/test_planning_models.py
uv run alembic upgrade head
uv run alembic current
uv run alembic check
uv run ruff check src/itime/data_export tests/test_data_export_models.py
```

Expected: PASS；迁移 head 为 `0027_data_exports`，无 ORM 漂移。

- [ ] **Step 5: 提交**

```bash
git add .env.example scripts/bootstrap.py server/src/itime/config.py \
  server/src/itime/data_export server/src/itime/migrations/versions/0027_data_exports.py \
  server/tests/test_data_export_models.py server/tests/test_planning_models.py
git commit -m "feat: 持久化本人数据导出作业"
```

### Task 2: 构建本人数据一致性快照

**Files:**
- Create: `server/src/itime/data_export/snapshot.py`
- Test: `server/tests/test_data_export_snapshot.py`

- [ ] **Step 1: 写全分类和敏感字段失败测试**

在一个账号中创建任务、日历、会话、规划、执行、复盘、记忆和额度数据，在第二账号中创建同名
数据。断言导出只包含第一个账号，并执行递归字段扫描：

```python
FORBIDDEN_KEYS = {
    "password_hash", "token_hash", "csrf_token", "api_key_ciphertext",
    "encrypted_content", "encrypted_url", "url_hmac", "lease_id",
    "lease_until", "request_body", "response_body", "provider_request_id",
}

payload = await collect_export_snapshot(connection, owner_id, settings)
assert set(payload) == {
    "account", "settings", "tasks", "calendar", "conversations",
    "planning", "execution", "reviews", "quota", "media",
}
assert not recursive_keys(payload) & FORBIDDEN_KEYS
assert other_owner_marker not in json.dumps(payload, ensure_ascii=False)
```

同时验证：

- 回收站任务/困难/记忆仍含未清理正文，purged 墓碑不恢复正文。
- 订阅 URL 解密后只出现在 `calendar.subscriptions[].url`。
- 设备不含 token，安全事件不含其他账号。
- AI job 仅保留用户可见状态；技术日志、质量样本、provider 成本完全不查询。

Run: `cd server && uv run pytest -q tests/test_data_export_snapshot.py`

Expected: FAIL，快照模块尚不存在。

- [ ] **Step 2: 定义显式白名单与通用值编码**

```python
def export_value(value: object) -> JSONValue:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, (date, time, UUID)):
        return value.isoformat() if not isinstance(value, UUID) else str(value)
    if isinstance(value, Decimal):
        return str(value)
    if value is None or isinstance(value, (str, int, bool, float)):
        return value
    if isinstance(value, list):
        return [export_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): export_value(item) for key, item in value.items()}
    raise TypeError(f"unsupported export value: {type(value).__name__}")
```

每类表用固定 `select(Model.column, ...)` 和固定键名；不得使用 `table.columns`、`vars(model)` 或
自动反射，防止未来新增敏感字段自动泄露。无 `owner_id` 的子表通过父表 join 限制。

- [ ] **Step 3: 实现只读一致性事务**

```python
async def read_consistent_snapshot(
    database: Database,
    owner_id: UUID,
    settings: Settings,
) -> ExportSnapshot:
    async with database.engine.connect() as raw:
        connection = await raw.execution_options(
            isolation_level="REPEATABLE READ",
        )
        async with connection.begin():
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            snapshot_at = await connection.scalar(
                text("SELECT transaction_timestamp()")
            )
            return await collect_export_snapshot(
                connection,
                owner_id,
                settings,
                snapshot_at=snapshot_at,
            )
```

快照顶层固定 10 个分类和 `snapshot_at/schema_version`。查询按业务顺序和稳定 ID 排序；
订阅 URL 调用 `decrypt_subscription_url()`，解密失败向 worker 返回确定性错误。

- [ ] **Step 4: 验证一致性和并发写**

在测试中用两连接协调：快照事务读取第一分类后，另一连接提交新任务，再继续读取剩余分类。
断言新任务不进入本次结果，下一次快照可见。

Run:

```bash
cd server
uv run pytest -q tests/test_data_export_snapshot.py
uv run ruff check src/itime/data_export/snapshot.py tests/test_data_export_snapshot.py
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add server/src/itime/data_export/snapshot.py server/tests/test_data_export_snapshot.py
git commit -m "feat: 构建本人数据一致性导出快照"
```

### Task 3: 生成 JSON、CSV、ICS 和 Markdown 归档

**Files:**
- Create: `server/src/itime/data_export/archive.py`
- Test: `server/tests/test_data_export_archive.py`

- [ ] **Step 1: 写归档合同失败测试**

测试固定文件清单、UTF-8、JSON 结构、CSV BOM/引用/公式注入、ICS 可由 `icalendar.Calendar`
解析、Markdown 转义、每文件 SHA 和容量上限：

```python
archive = build_export_archive(snapshot, export_id, limits)
with ZipFile(BytesIO(archive.content)) as package:
    assert package.namelist() == EXPECTED_EXPORT_PATHS
    manifest = json.loads(package.read("manifest.json"))
    assert sha256(package.read("itime-data.json")).hexdigest() == (
        manifest["files"]["itime-data.json"]["sha256"]
    )
    assert package.read("csv/tasks.csv").startswith(codecs.BOM_UTF8)
    assert Calendar.from_ical(package.read("calendar/itime.ics"))
```

构造标题 `=HYPERLINK("https://example.com")`，断言 CSV 单元格前有单引号，但 JSON/Markdown
保留用户原文语义。

Run: `cd server && uv run pytest -q tests/test_data_export_archive.py`

Expected: FAIL，归档构建器尚不存在。

- [ ] **Step 2: 实现稳定 JSON、CSV 和清单**

固定：

```python
EXPECTED_EXPORT_PATHS = (
    "README.txt",
    "itime-data.json",
    "csv/projects.csv",
    "csv/tasks.csv",
    "csv/task-steps.csv",
    "csv/task-occurrences.csv",
    "csv/calendar-events.csv",
    "csv/plan-blocks.csv",
    "csv/focus-sessions.csv",
    "csv/difficulties.csv",
    "csv/quota-ledger.csv",
    "calendar/itime.ics",
    "markdown/conversations.md",
    "markdown/reviews.md",
    "markdown/memories.md",
    "manifest.json",
)
```

先生成除 `manifest.json` 外的条目和记录数，再生成清单。CSV 使用 `csv.DictWriter` 和
`newline=""`；所有字符串先调用：

```python
def csv_cell(value: object) -> str:
    rendered = value if isinstance(value, str) else stable_json(value)
    return f"'{rendered}" if rendered.startswith(("=", "+", "-", "@")) else rendered
```

- [ ] **Step 3: 实现 ICS 和 Markdown**

ICS 使用结构化 `icalendar.Calendar/Event`。任务截止、当前计划块、日历系列和订阅事件使用不同
`X-ITIME-KIND`；标准重复输出 RRULE，课程指定周输出 RDATE，取消/移动使用 EXDATE、
RECURRENCE-ID 或独立事件。无法转换的条目写入 `manifest.warnings`，原结构仍在 JSON。

Markdown 对 `\ # * _ [ ] < >` 做结构转义，按稳定顺序输出：

```python
def markdown_heading(level: int, value: str) -> str:
    return f"{'#' * level} {escape_markdown(value)}"
```

- [ ] **Step 4: 实现确定性 ZIP 与容量检查**

使用固定条目路径和 `snapshot_at` 的 ZIP 时间。每加入一个明文条目前累计
`export_max_uncompressed_bytes`；ZIP 完成后检查 `export_max_archive_bytes`。任何超限抛出
`ExportBuildError("data_export_too_large", ...)`，不返回部分内容。

Run:

```bash
cd server
uv run pytest -q tests/test_data_export_archive.py
uv run ruff check src/itime/data_export/archive.py tests/test_data_export_archive.py
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add server/src/itime/data_export/archive.py server/tests/test_data_export_archive.py
git commit -m "feat: 生成多格式本人数据导出包"
```

### Task 4: 实现租约 worker、加密终态和清理

**Files:**
- Create: `server/src/itime/data_export/service.py`
- Create: `server/src/itime/data_export/worker.py`
- Test: `server/tests/test_data_export_worker.py`

- [ ] **Step 1: 写领取、恢复和清理失败测试**

覆盖：

- 两个连接同时 `claim_next_export()` 只有一个得到作业。
- 运行中有效租约不被第二 worker 领取；失效租约增加尝试并重领。
- 临时异常在次数内回到 `queued`，确定性错误和次数耗尽进入 `failed`。
- 构建期间续租；租约被替换后晚到结果不能写入。
- 成功 ZIP 以导出密钥加密，数据库和状态 API 中都不存在明文标记。
- 24 小时后清空密文并标记 `expired`；30 天后删除元数据；双清理 `SKIP LOCKED` 不重复。
- `python -m itime.data_export.worker --once` 可在独立进程完成一个作业。

Run: `cd server && uv run pytest -q tests/test_data_export_worker.py`

Expected: FAIL，作业服务和 worker 尚不存在。

- [ ] **Step 2: 实现领取与续租**

```python
@dataclass(frozen=True)
class ClaimedExport:
    id: UUID
    owner_id: UUID
    lease_id: UUID
    attempt_number: int

async def claim_next_export(
    database: Database,
    *,
    lease_seconds: int,
) -> ClaimedExport | None:
    ...

async def renew_export_lease(
    database: Database,
    claim: ClaimedExport,
    *,
    lease_seconds: int,
) -> bool:
    ...
```

查询只领取 `queued AND available_at <= now()` 或租约已过期的 `running`；按
`available_at, created_at, id` 排序，使用 `FOR UPDATE SKIP LOCKED`。

- [ ] **Step 3: 实现生成与终态**

```python
async def process_one(database: Database, settings: Settings) -> bool:
    claim = await claim_next_export(
        database,
        lease_seconds=settings.export_job_lease_seconds,
    )
    if claim is None:
        return False
    snapshot = await read_consistent_snapshot(
        database,
        claim.owner_id,
        settings,
    )
    archive = await asyncio.to_thread(
        build_export_archive,
        snapshot,
        claim.id,
        ExportLimits.from_settings(settings),
    )
    ciphertext = await asyncio.to_thread(
        export_cipher(settings).encrypt,
        archive.content,
    )
    await complete_export(database, settings, claim, snapshot, archive, ciphertext)
    return True
```

捕获 `ExportBuildError`、订阅 URL 解密错误和不可重试数据错误后写稳定失败码；未知系统异常在剩余
次数内延迟重试，日志只记录 `job_id/attempt/error_type`。

- [ ] **Step 4: 实现到期清理**

`cleanup_expired_exports()` 每批最多 100 个到期成功作业，行锁后清空密文并标记 `expired`；
第二批删除 30 天前的 `failed/expired` 元数据。worker 空闲时每分钟清理一次，`--once`
先处理一个作业，再执行一批清理。

Run:

```bash
cd server
uv run pytest -q tests/test_data_export_worker.py
uv run python -m itime.data_export.worker --once
uv run ruff check src/itime/data_export tests/test_data_export_worker.py
```

Expected: PASS；空队列运行一次正常退出。

- [ ] **Step 5: 提交**

```bash
git add server/src/itime/data_export/service.py \
  server/src/itime/data_export/worker.py server/tests/test_data_export_worker.py
git commit -m "feat: 异步生成并清理本人数据导出"
```

### Task 5: 提供本人作业与认证下载 API

**Files:**
- Create: `server/src/itime/data_export/router.py`
- Modify: `server/src/itime/main.py`
- Test: `server/tests/test_data_export_api.py`

- [ ] **Step 1: 写真实 HTTP 失败测试**

覆盖未登录、缺 CSRF、创建、同 operation 重放、相同 operation 不同内容冲突、活动作业复用、
最近 20 条、单作业、非本人统一 404、未完成/失败/过期下载、成功下载头与 ZIP 摘要、密文损坏、
会话撤销后下载拒绝。

```python
created = client.post(
    "/api/v1/data-exports",
    headers={"X-CSRF-Token": csrf},
    json={"operation_id": str(uuid4())},
)
assert created.status_code == 202
assert created.json()["job"]["status"] == "queued"
```

Run: `cd server && uv run pytest -q tests/test_data_export_api.py`

Expected: FAIL，路由尚不存在。

- [ ] **Step 2: 实现创建、列表和详情**

创建在 `authorized(write=True)` 的事务中先重放操作，再锁定用户并查询活动作业。新作业和
`UserOperation` 同事务提交；已有活动作业返回 `reused=true`。列表查询禁止选择
`artifact_ciphertext`。

统一公开结果：

```python
def export_job_result(job: DataExportJob) -> dict:
    return {
        "id": job.id,
        "status": effective_status(job),
        "schema_version": job.schema_version,
        "attempt_count": job.attempt_count,
        "snapshot_at": job.snapshot_at,
        "filename": job.filename,
        "byte_size": job.artifact_byte_size,
        "sha256": job.artifact_sha256,
        "expires_at": job.expires_at,
        "error": (
            {"code": job.error_code, "message": job.error_message}
            if job.error_code else None
        ),
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "finished_at": job.finished_at,
    }
```

读取时发现产物已到期，先在本人写事务中惰性清理，再返回 `expired`，避免定时 worker 延迟造成
过期文件仍可下载。

- [ ] **Step 3: 实现认证下载**

只按 `id + owner_id` 查询成功且未过期作业，解密后校验字节数和 SHA。返回 `Response`，
包含设计中的 `Content-Type/Disposition/Length/Digest/Cache-Control`；文件名来自服务端固定
格式，不接受请求输入。

Run:

```bash
cd server
uv run pytest -q tests/test_data_export_api.py
uv run pytest -q tests/test_auth.py tests/test_privacy_settings.py tests/test_quota_api.py
uv run ruff check src/itime/data_export tests/test_data_export_api.py
```

Expected: PASS。

- [ ] **Step 4: 提交**

```bash
git add server/src/itime/data_export/router.py server/src/itime/main.py \
  server/tests/test_data_export_api.py
git commit -m "feat: 提供本人数据导出接口"
```

### Task 6: 构建设置页数据导出工作区

**Files:**
- Create: `web/src/lib/data-exports.ts`
- Create: `web/src/components/DataExportSettings.vue`
- Modify: `web/src/main.ts`
- Modify: `web/src/pages/AccountPage.vue`
- Modify: `web/src/style.css`

- [ ] **Step 1: 定义客户端合同和下载**

```typescript
export type DataExportStatus =
  | 'queued' | 'running' | 'succeeded' | 'failed' | 'expired'

export interface DataExportJob {
  id: string
  status: DataExportStatus
  schema_version: number
  attempt_count: number
  snapshot_at: string | null
  filename: string | null
  byte_size: number | null
  sha256: string | null
  expires_at: string | null
  error: { code: string; message: string } | null
  created_at: string
  updated_at: string
  finished_at: string | null
}
```

`downloadDataExport()` 使用同源 `fetch`、`credentials: same-origin`、`cache: no-store` 和
15 秒超时；非 2xx 解析统一错误 JSON，成功返回 Blob 和响应头文件名。调用方创建 object URL，
点击隐藏 `<a download>` 后立即 revoke。

- [ ] **Step 2: 实现 `/account/data` 路由和导航**

`main.ts` 的账号 section 正则加入 `data`。`AccountPage.vue`：

- 标题映射新增“数据管理”。
- 导航使用 Lucide `Database` 或 `ArchiveRestore` 图标。
- 账号概览增加“导出个人数据”入口。
- `section === 'data'` 渲染 `DataExportSettings`。

- [ ] **Step 3: 实现状态恢复和交互**

组件挂载读取最近 20 条；活动作业每 2 秒轮询，页面隐藏时 5 秒，终态立即停止。创建按钮只在
无活动作业时可用，并复用 operation ID 处理未知结果：

```typescript
const fingerprint = active.value ? active.value.id : 'complete-export-v1'
if (pendingFingerprint.value !== fingerprint) {
  pendingFingerprint.value = fingerprint
  pendingOperationId.value = crypto.randomUUID()
}
```

未知保存后先重新读取列表；若出现活动作业即视为已保存。下载前刷新详情，过期则显示重新生成。
失去认证调用 `refreshSession()`，不把错误吞成普通失败。

- [ ] **Step 4: 完成响应式和可访问状态**

使用无嵌套卡片的分区列表、固定状态标签、图标按钮 tooltip 和可换行操作区。覆盖 320px：

```css
.data-export-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 16px;
  padding: 20px 0;
  border-bottom: 1px solid var(--border);
}
@media (max-width: 520px) {
  .data-export-row { grid-template-columns: minmax(0, 1fr); }
  .data-export-actions { justify-content: flex-start; }
}
```

状态用 `role=status/alert`，时间说明设备时区，按钮文本不使用“点击这里”。完成后执行：

Run: `cd web && pnpm build`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add web/src/lib/data-exports.ts web/src/components/DataExportSettings.vue \
  web/src/main.ts web/src/pages/AccountPage.vue web/src/style.css
git commit -m "feat: 构建本人数据导出工作区"
```

### Task 7: 验证真实浏览器下载与恢复链路

**Files:**
- Create: `web/e2e/data-exports.spec.ts`
- Modify: `server/tests/web_environment.py`

- [ ] **Step 1: 在隔离环境启动导出 worker**

与 mail/AI/subscription worker 相同，由 `ExitStack` 管理：

```python
export_process = stack.enter_context(process(
    [sys.executable, "-m", "itime.data_export.worker"],
    SERVER,
    env,
    logs / "data-export.log",
))
```

把 `export_process` 加入存活检查；测试结束由现有上下文终止，不遗留进程或随机数据库。

- [ ] **Step 2: 写真实数据和下载 E2E**

每个视口创建真实账号，并通过 API 建立任务、日历、对话、计划、专注、困难、复盘和记忆中的
代表数据。进入 `/account/data` 创建导出，等待状态成功，监听 `download`，读取 ZIP：

```typescript
const downloadPromise = page.waitForEvent('download')
await page.getByRole('button', { name: '下载 ZIP', exact: true }).click()
const download = await downloadPromise
expect(download.suggestedFilename()).toMatch(
  /^itime-export-\\d{8}-\\d{6}Z\\.zip$/,
)
```

使用 Node ZIP 读取库或系统 `unzip -p` 校验 `manifest.json`、`itime-data.json` 和标记文本；
不得只断言按钮出现。

- [ ] **Step 3: 覆盖失败、恢复和布局**

覆盖：

- 刷新和离页后恢复同一活动作业，重复点击不创建第二个。
- 路由注入一次 503 后保留历史并可重试。
- 过期元数据显示重新生成，不显示无效下载。
- 下载时会话失效展示重新登录，不留下 object URL。
- desktop/mobile/tablet、320px 最大字号深色、键盘、粗指针、axe、控制台、API 500 和横向溢出。

Run:

```bash
cd web
pnpm build
pnpm exec playwright test e2e/data-exports.spec.ts
pnpm exec playwright test e2e/auth.spec.ts e2e/privacy-governance.spec.ts \
  e2e/quota.spec.ts e2e/data-exports.spec.ts
```

Expected: 全部 Chromium projects 通过。

- [ ] **Step 4: 人工检查截图并提交**

截图保存到：

```text
/tmp/itime-ui-evidence/desktop-data-export-ready.png
/tmp/itime-ui-evidence/mobile-data-export-ready.png
/tmp/itime-ui-evidence/tablet-data-export-ready.png
/tmp/itime-ui-evidence/mobile-data-export-320-large-dark.png
```

使用图像查看工具确认状态、文件信息、操作区和长错误文本不重叠或裁切。

```bash
git add web/e2e/data-exports.spec.ts server/tests/web_environment.py
git commit -m "test: 验证本人数据导出真实链路"
```

### Task 8: 全量回归并同步交付证据

**Files:**
- Modify: `README.md`
- Modify: `doc/README.md`
- Modify: `designer.md`
- Modify: `doc/需求/iTime-需求追踪矩阵.md`
- Modify: `doc/设计/iTime-详细设计文档.md`
- Modify: `doc/计划/iTime-开发总计划.md`
- Modify: `doc/测试/iTime-测试方案与测试用例.md`
- Modify: `doc/验收/iTime-产品验收清单.md`
- Modify: `doc/验收/iTime-Web实施与验证.md`
- Modify: `doc/计划/iTime-M6本人数据导出实施计划.md`

- [ ] **Step 1: 执行服务端专项和全量质量门禁**

Run:

```bash
cd server
uv run pytest -q tests/test_data_export_models.py tests/test_data_export_snapshot.py \
  tests/test_data_export_archive.py tests/test_data_export_worker.py \
  tests/test_data_export_api.py
uv run ruff check .
uv run pytest -q
uv run alembic upgrade head
uv run alembic current
uv run alembic check
uv build --no-sources
```

Expected: 全部通过；迁移 head 为 `0027_data_exports` 且无模型漂移。

- [ ] **Step 2: 执行 Web 全量质量门禁**

Run:

```bash
cd web
pnpm build
pnpm test:e2e
```

Expected: 生产构建与全部 Chromium projects 通过。

- [ ] **Step 3: 检查运行状态和清理**

重启开发 API 和导出 worker 后验证：

```bash
curl -fsS http://127.0.0.1:8000/api/ready
curl -fsSI http://localhost:5173/account/data
docker --context colima-itime compose --env-file .env.local exec -T postgres \
  psql -U itime -d postgres -tAc \
  "SELECT count(*) FROM pg_database WHERE datname LIKE 'itime_test_%';"
```

Expected: API ready、页面 200、随机测试数据库数量为 0。

- [ ] **Step 4: 同步实际实现和证据**

文档只记录真实执行结果：

- README 和开发总计划把本人导出改为已有实现，并补导出 worker 启动命令。
- 追踪矩阵为 `REQ-EXPORT-01` 填入实际模型、路由、worker、页面和专项证据。
- 详细设计补充实际生命周期与 API；测试方案注明 `PRIV-04` 已覆盖范围。
- 验收清单、designer 和实施记录写入真实行为、命令、通过数、耗时及截图。
- 通用同步、账号注销、其他对象回收、Edge/真机、性能和生产部署继续明确为未完成。

- [ ] **Step 5: 检查暂存范围并提交证据**

Run:

```bash
git diff --check
git status --short
git diff --cached --name-only
```

确认无 `.env`、导出 ZIP、解压内容、凭据、临时截图、Playwright 输出或测试数据库文件进入暂存。

```bash
git add README.md doc/README.md designer.md \
  doc/需求/iTime-需求追踪矩阵.md doc/设计/iTime-详细设计文档.md \
  doc/计划/iTime-开发总计划.md doc/计划/iTime-M6本人数据导出实施计划.md \
  doc/测试/iTime-测试方案与测试用例.md \
  doc/验收/iTime-产品验收清单.md doc/验收/iTime-Web实施与验证.md
git commit -m "docs: 记录本人数据导出实施证据"
```
