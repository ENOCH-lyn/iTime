# iTime M5 AI 提案与日周规划实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven development where available; otherwise execute each task inline. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付可从规划页和 AI 对话发起、经服务端校验、由用户确认后原子应用的日/周计划，并支持手工纠错、日历投影和完整恢复。

**Architecture:** 新增独立 `planning` 业务域和 AI 能力。生成阶段冻结带账号修订锁的输入快照，模型通过唯一工具调用返回结构化候选，服务端校验后写通用提案；确认阶段再次锁定修订号并创建不可变周计划版本。提案和计划是权威数据，AI 作业输出只保存资源索引。

**Tech Stack:** Python 3.13、FastAPI、SQLAlchemy async、PostgreSQL/Alembic、Pydantic、httpx、Vue 3、TypeScript、Playwright Chromium。

---

### Task 1: 规划、提案和版本数据模型

**Files:**
- Create: `server/src/itime/migrations/versions/0022_ai_proposals_planning.py`
- Create: `server/src/itime/planning/__init__.py`
- Create: `server/src/itime/planning/models.py`
- Test: `server/tests/test_planning_models.py`

- [x] **Step 1: 写迁移失败测试**

在隔离数据库验证：

```python
assert table_names >= {
    "planning_input_revisions",
    "planning_input_snapshots",
    "ai_proposals",
    "ai_proposal_changes",
    "plans",
    "plan_versions",
    "scheduled_blocks",
    "plan_unscheduled_items",
}
```

并直接插入非法 owner 组合、第二次 `source_job_id`、可变历史版本和重复周计划，断言数据库拒绝。

- [x] **Step 2: 执行失败测试**

Run: `cd server && uv run pytest tests/test_planning_models.py -q`

Expected: FAIL，迁移和模型尚不存在。

- [x] **Step 3: 实现迁移和 ORM**

迁移必须：

- 建立设计文档 §3 的表、检查约束、复合外键和索引。
- 扩展 `planning` capability 到配置、作业、治理和质量样本约束。
- 让 planning 作业可选关联对话，同时必须关联一份 planning snapshot。
- 注册规划输入修订触发器；计划当前版本切换也递增修订。
- 对 `PlanVersion`、`ScheduledBlock` 和 `PlanUnscheduledItem` 建不可变触发器。
- 更新对话作业映射约束，使关联对话的 planning 作业必须存在 `ChatGeneration`。
- downgrade 先删除新数据和触发器，再恢复两类旧 capability 约束。

ORM 字段和约束名必须与迁移一致；`AIJob` 增加 `planning_snapshot_id`。

- [x] **Step 4: 验证迁移和模型**

Run:

```bash
cd server
uv run pytest tests/test_planning_models.py -q
uv run alembic upgrade head
uv run alembic check
```

Expected: PASS；head 为 `0022_ai_proposals_planning`；无模型漂移。

- [x] **Step 5: 提交**

```bash
git add server/src/itime/migrations/versions/0022_ai_proposals_planning.py \
  server/src/itime/planning server/tests/test_planning_models.py \
  server/src/itime/ai/models.py server/src/itime/ai/governance_models.py
git commit -m "feat: 建立 AI 提案与计划版本模型"
```

### Task 2: 一致规划输入快照和上下文 API

**Files:**
- Create: `server/src/itime/planning/schemas.py`
- Create: `server/src/itime/planning/snapshot_service.py`
- Create: `server/src/itime/planning/router.py`
- Modify: `server/src/itime/main.py`
- Test: `server/tests/test_planning_inputs.py`

- [x] **Step 1: 写输入和并发失败测试**

覆盖：

```text
day/week 周一边界
无截止和有截止任务
重复 occurrence 与步骤完成进度
归档/回收/完成过滤
busy 固定、取消、调课、补课和订阅投影
上海与纽约 DST
超过 100 候选拒绝而非截断
修订锁与并发写入只产生完整旧快照或完整新快照
跨账号任务 ID 返回统一 not found
```

- [x] **Step 2: 执行失败测试**

Run: `cd server && uv run pytest tests/test_planning_inputs.py -q`

Expected: FAIL，规划上下文路由不存在。

- [x] **Step 3: 实现快照服务**

提供以下稳定入口：

```python
async def lock_planning_revision(
    session: AsyncSession, owner_id: UUID, *, write: bool
) -> PlanningInputRevision: ...

async def build_planning_snapshot(
    session: AsyncSession,
    owner_id: UUID,
    request: PlanningRequestFields,
) -> PlanningInputSnapshot: ...

async def planning_context(
    session: AsyncSession,
    owner_id: UUID,
    request: PlanningRequestFields,
) -> dict: ...
```

实现时复用 `query_occurrences()`、`query_task_occurrences()` 和任务批量详情查询；补充无截止任务及完整步骤，规范化 JSON 后计算 SHA-256。

- [x] **Step 4: 实现只读上下文 API**

`GET /api/v1/planning/context` 接受 scope、date、density、buffer、availability 和重复 `task_id`，返回任务候选、固定占用、当前周计划及默认值。无时间设置返回 `time_settings_required`；无候选返回空结果，不调用 AI。

- [x] **Step 5: 验证并提交**

Run: `cd server && uv run pytest tests/test_planning_inputs.py -q`

```bash
git add server/src/itime/planning server/src/itime/main.py \
  server/tests/test_planning_inputs.py
git commit -m "feat: 提供一致规划输入快照"
```

### Task 3: 确定性计划校验和周版本服务

**Files:**
- Create: `server/src/itime/planning/validation.py`
- Create: `server/src/itime/planning/plan_service.py`
- Test: `server/tests/test_plan_validation.py`
- Test: `server/tests/test_plan_versions.py`

- [x] **Step 1: 写边界失败测试**

用参数化和随机区间覆盖：

```text
[start,end) 相邻允许、实际重叠拒绝
固定占用缓冲和计划块间缓冲
跨午夜固定块
可规划窗口、15 分钟粒度和每日密度上限
日期型/定时截止
DST 缺失/重复时间
未知候选、未知步骤和跨账号 ID
不可拆单块、最小块、总估时守恒
日版本保留同周其他日期，周版本完整替换
手工保存版本冲突和 operation_id 重放
```

- [x] **Step 2: 执行失败测试**

Run:

```bash
cd server
uv run pytest tests/test_plan_validation.py tests/test_plan_versions.py -q
```

Expected: FAIL，校验与版本服务不存在。

- [x] **Step 3: 实现纯校验器**

核心入口：

```python
def normalize_plan_payload(
    snapshot: dict,
    output: PlanningToolOutput,
) -> NormalizedPlan: ...

def validate_plan_blocks(
    snapshot: dict,
    blocks: list[NormalizedBlock],
    unscheduled: list[NormalizedUnscheduledItem],
) -> None: ...
```

校验器不得访问数据库；所有对象只从快照解析，便于对同一输入稳定复算。

- [x] **Step 4: 实现周版本服务**

核心入口：

```python
async def create_plan_version(
    session: AsyncSession,
    owner_id: UUID,
    normalized: NormalizedPlan,
    *,
    source: Literal["ai", "manual"],
    proposal_id: UUID | None,
    expected_plan_version: int,
    operation_id: UUID,
) -> dict: ...
```

服务锁定规划修订和周计划；日范围合并其他日期；写完整不可变版本并切换当前指针。

- [x] **Step 5: 验证并提交**

Run:

```bash
cd server
uv run pytest tests/test_plan_validation.py tests/test_plan_versions.py -q
```

```bash
git add server/src/itime/planning server/tests/test_plan_validation.py \
  server/tests/test_plan_versions.py
git commit -m "feat: 实现计划硬约束与周版本"
```

### Task 4: 提案查询、确认和拒绝 API

**Files:**
- Create: `server/src/itime/planning/proposal_service.py`
- Modify: `server/src/itime/planning/router.py`
- Modify: `server/src/itime/planning/schemas.py`
- Test: `server/tests/test_plan_proposals.py`

- [x] **Step 1: 写提案事务失败测试**

覆盖 pending/applied/rejected/expired/conflicted，有效期、输入修订、基础计划版本、重复确认、同 ID 异内容、客户端超时重试、跨账号、提案与计划原子回滚。

- [x] **Step 2: 执行失败测试**

Run: `cd server && uv run pytest tests/test_plan_proposals.py -q`

- [x] **Step 3: 实现服务和路由**

提供：

```python
async def proposal_detail(session, owner_id, proposal_id) -> dict: ...
async def apply_proposal(session, owner_id, proposal, data) -> dict: ...
async def reject_proposal(session, owner_id, proposal, data) -> dict: ...
```

实现设计文档 §7/§8 的分页、有效状态、冲突详情和幂等语义。提案应用必须调用 Task 3 的同一版本服务。

- [x] **Step 4: 验证并提交**

Run: `cd server && uv run pytest tests/test_plan_proposals.py -q`

```bash
git add server/src/itime/planning server/tests/test_plan_proposals.py
git commit -m "feat: 实现规划提案确认与拒绝"
```

### Task 5: Planning AI provider、worker 和额度终态

**Files:**
- Create: `server/src/itime/planning/provider.py`
- Create: `server/src/itime/planning/job_service.py`
- Modify: `server/src/itime/ai/worker.py`
- Modify: `server/src/itime/ai/provider.py`
- Modify: `server/src/itime/ai/schemas.py`
- Modify: `server/src/itime/ai/usage.py`
- Modify: `server/src/itime/quota/reconciliation.py`
- Test: `server/tests/test_planning_worker.py`
- Test: `server/tests/test_ai_config.py`
- Test: `server/tests/test_quota_service.py`

- [x] **Step 1: 写 provider 与终态失败测试**

本地 HTTP 上游覆盖工具参数分片、可见正文、JSON 响应、未知/多个/缺失工具、结构越界、网络重试、认证失败、超时、取消、租约丢失和重启恢复。

- [x] **Step 2: 执行失败测试**

Run:

```bash
cd server
uv run pytest tests/test_planning_worker.py \
  tests/test_ai_config.py tests/test_quota_service.py -q
```

- [x] **Step 3: 实现 provider**

请求固定 `submit_itime_plan` 工具和 `tool_choice`。解析器分别累计 `delta.content` 与目标工具 `arguments`，总响应限制 1 MiB，参数解析为 `PlanningToolOutput` 后调用确定性校验器。

- [x] **Step 4: 实现接单和 worker**

`POST /planning/generations` 同事务创建快照、作业、初始事件、预留和 operation replay。worker 成功事务写提案、变化、provider cost、额度结算、作业输出和质量样本；失败或取消释放预留。

- [x] **Step 5: 扩展配置合成测试和恢复**

后台 planning 配置测试必须收到合成工具调用 `{ "marker": "09:00" }`。过期作业对账把 planning 路由到规划投影，不能留下永久生成中状态。

- [x] **Step 6: 验证并提交**

Run:

```bash
cd server
uv run pytest tests/test_planning_worker.py tests/test_ai_config.py \
  tests/test_quota_service.py tests/test_ai_governance_lifecycle.py -q
```

```bash
git add server/src/itime/ai server/src/itime/planning \
  server/src/itime/quota/reconciliation.py server/tests
git commit -m "feat: 接入 AI 规划作业与额度结算"
```

### Task 6: 全局对话内生成规划提案

**Files:**
- Modify: `server/src/itime/ai/conversation_schemas.py`
- Modify: `server/src/itime/ai/conversation_service.py`
- Modify: `server/src/itime/ai/conversation_provider.py`
- Modify: `server/src/itime/ai/job_router.py`
- Test: `server/tests/test_ai_plan_proposal_api.py`
- Test: `server/tests/test_ai_conversation_worker.py`

- [x] **Step 1: 写对话规划失败测试**

覆盖：

```text
response_mode=text 完全保持现有行为
response_mode=planning 缺范围拒绝
同一会话 planning/text 互斥
账号三作业上限跨 capability 生效
正文流不含工具 JSON
消息、提案、作业、额度同一成功事务
断线重连和显式取消
```

- [x] **Step 2: 泛化对话接单和终态**

`ConversationMessageCreateInput` 增加：

```python
response_mode: Literal["text", "planning"] = "text"
planning: PlanningRequestFields | None = None
```

planning 作业继续写 `ChatGeneration`，assistant 消息正文使用模型可见说明或服务端保底文案，`AIJob.output` 同时保存 `message_id` 和 `proposal_id`。

- [x] **Step 3: 验证并提交**

Run:

```bash
cd server
uv run pytest tests/test_ai_plan_proposal_api.py \
  tests/test_ai_conversation_api.py tests/test_ai_conversation_worker.py -q
```

```bash
git add server/src/itime/ai server/src/itime/planning server/tests
git commit -m "feat: 支持对话生成规划提案"
```

### Task 7: 规划 Web 页面和手工纠错

**Files:**
- Create: `web/src/lib/plans.ts`
- Create: `web/src/pages/PlansPage.vue`
- Create: `web/src/components/PlanNavigation.vue`
- Create: `web/src/components/PlanPreview.vue`
- Create: `web/src/components/PlanBlockEditor.vue`
- Create: `web/src/planning.css`
- Modify: `web/src/main.ts`
- Modify: `web/src/components/ProductNavigation.vue`
- Modify: `web/src/pages/AccountPage.vue`
- Modify: `web/src/style.css`
- Modify: `web/src/task-workspace.css`
- Test: `web/e2e/planning.spec.ts`

- [x] **Step 1: 写真实浏览器失败测试**

三视口覆盖单日/整周输入、任务选择、生成恢复、失败/取消、提案预览、确认、拒绝、未排项、手工时间和顺序调整、版本冲突、请求编号。

- [x] **Step 2: 建立路由、共享导航和 API 类型**

新增 `/plans?scope=day|week&date=YYYY-MM-DD&proposal=<id>`。第五个导航项在粗指针下保持至少 44px 命中区；共享导航样式移到 `style.css`。

- [x] **Step 3: 实现规划页面**

桌面采用条件栏和预览区；840px 以下按日议程。所有块提供时间输入、步骤选择和上移/下移替代拖拽。确认前明确“尚未写入计划”，未知保存状态保留原 operation ID。

- [x] **Step 4: 验证并提交**

Run:

```bash
cd web
pnpm build
pnpm exec playwright test e2e/planning.spec.ts --project=desktop
pnpm exec playwright test e2e/planning.spec.ts --project=mobile
pnpm exec playwright test e2e/planning.spec.ts --project=tablet
```

```bash
git add web/src web/e2e/planning.spec.ts
git commit -m "feat: 增加日周规划与提案页面"
```

### Task 8: AI 提案卡和统一日历投影

**Files:**
- Modify: `web/src/lib/conversations.ts`
- Modify: `web/src/components/ConversationThread.vue`
- Modify: `web/src/pages/AIPage.vue`
- Modify: `web/src/lib/calendar.ts`
- Modify: `web/src/pages/CalendarPage.vue`
- Modify: `web/src/components/CalendarGrid.vue`
- Modify: `web/src/components/CalendarOccurrencePanel.vue`
- Modify: `web/src/ai-workspace.css`
- Modify: `web/src/calendar.css`
- Modify: `web/e2e/ai-conversations.spec.ts`
- Modify: `web/e2e/calendar.spec.ts`

- [x] **Step 1: 写浏览器失败测试**

从对话切换规划模式并提交，正文流不显示 JSON，完成后出现提案卡；打开规划页确认后，日历显示计划块；提案未确认、拒绝和过期均不进入日历。

- [x] **Step 2: 实现提案卡和模式输入**

对话 composer 使用分段控件选择普通回复或生成计划；规划模式显示范围、日期、密度和缓冲。消息通过 job output 的 `proposal_id` 读取提案卡，不解析正文。

- [x] **Step 3: 实现日历投影**

日历加载范围内当前计划块，合并为 `source=plan`；详情显示任务、步骤、计划版本和前往规划页入口，不提供任务完成按钮。

- [x] **Step 4: 验证并提交**

Run:

```bash
cd web
pnpm build
pnpm exec playwright test e2e/ai-conversations.spec.ts e2e/calendar.spec.ts \
  e2e/planning.spec.ts
```

```bash
git add web/src web/e2e
git commit -m "feat: 联通对话提案与日历计划块"
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
- Modify: `doc/计划/iTime-M5提案与规划实施计划.md`

- [x] **Step 1: 服务端全量验证**

Run:

```bash
cd server
uv run ruff check .
uv run pytest -q
uv run alembic upgrade head
uv run alembic check
uv build --no-sources
```

记录真实通过数量、耗时、迁移 head 和任何未验证项。

- [x] **Step 2: Web 全量验证**

Run:

```bash
cd web
pnpm build
pnpm test:e2e
```

人工检查 `/tmp/itime-ui-evidence/` 的桌面、平板、手机、320px 大字号和深色截图；确认无重叠、裁切、空白画面或提案状态误导。

- [x] **Step 3: 同步文档**

只把实际通过的行为写为已实现；正式第三方模型质量、Edge、实体设备、性能和生产部署未执行时继续明确标注。

- [x] **Step 4: 提交**

```bash
git add README.md doc designer.md
git commit -m "docs: 记录 AI 提案与规划实施证据"
```
