# iTime M5 订阅来源计划冲突实施计划

当前进度：全部任务已实现、验证并提交；实际命令与结果见
[Web 实施与验证](../验收/iTime-Web实施与验证.md)。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 在 ICS 订阅成功更新未来固定安排时，为受影响的已确认当前计划创建可审计、可同步、可
处理的来源冲突，并在规划页和统一日历提供完整恢复入口。

**Architecture:** 新增按“当前计划版本 + 订阅来源 + 来源代次”聚合的
`PlanSourceConflict`。订阅刷新事务在同一 planning revision 锁下比较来源更新前后的周投影并
写冲突；计划新版本解决旧冲突，用户也可幂等确认保留。现有计划 API、同步 `plan` 事件和 Vue
页面读取同一状态，不建立前端推断或第二套冲突事实。

**Tech Stack:** Python 3.13、FastAPI、SQLAlchemy async、PostgreSQL 18、Alembic、Vue 3、
TypeScript、Lucide、Playwright Chromium、axe。

---

## 文件结构

服务端持久化与判定：

- `server/src/itime/migrations/versions/0031_plan_source_conflicts.py`：冲突表、约束、索引和回滚。
- `server/src/itime/planning/models.py`：`PlanSourceConflict` ORM。
- `server/src/itime/planning/source_conflict_service.py`：刷新前后投影、差异、重叠、序列化和解决
  生命周期。
- `server/src/itime/calendar/subscription_projection.py`：允许只投影一个订阅来源。
- `server/src/itime/calendar/subscription_service.py`：在来源快照事务内原子检测并创建冲突。
- `server/src/itime/planning/plan_service.py`：计划读取、块投影和新版本解决旧冲突。
- `server/src/itime/planning/schemas.py`、`router.py`：本人处理 API。
- `server/src/itime/data_export/snapshot.py`：本人 JSON 导出冲突历史。

服务端测试：

- `server/tests/test_plan_source_conflict_models.py`：迁移、约束、级联和 ORM。
- `server/tests/test_plan_source_conflicts.py`：真实 PostgreSQL/HTTP/worker 的创建、取消、失败、
  并发、解决、同步与导出。
- `server/tests/test_plan_versions.py`：新计划版本自动解决旧冲突。
- `server/tests/test_account_deletion_worker.py`：账号删除后无冲突残留。
- `server/tests/test_data_export_snapshot.py`：导出结构与敏感字段排除。

Web：

- `web/src/lib/plans.ts`：冲突类型、处理 API 和计划块响应。
- `web/src/pages/PlansPage.vue`：来源冲突摘要、详情、保留、重新生成和日历入口。
- `web/src/pages/CalendarPage.vue`：范围提示、定位来源和重叠计划块状态。
- `web/src/components/CalendarOccurrencePanel.vue`：计划块来源冲突说明。
- `web/src/planning.css`、`web/src/calendar.css`：响应式状态带和详情布局。
- `web/e2e/calendar-subscriptions.spec.ts`：真实来源更新到计划冲突的跨页面流程。

## Task 1：建立持久模型

**Files:**
- Create: `server/src/itime/migrations/versions/0031_plan_source_conflicts.py`
- Modify: `server/src/itime/planning/models.py`
- Create: `server/tests/test_plan_source_conflict_models.py`

- [x] **Step 1: 写迁移失败测试**

升级 head 后断言 `plan_source_conflicts` 存在，并以真实 UUID 写入最小合法记录。覆盖非法状态、
resolution/state 不一致、非数组 JSON、超过 2000 个 changes、超过 100 个 block ID、负代次和
跨 owner 外键：

```python
with pytest.raises(CheckViolation):
    with connection.transaction():
        connection.execute(
            """
            UPDATE plan_source_conflicts
            SET status='resolved', resolution=NULL, resolved_at=NULL
            WHERE id=%s
            """,
            (conflict_id,),
        )
```

断言 `(plan_version_id, source_id, source_generation)` 唯一；删除测试账号后记录级联为 0；空表可
降到 `0030_public_task_links` 再升级。

- [x] **Step 2: 运行测试确认失败**

```bash
cd server
uv run pytest -q tests/test_plan_source_conflict_models.py
```

Expected: FAIL，迁移和 ORM 尚不存在。

- [x] **Step 3: 实现迁移和 ORM**

模型固定为：

```python
class PlanSourceConflict(Base):
    __tablename__ = "plan_source_conflicts"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID]
    plan_id: Mapped[UUID]
    plan_version_id: Mapped[UUID]
    source_id: Mapped[UUID]
    source_generation: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12), default="open")
    resolution: Mapped[str | None] = mapped_column(String(12))
    changes: Mapped[list[dict]] = mapped_column(JSONB, default=list)
    affected_block_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    details_limited: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

迁移同时创建 owner/status/detected、plan-version/status 和 source/generation 索引；组合外键约束
owner 与计划版本、来源归属。

- [x] **Step 4: 验证并提交**

```bash
cd server
uv run pytest -q tests/test_plan_source_conflict_models.py
uv run alembic upgrade head
uv run alembic current
uv run alembic check
uv run ruff check src/itime/planning/models.py \
  src/itime/migrations/versions/0031_plan_source_conflicts.py \
  tests/test_plan_source_conflict_models.py
git add src/itime/planning/models.py \
  src/itime/migrations/versions/0031_plan_source_conflicts.py \
  tests/test_plan_source_conflict_models.py
git commit -m "feat: 建立计划来源冲突模型"
```

Expected: 模型专项、迁移往返、Alembic 漂移和 Ruff 均通过。

## Task 2：在订阅刷新事务内检测冲突

**Files:**
- Create: `server/src/itime/planning/source_conflict_service.py`
- Modify: `server/src/itime/calendar/subscription_projection.py`
- Modify: `server/src/itime/calendar/subscription_service.py`
- Create: `server/tests/test_plan_source_conflicts.py`
- Modify: `server/tests/test_calendar_subscriptions.py`

- [x] **Step 1: 写真实 worker 失败测试**

测试先创建账号、UTC 时间设置、任务和当前手工计划，再创建订阅并运行真实
`process_one()`。首个来源事件避开计划块；下一代把事件移动到计划块时断言：

```python
context = client.get("/api/v1/planning/context", params=query).json()["context"]
conflict = context["current_plan"]["source_conflicts"]["items"][0]
assert conflict["source_generation"] == 2
assert conflict["changes"][0]["kind"] == "moved"
assert conflict["affected_block_ids"] == [block_id]
assert current_plan["version"] == original_version
assert current_plan["blocks"] == original_blocks
```

同文件继续覆盖：

- 新增占用、来源取消、busy 变更及移出本周均创建“需复核”记录。
- 只有刷新后真实重叠才填写 `affected_block_ids`。
- 标题/地点变化、304、抓取失败、坏项目保旧和纯过去 occurrence 不创建记录。
- 本地 `busy=false` 覆盖后来源时间变化不制造占用冲突。
- 同内容重试不重复；来源再次变化把旧记录处理为 `superseded`。
- 单周投影超过 2000 时来源仍成功，记录 `details_limited=true`。

- [x] **Step 2: 运行测试确认失败**

```bash
cd server
uv run pytest -q tests/test_plan_source_conflicts.py \
  tests/test_calendar_subscriptions.py
```

Expected: 新专项因缺少 `source_conflicts` 失败；既有订阅用例保持通过。

- [x] **Step 3: 增加单来源投影**

`query_subscription_occurrences()` 增加可选 `source_id`，查询条件固定归属：

```python
statement = select(CalendarSubscriptionEvent).where(
    CalendarSubscriptionEvent.owner_id == owner_id,
)
if source_id is not None:
    statement = statement.where(
        CalendarSubscriptionEvent.source_id == source_id,
    )
```

既有调用不传时行为不变；新服务只读取当前 claim 的来源。

- [x] **Step 4: 实现前后快照与差异**

`source_conflict_service.py` 定义不可变的基线值：

```python
@dataclass(frozen=True)
class PlanConflictBaseline:
    plan_id: UUID
    plan_version_id: UUID
    plan_version: int
    week_start_date: date
    query_start: datetime
    query_end: datetime
    occurrences: tuple[dict[str, Any], ...]
    details_limited: bool
```

`capture_source_plan_baselines(session, owner_id, source_id, at)` 返回
`list[PlanConflictBaseline]`：锁定 planning revision 和当前/未来 Plan 行，按周投影更新前来源。
`record_source_plan_conflicts(session, owner_id, source, source_generation, baselines, at)` 返回
`list[PlanSourceConflict]`：在 flush 后投影更新后来源，以 occurrence key 比较调度签名，按
`[start,end)` 与当前版本块相交，先 supersede 同版本同来源旧开放记录，再写一条新代次记录。
JSON 快照只含用户可见名称、日期/时刻、时区、UTC 起止、busy、状态和 event ID，不含 UID、URL
或正文。

- [x] **Step 5: 原子接入来源刷新**

`apply_subscription_snapshot()` 在任何事件修改前调用 `capture_source_plan_baselines()`；完成来源
事件变更并 `flush()` 后，仅在 `changed_count > 0` 时调用
`record_source_plan_conflicts()`。来源更新、规划 revision trigger、冲突和 `plan` change event
同事务提交；失败回滚全部。

- [x] **Step 6: 验证并提交**

```bash
cd server
uv run pytest -q tests/test_plan_source_conflicts.py \
  tests/test_calendar_subscriptions.py tests/test_planning_inputs.py
uv run ruff check src/itime/planning/source_conflict_service.py \
  src/itime/calendar/subscription_projection.py \
  src/itime/calendar/subscription_service.py \
  tests/test_plan_source_conflicts.py tests/test_calendar_subscriptions.py
git add src/itime/planning/source_conflict_service.py \
  src/itime/calendar/subscription_projection.py \
  src/itime/calendar/subscription_service.py \
  tests/test_plan_source_conflicts.py tests/test_calendar_subscriptions.py
git commit -m "feat: 检测订阅来源计划冲突"
```

## Task 3：查询、处理、版本生命周期与导出

**Files:**
- Modify: `server/src/itime/planning/source_conflict_service.py`
- Modify: `server/src/itime/planning/schemas.py`
- Modify: `server/src/itime/planning/router.py`
- Modify: `server/src/itime/planning/plan_service.py`
- Modify: `server/src/itime/planning/snapshot_service.py`
- Modify: `server/src/itime/data_export/snapshot.py`
- Modify: `server/tests/test_plan_source_conflicts.py`
- Modify: `server/tests/test_plan_versions.py`
- Modify: `server/tests/test_data_export_snapshot.py`
- Modify: `server/tests/test_account_deletion_worker.py`

- [x] **Step 1: 写 API、并发和生命周期失败测试**

覆盖：

```python
resolved = client.post(
    f"/api/v1/plans/{plan_id}/source-conflicts/resolve",
    json={
        "operation_id": str(operation_id),
        "expected_plan_version": plan_version,
        "conflict_ids": [conflict_id],
        "resolution": "kept",
    },
)
assert resolved.status_code == 200
assert resolved.json()["source_conflicts"]["open_count"] == 0
```

同操作重放结果一致；同 ID 异参 409；跨账号 404；旧计划版本 409；请求期间新建的冲突不被处理。
保存新手工版本后旧记录为 `replanned`。`GET /planning/blocks` 返回范围冲突，并只给实际重叠块
非零 `source_conflict_count`。同步 feed 出现 `plan` 更新。

导出快照断言 `planning.source_conflicts` 含历史状态但不含 URL、UID、lease；账号注销 worker
完成后该表 owner 行为 0。

- [x] **Step 2: 运行测试确认失败**

```bash
cd server
uv run pytest -q tests/test_plan_source_conflicts.py \
  tests/test_plan_versions.py tests/test_data_export_snapshot.py \
  tests/test_account_deletion_worker.py
```

Expected: API、版本解决和导出断言失败。

- [x] **Step 3: 实现处理输入和服务**

`schemas.py` 增加：

```python
class ResolvePlanSourceConflictsInput(Input):
    operation_id: UUID
    expected_plan_version: int = Field(ge=1)
    conflict_ids: list[UUID] = Field(min_length=1, max_length=100)
    resolution: Literal["kept"]

    @field_validator("conflict_ids")
    @classmethod
    def unique_conflict_ids(cls, values: list[UUID]) -> list[UUID]:
        if len(values) != len(set(values)):
            raise ValueError("计划来源冲突不能重复")
        return values
```

`resolve_source_conflicts()` 使用 scope
`planning.source_conflicts.resolve:{plan_id}`、operation replay、planning revision 写锁、
Plan 行锁和 conflict 行锁。只接受当前版本开放记录，更新
`status/resolution/resolved_at/version`，更新 `plan.updated_at` 并发出 `plan` change event。

- [x] **Step 4: 把状态接入读取和新版本**

- `planning_context.current_plan` 和 `Plan.current_version` 返回
  `{open_count, overlap_count, items}`。
- `read_current_plan_blocks()` 返回范围内冲突；计划块按 `affected_block_ids` 统计。
- `create_plan_version()` 在替换 current version 后把 `previous_version_id` 的开放记录更新为
  `resolved/replanned`，但不改历史计划版本。
- 路由增加 `POST /plans/{plan_id}/source-conflicts/resolve`，返回 operation ID、当前计划版本和
  最新 summary。

- [x] **Step 5: 接入本人导出和账号删除**

`collect_export_snapshot()` 在 `planning` 分类增加按 `detected_at,id` 排序的
`source_conflicts`。账号删除依赖组合外键级联；测试显式核对，避免仅凭数据库设计推断完成。

- [x] **Step 6: 验证并提交**

```bash
cd server
uv run pytest -q tests/test_plan_source_conflicts.py \
  tests/test_plan_versions.py tests/test_planning_api.py \
  tests/test_data_export_snapshot.py tests/test_account_deletion_worker.py \
  tests/test_sync_feed.py tests/test_sync_event_coverage.py
uv run ruff check src/itime/planning src/itime/data_export/snapshot.py \
  tests/test_plan_source_conflicts.py tests/test_plan_versions.py \
  tests/test_data_export_snapshot.py tests/test_account_deletion_worker.py
git add src/itime/planning src/itime/data_export/snapshot.py \
  tests/test_plan_source_conflicts.py tests/test_plan_versions.py \
  tests/test_data_export_snapshot.py tests/test_account_deletion_worker.py
git commit -m "feat: 支持处理计划来源冲突"
```

## Task 4：规划页与日历提示

**Files:**
- Modify: `web/src/lib/plans.ts`
- Modify: `web/src/pages/PlansPage.vue`
- Modify: `web/src/pages/CalendarPage.vue`
- Modify: `web/src/components/CalendarOccurrencePanel.vue`
- Modify: `web/src/planning.css`
- Modify: `web/src/calendar.css`
- Modify: `web/e2e/calendar-subscriptions.spec.ts`

- [x] **Step 1: 编写失败的真实 Chromium 流程**

扩展受控 ICS 服务，使同一 URL 可依次返回初始、移动、取消和失败版本。通过 HTTP 创建任务和手工
计划，但所有冲突查看与处理必须走可见 UI：

```ts
await expect(page.getByRole('heading', {
  name: '订阅来源更新后，当前计划需复核',
})).toBeVisible()
await expect(page.getByText('第 1 版计划没有被自动改动。')).toBeVisible()
await expect(page.getByText(/与 1 个计划块重叠/)).toBeVisible()
```

断言“在日历查看”进入正确日期并打开来源；重叠计划块显示冲突说明。点击“保留当前计划”后，
当前块和版本内容完全不变、提示消失；下一次来源取消产生“计划需复核”但不显示重叠。刷新失败
不新增记录且旧数据保留。

每个 Playwright 项目执行核心链路；desktop 另切 320×700、最大字号和深色，执行 axe、键盘、
根级横向溢出、控制台和截图检查。

- [x] **Step 2: 运行测试确认失败**

```bash
cd web
pnpm exec playwright test e2e/calendar-subscriptions.spec.ts \
  --grep "订阅来源更新后已确认计划"
```

Expected: desktop、mobile、tablet 均因缺少来源冲突提示失败。

- [x] **Step 3: 增加类型和处理 API**

`plans.ts` 增加 `PlanSourceConflictChange`、`PlanSourceConflict`、
`PlanSourceConflictSummary`，并扩展 `PlanningCurrentPlan`、`Plan` 与 block response。

```ts
export async function keepPlanAfterSourceChanges(
  planId: string,
  expectedPlanVersion: number,
  conflictIds: string[],
  operationId: string,
) {
  return api<{ source_conflicts: PlanSourceConflictSummary }>(
    `/plans/${planId}/source-conflicts/resolve`,
    {
      operation_id: operationId,
      expected_plan_version: expectedPlanVersion,
      conflict_ids: conflictIds,
      resolution: 'kept',
    },
  )
}
```

- [x] **Step 4: 实现规划页恢复交互**

当前计划预览顶部渲染不嵌套卡片的 `section.plan-source-conflicts`：

- `AlertTriangle` 标题、未自动改动说明和来源级变化列表。
- 使用 `<details>` 展示前后日期/时刻及重叠块数。
- `CalendarSearch` 链接到 `/calendar?date=...&source=...&event=...`。
- `CircleCheck`“保留当前计划”提交当前冲突 ID，响应未知复用 operation ID，409 重读。
- `RefreshCw`“按最新来源重新生成”切到整周并保留当前任务选择；用户显式生成，提案确认后旧冲突
  才消失。

- [x] **Step 5: 实现日历范围和块级提示**

`CalendarPage.vue` 读取 blocks 响应的 `source_conflicts`，顶部显示范围摘要；URL query 初始化
anchor 并在数据加载后调用既有 `openSubscription(source,event)`。合并计划块时：

```ts
conflict: item.source_conflict_count > 0
```

`CalendarOccurrencePanel.vue` 对命中的计划块显示来源变化说明和进入规划页按钮；无实际重叠的
复核不标红所有计划块。

- [x] **Step 6: 构建、专项验证并提交**

```bash
cd web
pnpm build
pnpm exec playwright test e2e/calendar-subscriptions.spec.ts \
  --grep "订阅来源更新后已确认计划"
git add src/lib/plans.ts src/pages/PlansPage.vue src/pages/CalendarPage.vue \
  src/components/CalendarOccurrencePanel.vue src/planning.css src/calendar.css \
  e2e/calendar-subscriptions.spec.ts
git commit -m "feat: 展示并处理计划来源冲突"
```

Expected: 构建通过，专项在 desktop、mobile、tablet 共 3 项通过。

## Task 5：回归、文档与证据

**Files:**
- Modify: `README.md`
- Modify: `doc/README.md`
- Modify: `doc/需求/iTime-需求追踪矩阵.md`
- Modify: `doc/设计/iTime-详细设计文档.md`
- Modify: `doc/设计/iTime-时间课表与重复规则设计.md`
- Modify: `doc/设计/iTime-ICS订阅设计.md`
- Modify: `doc/设计/iTime-AI提案与日周规划设计.md`
- Modify: `doc/测试/iTime-测试方案与测试用例.md`
- Modify: `doc/验收/iTime-产品验收清单.md`
- Modify: `doc/验收/iTime-Web实施与验证.md`
- Modify: `doc/计划/iTime-M5订阅来源计划冲突实施计划.md`

- [x] **Step 1: 执行迁移与服务端受影响回归**

```bash
cd server
uv run alembic upgrade head
uv run alembic current
uv run alembic check
uv run pytest -q tests/test_plan_source_conflict_models.py \
  tests/test_plan_source_conflicts.py tests/test_calendar_subscriptions.py \
  tests/test_planning_inputs.py tests/test_planning_api.py \
  tests/test_plan_versions.py tests/test_plan_proposals.py \
  tests/test_data_export_snapshot.py tests/test_account_deletion_worker.py \
  tests/test_sync_api.py
uv run ruff check .
uv build --no-sources
```

Expected: 迁移为 `0031_plan_source_conflicts`，专项和受影响测试、Ruff、服务包构建均通过。

- [x] **Step 2: 执行前端受影响回归**

```bash
cd web
pnpm build
pnpm exec playwright test e2e/calendar-subscriptions.spec.ts \
  e2e/planning.spec.ts e2e/calendar.spec.ts e2e/sync.spec.ts \
  e2e/data-exports.spec.ts e2e/account-deletion.spec.ts
```

Expected: 订阅、规划、日历、同步、导出和注销场景在适用三视口通过。

- [x] **Step 3: 执行全量回归**

```bash
cd server
uv run pytest -q

cd ../web
pnpm test:e2e
```

Expected: 服务端全量和 Chromium 全量通过；仅允许测试文件中已说明的 desktop-only skip。

- [x] **Step 4: 核对运行态和视觉证据**

```bash
curl --fail --silent http://127.0.0.1:8000/api/ready
curl --fail --silent http://localhost:5173/plans
curl --fail --silent http://localhost:5173/calendar
git diff --check
```

人工检查 `/tmp/itime-ui-evidence/*plan-source-conflict*` 的桌面、平板、手机和 320px 最大字号深色
截图；确认无内容重叠、截断或根级横向滚动。查询本地 PostgreSQL，确认 `itime_test_%` 测试库残留
为 0。

- [x] **Step 5: 同步实际状态并提交**

只有在命令真实执行后，才把数量、耗时、截图和限制写入验收文档。`REQ-CAL-03` 可标记本批范围
完成，但订阅 occurrence 单次例外、任务/困难语音、Edge、真机、性能和生产部署仍保持未完成。

```bash
git add README.md doc/README.md \
  doc/需求/iTime-需求追踪矩阵.md \
  doc/设计/iTime-详细设计文档.md \
  doc/设计/iTime-时间课表与重复规则设计.md \
  doc/设计/iTime-ICS订阅设计.md \
  doc/设计/iTime-AI提案与日周规划设计.md \
  doc/测试/iTime-测试方案与测试用例.md \
  doc/验收/iTime-产品验收清单.md \
  doc/验收/iTime-Web实施与验证.md \
  doc/计划/iTime-M5订阅来源计划冲突实施计划.md
git commit -m "docs: 记录计划来源冲突证据"
```

## 自检

- 规格覆盖：成功更新、取消、失败保旧、不自动重排、查看、保留、重新生成均有明确步骤。
- 一致性：来源事件、planning revision、冲突和同步在同一事务；新计划版本与用户处理均保留历史。
- 隐私：变化摘要不保存 URL、UID、ICS 正文或 worker lease；导出只包含本人可见事实。
- 容量：计划周、occurrence、changes、block IDs 和批量处理都有上限；超限不回滚有效来源快照。
- 测试：迁移、真实 worker/HTTP/PostgreSQL、并发、同步、导出、注销和多视口 Chromium 均覆盖。
- 范围：不自动调用或确认 AI，不实现订阅 occurrence 单次例外，不扩张为所有输入变化的通用冲突。
