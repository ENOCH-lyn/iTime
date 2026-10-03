# iTime M4 用户额度账本实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: use subagent-driven development where tasks are independent; the main agent owns schema, transaction boundaries, integration review, verification, and commits.

**Goal:** 实现按用户时区 04:00 活动日刷新的 AI 金额额度，包括后台策略、原子预留、成功结算、失败释放、用户账本和管理调整。

**Architecture:** PostgreSQL 是额度权威源。接单事务冻结活动窗口和配置价格，创建 `user` 模式 AI 作业及一对一预留；终态事务直接引用本次最终成功 attempt 产生的 `AIProviderCost`，结算 `min(actual, reserved)` 或释放。余额从每日基础额度、不可变账本和活动预留推导，所有写路径按固定锁顺序串行化。

**Tech Stack:** Python 3.13、FastAPI、SQLAlchemy 2 async、PostgreSQL 18、Alembic、Pydantic、Vue 3、TypeScript、Playwright Chromium。

---

## 文件边界

- 新增 `server/src/itime/quota/models.py`：策略、每日额度、预留、用户账本、管理调整模型。
- 新增 `server/src/itime/quota/service.py`：活动窗口、余额快照、预算估算、预留、结算、释放。
- 新增 `server/src/itime/quota/router.py`：本人额度摘要和账本。
- 新增 `server/src/itime/quota/admin_router.py`：策略、调整和管理账本。
- 新增 `server/src/itime/quota/schemas.py`：金额字符串和 API 输入输出。
- 修改 `server/src/itime/ai/models.py`：`AIJob.quota_mode`。
- 修改 `server/src/itime/ai/{conversation_service,cost_service,job_service}.py` 和 `server/src/itime/calendar/{screenshot_router,screenshot_service}.py`：接单和终态原子接入。
- 新增 `web/src/lib/{quota,admin-quota}.ts`、`QuotaOverview.vue`、`AdminQuotaManagement.vue`。
- 修改 `AccountPage.vue`、`AdminPage.vue`、`main.ts` 和测试环境。
- 新增 `server/tests/test_quota_*.py`、`web/e2e/quota.spec.ts`。

### Task 1：额度模型和迁移

**Files:**
- Create: `server/src/itime/quota/__init__.py`
- Create: `server/src/itime/quota/models.py`
- Create: `server/src/itime/migrations/versions/0021_ai_user_quota.py`
- Modify: `server/src/itime/ai/models.py`
- Modify: `server/src/itime/migrations/env.py`
- Test: `server/tests/test_quota_models.py`

- [ ] **Step 1: 先写迁移约束测试**

覆盖：

```python
def test_quota_schema_enforces_single_reservation_and_immutable_ledger(...):
    # 同一 job 只能一个 reservation。
    # usage_charge 每个 reservation 只能一个。
    # settled/released 字段组合错误触发 CheckViolation。
    # ledger 和 adjustment UPDATE 触发 CheckViolation。
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd server && uv run pytest -q tests/test_quota_models.py`

Expected: `ai_quota_policy_versions` 等表不存在。

- [ ] **Step 3: 建立模型**

核心状态：

```python
QuotaPolicyStatus = Literal["draft", "published", "retired"]
ReservationStatus = Literal["reserved", "settled", "released"]
LedgerKind = Literal["usage_charge", "admin_credit", "admin_debit"]
QuotaMode = Literal["platform", "user"]
```

约束：

- `AIQuotaPolicyState(id=1)` 指向唯一发布版本。
- 策略金额、币种和版本不可修改；首次策略后币种不可更换。
- `AIDailyAllowance` 唯一 `(owner_id, window_start_at, currency)`，保存 UTC 半开窗口和冻结时区。
- `AIQuotaReservation.job_id` 唯一；状态字段完整。
- `AIQuotaLedgerEntry` 与 `AIQuotaAdminAdjustment` 只插入不更新。
- `AIJob.quota_mode='user'` 由延迟约束触发器要求同事务存在 reservation；历史作业默认 `platform`。

- [ ] **Step 4: 执行迁移和模型检查**

Run:

```bash
cd server
uv run alembic upgrade head
uv run alembic check
uv run pytest -q tests/test_quota_models.py
```

Expected: 迁移头为 `0021_ai_user_quota`，无模型漂移，专项通过。

- [ ] **Step 5: 提交**

```bash
git add server/src/itime/quota server/src/itime/ai/models.py \
  server/src/itime/migrations server/tests/test_quota_models.py
git commit -m "feat: 建立 AI 用户额度账本模型"
```

### Task 2：活动窗口、余额和预算服务

**Files:**
- Create: `server/src/itime/quota/service.py`
- Modify: `server/src/itime/ai/usage.py`
- Modify: `server/src/itime/ai/pricing.py`
- Test: `server/tests/test_quota_service.py`

- [ ] **Step 1: 写失败测试**

覆盖：

```python
def test_activity_window_uses_local_four_am_and_dst(...): ...
def test_timezone_change_reuses_covering_window(...): ...
def test_available_balance_is_base_plus_ledger_minus_active_reservations(...): ...
def test_reservation_estimate_uses_full_input_and_max_output(...): ...
def test_concurrent_reservations_cannot_overdraw(...): ...
```

- [ ] **Step 2: 实现纯函数和事务服务**

接口：

```python
@dataclass(frozen=True)
class BudgetEstimate:
    input_tokens: int
    output_tokens: int
    reserved_nanos: int
    estimator_version: str

def activity_window(instant: datetime, time_zone: str, start_minute: int) -> ActivityWindow: ...
def estimate_reservation_budget(config: AIConfigVersion, request_body: dict, input_summary: dict) -> BudgetEstimate: ...
async def quota_snapshot(session, daily: AIDailyAllowance) -> QuotaSnapshot: ...
async def reserve_job_budget(session, *, owner_id, job, accepted_at, request_body, input_summary) -> AIQuotaReservation: ...
```

接单先锁目标 `User`，复用覆盖 `accepted_at` 的既有 daily；否则锁时间设置并按当前策略创建 daily。余额不足抛：

```python
AppError(
    "ai_quota_exhausted",
    "今日 AI 额度不足，本次请求未接收。",
    status=429,
    details={"activity_date": "...", "required": "...", "available": "...", "refresh_at": "..."},
)
```

- [ ] **Step 3: 动态验证并提交**

Run: `cd server && uv run pytest -q tests/test_quota_service.py`

Commit: `feat: 实现 AI 额度活动日与原子预留服务`

### Task 3：后台额度策略

**Files:**
- Create: `server/src/itime/quota/schemas.py`
- Create: `server/src/itime/quota/admin_router.py`
- Modify: `server/src/itime/main.py`
- Modify: `server/src/itime/ai/admin_router.py`
- Test: `server/tests/test_admin_quota_policy.py`

- [ ] **Step 1: 写策略 API 失败测试**

路由：

```text
GET  /api/v1/admin/quota/policies
POST /api/v1/admin/quota/policies
POST /api/v1/admin/quota/policies/{id}/publish
```

创建和发布均带 `operation_id`。发布检查：

- 当前两类已发布 AI 配置都有价格。
- 所有价格币种与策略一致。
- 旧策略原子退役，新策略成为唯一发布版本。
- 响应丢失后原 operation ID 返回原结果。

- [ ] **Step 2: 实现并验证**

Run:

```bash
cd server
uv run pytest -q tests/test_admin_quota_policy.py tests/test_ai_config.py
```

Expected: 权限、CSRF、并发发布、币种错配、幂等和 Key 不回显全部通过。

- [ ] **Step 3: 提交**

Commit: `feat: 实现 AI 每日额度策略后台`

### Task 4：接单预留

**Files:**
- Modify: `server/src/itime/ai/conversation_service.py`
- Modify: `server/src/itime/calendar/screenshot_router.py`
- Modify: `server/src/itime/ai/job_service.py`
- Test: `server/tests/test_quota_admission.py`
- Test: `server/tests/test_ai_conversation_api.py`
- Test: `server/tests/test_calendar_screenshot_imports.py`

- [ ] **Step 1: 冻结完整请求估算**

抽取对话消息构造函数，使接单估算与 worker 使用同一冻结消息集合：

```python
async def conversation_provider_messages(
    session: AsyncSession,
    *,
    owner_id: UUID,
    conversation_id: UUID,
    history_through_sequence: int,
    context_message_ids: list[UUID],
) -> list[dict[str, str]]:
    ...
```

- [ ] **Step 2: 接入两个接单事务**

顺序：

```text
operation replay
→ 鉴权/AI 状态/并发
→ 发布配置及价格
→ 冻结请求和活动窗口
→ 创建 AIJob(quota_mode=user)
→ 创建 Reservation
→ 保存 operation 结果
```

错误 `time_settings_required`、`ai_quota_policy_unavailable`、`ai_quota_pricing_unavailable`、`ai_quota_exhausted` 均不得留下消息、媒体、job 或 reservation。

- [ ] **Step 3: 并发与幂等验证**

Run:

```bash
cd server
uv run pytest -q tests/test_quota_admission.py \
  tests/test_ai_conversation_api.py tests/test_calendar_screenshot_imports.py
```

覆盖两个会话和截图并发竞争、差 1 nanos、相同 operation 重放及不同正文复用。

- [ ] **Step 4: 提交**

Commit: `feat: 接入 AI 作业额度预留`

### Task 5：终态结算与释放

**Files:**
- Modify: `server/src/itime/ai/cost_service.py`
- Modify: `server/src/itime/ai/job_service.py`
- Modify: `server/src/itime/ai/conversation_service.py`
- Modify: `server/src/itime/calendar/screenshot_service.py`
- Modify: `server/src/itime/ai/worker.py`
- Test: `server/tests/test_quota_settlement.py`
- Test: `server/tests/test_ai_conversation_worker.py`

- [ ] **Step 1: 让 attempt 完成返回成本事实**

```python
@dataclass(frozen=True)
class FinalizedAttempt:
    attempt: AIJobAttempt
    provider_cost: AIProviderCost

async def finish_running_attempt(...) -> FinalizedAttempt | None: ...
```

成功路径若缺少 finalized cost，抛 `ai_quota_state_invalid` 并回滚，不允许“结果成功但无账本”。

- [ ] **Step 2: 实现结算和释放**

```python
async def settle_success(session, *, job, provider_cost) -> AIQuotaLedgerEntry:
    # charge = min(actual_cost, reserved)
    # overage = actual_cost - charge
    # reservation -> settled；daily.version += 1

async def release_reservation(session, *, job, reason) -> AIQuotaReservation:
    # reserved -> released；幂等；daily.version += 1
```

可重试失败和 `abandoned` 保留预留；最终失败、超时、取消释放；成功只引用最终成功 attempt。

- [ ] **Step 3: 覆盖竞态**

Run:

```bash
cd server
uv run pytest -q tests/test_quota_settlement.py \
  tests/test_ai_conversation_worker.py tests/test_calendar_screenshot_imports.py
```

断言：取消/成功只能一个终态、429 后只扣成功 attempt、超预留不成负数、迟到 usage 不追扣、事务故障整体回滚。

- [ ] **Step 4: 提交**

Commit: `feat: 实现 AI 额度成功结算与失败释放`

### Task 6：过期预留恢复

**Files:**
- Create: `server/src/itime/quota/reconciliation.py`
- Modify: `server/src/itime/ai/worker.py`
- Test: `server/tests/test_quota_reconciliation.py`

- [ ] **Step 1: 实现限批恢复**

worker 每轮先按现有 job 顺序领取到期 queued/running 作业；沿既有对话/截图终态投影结束作业并释放预留。使用 `FOR UPDATE SKIP LOCKED`，不得绕开消息状态和 SSE 终态事件。

- [ ] **Step 2: 验证 worker 停机恢复**

覆盖 queued 超时、running 租约失效后最终超时、双进程 sweep、重复运行和无孤儿 active reservation。

- [ ] **Step 3: 提交**

Commit: `feat: 恢复 AI 过期作业额度预留`

### Task 7：用户额度 API

**Files:**
- Create: `server/src/itime/quota/router.py`
- Modify: `server/src/itime/main.py`
- Test: `server/tests/test_quota_api.py`

- [ ] **Step 1: 实现本人查询**

```text
GET /api/v1/quota
GET /api/v1/quota/ledger?activity_date=&kind=&before_time=&before_id=
```

摘要返回活动日、冻结时区、刷新时间、基础、调整、已扣、预留、可用和币种；账本只返回本人数据，usage debit 标明 provider/estimated。

- [ ] **Step 2: 验证权限、分页和时区**

Run: `cd server && uv run pytest -q tests/test_quota_api.py`

- [ ] **Step 3: 提交**

Commit: `feat: 提供用户 AI 额度与账本接口`

### Task 8：管理调整和账本 API

**Files:**
- Modify: `server/src/itime/quota/admin_router.py`
- Test: `server/tests/test_admin_quota.py`

- [ ] **Step 1: 实现路由**

```text
GET  /api/v1/admin/quota/users/{user_id}?activity_date=
POST /api/v1/admin/quota/users/{user_id}/adjustments
GET  /api/v1/admin/quota/ledger
```

调整输入：

```json
{
  "operation_id": "uuid",
  "expected_daily_version": 3,
  "activity_date": "2026-10-03",
  "mode": "credit",
  "amount": "0.25",
  "reason": "人工补偿"
}
```

同 operation ID 同内容返回原结果，不同内容拒绝。debit 不能使 `base + ledger - active reservations` 为负；调整与安全审计同事务。

- [ ] **Step 2: 动态验证并提交**

Run: `cd server && uv run pytest -q tests/test_admin_quota.py`

Commit: `feat: 实现用户额度调整与后台账本`

### Task 9：用户额度 Web 页面

**Files:**
- Create: `web/src/lib/quota.ts`
- Create: `web/src/components/QuotaOverview.vue`
- Modify: `web/src/pages/AccountPage.vue`
- Modify: `web/src/main.ts`
- Test: `web/e2e/quota.spec.ts`

- [ ] **Step 1: 实现 `/account/quota`**

展示稳定四项：

```text
今日可用 / 已扣 / 处理中 / 每日基础
```

同时显示活动日、IANA 时区、当地 04:00、下次刷新；活动预留和账本分开列表。估算使用文字标签“平台估算”，错误显示是否保存、恢复动作和请求编号。

- [ ] **Step 2: 三视口验证**

Run:

```bash
cd web
pnpm build
pnpm exec playwright test e2e/quota.spec.ts --grep "用户额度"
```

覆盖成功转扣额、失败/取消释放、余额不足、刷新恢复、axe、控制台、横向溢出和 320×700 大字号深色。

- [ ] **Step 3: 提交**

Commit: `feat: 增加用户 AI 额度页面`

### Task 10：后台额度 Web 页面

**Files:**
- Create: `web/src/lib/admin-quota.ts`
- Create: `web/src/components/AdminQuotaManagement.vue`
- Modify: `web/src/pages/AdminPage.vue`
- Modify: `web/src/main.ts`
- Modify: `server/tests/web_environment.py`
- Test: `web/e2e/quota.spec.ts`

- [ ] **Step 1: 实现 `/admin/quota`**

一个侧栏入口，页内使用“策略 / 用户调整 / 账本”标签。策略创建发布、用户搜索和增减、账本筛选均保留输入与明确错误；调整确认对话框展示调整前后金额。

- [ ] **Step 2: 真浏览器验证**

Run:

```bash
cd web
pnpm build
pnpm exec playwright test e2e/quota.spec.ts --grep "后台额度"
```

覆盖策略版本、新活动日生效、调整未知保存状态同 operation 对账、版本冲突、三视口和无障碍。

- [ ] **Step 3: 提交**

Commit: `feat: 增加 AI 额度管理后台`

### Task 11：全量回归与证据

**Files:**
- Modify: `README.md`
- Modify: `doc/README.md`
- Modify: `doc/需求/iTime-需求追踪矩阵.md`
- Modify: `doc/设计/iTime-AI额度与成本账本设计.md`
- Modify: `doc/验收/iTime-Web实施与验证.md`
- Modify: `doc/验收/iTime-产品验收清单.md`

- [ ] **Step 1: 服务全量**

```bash
cd server
uv run ruff check .
uv run pytest -q
uv run alembic current
uv run alembic check
uv build --no-sources
```

- [ ] **Step 2: Web 全量**

```bash
cd web
pnpm build
pnpm test:e2e
```

- [ ] **Step 3: 人工检查截图**

检查桌面、手机、平板、320×700 大字号深色下金额、长邮箱、UUID、错误和确认对话框无重叠或横向滚动。

- [ ] **Step 4: 同步证据并提交**

只记录实际命令和结果，保留“支付/充值不属于首版”和“业务提案尚待实现”边界。

Commit: `docs: 记录 AI 用户额度实施证据`
