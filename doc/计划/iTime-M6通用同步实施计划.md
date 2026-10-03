# iTime M6 通用同步实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付账号隔离的增量同步、跨标签/跨设备页面更新、已认证页面离线读取、任务步骤与现有专注操作离线重放，以及可见的同步状态和冲突恢复。

**Architecture:** 复用 PostgreSQL `change_events` 作为无正文失效日志，以账号绑定 HMAC 游标分页读取；所有写入继续走现有类型化领域 API 和 `UserOperation`，不增加通用 mutation 解释器。Vue 单例协调器在可见页轮询并向各工作区派发批量失效事件，IndexedDB 保存账号隔离的 GET 快照，类型化本地队列只覆盖任务步骤和现有专注操作。

**Tech Stack:** Python 3.13、FastAPI、SQLAlchemy 2、PostgreSQL 18、Alembic、Pydantic 2、HMAC-SHA256、Vue 3、TypeScript、IndexedDB、BroadcastChannel、Playwright Chromium、pytest。

---

## 文件结构

新增服务端文件：

- `server/src/itime/sync/__init__.py`：同步领域包入口。
- `server/src/itime/sync/models.py`：每账号事件保留水位。
- `server/src/itime/sync/cursor.py`：账号绑定不透明游标签发与校验。
- `server/src/itime/sync/schemas.py`：步骤完成和实例步骤完成输入。
- `server/src/itime/sync/service.py`：增量读取、清理及安全步骤合并。
- `server/src/itime/sync/router.py`：同步状态、变更和步骤写入 API。
- `server/src/itime/sync/cleanup_worker.py`：事件保留期清理进程。
- `server/src/itime/migrations/versions/0028_sync_feed.py`：同步状态、用户触发器和事件类型约束。

新增服务端测试：

- `server/tests/test_sync_models.py`：迁移、约束、账号同步状态和配置。
- `server/tests/test_sync_feed.py`：游标、分页、隔离、清理竞争和恢复。
- `server/tests/test_sync_event_coverage.py`：会话及既有领域事件覆盖。
- `server/tests/test_sync_task_completion.py`：普通/重复任务步骤安全收敛。

新增 Web 文件：

- `web/src/lib/data-cache.ts`：账号隔离 IndexedDB GET 快照。
- `web/src/lib/sync.ts`：轮询、跨标签广播、状态与页面事件派发。
- `web/src/lib/sync-queue.ts`：任务步骤类型化离线队列。
- `web/src/lib/use-sync-refresh.ts`：页面事件筛选与去抖。
- `web/src/components/SyncStatusIndicator.vue`：主导航同步状态图标。
- `web/src/components/SyncSettings.vue`：同步状态、队列和冲突工作区。
- `web/e2e/sync.spec.ts`：双上下文、断网、缓存、重放和响应式测试。

主要修改：

- `.env.example`
- `README.md`
- `scripts/bootstrap.py`
- `server/src/itime/config.py`
- `server/src/itime/main.py`
- `server/src/itime/migrations/env.py`
- `server/src/itime/tasks/models.py`
- `server/src/itime/tasks/router.py`
- `server/src/itime/tasks/progress_service.py`
- `server/src/itime/ai/conversation_router.py`
- `server/src/itime/ai/conversation_service.py`
- `server/tests/test_planning_models.py`
- `server/tests/web_environment.py`
- `web/src/App.vue`
- `web/src/main.ts`
- `web/src/lib/api.ts`
- `web/src/lib/auth.ts`
- `web/src/lib/focus-queue.ts`
- `web/src/pages/AccountPage.vue`
- `web/src/pages/AIPage.vue`
- `web/src/pages/TasksPage.vue`
- `web/src/pages/PlansPage.vue`
- `web/src/pages/CalendarPage.vue`
- `web/src/pages/FocusPage.vue`
- `web/src/pages/ReviewsPage.vue`
- `web/src/pages/StatisticsPage.vue`
- `web/src/components/ProductNavigation.vue`
- `web/src/components/TaskEditor.vue`
- `web/src/components/CalendarOccurrencePanel.vue`
- `web/src/style.css`
- 需求、设计、计划、测试与验收文档。

### Task 1: 建立同步状态、事件约束与保留配置

**Files:**
- Create: `server/src/itime/sync/__init__.py`
- Create: `server/src/itime/sync/models.py`
- Create: `server/src/itime/migrations/versions/0028_sync_feed.py`
- Modify: `server/src/itime/tasks/models.py`
- Modify: `server/src/itime/config.py`
- Modify: `.env.example`
- Modify: `server/src/itime/migrations/env.py`
- Modify: `server/tests/test_planning_models.py`
- Test: `server/tests/test_sync_models.py`

- [x] **Step 1: 写迁移和配置失败测试**

按现有集成测试模式在本文件定义 `create_account()`，通过公开注册、测试库邮箱验证和登录返回
`(email, user_id)`。测试内容：

```python
def test_sync_state_trigger_for_new_user(client, database_url):
    _, user_id = create_account(client, database_url)
    with psycopg.connect(psycopg_url(database_url)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM sync_states WHERE owner_id=%s",
            (user_id,),
        ).fetchone()[0] == 1
        connection.execute(
            """
            INSERT INTO change_events (
                owner_id, entity_type, entity_id, action, entity_version
            ) VALUES (%s, 'conversation', %s, 'updated', 1)
            """,
            (user_id, uuid4()),
        )


def test_sync_retention_configuration(settings_kwargs):
    assert Settings(**settings_kwargs).sync_event_retention_days == 30
    with pytest.raises(ValidationError):
        Settings(**settings_kwargs, sync_event_retention_days=6)
```

同时断言迁移前既有用户被回填、用户删除级联、`discarded_through_sequence >= 0`、降级会拒绝仍
有 `conversation` 事件的数据，且 ORM `ChangeEvent` 约束包含数据库当前全部类型。

Run:

```bash
cd server
uv run pytest -q tests/test_sync_models.py tests/test_planning_models.py
```

Expected: FAIL，`0028_sync_feed` 和 `SyncState` 尚不存在。

- [x] **Step 2: 新增配置和模型**

`Settings` 增加：

```python
sync_event_retention_days: int = Field(default=30, ge=7, le=90)
```

`SyncState`：

```python
class SyncState(Base):
    __tablename__ = "sync_states"
    __table_args__ = (
        CheckConstraint(
            "discarded_through_sequence >= 0",
            name="discarded_through_sequence",
        ),
    )

    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    discarded_through_sequence: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        server_default="0",
    )
    last_cleanup_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
```

`.env.example` 增加 `ITIME_SYNC_EVENT_RETENTION_DAYS=30`；无新密钥。

- [x] **Step 3: 实现迁移和用户触发器**

迁移必须：

1. 创建并回填 `sync_states`。
2. 创建 `itime_create_sync_state()` 与 `users_sync_state_after_insert`，保证所有用户创建路径一致。
3. 把 `change_events.entity_type` 约束更新为数据库现有全部类型并增加 `conversation`。
4. 降级前检查 `conversation` 事件；存在时明确拒绝无损降级。
5. 降级删除触发器、函数和同步状态，并恢复 `0027` 约束。

`server/src/itime/tasks/models.py` 使用与迁移完全一致的实体类型字符串，修复此前 ORM 未列出
复盘和记忆类型的漂移。

- [x] **Step 4: 运行迁移、模型和静态检查**

```bash
cd server
uv run pytest -q tests/test_sync_models.py tests/test_planning_models.py
uv run alembic upgrade head
uv run alembic current
uv run alembic check
uv run ruff check src/itime/sync src/itime/tasks/models.py tests/test_sync_models.py
```

Expected: PASS；head 为 `0028_sync_feed`，无 ORM 漂移。

- [x] **Step 5: 提交**

```bash
git add .env.example server/src/itime/config.py server/src/itime/sync \
  server/src/itime/tasks/models.py server/src/itime/migrations/env.py \
  server/src/itime/migrations/versions/0028_sync_feed.py \
  server/tests/test_sync_models.py server/tests/test_planning_models.py
git commit -m "feat: 建立通用同步事件状态"
```

### Task 2: 提供账号绑定增量游标 API

**Files:**
- Create: `server/src/itime/sync/cursor.py`
- Create: `server/src/itime/sync/service.py`
- Create: `server/src/itime/sync/router.py`
- Modify: `server/src/itime/main.py`
- Test: `server/tests/test_sync_feed.py`

- [x] **Step 1: 写游标与真实 HTTP 失败测试**

覆盖：

- 未认证拒绝。
- 无游标返回 `initial` 重建和本人当前基线。
- 两账号事件隔离；响应不含任何业务正文。
- `limit=1` 多页无重复/遗漏，空尾页游标稳定。
- 篡改、截断、另一账号游标返回 `invalid_sync_cursor`。
- 游标低于保留水位返回 `cursor_expired` 重建。
- 游标高于本人最大事件返回 `cursor_ahead` 重建。

```python
response = client.get("/api/v1/sync/changes", params={"cursor": cursor, "limit": 1})
assert response.status_code == 200
assert response.json()["events"] == [{
    "sequence": event_sequence,
    "entity_type": "task",
    "entity_id": task_id,
    "action": "updated",
    "entity_version": 2,
    "changed_at": changed_at,
}]
assert "title" not in response.text
```

Run: `cd server && uv run pytest -q tests/test_sync_feed.py`

Expected: FAIL，路由和游标签名尚不存在。

- [x] **Step 2: 实现不透明游标**

`cursor.py` 固定结构和上限：

```python
@dataclass(frozen=True)
class SyncCursor:
    owner_id: UUID
    sequence: int


def encode_sync_cursor(security: Security, cursor: SyncCursor) -> str:
    payload = json.dumps(
        {"v": 1, "owner_id": str(cursor.owner_id), "sequence": cursor.sequence},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    encoded = base64.urlsafe_b64encode(payload).rstrip(b"=")
    signature = security.keyed_digest("sync:" + encoded.decode())
    return f"{encoded.decode()}.{signature}"
```

解码必须限制长度、字段集合、版本、UUID、非负序号并常量时间校验签名；任何失败统一抛
`invalid_sync_cursor`，不回显载荷。

- [x] **Step 3: 实现一致的增量读取**

`read_change_page()` 在一个事务中：

```python
state = await session.scalar(
    select(SyncState)
    .where(SyncState.owner_id == owner_id)
    .with_for_update(read=True)
)
latest = await session.scalar(
    select(func.max(ChangeEvent.sequence))
    .where(ChangeEvent.owner_id == owner_id)
) or 0
```

- 无游标返回当前 `latest` 和 `reset_required=true`。
- `cursor.sequence < state.discarded_through_sequence` 返回 `cursor_expired`。
- `cursor.sequence > latest` 返回 `cursor_ahead`。
- 正常查询 `cursor < sequence <= latest`，按升序取 `limit + 1`。
- 有后页时下一游标为最后返回事件；无后页时为 `latest`。

响应包含 `server_time`、`next_cursor`、`has_more`、`reset_required`、`reset_reason` 和事件数组。

- [x] **Step 4: 注册路由并验证**

提供：

```python
router = APIRouter(prefix="/api/v1/sync", tags=["通用同步"])

@router.get("/changes")
async def changes(
    request: Request,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> dict: ...

@router.get("/status")
async def status(request: Request) -> dict: ...
```

`main.py` 注册路由。运行：

```bash
cd server
uv run pytest -q tests/test_sync_feed.py
uv run ruff check src/itime/sync tests/test_sync_feed.py
```

Expected: PASS。

- [x] **Step 5: 提交**

```bash
git add server/src/itime/sync server/src/itime/main.py \
  server/tests/test_sync_feed.py
git commit -m "feat: 提供本人增量同步游标"
```

### Task 3: 实现同步事件保留期清理

**Files:**
- Create: `server/src/itime/sync/cleanup_worker.py`
- Modify: `server/src/itime/sync/service.py`
- Test: `server/tests/test_sync_feed.py`

- [x] **Step 1: 写清理并发失败测试**

覆盖单批 1000 上限、未到期保留、两账号独立 floor、重复清理幂等、双 worker 不重复，以及读取
事务与清理事务竞争：

```python
assert await cleanup_expired_events(database, retention_days=30, limit=2) == 2
assert state_a.discarded_through_sequence == max(deleted_a)
assert state_b.discarded_through_sequence == max(deleted_b)
assert pull_from(old_cursor)["reset_reason"] == "cursor_expired"
```

使用两个真实连接协调共享/排他 `sync_states` 行锁，断言结果只能是完整旧页或明确重建。

Run: `cd server && uv run pytest -q tests/test_sync_feed.py -k cleanup`

Expected: FAIL，清理函数尚不存在。

- [x] **Step 2: 实现有界清理**

`cleanup_expired_events()`：

1. 按 `changed_at, sequence` 选择最多 1000 条到期行并 `FOR UPDATE SKIP LOCKED`。
2. 汇总每个 owner 的最大实际删除序号。
3. 按 owner UUID 排序锁定 `SyncState`。
4. 删除所选事件并使用 `greatest()` 推进 floor。
5. 同事务更新时间并返回删除数。

不得按一个全局最小序号推进所有账号，也不得删除未选入本批的事件。

- [x] **Step 3: 实现独立 worker**

```python
async def run(*, once: bool = False) -> None:
    settings = get_settings()
    database = Database(settings)
    try:
        while True:
            cleaned = await cleanup_expired_events(
                database,
                retention_days=settings.sync_event_retention_days,
            )
            if once:
                break
            if cleaned == 0:
                await asyncio.sleep(60)
    finally:
        await database.close()
```

未知异常仅记录类型；`--once` 返回非零并给出不含正文的稳定错误。

- [x] **Step 4: 验证并提交**

```bash
cd server
uv run pytest -q tests/test_sync_feed.py
uv run python -m itime.sync.cleanup_worker --once
uv run ruff check src/itime/sync tests/test_sync_feed.py
git add server/src/itime/sync server/tests/test_sync_feed.py
git commit -m "feat: 清理到期同步事件"
```

### Task 4: 补齐普通会话同步事件

**Files:**
- Modify: `server/src/itime/ai/conversation_router.py`
- Modify: `server/src/itime/ai/conversation_service.py`
- Test: `server/tests/test_sync_event_coverage.py`

- [x] **Step 1: 写会话事件失败测试**

真实 HTTP/worker 覆盖：

- 创建会话产生 `conversation created`。
- 改名/归档产生 `updated/archived/unarchived`，无变化不产生事件。
- 发送消息接单使会话版本递增并产生 `updated`。
- worker 成功、最终失败和用户取消各在终态版本产生一个 `updated`。
- 流式 token 不逐片创建 `ChangeEvent`。
- 业务结果与事件同事务；注入事件约束失败时业务终态回滚。

Run: `cd server && uv run pytest -q tests/test_sync_event_coverage.py`

Expected: FAIL，会话目前不发通用事件。

- [x] **Step 2: 在现有事务中发聚合事件**

每个 `conversation.version += 1` 的业务提交点紧邻调用：

```python
emit_change(
    session,
    conversation.owner_id,
    "conversation",
    conversation.id,
    action,
    conversation.version,
)
```

创建使用 `created`；归档/恢复使用 `archived/unarchived`；消息接单和终态使用 `updated`。重试中间态
不增会话版本也不发事件，避免轮询风暴。

- [x] **Step 3: 审计既有领域覆盖**

测试查询迁移约束允许的类型，并为以下代表操作逐项断言事件：

```text
task / step / task_occurrence
time_settings / term / calendar_series / calendar_source
plan / focus_session / difficulty
review_session / review_report / review_annotation
memory / memory_settings / privacy_settings / conversation
```

只检查真实业务提交产生事件，不为内部作业或导出状态伪造业务事件。

- [x] **Step 4: 回归并提交**

```bash
cd server
uv run pytest -q tests/test_sync_event_coverage.py \
  tests/test_ai_conversation_api.py tests/test_ai_conversation_worker.py
uv run ruff check src/itime/ai tests/test_sync_event_coverage.py
git add server/src/itime/ai/conversation_router.py \
  server/src/itime/ai/conversation_service.py \
  server/tests/test_sync_event_coverage.py
git commit -m "feat: 同步普通会话变更"
```

### Task 5: 提供离线步骤安全收敛 API

**Files:**
- Modify: `server/src/itime/sync/schemas.py`
- Modify: `server/src/itime/sync/service.py`
- Modify: `server/src/itime/sync/router.py`
- Modify: `server/src/itime/tasks/progress_service.py`
- Test: `server/tests/test_sync_task_completion.py`

- [x] **Step 1: 写普通和重复步骤失败测试**

输入：

```python
class StepCompletionInput(OperationInput):
    expected_task_version: int = Field(ge=1)
    base_completed: bool
    completed: bool


class OccurrenceStepCompletionInput(StepCompletionInput):
    task_id: UUID
    occurrence_key: str = Field(min_length=10, max_length=80)
    step_id: UUID
    expected_occurrence_version: int = Field(ge=0)
```

覆盖：

- 普通步骤离线勾选、取消勾选、同操作重放。
- 另一设备修改任务标题后，旧页面步骤勾选只改变布尔值并返回 `rebased`。
- 另一设备已改成目标值时返回 `converged`，不重复增版。
- 删除步骤、任务归档/回收、任务改成重复模板均 409 且不保存。
- 重复实例只修改目标实例的 `completed_step_ids`，不修改模板和其他日期。
- 实例已完成/跳过、无效 occurrence、跨账号均拒绝。

Run: `cd server && uv run pytest -q tests/test_sync_task_completion.py`

Expected: FAIL，专用同步端点尚不存在。

- [x] **Step 2: 实现普通步骤合并**

`merge_template_step_completion()` 锁定任务和步骤，复用活动任务及重复规则检查：

```python
task_rebased = task.version != data.expected_task_version
if step.completed == data.completed:
    merge = "converged"
else:
    merge = "rebased" if task_rebased else "applied"
    step.completed = data.completed
    step.completed_at = current if data.completed else None
    task.version += 1
    emit_change(session, owner_id, "step", step.id, action, task.version)
```

即使任务其他字段版本变化，也不从请求写回标题、顺序、日期或任务终态。

- [x] **Step 3: 实现 occurrence 步骤合并**

在 `tasks/progress_service.py` 增加只切换一个步骤的函数。它必须复用 occurrence 解析与步骤所有权
校验，在状态缺失时创建 `open` 投影，在状态存在时锁行并只增删目标步骤 ID。返回当前
occurrence、任务版本和 `applied/rebased/converged`。

- [x] **Step 4: 注册 API、验证并提交**

```bash
cd server
uv run pytest -q tests/test_sync_task_completion.py tests/test_task_schedules.py \
  tests/test_tasks.py tests/test_focus_sessions.py
uv run ruff check src/itime/sync src/itime/tasks tests/test_sync_task_completion.py
git add server/src/itime/sync server/src/itime/tasks/progress_service.py \
  server/tests/test_sync_task_completion.py
git commit -m "feat: 安全同步离线步骤进度"
```

### Task 6: 建立账号隔离的浏览器读取缓存

**Files:**
- Create: `web/src/lib/data-cache.ts`
- Modify: `web/src/lib/api.ts`
- Modify: `web/src/lib/auth.ts`
- Test: `web/e2e/sync.spec.ts`

- [x] **Step 1: 写缓存失败 E2E**

流程：

1. 注册登录并创建任务。
2. 在线打开任务和统计页，使 GET 响应进入 IndexedDB。
3. 只中断 `/api/v1/tasks*` 和 `/api/v1/statistics*`，保持页面应用运行。
4. 重新加载筛选/范围，断言仍显示缓存内容和“离线数据”状态。
5. 返回 HTTP 500 时断言显示错误，不回退缓存掩盖服务故障。
6. 切换账号后断言不能读取前一账号缓存。

Run:

```bash
cd web
pnpm exec playwright test e2e/sync.spec.ts --grep "offline cache"
```

Expected: FAIL，IndexedDB 缓存尚不存在。

- [x] **Step 2: 实现缓存存储**

`data-cache.ts` 暴露：

```typescript
export interface CachedResponse<T = unknown> {
  value: T
  cached_at: string
}

interface StoredResponse {
  account_id: string
  key: string
  iv: ArrayBuffer
  ciphertext: ArrayBuffer
  byte_size: number
  cached_at: string
  accessed_at: string
}

export async function setCacheAccount(accountId: string | null): Promise<void>
export function cacheableGet(path: string): boolean
export async function putCachedResponse(path: string, value: unknown): Promise<void>
export async function getCachedResponse<T>(path: string): Promise<CachedResponse<T> | null>
export async function clearAccountCache(accountId: string): Promise<void>
export async function pruneAccountCache(accountId: string): Promise<void>
```

数据库名和 store schema 固定版本。每次建立已认证 SPA 会话时用 Web Crypto 生成仅保存在内存
的 AES-GCM 密钥；store 只保存随机 IV 和密文，刷新后在线重新认证会清除不可解密旧缓存。
单项 JSON 最多 2 MiB，每账号最多 20 MiB/200 项/7 天。路径白名单只允许用户业务 GET，
明确排除 `/auth`、`/admin`、`/sync`、下载和 SSE。

- [x] **Step 3: 接入 API 和身份边界**

`api.ts`：

- 当前账号存在且 GET 成功时异步写缓存。
- 只有 fetch 网络错误或超时才读取缓存。
- 命中后派发 `itime:cache-fallback`，detail 只有路径和缓存时间。
- HTTP 4xx/5xx、无账号、不可缓存路径和缓存异常继续走原错误。

`auth.ts` 在 `acceptSession()` 异步设置账号和会话密钥；账号变化或密钥重新建立时先清理不可读
旧数据。显式清理函数在后续退出流程调用；普通网络错误不清空当前账号缓存。

- [x] **Step 4: 构建、运行 E2E 并提交**

```bash
cd web
pnpm build
pnpm exec playwright test e2e/sync.spec.ts --grep "offline cache"
git add web/src/lib/data-cache.ts web/src/lib/api.ts web/src/lib/auth.ts \
  web/e2e/sync.spec.ts
git commit -m "feat: 缓存本人最近同步数据"
```

### Task 7: 实现全局同步协调器与状态页

**Files:**
- Create: `web/src/lib/sync.ts`
- Create: `web/src/components/SyncStatusIndicator.vue`
- Create: `web/src/components/SyncSettings.vue`
- Modify: `web/src/App.vue`
- Modify: `web/src/components/ProductNavigation.vue`
- Modify: `web/src/pages/AccountPage.vue`
- Modify: `web/src/main.ts`
- Modify: `web/src/style.css`
- Test: `web/e2e/sync.spec.ts`

- [x] **Step 1: 写状态和游标失败 E2E**

覆盖初始化、两秒轮询、成功写后立即拉取、跨标签广播、隐藏页暂停、重新可见追赶、网络退避、
损坏游标重建和认证失效停止。断言 `/account/sync` 与主导航图标显示真实状态、时间和请求编号。

Run:

```bash
cd web
pnpm exec playwright test e2e/sync.spec.ts --grep "sync coordinator"
```

Expected: FAIL，协调器和页面尚不存在。

- [x] **Step 2: 实现同步客户端**

`sync.ts` 固定类型：

```typescript
export type SyncPhase =
  | 'offline' | 'connecting' | 'synced' | 'pending' | 'conflict' | 'error'

export interface SyncEvent {
  sequence: number
  entity_type: string
  entity_id: string
  action: string
  entity_version: number
  changed_at: string
}
```

实现 `startSyncCoordinator()`、`stopSyncCoordinator()`、`requestSync()`、
`discardLocalSyncData(accountId)` 和响应式 `syncStatus`。每标签游标写
`sessionStorage["itime-sync-cursor:<account>"]`；每轮最多排空 1000 事件；批量派发
`itime:sync-events`。写请求成功通过 `api.ts` 注册的 observer 立即触发并广播。

- [x] **Step 3: 实现状态入口和设置页**

`SyncStatusIndicator.vue` 使用 `CloudCheck / RefreshCw / CloudOff / TriangleAlert`，固定 44px，
状态数量用徽标；链接 `/account/sync`。

`SyncSettings.vue` 显示状态、最后成功时间、服务端时刻、缓存回退时刻、任务/专注待传与冲突，
提供“立即同步”和冲突对象入口。无 pending 时不显示危险清理动作。

路由正则和账号 section 增加 `sync`；账号概览增加“同步与离线”入口。

- [x] **Step 4: 构建、验证并提交**

```bash
cd web
pnpm build
pnpm exec playwright test e2e/sync.spec.ts --grep "sync coordinator"
git add web/src/lib/sync.ts web/src/lib/api.ts web/src/App.vue \
  web/src/components/SyncStatusIndicator.vue web/src/components/SyncSettings.vue \
  web/src/components/ProductNavigation.vue web/src/pages/AccountPage.vue \
  web/src/main.ts web/src/style.css web/e2e/sync.spec.ts
git commit -m "feat: 展示全局同步状态"
```

### Task 8: 接入页面刷新与任务步骤离线队列

**Files:**
- Create: `web/src/lib/sync-queue.ts`
- Create: `web/src/lib/use-sync-refresh.ts`
- Modify: `web/src/lib/tasks.ts`
- Modify: `web/src/lib/task-schedules.ts`
- Modify: `web/src/lib/focus-queue.ts`
- Modify: `web/src/pages/AIPage.vue`
- Modify: `web/src/pages/TasksPage.vue`
- Modify: `web/src/pages/PlansPage.vue`
- Modify: `web/src/pages/CalendarPage.vue`
- Modify: `web/src/pages/FocusPage.vue`
- Modify: `web/src/pages/ReviewsPage.vue`
- Modify: `web/src/pages/StatisticsPage.vue`
- Modify: `web/src/components/TaskEditor.vue`
- Modify: `web/src/components/CalendarOccurrencePanel.vue`
- Modify: `web/src/components/SyncSettings.vue`
- Test: `web/e2e/sync.spec.ts`

- [x] **Step 1: 写双会话和离线队列失败 E2E**

真实两个浏览器 context 登录同一账号：

- A 创建任务，B 已打开任务页在 5 秒内出现。
- A 修改日历/计划/困难/记忆，B 对应页自动刷新或在有草稿时显示远端变化。
- B 离线连续勾选普通步骤，显示待同步数量；恢复后服务端最终状态一致且队列为空。
- A 在 B 离线时修改任务标题，B 步骤重放只改变完成值，不覆盖标题。
- A 删除步骤或归档任务后，B 重放停止并保留冲突，明确丢弃后继续。
- 重复任务实例步骤只影响目标 occurrence。
- 现有专注离线队列继续计入统一状态。

Run:

```bash
cd web
pnpm exec playwright test e2e/sync.spec.ts --grep "cross-device|offline queue"
```

Expected: FAIL，页面适配器和任务队列尚不存在。

- [x] **Step 2: 实现类型化任务队列**

`sync-queue.ts` 使用判别联合：

```typescript
export type QueuedTaskOperation =
  | {
      schema_version: 1
      kind: 'task_step_completion'
      operation_id: string
      task_id: string
      step_id: string
      expected_task_version: number
      base_completed: boolean
      completed: boolean
      created_at: string
    }
  | {
      schema_version: 1
      kind: 'task_occurrence_step_completion'
      operation_id: string
      task_id: string
      occurrence_key: string
      step_id: string
      expected_task_version: number
      expected_occurrence_version: number
      base_completed: boolean
      completed: boolean
      created_at: string
    }
```

严格校验 UUID、key、布尔值、未来时钟偏移、7 天和 100 项；按账号一个 key。重放只调用两个
固定客户端函数，不能从存储读取 URL、method 或任意 body。

- [x] **Step 3: 接入任务和实例勾选**

在线先调用专用端点；网络错误或超时把完全相同操作加入队列并更新本地投影。离线直接入队。
恢复后顺序重放：

- `applied/rebased/converged` 均移除当前项并采用服务端结果。
- 401 停止并刷新身份。
- 409 保留当前及后续项，写入同步冲突。
- 存储失败或队列满不改变本地勾选。

任务完成、标题/正文、步骤增删改名、实例完成/跳过和新建对象仍要求联网。

- [x] **Step 4: 接入页面事件刷新**

`use-sync-refresh.ts`：

```typescript
export function useSyncRefresh(
  entityTypes: readonly string[],
  refresh: (events: SyncEvent[]) => Promise<void> | void,
): void
```

按设计表接入七个主工作区。任务编辑器 `dirty=true`、日历对话框打开、计划草稿修改或复盘补充
事实未提交时，只刷新安全列表并显示“其他设备有更新”；不得重置本地表单。

- [x] **Step 5: 清理账号本地数据**

`focus-queue.ts` 和 `sync-queue.ts` 增加账号总数与清理函数。所有普通用户显式退出入口：

1. 查询待传总数。
2. 非零时显示原生确认，明确这些操作不会保存。
3. 服务端退出成功后清理当前账号任务/专注队列、IndexedDB 缓存和游标。
4. 会话意外失效只停止读取；新账号登录时不能加载旧账号 namespace。

- [x] **Step 6: 构建、验证并提交**

```bash
cd web
pnpm build
pnpm exec playwright test e2e/sync.spec.ts
pnpm exec playwright test e2e/tasks.spec.ts e2e/calendar.spec.ts \
  e2e/focus.spec.ts e2e/reviews.spec.ts e2e/planning.spec.ts
git add web/src/lib web/src/pages web/src/components web/e2e/sync.spec.ts
git commit -m "feat: 同步多端页面与离线步骤"
```

### Task 9: 完成隔离环境与真实浏览器质量覆盖

**Files:**
- Modify: `server/tests/web_environment.py`
- Modify: `web/e2e/sync.spec.ts`

- [x] **Step 1: 接入同步清理 worker**

隔离 Web 环境由 `ExitStack` 启动：

```python
sync_cleanup_process = stack.enter_context(
    process(
        [sys.executable, "-m", "itime.sync.cleanup_worker"],
        env=environment,
        cwd=SERVER,
    )
)
```

把它加入存活检查；结束时由现有上下文终止，不遗留进程或随机数据库。

- [x] **Step 2: 补游标过期与本地清理场景**

测试环境提供仅挂在 `tests.support_app` 的受控接口，把当前账号旧事件改为超过保留期并执行一次
清理。浏览器断网保留任务队列，恢复后收到 `cursor_expired`，先刷新当前任务再重放；断言标题不
回退、步骤目标生效。显式退出有 pending 时取消一次、确认一次，确认后 IndexedDB、任务队列、
专注队列和 session cursor 均无该账号数据。

- [x] **Step 3: 完成多视口和人工截图**

每个 desktop/mobile/tablet 项目检查：

- 同步状态图标、状态页、待传和冲突。
- 320×700 最大字号深色。
- 键盘、粗指针、减少动效、axe、控制台、Vite 错误层、API 500 和根级横向溢出。

截图：

```text
/tmp/itime-ui-evidence/{desktop,mobile,tablet}-sync-status.png
/tmp/itime-ui-evidence/mobile-sync-offline-320-large-dark.png
/tmp/itime-ui-evidence/desktop-sync-conflict.png
```

使用图像查看工具逐张确认内容非空、状态可辨、长时间/请求编号/操作按钮不重叠。

- [x] **Step 4: 提交**

```bash
git add server/tests/web_environment.py web/e2e/sync.spec.ts
git commit -m "test: 验证通用同步真实链路"
```

### Task 10: 全量回归并同步交付证据

**Files:**
- Modify: `README.md`
- Modify: `doc/README.md`
- Modify: `designer.md`
- Modify: `doc/需求/iTime-需求追踪矩阵.md`
- Modify: `doc/设计/iTime-详细设计文档.md`
- Modify: `doc/设计/iTime-通用同步设计.md`
- Modify: `doc/计划/iTime-开发总计划.md`
- Modify: `doc/计划/iTime-M6通用同步实施计划.md`
- Modify: `doc/测试/iTime-测试方案与测试用例.md`
- Modify: `doc/验收/iTime-产品验收清单.md`
- Modify: `doc/验收/iTime-Web实施与验证.md`

- [x] **Step 1: 执行服务端专项与全量门禁**

```bash
cd server
uv run pytest -q tests/test_sync_models.py tests/test_sync_feed.py \
  tests/test_sync_event_coverage.py tests/test_sync_task_completion.py
uv run pytest -q tests/test_tasks.py tests/test_task_schedules.py \
  tests/test_ai_conversation_api.py tests/test_ai_conversation_worker.py \
  tests/test_focus_sessions.py tests/test_review_sessions.py
uv run ruff check .
uv run pytest -q
uv run alembic upgrade head
uv run alembic current
uv run alembic check
uv run python -m itime.sync.cleanup_worker --once
uv build --no-sources
```

Expected: 全部通过；迁移 head 为 `0028_sync_feed`，测试库最终清理为 0。

- [x] **Step 2: 执行 Web 专项与全量门禁**

```bash
cd web
pnpm build
pnpm exec playwright test e2e/sync.spec.ts
pnpm exec playwright test e2e/auth.spec.ts e2e/tasks.spec.ts \
  e2e/calendar.spec.ts e2e/focus.spec.ts e2e/planning.spec.ts \
  e2e/reviews.spec.ts e2e/statistics.spec.ts e2e/sync.spec.ts
pnpm test:e2e
```

Expected: 全部 Chromium projects 通过。

- [x] **Step 3: 核对约 5 秒目标与运行状态**

真实双浏览器 context 记录业务提交完成到另一端 UI 出现结果的耗时，三个视口均小于等于 5 秒。
重启开发 API 和同步清理 worker 后验证：

```bash
curl -fsS http://127.0.0.1:8000/api/ready
curl -fsS http://127.0.0.1:8000/api/openapi.json | \
  jq -e '.paths[\"/api/v1/sync/changes\"]'
curl -fsSI http://localhost:5173/account/sync
docker --context colima-itime compose --env-file .env.local exec -T postgres \
  psql -U itime -d postgres -tAc \
  "SELECT count(*) FROM pg_database WHERE datname LIKE 'itime_test_%';"
```

- [x] **Step 4: 同步实际实现和证据**

- README 增加同步清理 worker 与 `/account/sync`。
- 追踪矩阵更新 `REQ-SYNC-01`，不得把离线新建、冷启动、Edge/真机或 M8 性能扩写为完成。
- 详细设计写入实际游标、事件保留、步骤合并和本地清理合同。
- 测试方案注明 `SYN-01`—`SYN-04` 的实际覆盖范围。
- 验收清单、designer 和实施记录写入命令、数量、耗时、5 秒实测和截图。
- 账号注销、其他对象回收、困难语音、公开链接、正式模型质量、备份恢复和生产部署继续明确为
  未完成。

- [x] **Step 5: 检查暂存范围并提交证据**

```bash
git diff --check
git status --short
git diff --cached --name-only
```

确认无 `.env`、IndexedDB 导出、Cookie、游标签名密钥、测试截图、Playwright 输出或测试数据库
文件进入暂存。

```bash
git add README.md doc/README.md designer.md \
  doc/需求/iTime-需求追踪矩阵.md doc/设计/iTime-详细设计文档.md \
  doc/设计/iTime-通用同步设计.md doc/计划/iTime-开发总计划.md \
  doc/计划/iTime-M6通用同步实施计划.md \
  doc/测试/iTime-测试方案与测试用例.md \
  doc/验收/iTime-产品验收清单.md doc/验收/iTime-Web实施与验证.md
git commit -m "docs: 记录通用同步实施证据"
```

## 执行结果

2026-10-04 已按本计划完成实现与验证：

- `1849982`：同步状态、用户触发器、事件约束和迁移 `0028_sync_feed`。
- `ba19707`：账号绑定 HMAC 游标、增量变更与状态 API。
- `46ee5b6`：有界事件清理及独立 worker。
- `0bbeb8f`：普通会话创建、更新、消息和终态聚合事件。
- `3d7053a`：普通与重复任务步骤安全合并端点。
- `6f696ba`：当前已认证会话的 AES-GCM IndexedDB 读取缓存。
- `b19d12f`：全局同步协调器、导航状态和 `/account/sync`。
- `0c64b11`：主页面刷新适配器、任务步骤队列及账号本地清理。
- `b2c7946`：双上下文、断网、游标恢复、冲突与多视口真实 E2E。
- `81ac753`、`5905702`、`53e5ca4`、`191e3f2`：测试数据隔离、本地事件去重、
  异步刷新失败收敛和统计趋势键盘滚动修复。

实际质量门禁：

| 验证 | 结果 |
| --- | --- |
| 同步专项及受影响服务端组合回归 | **56 passed in 63.23s** |
| `uv run pytest -q` | **412 passed in 350.88s** |
| Ruff、Alembic head/漂移检查、清理 worker `--once`、`uv build --no-sources` | 通过；head 为 `0028_sync_feed` |
| `pnpm build` | 通过 |
| `pnpm exec playwright test e2e/sync.spec.ts` | **18 passed** |
| 受影响 Chromium 组合回归 | **117 passed in 5.3m** |
| `pnpm test:e2e` | **207 passed in 10.0m** |
| 开发 API、OpenAPI、同步 worker、`/account/sync` 与测试库清理 | ready/page 200，同步路由已加载，worker 运行，测试库残留 0 |

真实双浏览器上下文使用 5 秒断言上界验证跨端可见；专项还覆盖缓存密文、HTTP 失败、断网重放、
普通/重复步骤、字段安全合并、终态冲突、游标过期和退出清理。桌面、手机、平板、320px 深色
最大字号和冲突截图位于 `/tmp/itime-ui-evidence/*sync-*`，已人工检查。账号注销、其他对象回收、
困难语音、公开链接、正式模型质量、M8 性能分位数、Edge/真机、备份恢复和生产部署仍未完成。
