# iTime M5 执行与困难实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven development where available; otherwise execute each task inline. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付可恢复、可修订且不隐式完成任务的专注计时，以及可独立或关联任务记录的困难管理和显式 AI 求助入口。

**Architecture:** 新增独立 `execution` 业务域，以 `FocusSession` 当前投影配合不可变 `FocusEvent` 和 `ExecutionRevision` 保存执行事实，用户行锁和部分唯一索引保证账号只有一个开放专注。任务步骤和完成继续写现有任务事实；困难使用版本化软删除与独立清理。Web `/focus` 根据服务端时间锚点显示计时，并仅为已加载开放会话维护按账号隔离的离线操作队列。

**Tech Stack:** Python 3.13、FastAPI、SQLAlchemy async、PostgreSQL/Alembic、Pydantic、Vue 3、TypeScript、Lucide、Playwright Chromium。

---

### Task 1: 执行与困难数据模型

**Files:**
- Create: `server/src/itime/migrations/versions/0025_execution_difficulties.py`
- Create: `server/src/itime/execution/__init__.py`
- Create: `server/src/itime/execution/models.py`
- Modify: `server/src/itime/migrations/env.py`
- Modify: `server/src/itime/tasks/models.py`
- Test: `server/tests/test_execution_models.py`

- [x] **Step 1: 写迁移失败测试**

在隔离数据库断言表和核心约束：

```python
assert {
    "focus_sessions",
    "focus_events",
    "execution_revisions",
    "difficulty_records",
}.issubset(table_names)
```

分别实现 `test_execution_migration_round_trip_and_orm_consistency`、`test_only_one_open_focus_per_owner`、`test_execution_owner_and_state_constraints`、`test_focus_events_are_immutable` 和 `test_execution_user_cascade_delete`。使用直接 SQL 插入非法行并断言 `psycopg.errors.CheckViolation`、`ForeignKeyViolation` 或 `UniqueViolation`；直接更新/删除 `focus_events` 并断言数据库触发器拒绝。

- [x] **Step 2: 执行失败测试**

Run:

```bash
cd server
uv run pytest -q tests/test_execution_models.py
```

Expected: FAIL，迁移和 `itime.execution.models` 尚不存在。

- [x] **Step 3: 实现迁移和 ORM**

在 `models.py` 创建 `FocusSession`、`FocusEvent`、`ExecutionRevision` 和 `DifficultyRecord` 四个 ORM 类；字段、枚举、外键、索引和生命周期必须逐项对应设计文档 §3，不增加未定义的状态或客户端权威字段。

迁移必须：

- 建立设计文档 §3 的字段、检查约束、复合外键和索引。
- 为 `scheduled_blocks(id, owner_id)` 补唯一约束后建立本人复合外键。
- 建立 `owner_id WHERE status IN ('running', 'paused')` 部分唯一索引。
- 为 `focus_events` 建不可变更新/删除触发器。
- 扩展 `change_events` 的 `entity_type` 和 `action` 检查约束。
- downgrade 先删除执行数据与触发器，再恢复旧变化事件约束。

- [x] **Step 4: 验证迁移和提交**

Run:

```bash
cd server
uv run pytest -q tests/test_execution_models.py
uv run alembic upgrade head
uv run alembic check
```

Expected: PASS；head 为 `0025_execution_difficulties`；无模型漂移。

```bash
git add server/src/itime/migrations/versions/0025_execution_difficulties.py \
  server/src/itime/execution server/src/itime/migrations/env.py \
  server/src/itime/tasks/models.py server/tests/test_execution_models.py
git commit -m "feat: 建立执行与困难数据模型"
```

### Task 2: 专注状态机与查询 API

**Files:**
- Create: `server/src/itime/execution/schemas.py`
- Create: `server/src/itime/execution/service.py`
- Create: `server/src/itime/execution/router.py`
- Modify: `server/src/itime/main.py`
- Test: `server/tests/test_focus_sessions.py`

- [x] **Step 1: 写状态机失败测试**

覆盖：

```text
自由标题、普通任务、重复实例和当前计划块开始
正计时与倒计时字段
running → paused → running → finished
running|paused → abandoned
暂停仍阻止第二个会话
双数据库连接同时开始只成功一个
操作重放与同 ID 异内容
版本冲突、非法转换和终态不可恢复
倒计时归零后保持 running 且不完成任务
关闭/重新进入读取同一当前状态
跨账号统一 404
```

- [x] **Step 2: 执行失败测试**

Run: `cd server && uv run pytest -q tests/test_focus_sessions.py`

Expected: FAIL，专注路由不存在。

- [x] **Step 3: 实现输入类型与纯计时函数**

提供：

```python
class FocusStartInput(OperationInput):
    mode: Literal["stopwatch", "countdown"]
    target_seconds: int | None = Field(default=None, ge=60, le=86_400)
    title: str | None = Field(default=None, max_length=200)
    task_id: UUID | None = None
    occurrence_key: str | None = Field(default=None, max_length=120)
    scheduled_block_id: UUID | None = None

class FocusTransitionInput(OperationInput):
    expected_version: int = Field(ge=1)
    action: Literal["pause", "resume", "finish", "abandon"]
    client_recorded_at: datetime | None = None

def elapsed_seconds(session: FocusSession, at: datetime) -> int:
    elapsed = session.accumulated_seconds
    if session.status == "running":
        if session.active_started_at is None:
            raise AppError("invalid_focus_state", "专注计时状态不完整。", status=500)
        elapsed += max(0, int((at - session.active_started_at).total_seconds()))
    return elapsed

def countdown_state(
    session: FocusSession,
    at: datetime,
) -> dict[str, int | bool | None]:
    elapsed = elapsed_seconds(session, at)
    remaining = (
        session.target_seconds - elapsed
        if session.target_seconds is not None
        else None
    )
    return {
        "elapsed_seconds": elapsed,
        "remaining_seconds": max(0, remaining) if remaining is not None else None,
        "overtime_seconds": max(0, -remaining) if remaining is not None else 0,
        "overtime": remaining is not None and remaining <= 0,
    }
```

另实现 `apply_transition(session, action, occurred_at) -> str`。该函数不访问数据库，只按设计状态表校验转换、累计当前运行段、设置终态字段并返回变化动作；所有时间先规范化为 UTC，事件由事务服务在取得序号后创建。

- [x] **Step 4: 实现服务和路由**

在 `service.py` 提供 `start_focus(session, owner_id, data, *, current_time)`、`transition_focus(session, owner_id, focus_id, data, *, current_time)`、`current_focus(session, owner_id, *, current_time)`、`focus_detail(session, owner_id, focus_id, *, current_time)` 和 `list_focus_sessions(session, owner_id, query, *, current_time)`；返回值均为可直接交给 FastAPI 编码的字典，当前查询在无开放会话时返回 `None`。

开始事务锁定 `User`，检查现有开放会话和关联对象；转换锁定会话并按设计顺序写事件、投影、版本、操作结果和变化事件。`client_recorded_at` 为空时使用服务端时间；非空时校验前一事件和未来 5 分钟边界。

注册：

```text
GET  /api/v1/focus-sessions/current
GET  /api/v1/focus-sessions
POST /api/v1/focus-sessions
GET  /api/v1/focus-sessions/{id}
POST /api/v1/focus-sessions/{id}/transitions
```

- [x] **Step 5: 验证并提交**

Run:

```bash
cd server
uv run pytest -q tests/test_execution_models.py tests/test_focus_sessions.py
uv run ruff check src/itime/execution tests/test_focus_sessions.py
```

```bash
git add server/src/itime/execution server/src/itime/main.py \
  server/tests/test_focus_sessions.py
git commit -m "feat: 实现可恢复专注状态机"
```

### Task 3: 专注中的任务步骤与明确完成

**Files:**
- Create: `server/src/itime/tasks/progress_service.py`
- Modify: `server/src/itime/tasks/router.py`
- Modify: `server/src/itime/tasks/schedule_router.py`
- Modify: `server/src/itime/execution/schemas.py`
- Modify: `server/src/itime/execution/service.py`
- Modify: `server/src/itime/execution/router.py`
- Test: `server/tests/test_focus_task_progress.py`
- Test: `server/tests/test_tasks.py`
- Test: `server/tests/test_task_schedules.py`

- [x] **Step 1: 写任务联动失败测试**

覆盖：

```text
普通任务步骤集合更新但任务仍 open
重复任务只改 occurrence completed_step_ids
步骤全完成、倒计时归零和结束专注都不完成任务
终态摘要中用户明确完成普通任务或重复实例
计划换版、任务改名不改变会话标题/步骤快照
归档、回收站、已清理、已完成和已跳过阻断
任务与会话版本冲突保留双方当前状态
操作重放不会重复递增任务或会话版本
```

- [x] **Step 2: 抽取现有任务进度服务**

从两个 router 中抽出三个可复用事务内函数：

- `set_template_step_progress(session, owner_id, task, completed_step_ids, *, changed_at) -> tuple[dict, bool]`
- `set_occurrence_progress(session, owner_id, task, occurrence_key, expected_occurrence_version, completed_step_ids, *, status, require_current_open, changed_at) -> tuple[dict, dict, bool]`
- `set_task_completion_state(session, owner_id, task, *, completed, changed_at) -> tuple[dict, bool]`
- `complete_task(session, owner_id, task, occurrence_key, expected_occurrence_version, *, changed_at) -> tuple[dict, dict | None, bool]`

原任务接口和新执行接口必须调用同一函数，避免两套完成语义。

- [x] **Step 3: 实现执行进度接口**

输入：

```python
class FocusProgressInput(OperationInput):
    expected_version: int = Field(ge=1)
    expected_task_version: int = Field(ge=1)
    expected_occurrence_version: int | None = None
    completed_step_ids: list[UUID] = Field(max_length=100)

class FocusTaskCompletionInput(OperationInput):
    expected_version: int = Field(ge=1)
    expected_task_version: int = Field(ge=1)
    expected_occurrence_version: int | None = None
```

注册：

```text
PUT  /api/v1/focus-sessions/{id}/progress
POST /api/v1/focus-sessions/{id}/complete-task
```

事务锁顺序固定为用户、会话、任务、实例。进度成功写 `progress` 事件并更新会话版本；明确完成写 `task_completed` 事件，但不改时长。

- [x] **Step 4: 验证并提交**

Run:

```bash
cd server
uv run pytest -q tests/test_focus_task_progress.py \
  tests/test_tasks.py tests/test_task_schedules.py
```

```bash
git add server/src/itime/tasks server/src/itime/execution \
  server/tests/test_focus_task_progress.py server/tests/test_tasks.py \
  server/tests/test_task_schedules.py
git commit -m "feat: 联通专注步骤与任务完成"
```

### Task 4: 补记、修订与活动日语义

**Files:**
- Create: `server/src/itime/execution/time_math.py`
- Modify: `server/src/itime/execution/schemas.py`
- Modify: `server/src/itime/execution/service.py`
- Modify: `server/src/itime/execution/router.py`
- Test: `server/tests/test_execution_revisions.py`

- [x] **Step 1: 写时间与修订失败测试**

覆盖：

```text
手工补记终态记录
结束或开始在未来超过 5 分钟拒绝
结束不晚于开始、有效时长 0—24 小时
上海 03:59/04:00 活动日
纽约 DST 缺失和重复时间
跨午夜专注
运行中不可修订
修订保留 before/after/reason
与其他有效专注重叠拒绝
相同操作重放和版本冲突
```

- [x] **Step 2: 实现时间函数**

提供：

```python
def activity_day_for(instant: datetime, time_zone: str) -> date:
    local = instant.astimezone(ZoneInfo(time_zone))
    boundary = local.replace(hour=4, minute=0, second=0, microsecond=0)
    return local.date() if local >= boundary else local.date() - timedelta(days=1)

def resolve_manual_interval(
    local_start: datetime,
    local_end: datetime,
    time_zone: str,
    start_offset_minutes: int | None,
    end_offset_minutes: int | None,
) -> tuple[datetime, datetime]:
    start, _ = resolve_local_datetime(
        local_start,
        time_zone,
        preferred_offset_minutes=start_offset_minutes,
        strict_ambiguous=True,
    )
    end, _ = resolve_local_datetime(
        local_end,
        time_zone,
        preferred_offset_minutes=end_offset_minutes,
        strict_ambiguous=True,
    )
    if start is None or end is None or end <= start:
        raise AppError("invalid_focus_interval", "专注结束时间必须晚于开始时间。")
    return start, end
```

复用 `calendar.time_math` 的 DST 候选解析，不用固定 UTC 减四小时替代活动日。

- [x] **Step 3: 实现补记和修订**

输入：

```python
class FocusManualCreateInput(OperationInput):
    title: str = Field(min_length=1, max_length=200)
    task_id: UUID | None = None
    occurrence_key: str | None = Field(default=None, max_length=120)
    local_started_at: datetime
    local_ended_at: datetime
    time_zone: str = Field(min_length=1, max_length=64)
    start_utc_offset_minutes: int | None = Field(default=None, ge=-840, le=840)
    end_utc_offset_minutes: int | None = Field(default=None, ge=-840, le=840)
    effective_duration_seconds: int = Field(ge=0, le=86_400)

class FocusRevisionInput(FocusManualCreateInput):
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500)
```

注册：

```text
POST /api/v1/focus-sessions/manual
PUT  /api/v1/focus-sessions/{id}
```

补记写两个原始事件；修订写 `ExecutionRevision` 和新的有效值。两者都锁定用户后检查重叠。

- [x] **Step 4: 验证并提交**

Run: `cd server && uv run pytest -q tests/test_execution_revisions.py tests/test_focus_sessions.py`

```bash
git add server/src/itime/execution server/tests/test_execution_revisions.py
git commit -m "feat: 支持专注补记与事实修订"
```

### Task 5: 困难 CRUD、回收与清理

**Files:**
- Create: `server/src/itime/execution/difficulty_service.py`
- Create: `server/src/itime/execution/cleanup_worker.py`
- Modify: `server/src/itime/execution/schemas.py`
- Modify: `server/src/itime/execution/router.py`
- Modify: `server/src/itime/tasks/lifecycle_service.py`
- Test: `server/tests/test_difficulties.py`
- Test: `server/tests/test_execution_cleanup.py`

- [x] **Step 1: 写困难生命周期失败测试**

覆盖：

```text
独立、关联任务、关联专注创建
专注与显式任务不一致拒绝
空分类/情绪可保存且不推断
按日期、任务、分类和活动/回收站分页
编辑版本冲突和跨账号统一 404
删除 30 天、恢复、归档任务只读关联
任务到期清理后保留困难与任务标题快照
到期清空正文并保留墓碑
两个清理 worker SKIP LOCKED 不重复领取
旧操作响应清理后不能复活正文
```

- [x] **Step 2: 实现服务和 API**

在 `difficulty_service.py` 提供 `create_difficulty`、`update_difficulty`、`trash_difficulty`、`restore_difficulty` 和 `cleanup_expired_difficulties`。前四个函数接收 session、owner、输入及适用对象 ID并返回稳定结果字典；清理函数接收 `Database` 和默认批量 100，返回实际清理数量。

注册设计文档 §7 的困难路由。编辑和删除锁定本人记录并校验版本；删除后普通编辑拒绝。清理 worker 支持 `--once`，普通日志只输出计数和 ID，不输出正文。

- [x] **Step 3: 联动任务清理**

任务到期清理前，将关联困难的 `task_id` 置空但保留 `task_title_snapshot`；同一事务清理关联 `UserOperation.response`。不得因困难外键阻止任务墓碑处理。

- [x] **Step 4: 验证并提交**

Run:

```bash
cd server
uv run pytest -q tests/test_difficulties.py tests/test_execution_cleanup.py \
  tests/test_task_lifecycle.py
uv run python -m itime.execution.cleanup_worker --once
```

```bash
git add server/src/itime/execution server/src/itime/tasks/lifecycle_service.py \
  server/tests/test_difficulties.py server/tests/test_execution_cleanup.py \
  server/tests/test_task_lifecycle.py
git commit -m "feat: 实现困难记录与回收清理"
```

### Task 6: 专注工作台与在线计时

**Files:**
- Create: `web/src/lib/execution.ts`
- Create: `web/src/pages/FocusPage.vue`
- Create: `web/src/components/FocusTimer.vue`
- Create: `web/src/components/FocusSetup.vue`
- Create: `web/src/components/FocusTaskProgress.vue`
- Create: `web/src/components/FocusHistory.vue`
- Create: `web/src/focus.css`
- Modify: `web/src/main.ts`
- Modify: `web/src/components/ProductNavigation.vue`
- Modify: `web/src/style.css`
- Test: `web/e2e/focus.spec.ts`

- [x] **Step 1: 写在线流程浏览器失败测试**

三视口覆盖：

```text
自由标题正计时
任务倒计时和步骤勾选
暂停、刷新、恢复、结束
倒计时超时后继续且任务未完成
结束后明确完成普通任务/重复实例
第二页面并发开始和版本冲突
放弃确认
历史详情和 12 小时提示
```

- [x] **Step 2: 建立前端类型和 API**

`web/src/lib/execution.ts` 定义：

```ts
export type FocusStatus = 'running' | 'paused' | 'finished' | 'abandoned'
export type FocusMode = 'stopwatch' | 'countdown'

export interface FocusStep {
  id: string
  title: string
  completed: boolean
}

export interface FocusSession {
  id: string
  source: 'timer' | 'manual'
  task_id: string | null
  occurrence_key: string | null
  scheduled_block_id: string | null
  title: string
  task_title: string | null
  steps: FocusStep[]
  task_version: number | null
  occurrence_version: number | null
  task_status: 'open' | 'completed' | 'skipped' | null
  mode: FocusMode
  target_seconds: number | null
  status: FocusStatus
  started_at: string
  ended_at: string | null
  active_started_at: string | null
  accumulated_seconds: number
  effective_started_at: string | null
  effective_ended_at: string | null
  effective_duration_seconds: number | null
  time_zone: string
  version: number
  attention_required: boolean
  revised: boolean
  server_now: string
}

export interface FocusEvent {
  id: string
  sequence: number
  kind: 'start' | 'pause' | 'resume' | 'finish' | 'abandon' | 'progress' | 'task_completed'
  occurred_at: string
  client_recorded_at: string | null
  source: 'online' | 'offline'
  details: Record<string, unknown>
}

export interface DifficultyRecord {
  id: string
  task_id: string | null
  focus_session_id: string | null
  occurrence_key: string | null
  task_title: string | null
  content: string
  category: string | null
  emotion: string | null
  occurred_at: string
  time_zone: string
  lifecycle: 'active' | 'trashed' | 'purged'
  purge_after: string | null
  version: number
}
```

所有写操作由调用方传入稳定 operation ID；未知结果时不生成新 ID。

- [x] **Step 3: 实现 `/focus` 在线工作台**

页面逻辑：

```ts
const serverAnchor = shallowRef<{ server: number; client: number } | null>(null)
const displayNow = computed(() =>
  serverAnchor.value
    ? serverAnchor.value.server + performance.now() - serverAnchor.value.client
    : Date.now()
)
```

使用固定宽度数字和稳定计时器容器；每秒只更新前端显示。暂停、恢复、结束和放弃成功后以服务端快照替换本地投影。步骤、任务完成和计时状态分别显示。

- [x] **Step 4: 扩展导航和响应式样式**

新增 `/focus` 路由和 `TimerReset` 图标导航。窄屏产品导航只显示图标，保持至少 44px 命中区；布局不得出现卡片套卡片或滚动遮挡主操作。

- [x] **Step 5: 验证并提交**

Run:

```bash
cd web
pnpm build
pnpm exec playwright test e2e/focus.spec.ts --project=desktop
pnpm exec playwright test e2e/focus.spec.ts --project=mobile
pnpm exec playwright test e2e/focus.spec.ts --project=tablet
```

```bash
git add web/src web/e2e/focus.spec.ts
git commit -m "feat: 实现专注计时工作台"
```

### Task 7: 专注离线队列与冲突恢复

**Files:**
- Create: `web/src/lib/focus-queue.ts`
- Modify: `web/src/pages/FocusPage.vue`
- Modify: `web/src/components/FocusTimer.vue`
- Modify: `web/src/components/FocusTaskProgress.vue`
- Modify: `web/src/focus.css`
- Modify: `web/e2e/focus.spec.ts`

- [ ] **Step 1: 写离线恢复浏览器失败测试**

覆盖：

```text
已加载会话断网后暂停/恢复/结束
多个操作严格按顺序重放
响应已保存但浏览器未收到时重用 operation_id
刷新后从当前账号队列恢复
账号切换不重放旧队列
另一页面结束导致冲突时保留队列
明确采用服务端状态后才清队列
终态冲突可转成修订草稿
100 项和 7 天边界
```

- [ ] **Step 2: 实现账号隔离队列**

提供：

```ts
export interface QueuedFocusOperation {
  operation_id: string
  session_id: string
  expected_version: number
  action: 'pause' | 'resume' | 'finish' | 'abandon' | 'progress'
  client_recorded_at: string
  payload: Record<string, unknown>
  created_at: string
}

export interface ReplayResult {
  session: FocusSession | null
  pending: QueuedFocusOperation[]
  conflict: QueuedFocusOperation | null
}

export function loadFocusQueue(userId: string, sessionId: string): QueuedFocusOperation[]
export function appendFocusOperation(userId: string, item: QueuedFocusOperation): void
export type SendFocusOperation = (
  item: QueuedFocusOperation,
) => Promise<FocusSession>
```

实现 `replayFocusQueue(userId, sessionId, send): Promise<ReplayResult>`，逐项调用注入的 `send`，只在成功响应后移除当前项，遇到 `409` 时返回冲突项并停止。解析 localStorage 时严格校验形状、数量和期限；无效项丢弃，不执行任意路径或方法。

- [ ] **Step 3: 接入离线投影和恢复 UI**

`navigator.onLine=false` 或网络错误时只允许设计范围内的动作。页面显示待同步数、最后服务端时间和冲突详情；冲突对话提供采用服务端、重新应用下一项或转为修订草稿，任何按钮都不静默丢弃队列。

- [ ] **Step 4: 验证并提交**

Run:

```bash
cd web
pnpm build
pnpm exec playwright test e2e/focus.spec.ts
```

```bash
git add web/src/lib/focus-queue.ts web/src/pages/FocusPage.vue \
  web/src/components/FocusTimer.vue web/src/components/FocusTaskProgress.vue \
  web/src/focus.css web/e2e/focus.spec.ts
git commit -m "feat: 支持专注离线恢复"
```

### Task 8: 困难 UI、跨页面入口与 AI 草稿

**Files:**
- Modify: `web/src/lib/execution.ts`
- Modify: `web/src/pages/FocusPage.vue`
- Create: `web/src/components/DifficultyPanel.vue`
- Modify: `web/src/components/TaskEditor.vue`
- Modify: `web/src/components/CalendarOccurrencePanel.vue`
- Modify: `web/src/pages/AIPage.vue`
- Modify: `web/src/focus.css`
- Modify: `web/src/task-workspace.css`
- Modify: `web/src/calendar.css`
- Test: `web/e2e/focus.spec.ts`
- Test: `web/e2e/ai-conversations.spec.ts`
- Test: `web/e2e/tasks.spec.ts`
- Test: `web/e2e/calendar.spec.ts`

- [ ] **Step 1: 写跨页面浏览器失败测试**

覆盖：

```text
任务详情携带 task_id 进入专注
当前计划块携带 scheduled_block_id 进入专注
困难独立/关联创建、编辑、删除和恢复
分类/情绪为空不阻断保存
带到 AI 只填草稿且网络中没有新作业
用户明确发送后才产生普通对话作业与额度预留
长困难、长任务名、请求编号和回收时间响应式显示
```

- [ ] **Step 2: 实现困难面板**

困难面板使用活动/回收站分段控件、一个主要文本框、可选短标签输入和明确保存。列表操作使用编辑、删除、恢复和消息图标；图标按钮提供 tooltip 和可访问名称。

- [ ] **Step 3: 实现任务、日历和 AI 联通**

- 任务详情入口路由到 `/focus?task=<id>`。
- 计划块详情路由到 `/focus?block=<id>`。
- `/ai?difficulty=<id>` 鉴权读取困难并填入普通回复草稿；加载完成后移除 query，避免重复填入。
- 不把困难正文写入 URL、localStorage 或普通控制台。

- [ ] **Step 4: 验证并提交**

Run:

```bash
cd web
pnpm build
pnpm exec playwright test e2e/focus.spec.ts e2e/ai-conversations.spec.ts \
  e2e/tasks.spec.ts e2e/calendar.spec.ts
```

```bash
git add web/src web/e2e
git commit -m "feat: 联通困难记录与专注入口"
```

### Task 9: 全量回归、证据和文档同步

**Files:**
- Modify: `README.md`
- Modify: `doc/README.md`
- Modify: `designer.md`
- Modify: `doc/设计/iTime-详细设计文档.md`
- Modify: `doc/需求/iTime-需求追踪矩阵.md`
- Modify: `doc/测试/iTime-测试方案与测试用例.md`
- Modify: `doc/验收/iTime-产品验收清单.md`
- Modify: `doc/验收/iTime-Web实施与验证.md`
- Modify: `doc/计划/iTime-开发总计划.md`
- Modify: `doc/计划/iTime-M5执行与困难实施计划.md`

- [ ] **Step 1: 服务端全量验证**

Run:

```bash
cd server
uv run ruff check .
uv run pytest -q
uv run alembic upgrade head
uv run alembic current
uv run alembic check
uv build --no-sources
```

记录真实通过数量、耗时、迁移 head、双连接并发和清理 worker 结果。

- [ ] **Step 2: Web 全量验证**

Run:

```bash
cd web
pnpm build
pnpm test:e2e
```

人工检查 `/tmp/itime-ui-evidence/` 的桌面、平板、手机、320px 大字号和深色截图；确认计时数字、状态动作、离线冲突、长正文和六项导航无重叠、裁切或布局跳动。

- [ ] **Step 3: 同步文档**

只把实际通过的执行与困难行为写为已实现；复盘、统计、通用同步、语音、Edge、实体设备、性能和生产部署未执行时继续明确标注。

- [ ] **Step 4: 提交**

```bash
git add README.md doc designer.md
git commit -m "docs: 记录执行与困难实施证据"
```
