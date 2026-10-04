# iTime M4 AI 备用服务路由实施计划

当前进度：全部任务已实现并完成服务端、真实 Chromium 与运行态验证；首版之外的正式供应商、
Edge/实体设备、性能和生产部署继续按总计划推进。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为四类 AI 能力提供管理员可配置、作业级冻结、成本可追溯且不重置总截止时间的主备
服务路由。

**Architecture:** `AIConfigFallback` 保存能力当前备用顺序，`AIJobConfigRoute` 在接单事务内把
三次 attempt 的配置固化到作业。claim 从快照选择配置并写入 `AIJobAttempt`，provider cost 按
attempt 配置计价；后台路由变更只影响新作业。

**Tech Stack:** Python 3.13、FastAPI、SQLAlchemy async、PostgreSQL 18、Alembic、Vue 3、
TypeScript、Lucide、Playwright Chromium、axe。

---

## 文件结构

持久模型：

- `server/src/itime/migrations/versions/0033_ai_fallback_routes.py`：能力备用行、作业路由、默认
  路由触发器、历史回填和回滚保护。
- `server/src/itime/ai/models.py`：`AIConfigFallback`、`AIJobConfigRoute` 及新增字段。
- `server/tests/test_ai_fallback_models.py`：迁移、约束、回填、不可变成本和级联。

路由与管理：

- `server/src/itime/ai/routing_service.py`：活动路由读取、资格/币种校验、attempt 展开和最坏预留。
- `server/src/itime/ai/schemas.py`：路由 GET/PUT 输入输出。
- `server/src/itime/ai/admin_router.py`：管理路由、发布联动、测试保护和审计。
- `server/tests/test_ai_config.py`、`test_ai_fallback_routing.py`：管理 API 和并发。

作业与成本：

- `server/src/itime/ai/job_service.py`：claim 选择不可变快照配置并写 attempt。
- `server/src/itime/ai/cost_service.py`、`admin_cost_router.py`：实际配置计价与展示。
- `server/src/itime/calendar/screenshot_{router,service}.py`、
  `server/src/itime/ai/conversation_service.py`、
  `server/src/itime/planning/job_service.py`、
  `server/src/itime/review/job_service.py`：四类接单和 loader。
- `server/tests/test_ai_jobs.py`、四类 worker/API 测试、额度和成本测试。

Web：

- `web/src/lib/admin-ai.ts`：路由类型和 GET/PUT。
- `web/src/components/AdminAISettings.vue`、`web/src/style.css`：主服务及两个备用下拉框。
- `web/e2e/admin-management.spec.ts`：配置、发布、备用顺序、并发恢复、真实主服务失败后备用成功
  及逐尝试成本展示。

## Task 1：建立能力路由和作业快照模型

**Files:**
- Create: `server/src/itime/migrations/versions/0033_ai_fallback_routes.py`
- Modify: `server/src/itime/ai/models.py`
- Create: `server/tests/test_ai_fallback_models.py`
- Modify: existing model tests with exact column/head assertions

- [x] **Step 1: 写失败的迁移与约束测试**

测试创建同能力主配置、两个已测试配置和一个作业，断言：

```python
connection.execute(
    """
    INSERT INTO ai_config_fallbacks (
        capability, position, config_version_id
    ) VALUES ('conversation', 1, %s), ('conversation', 2, %s)
    """,
    (fallback_one, fallback_two),
)
```

覆盖 position 0/3、重复位置、重复配置、跨能力配置、删除被引用配置、job route 跨 owner/能力和
attempt 越界。迁移后既有 job 的 1..`max_attempts` 路由、attempt 和 cost 都回填原
`job.config_version_id`。

删除账号后 job route 级联为 0，删除 fallback 关系不删除全局配置。存在不同于主配置的作业路由
时拒绝降级；纯回填路由可无损降到 `0032_subscription_exceptions` 再升级。

- [x] **Step 2: 运行测试确认失败**

```bash
cd server
uv run pytest -q tests/test_ai_fallback_models.py
```

Expected: FAIL，迁移表和 ORM 尚不存在。

- [x] **Step 3: 实现迁移和 ORM**

核心模型：

```python
class AIConfigFallback(Base):
    __tablename__ = "ai_config_fallbacks"
    capability: Mapped[str] = mapped_column(String(50), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    config_version_id: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

class AIJobConfigRoute(Base):
    __tablename__ = "ai_job_config_routes"
    job_id: Mapped[UUID] = mapped_column(primary_key=True)
    attempt_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[UUID]
    capability: Mapped[str] = mapped_column(String(50))
    config_version_id: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
```

`AIConfigCapability.routing_version` 默认 1。迁移 backfill 使用
`generate_series(1, ai_jobs.max_attempts)` 写旧作业路由；`ai_jobs` AFTER INSERT trigger 为
后续直接插入生成相同默认路由。路由 guard 只允许 `queued/attempt_count=0` 的作业替换路由，
首个 attempt 开始后禁止 UPDATE/DELETE，账号或作业级联删除除外。

- [x] **Step 4: 验证并提交**

```bash
cd server
uv run pytest -q tests/test_ai_fallback_models.py \
  tests/test_ai_jobs.py tests/test_ai_provider_cost_models.py
uv run alembic upgrade head
uv run alembic current
uv run alembic check
uv run ruff check src/itime/ai/models.py \
  src/itime/migrations/versions/0033_ai_fallback_routes.py \
  tests/test_ai_fallback_models.py
git add src/itime/ai/models.py \
  src/itime/migrations/versions/0033_ai_fallback_routes.py \
  tests/test_ai_fallback_models.py tests/test_ai_jobs.py \
  tests/test_ai_provider_cost_models.py
git commit -m "feat: 建立AI备用路由模型"
```

## Task 2：实现备用路由管理 API

**Files:**
- Create: `server/src/itime/ai/routing_service.py`
- Modify: `server/src/itime/ai/schemas.py`
- Modify: `server/src/itime/ai/admin_router.py`
- Modify: `server/tests/test_ai_config.py`
- Create: `server/tests/test_ai_fallback_routing.py`

- [x] **Step 1: 写路由管理失败测试**

创建并合成测试三个 conversation 配置，发布主配置后：

```python
saved = client.put(
    "/api/v1/admin/ai-configs/routing/conversation",
    json={
        "expected_routing_version": 2,
        "fallback_config_ids": [str(backup_one), str(backup_two)],
    },
)
assert saved.status_code == 200
assert [item["position"] for item in saved.json()["fallbacks"]] == [1, 2]
```

覆盖空列表、相同顺序无变化、重复 ID、当前主配置、跨能力、未测试、测试失败、缺价格、额度币种不
一致、陈旧版本、并发 PUT、普通用户 401/403 和审计 before/after。发布当前备用为新主配置时该项
自动移除且 routing version 递增。

- [x] **Step 2: 运行测试确认失败**

```bash
cd server
uv run pytest -q tests/test_ai_config.py tests/test_ai_fallback_routing.py
```

Expected: 新路由 404，schema 和 routing version 尚不存在。

- [x] **Step 3: 实现路由服务和 schema**

共享值对象：

```python
@dataclass(frozen=True)
class ActiveConfigRoute:
    capability: str
    routing_version: int
    configs: tuple[AIConfigVersion, ...]

    def attempts(self, max_attempts: int) -> tuple[AIConfigVersion, ...]:
        return tuple(
            self.configs[min(index, len(self.configs) - 1)]
            for index in range(max_attempts)
        )
```

`load_active_config_route()` 验证主配置 published，备用配置为 tested/retired、最近测试成功、价格
完整且币种一致。`replace_fallbacks()` 在能力行锁内按请求顺序原子替换，完全相同不增版。

schema：

```python
class AIConfigRouteUpdate(Input):
    expected_routing_version: int = Field(ge=1)
    fallback_config_ids: list[UUID] = Field(max_length=2)

class AIConfigRouteOutput(BaseModel):
    capability: Capability
    routing_version: int
    primary: AIConfigVersionOutput | None
    fallbacks: list[AIConfigFallbackOutput]
```

- [x] **Step 4: 接入管理路由、发布和测试保护**

在动态 `/{config_id}` 之前注册：

```text
GET /api/v1/admin/ai-configs/routing/{capability}
PUT /api/v1/admin/ai-configs/routing/{capability}
```

PUT 成功写 `ai_config_fallbacks_updated` 审计。`test_config()` 若配置正在备用列表中返回 409；
`publish_config()` 与备用行使用同一能力锁，目标若在备用列表中则移除，并递增 routing version。

- [x] **Step 5: 验证并提交**

```bash
cd server
uv run pytest -q tests/test_ai_config.py tests/test_ai_fallback_routing.py \
  tests/test_admin_ai_governance.py
uv run ruff check src/itime/ai/routing_service.py src/itime/ai/schemas.py \
  src/itime/ai/admin_router.py tests/test_ai_fallback_routing.py \
  tests/test_admin_ai_governance.py
git add src/itime/ai/routing_service.py src/itime/ai/schemas.py \
  src/itime/ai/admin_router.py tests/test_ai_config.py \
  tests/test_ai_fallback_routing.py
git commit -m "feat: 配置AI备用服务顺序"
```

## Task 3：冻结作业尝试路由并按实际配置计价

**Files:**
- Modify: `server/src/itime/ai/routing_service.py`
- Modify: `server/src/itime/ai/job_service.py`
- Modify: `server/src/itime/ai/cost_service.py`
- Modify: `server/src/itime/ai/admin_cost_router.py`
- Modify: `server/tests/test_ai_jobs.py`
- Modify: `server/tests/test_ai_provider_cost_models.py`
- Modify: `server/tests/test_admin_provider_costs.py`

- [x] **Step 1: 写 claim、成本和旧作业稳定性失败测试**

为一个三次尝试作业冻结 `[primary, backup1, backup2]`，连续 claim/retry 后断言：

```python
assert [
    first.config_version_id,
    second.config_version_id,
    third.config_version_id,
] == [primary_id, backup_one_id, backup_two_id]
```

无备用断言三次均为 primary；一个备用断言 `[primary, backup, backup]`。后台修改能力路由后，既有
job route 不变，新作业使用新顺序。provider cost 的冻结价格和后台列表模型均对应实际 attempt
路由；lease 过期重领也进入下一条路由。

- [x] **Step 2: 运行测试确认失败**

```bash
cd server
uv run pytest -q tests/test_ai_jobs.py tests/test_ai_provider_cost_models.py \
  tests/test_admin_provider_costs.py
```

Expected: claim 和 cost 仍固定读取 job 主配置。

- [x] **Step 3: 实现作业快照和 claim 选路**

接单共享函数：

```python
async def replace_job_config_route(
    session: AsyncSession,
    job: AIJob,
    route: ActiveConfigRoute,
) -> None:
    configs = route.attempts(job.max_attempts)
    rows = list(await session.scalars(
        select(AIJobConfigRoute)
        .where(AIJobConfigRoute.job_id == job.id)
        .order_by(AIJobConfigRoute.attempt_number)
        .with_for_update()
    ))
    for row, config in zip(rows, configs, strict=True):
        row.config_version_id = config.id
```

`claim_next_job()` 增加 attempt 后锁定相同 `(job_id, attempt_number)` route；缺失时返回
`ai_job_route_invalid` 终态，不调用 provider。`ClaimedJob.config_version_id` 使用 route config
ID，`AIJobAttempt` 继续通过相同 job/attempt 键关联该不可变路由。

- [x] **Step 4: 按 attempt 配置计价和展示**

四个 work loader 从 `claim.config_version_id` 读取配置。`add_provider_cost()` 以
`(attempt.job_id, attempt.attempt_number)` 读取路由配置和价格；后台成本查询经
`AIJobConfigRoute` 关联实际 `AIConfigVersion`。成功结算继续引用该 cost。

- [x] **Step 5: 验证并提交**

```bash
cd server
uv run pytest -q tests/test_ai_jobs.py tests/test_ai_provider_cost_models.py \
  tests/test_admin_provider_costs.py tests/test_ai_governance_lifecycle.py \
  tests/test_quota_service.py
uv run ruff check src/itime/ai/job_service.py src/itime/ai/cost_service.py \
  src/itime/ai/admin_cost_router.py tests/test_ai_jobs.py \
  tests/test_ai_provider_cost_models.py tests/test_admin_provider_costs.py
git add src/itime/ai/job_service.py src/itime/ai/cost_service.py \
  src/itime/ai/admin_cost_router.py tests/test_ai_jobs.py \
  tests/test_ai_provider_cost_models.py tests/test_admin_provider_costs.py
git commit -m "feat: 按尝试选择并计价AI配置"
```

## Task 4：接入四类接单与真实 failover

**Files:**
- Modify: `server/src/itime/ai/routing_service.py`
- Modify: `server/src/itime/quota/service.py`
- Modify: `server/src/itime/calendar/screenshot_router.py`
- Modify: `server/src/itime/calendar/screenshot_service.py`
- Modify: `server/src/itime/ai/conversation_service.py`
- Modify: `server/src/itime/planning/job_service.py`
- Modify: `server/src/itime/review/job_service.py`
- Modify: four capability worker/API test files

- [x] **Step 1: 写接单快照和最坏预留失败测试**

四类作业分别配置 primary、低价备用和高价备用，接单后断言三条 route 与能力一致；额度 reservation
等于三个配置估算中的最大值。任一配置价格缺失或币种与当天额度不一致时，不创建 job、业务映射
或预留。

- [x] **Step 2: 写真实 provider failover 失败测试**

本地两个 OpenAI 兼容端点分别记录请求。primary 首次返回 429/503，备用返回有效结果：

```python
assert [request.provider for request in state.requests] == [
    "primary",
    "backup",
]
assert [attempt.status for attempt in attempts] == [
    "retryable_failed",
    "succeeded",
]
assert job.deadline_at == accepted_deadline
assert reservation.charged_nanos == backup_cost.total_cost_nanos
```

对 conversation 验证 `content_reset`，对 screenshot/planning/review 各验证结果落入既有业务对象。
401、非法结构和空输出不得调用备用。后台调整路由后，已排队作业仍调用旧快照。

- [x] **Step 3: 实现四类接单**

每个接单事务使用 `load_active_config_route()`；按每个不同配置生成该能力真实 request body 并调用
`estimate_reservation_budget()`，要求币种一致后选择最大 `reserved_nanos`。创建 job 后立即
`flush()` 触发默认主配置路由，再调用 `replace_job_config_route(session, job, route)` 替换为冻结
主备顺序，最后创建业务映射和额度预留。

- [x] **Step 4: 实现四类 loader**

`load_screenshot_work()`、`load_conversation_work()`、`load_planning_work()`、
`load_review_work()` 都改为：

```python
config = await session.get(AIConfigVersion, claim.config_version_id)
if config is None or config.capability != job.capability:
    raise WorkUnavailable(
        "ai_configuration_unavailable",
        "此次尝试的 AI 配置已不可用。",
        "configuration",
    )
```

worker 已使用 `remaining = claim.deadline_at - now()`，保持不变。

- [x] **Step 5: 验证并提交**

```bash
cd server
uv run pytest -q tests/test_calendar_screenshot_imports.py \
  tests/test_ai_conversation_worker.py tests/test_planning_worker.py \
  tests/test_review_worker.py tests/test_quota_service.py \
  tests/test_ai_governance_lifecycle.py
uv run ruff check src/itime/calendar/screenshot_router.py \
  src/itime/calendar/screenshot_service.py \
  src/itime/ai/conversation_service.py src/itime/planning/job_service.py \
  src/itime/review/job_service.py src/itime/quota/service.py
git add src/itime/calendar/screenshot_router.py \
  src/itime/calendar/screenshot_service.py \
  src/itime/ai/conversation_service.py src/itime/planning/job_service.py \
  src/itime/review/job_service.py src/itime/quota/service.py \
  tests/test_calendar_screenshot_imports.py \
  tests/test_ai_conversation_worker.py tests/test_planning_worker.py \
  tests/test_review_worker.py tests/test_quota_service.py
git commit -m "feat: 接入AI作业备用切换"
```

## Task 5：实现后台路由 UI 和浏览器验证

**Files:**
- Modify: `web/src/lib/admin-ai.ts`
- Modify: `web/src/components/AdminAISettings.vue`
- Modify: `web/src/style.css`
- Modify: `web/e2e/admin-management.spec.ts`
- Modify: `server/tests/web_environment.py`

- [x] **Step 1: 写失败的后台和用户流程**

后台流程创建并测试三个 conversation 配置，发布主配置，在“调用顺序”选择备用 1/2 并保存；
断言版本行标签和顺序。第二页面更新路由后，当前页面保存返回 409，重读仍保留用户选择。

用户流程让 primary 返回 429，备用返回流式成功；页面显示一次 reset 后的最终内容，作业
attempt=2。后台成本页显示 attempt 1 主配置失败、attempt 2 备用配置成功。

- [x] **Step 2: 运行测试确认失败**

```bash
cd web
pnpm exec playwright test e2e/admin-management.spec.ts --grep "分别创建"
```

Expected: 后台没有路由控件，worker 第二次仍调用主配置。

- [x] **Step 3: 增加前端 API 和路由编辑器**

`admin-ai.ts` 增加 `AIConfigRoute`、`getAIConfigRoute()` 和 `saveAIConfigRoute()`。后台组件并行读取
当前能力配置与路由，使用两个原生 select 保存有序 ID；选项排除当前主配置和另一备用。发布、
回滚或保存后重读两份状态。

版本行根据 route 显示“主服务”“备用 1”“备用 2”；错误继续使用 `StatusMessage`，409 保留当前
下拉选择并提供重新读取。

- [x] **Step 4: 构建、浏览器专项并提交**

```bash
cd web
pnpm build
pnpm exec playwright test e2e/admin-management.spec.ts --grep "分别创建"
pnpm exec playwright test e2e/admin-management.spec.ts \
  e2e/ai-conversations.spec.ts
git add src/lib/admin-ai.ts src/components/AdminAISettings.vue src/style.css \
  e2e/admin-management.spec.ts ../server/tests/web_environment.py
git commit -m "feat: 管理并验证AI备用路由"
```

desktop、mobile、tablet 均执行；desktop 另切换 320×700 最大字号深色，检查 axe、键盘、控制台、
API 500、Vite 错误层和根级横向溢出。

## Task 6：完整回归与证据

**Files:**
- Modify: `README.md`
- Modify: `doc/README.md`
- Modify: `doc/需求/iTime-需求追踪矩阵.md`
- Modify: `doc/设计/iTime-详细设计文档.md`
- Modify: `doc/设计/iTime-AI作业与临时媒体设计.md`
- Modify: `doc/设计/iTime-全局AI对话设计.md`
- Modify: `doc/设计/iTime-AI额度与成本账本设计.md`
- Modify: `doc/设计/iTime-AI备用服务路由设计.md`
- Modify: `doc/测试/iTime-测试方案与测试用例.md`
- Modify: `doc/验收/iTime-产品验收清单.md`
- Modify: `doc/验收/iTime-Web实施与验证.md`
- Modify: `doc/计划/iTime-M4AI备用服务路由实施计划.md`

- [x] **Step 1: 服务端质量门禁**

```bash
cd server
uv run alembic upgrade head
uv run alembic current
uv run alembic check
uv run pytest -q
uv run ruff check .
uv build --no-sources
```

Expected: 全量通过，head 为 `0033_ai_fallback_routes`，无模型漂移。

- [x] **Step 2: Web 质量门禁**

```bash
cd web
pnpm build
pnpm test:e2e
```

Expected: 完整 Chromium 通过；只允许测试文件明确声明的非桌面深度 skip。

- [x] **Step 3: 运行态和证据**

重启开发 API/AI worker，确认 `/api/ready`、`/admin/ai`、`/ai` 可访问；检查 OpenAPI 路由、
`itime_test_%` 残留为 0、截图无裁切/重叠、console 无未解释错误。

- [x] **Step 4: 同步文档并提交**

记录实际提交、数量、耗时、失败复跑和截图路径；只把备用路由范围标记完成，正式供应商质量、
数据处理地区、M8 性能/恢复、Edge 和真机继续保持未验证。

```bash
git add README.md doc/README.md doc/需求/iTime-需求追踪矩阵.md \
  doc/设计/iTime-详细设计文档.md \
  doc/设计/iTime-AI作业与临时媒体设计.md \
  doc/设计/iTime-全局AI对话设计.md \
  doc/设计/iTime-AI额度与成本账本设计.md \
  doc/设计/iTime-AI备用服务路由设计.md \
  doc/测试/iTime-测试方案与测试用例.md \
  doc/验收/iTime-产品验收清单.md \
  doc/验收/iTime-Web实施与验证.md \
  doc/计划/iTime-M4AI备用服务路由实施计划.md
git commit -m "docs: 记录AI备用服务路由证据"
```

## 自检

- 规格覆盖：按能力备用、测试发布、旧作业稳定、有界切换和总截止时间均有任务。
- 数据一致性：能力路由、作业快照、attempt 和 cost 使用关系外键，避免 JSON 孤儿。
- 成本：预留取路由最坏估算，失败尝试归平台，成功仅结算实际配置一次。
- 并发：route PUT、发布/回滚、claim、lease 过期和后台修改均有明确锁序。
- 安全：Key 不回显，错误/日志不含正文或凭据，跨能力和普通用户访问被拒绝。
- 测试：四类真实 worker、管理 API、额度成本、三视口后台与用户 failover、全量回归均覆盖。
- 范围：不做权重流量、自动熔断、跨能力降级、地域路由或正式供应商质量评测。
