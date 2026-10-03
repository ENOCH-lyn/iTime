# iTime M6 个人统计实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付按 04:00 活动日计算、计划/任务/专注分开呈现的个人日周月统计，并通过真实 PostgreSQL、HTTP 与多视口 Chromium 验证。

**Architecture:** 新建只读 `itime.statistics` 领域模块，按请求从当前计划版本、当前任务结果和专注最新有效投影聚合最多 31 个活动日。服务端完成时区、DST、跨日拆分和去重，Web 只负责范围导航、格式化和可访问趋势呈现，不引入统计表、后台任务或综合效率分。

**Tech Stack:** Python 3.13、FastAPI、SQLAlchemy 2、PostgreSQL 18、Pydantic 2、Vue 3、TypeScript、Vite、Lucide、pytest、Playwright Chromium。

---

## 文件结构

新增服务端文件：

- `server/src/itime/statistics/__init__.py`：个人统计领域包入口。
- `server/src/itime/statistics/time_math.py`：日/周/月范围、04:00 窗口、区间交集和整数秒分配。
- `server/src/itime/statistics/schemas.py`：范围和固定指标响应类型。
- `server/src/itime/statistics/service.py`：计划、任务、专注事实的有界查询、分配、去重和汇总。
- `server/src/itime/statistics/router.py`：本人只读统计 API。

新增服务端测试：

- `server/tests/test_statistics_time_math.py`：范围、DST、交集和秒数守恒。
- `server/tests/test_statistics_api.py`：真实 PostgreSQL/HTTP 指标口径、去重、修订、空状态与权限。

新增 Web 文件：

- `web/src/lib/statistics.ts`：统计响应类型、范围类型和 GET API。
- `web/src/components/StatisticsNavigation.vue`：产品导航、日周月模式和范围移动。
- `web/src/components/StatisticsOverview.vue`：计划、任务、专注三组独立汇总。
- `web/src/components/StatisticsTrend.vue`：三个独立量纲的逐日趋势。
- `web/src/pages/StatisticsPage.vue`：认证、时区初始化、URL 恢复、读取与失败恢复。
- `web/src/statistics.css`：桌面、平板、手机和大字号统计布局。
- `web/e2e/statistics.spec.ts`：真实数据、范围切换、导航、空状态、失败恢复和多视口验证。

修改的共用文件：

- `server/src/itime/main.py`
- `web/src/main.ts`
- `web/src/components/ProductNavigation.vue`
- `web/src/ai-workspace.css`
- `web/src/calendar.css`
- `web/src/focus.css`
- `web/src/planning.css`
- `web/src/reviews.css`
- `web/src/task-workspace.css`
- `README.md`
- `doc/README.md`
- `designer.md`
- `doc/需求/iTime-需求追踪矩阵.md`
- `doc/设计/iTime-详细设计文档.md`
- `doc/计划/iTime-开发总计划.md`
- `doc/测试/iTime-测试方案与测试用例.md`
- `doc/验收/iTime-产品验收清单.md`
- `doc/验收/iTime-Web实施与验证.md`

### Task 1: 实现活动日范围与秒数分配

**Files:**
- Create: `server/src/itime/statistics/__init__.py`
- Create: `server/src/itime/statistics/time_math.py`
- Create: `server/src/itime/statistics/schemas.py`
- Test: `server/tests/test_statistics_time_math.py`

- [ ] **Step 1: 写范围和分配失败测试**

覆盖日/周/月自然范围、当前周/月截止、未来锚点拒绝、上海 04:00 边界、纽约 DST 短日/长日、跨界区间以及比例分配的整数秒守恒。

```python
resolved = resolve_statistics_range(
    scope="month",
    anchor_date=date(2026, 10, 3),
    current_time=datetime(2026, 10, 3, 20, 30, tzinfo=UTC),
    time_zone="Asia/Shanghai",
)
assert resolved.period_start_date == date(2026, 10, 1)
assert resolved.period_end_date == date(2026, 10, 31)
assert resolved.data_end_date == date(2026, 10, 4)
assert sum(allocate_seconds(1801, weights=[1800, 1800])) == 1801
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd server && uv run pytest -q tests/test_statistics_time_math.py`

Expected: FAIL，`itime.statistics` 尚不存在。

- [ ] **Step 3: 实现固定范围和值对象**

在 `schemas.py` 定义：

```python
StatisticsScope = Literal["day", "week", "month"]
METHODOLOGY_VERSION = "statistics_v1"
ACTIVITY_DAY_START_MINUTE = 240
```

在 `time_math.py` 实现不可变 `StatisticsRange`、`ActivityWindow`，以及：

```python
def resolve_statistics_range(
    scope: StatisticsScope,
    anchor_date: date,
    current_time: datetime,
    time_zone: str,
) -> StatisticsRange: ...

def activity_windows(
    start_date: date,
    end_date: date,
    time_zone: str,
) -> tuple[ActivityWindow, ...]: ...

def interval_weights(
    starts_at: datetime,
    ends_at: datetime,
    windows: Sequence[ActivityWindow],
) -> dict[date, float]: ...

def allocate_seconds(
    total_seconds: int,
    weights: Mapping[date, float],
) -> dict[date, int]: ...
```

`allocate_seconds` 先按比例向下取整，再按小数余数降序和日期升序补齐，保证非负、确定性和总和严格相等。

- [ ] **Step 4: 运行单元和静态检查**

Run: `cd server && uv run pytest -q tests/test_statistics_time_math.py && uv run ruff check src/itime/statistics tests/test_statistics_time_math.py`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add server/src/itime/statistics server/tests/test_statistics_time_math.py
git commit -m "feat: 定义个人统计活动日范围"
```

### Task 2: 聚合计划、任务与专注权威事实

**Files:**
- Create: `server/src/itime/statistics/service.py`
- Test: `server/tests/test_statistics_api.py`

- [ ] **Step 1: 写真实数据库聚合失败测试**

在同一账号构造两个计划版本、跨 04:00 当前计划块、普通任务、重复任务完成/跳过/补做、重新打开结果，以及完成、放弃、补记、修订和开放专注。断言旧计划版本和已重开任务不计入，三组指标独立，逐日分配与范围汇总守恒。

```python
result = await read_statistics(
    session,
    owner_id,
    scope="week",
    anchor_date=date(2026, 10, 2),
    current_time=datetime(2026, 10, 4, 8, tzinfo=UTC),
)
assert result["summary"]["planning"]["block_count"] == 1
assert result["summary"]["tasks"]["completed_count"] == 2
assert result["summary"]["tasks"]["skipped_occurrence_count"] == 1
assert result["summary"]["focus"]["manual_count"] == 1
assert sum(day["focus"]["total_seconds"] for day in result["days"]) \
    == result["summary"]["focus"]["total_seconds"]
```

- [ ] **Step 2: 运行聚合测试确认失败**

Run: `cd server && uv run pytest -q tests/test_statistics_api.py -k aggregate`

Expected: FAIL，聚合服务尚不存在。

- [ ] **Step 3: 实现计划与任务聚合**

`read_statistics()` 读取 `UserTimeSettings` 后创建固定日期桶。计划查询只联接 `Plan.current_version_id`，按块 UTC 区间与活动日窗口交集累计秒数，汇总使用块 ID 和任务 ID 集合去重。

任务查询只纳入：

```python
Task.status == "completed"
Task.completed_at >= overall_start
Task.completed_at < overall_end
Task.purged_at.is_(None)
```

重复任务分别查询当前 `completed` 和 `skipped` occurrence，并联接未彻底清理的 `Task`。完成按 `completed_at`、跳过按 `updated_at` 定位活动日；同一状态只进入一个桶。

- [ ] **Step 4: 实现专注段恢复与分配**

一次加载命中范围的 `FocusSession`，再按会话 ID 批量加载 `FocusEvent` 和存在的 `ExecutionRevision`：

```python
if focus.source == "timer" and focus.id not in revised_ids:
    weights = active_segment_weights(events_by_focus[focus.id], windows, calculated_at)
else:
    weights = interval_weights(
        focus.effective_started_at,
        focus.effective_ended_at,
        windows,
    )
allocated = allocate_seconds(effective_seconds(focus, calculated_at), weights)
```

未修订计时会话只把 `start/resume → pause/finish/abandon` 运行段作为权重；运行中最后一段截止服务器时刻。人工补记或修订按有效区间比例分配并增加 `proportional_focus_count`。完成、放弃、人工、修订和进行中分别累计，汇总会话数使用 ID 集合去重。

- [ ] **Step 5: 验证边界、守恒和无 N+1**

测试增加：

- 正常计时跨界且暂停覆盖 04:00，只把真实运行段分到两天。
- 人工/修订专注跨界按比例分配，奇数秒由稳定最大余数补齐。
- 开放专注截止固定 `current_time`，暂停时不继续增长。
- 相同会话跨两天时逐日计数可为 2，范围汇总唯一会话数仍为 1。
- 空范围返回固定零结构；聚合查询数量不随 31 个日期桶线性增长。

Run: `cd server && uv run pytest -q tests/test_statistics_time_math.py tests/test_statistics_api.py && uv run ruff check src/itime/statistics tests/test_statistics_api.py`

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add server/src/itime/statistics/service.py server/tests/test_statistics_api.py
git commit -m "feat: 聚合计划任务与专注统计"
```

### Task 3: 提供本人统计 HTTP API

**Files:**
- Create: `server/src/itime/statistics/router.py`
- Modify: `server/src/itime/main.py`
- Modify: `server/tests/test_statistics_api.py`

- [ ] **Step 1: 写 HTTP 合同和权限失败测试**

覆盖三种 `scope`、必填锚点、未来日期、未设置时区、未登录、账号停用、两个账号同日期隔离、空日固定零结构，以及 `calculated_at`、`methodology_version`、自然范围和实际截止日。

```python
response = client.get(
    "/api/v1/statistics",
    params={"scope": "week", "anchor_date": "2026-10-02"},
)
assert response.status_code == 200
assert response.json()["methodology_version"] == "statistics_v1"
assert len(response.json()["days"]) == 7
```

- [ ] **Step 2: 运行 HTTP 测试确认失败**

Run: `cd server && uv run pytest -q tests/test_statistics_api.py -k http`

Expected: FAIL，路由返回 404。

- [ ] **Step 3: 实现并注册只读路由**

```python
router = APIRouter(prefix="/api/v1/statistics", tags=["个人统计"])

@router.get("")
async def get_statistics(
    request: Request,
    scope: Literal["day", "week", "month"],
    anchor_date: date,
) -> dict:
    current_time = now()
    async with authorized(request) as identity:
        return await read_statistics(
            identity.session,
            identity.user.id,
            scope=scope,
            anchor_date=anchor_date,
            current_time=current_time,
        )
```

在 `main.py` 注册 `statistics_router`。不增加写方法、CSRF、缓存或数据库迁移。

- [ ] **Step 4: 运行统计专项和受影响服务回归**

Run: `cd server && uv run pytest -q tests/test_statistics_time_math.py tests/test_statistics_api.py tests/test_tasks.py tests/test_task_schedules.py tests/test_plan_versions.py tests/test_focus_sessions.py tests/test_execution_revisions.py tests/test_review_sessions.py`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add server/src/itime/main.py server/src/itime/statistics/router.py \
  server/tests/test_statistics_api.py
git commit -m "feat: 提供个人统计接口"
```

### Task 4: 构建日周月统计 Web 工作台

**Files:**
- Create: `web/src/lib/statistics.ts`
- Create: `web/src/components/StatisticsNavigation.vue`
- Create: `web/src/components/StatisticsOverview.vue`
- Create: `web/src/components/StatisticsTrend.vue`
- Create: `web/src/pages/StatisticsPage.vue`
- Create: `web/src/statistics.css`
- Modify: `web/src/main.ts`
- Modify: `web/src/components/ProductNavigation.vue`
- Modify: `web/src/ai-workspace.css`
- Modify: `web/src/calendar.css`
- Modify: `web/src/focus.css`
- Modify: `web/src/planning.css`
- Modify: `web/src/reviews.css`
- Modify: `web/src/task-workspace.css`

- [ ] **Step 1: 定义 API 类型和读取函数**

`statistics.ts` 精确映射服务端三组指标和逐日结构：

```ts
export type StatisticsScope = 'day' | 'week' | 'month'

export async function getStatistics(scope: StatisticsScope, anchorDate: string) {
  const params = new URLSearchParams({
    scope,
    anchor_date: anchorDate,
  })
  return api<StatisticsResult>(`/statistics?${params}`)
}
```

- [ ] **Step 2: 实现页面状态与 URL 恢复**

`StatisticsPage.vue`：

- 登录失效跳转 `/login`。
- 读取或初始化浏览器 IANA 时区。
- 从 `?scope=&date=` 恢复范围，非法值回退到当前活动日。
- 日/周/月切换、上一范围、下一范围和回到当前均更新 URL 后读取。
- 保留上次成功结果；刷新失败显示“仍显示计算于 … 的结果”和请求编号。
- 使用服务端 `current_activity_date` 控制未来导航，不依赖设备日期。

- [ ] **Step 3: 实现三组总览与独立趋势**

`StatisticsOverview.vue` 使用三个并列但不嵌套的区域：

```text
计划：计划块 / 计划时长 / 涉及任务
任务：完成 / 其中重复实例 / 跳过实例
专注：总时长 / 完成 / 放弃 / 补记 / 修订
```

`StatisticsTrend.vue` 输出三个独立图组，每个图使用固定高度、CSS 条形、数值文本和 `aria-label`。最大值为零时保持稳定尺寸并显示零基线；分钟显示不足一分钟时保留“< 1 分钟”，不伪造为零。

- [ ] **Step 4: 实现响应式导航和视觉状态**

`StatisticsNavigation.vue` 使用 `BarChart3`、`ChevronLeft`、`ChevronRight`、`LocateFixed` 和 `LogOut` 图标并提供 tooltip。桌面侧栏固定宽度，平板/手机转顶部布局。

给 `ProductNavigation` 增加：

```ts
{ key: 'stats', to: '/stats', label: '统计', icon: ChartNoAxesCombined }
```

把各工作区窄屏产品导航中专用于产品项的 `repeat(7, ...)` 调整为八项稳定网格；不得修改日历星期或重复规则的七列网格。

- [ ] **Step 5: 添加路由并构建**

在 `main.ts` 增加：

```ts
{ path: '/stats', component: () => import('./pages/StatisticsPage.vue') }
```

Run: `cd web && pnpm build`

Expected: Vue TypeScript 检查和 Vite 生产构建通过。

- [ ] **Step 6: 提交**

```bash
git add web/src/lib/statistics.ts web/src/components/StatisticsNavigation.vue \
  web/src/components/StatisticsOverview.vue web/src/components/StatisticsTrend.vue \
  web/src/pages/StatisticsPage.vue web/src/statistics.css web/src/main.ts \
  web/src/components/ProductNavigation.vue web/src/ai-workspace.css \
  web/src/calendar.css web/src/focus.css web/src/planning.css \
  web/src/reviews.css web/src/task-workspace.css
git commit -m "feat: 构建个人统计工作台"
```

### Task 5: 验证真实统计用户流程

**Files:**
- Create: `web/e2e/statistics.spec.ts`
- Modify: `server/tests/web_environment.py` only if the existing environment needs deterministic statistics fixtures.

- [ ] **Step 1: 写真实浏览器流程**

通过注册、邮件验证和登录取得真实会话，再用同一浏览器上下文的 API 创建任务、当前计划版本、已完成任务、已结束/人工补记专注。不得拦截统计 API 或使用页面内模拟数据。

```ts
await page.goto('/stats?scope=week')
await expect(page).toHaveTitle('统计 · iTime')
await expect(page.getByRole('heading', { name: '本周统计' })).toBeVisible()
await expect(page.getByText('1 小时', { exact: true })).toBeVisible()
```

- [ ] **Step 2: 覆盖交互与恢复**

专项用例覆盖：

- 日/周/月分段控件、前后导航、回到当前和刷新后 URL 恢复。
- 当前计划版本替换后旧块不重复，任务重新打开后完成数刷新，补记/修订后专注时长刷新。
- 放弃、跳过和补记单独显示；空历史范围使用中性空状态。
- 临时中断一次统计 GET 后保留旧结果、显示请求编号并可重试。
- 导航中的统计入口从 AI、任务、规划、日历、专注和复盘页面可达。

- [ ] **Step 3: 覆盖视觉、响应式和无障碍**

每个 Playwright project 检查：

```ts
expect(await page.evaluate(
  () => document.documentElement.scrollWidth <= window.innerWidth,
)).toBe(true)
expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
expect(consoleErrors).toEqual([])
```

保存桌面、手机、平板统计截图，并在手机 project 内切换 `320×700`、深色、最大字号复查最长日期、指标和口径文本。

- [ ] **Step 4: 运行专项与既有导航回归**

Run: `cd web && pnpm exec playwright test e2e/statistics.spec.ts`

Expected: 三种 project 全部通过。

Run: `cd web && pnpm exec playwright test e2e/reviews.spec.ts e2e/focus.spec.ts e2e/planning.spec.ts e2e/tasks.spec.ts e2e/calendar.spec.ts e2e/ai-conversations.spec.ts`

Expected: 受八项导航影响的工作区全部通过。

- [ ] **Step 5: 人工检查截图并提交**

使用图像查看工具检查 `/tmp/itime-ui-evidence/*-statistics-*.png`，确认条形图非空、数值可读、深色与大字号无重叠或裁切。

```bash
git add web/e2e/statistics.spec.ts server/tests/web_environment.py
git commit -m "test: 验证个人统计真实链路"
```

若 `server/tests/web_environment.py` 无需修改，不加入提交。

### Task 6: 全量回归并同步交付证据

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

- [ ] **Step 1: 执行服务端质量门禁**

Run:

```bash
cd server
uv run ruff check .
uv run pytest -q
uv run alembic current
uv run alembic check
uv build --no-sources
```

Expected: 全部通过；迁移 head 仍为 `0026_review_memory` 且无模型漂移。

- [ ] **Step 2: 执行 Web 全量质量门禁**

Run:

```bash
cd web
pnpm build
pnpm test:e2e
```

Expected: 生产构建与全部 Chromium projects 通过。

- [ ] **Step 3: 同步实际实现和证据**

文档只记录真实执行结果：

- `README.md`、`doc/README.md` 和开发总计划把个人统计改为已有实现。
- 追踪矩阵为 `REQ-STATS-01` 填入实际代码、接口和专项证据。
- 详细设计补充当前聚合实现，不改写已确认需求。
- 测试方案注明 `MET-01/02` 已覆盖的准确范围。
- 验收清单和实施记录写入实际命令、通过数、耗时、截图路径与未验证边界。
- `designer.md` 增加 `/stats` 的真实布局和状态。
- 通用同步、导出、注销、后台运营指标、Edge/真机、性能和生产部署继续明确为未完成。

- [ ] **Step 4: 检查提交范围并提交证据**

Run:

```bash
git diff --check
git status --short
git diff --cached --name-only
```

确认无 `.env`、凭据、临时截图、Playwright 输出或测试数据库文件进入暂存。

```bash
git add README.md doc/README.md designer.md \
  doc/需求/iTime-需求追踪矩阵.md doc/设计/iTime-详细设计文档.md \
  doc/计划/iTime-开发总计划.md doc/测试/iTime-测试方案与测试用例.md \
  doc/验收/iTime-产品验收清单.md doc/验收/iTime-Web实施与验证.md
git commit -m "docs: 记录个人统计实施证据"
```

- [ ] **Step 5: 最终核验**

Run: `git status --short --branch && git log -8 --oneline`

Expected: 工作区干净，提交按设计、计划、后端、Web、测试和证据分开；本地开发 API、AI worker、邮件 worker 与 Vite 若仍需后续开发则保持可访问。
