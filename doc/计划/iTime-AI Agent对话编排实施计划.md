# iTime AI Agent 对话编排实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让全局会话自动识别意图、调用本人只读工具、生成任务/日历/规划/专注/复盘/记忆提案，并在用户确认后执行。

**Architecture:** 保留现有会话作业、SSE、主备路由、额度和治理链路，在 `conversation` worker 中加入有界工具循环。读取工具即时执行；写工具只创建类型化提案，确认 API 再调用现有领域服务并重新校验版本。

**Tech Stack:** FastAPI、SQLAlchemy、PostgreSQL、Pydantic、OpenAI-compatible tool calling、Vue 3、Element Plus、Playwright

---

### Task 1: 建立通用提案数据模型

**Files:**
- Create: `server/src/itime/migrations/versions/0037_agent_proposals.py`
- Create: `server/src/itime/ai/agent_models.py`
- Create: `server/src/itime/ai/agent_schemas.py`
- Modify: `server/src/itime/models.py`
- Test: `server/tests/test_agent_proposal_models.py`

- [ ] **Step 1: 写失败的模型测试**

覆盖本人复合外键、提案 kind、变化顺序唯一、待确认/已应用/已拒绝状态、七天过期、来源消息和目标版本字段。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd server && uv run pytest tests/test_agent_proposal_models.py -q`

Expected: FAIL，模型和迁移尚不存在。

- [ ] **Step 3: 实现迁移和模型**

新增 `ActionProposal`、`ActionProposalChange`，kind 允许 `task|calendar|plan|focus|review|memory|timetable_import|mixed`。已有规划提案保留并通过适配层读取，不在迁移中重写历史正文。

- [ ] **Step 4: 验证迁移与测试**

Run: `cd server && uv run alembic upgrade head && uv run pytest tests/test_agent_proposal_models.py tests/test_planning_models.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

Run: `git add server/src/itime/migrations server/src/itime/ai/agent_models.py server/src/itime/ai/agent_schemas.py server/src/itime/models.py server/tests/test_agent_proposal_models.py && git commit -m "feat: 建立 Agent 通用提案模型"`

### Task 2: 建立所有者隔离的读取工具

**Files:**
- Create: `server/src/itime/ai/agent_tools.py`
- Test: `server/tests/test_agent_tools.py`

- [ ] **Step 1: 写工具合同测试**

覆盖 `search_tasks`、`read_calendar`、`read_plan`、`read_focus`、`read_review` 和 `read_memories` 的 schema、结果上限、稳定 ID、版本、正文裁剪和双账号隔离。

- [ ] **Step 2: 实现工具注册表**

每个工具声明名称、Pydantic 输入、输出清理器、最大结果数和是否只读。执行函数只接收认证得到的 `owner_id`，不接受模型提供 owner。

- [ ] **Step 3: 验证**

Run: `cd server && uv run pytest tests/test_agent_tools.py -q`

Expected: PASS，越权 ID 统一表现为不可见。

- [ ] **Step 4: 提交**

Run: `git add server/src/itime/ai/agent_tools.py server/tests/test_agent_tools.py && git commit -m "feat: 增加 Agent 只读工具"`

### Task 3: 实现有界工具调用协议

**Files:**
- Modify: `server/src/itime/ai/conversation_provider.py`
- Modify: `server/src/itime/ai/provider.py`
- Modify: `server/src/itime/ai/usage.py`
- Create: `server/src/itime/ai/agent_provider.py`
- Test: `server/tests/test_agent_provider.py`

- [ ] **Step 1: 写 provider 失败测试**

覆盖文本回复、单个读取工具、提案工具、分片参数、未知工具、并行冲突写工具、四轮上限、超大参数、DeepSeek 工具调用形态和 usage 估算。

- [ ] **Step 2: 实现请求与解析**

会话请求携带版本化工具 schema 和 `tool_choice=auto`；解析器严格区分文本、读取工具和提案工具，不从正文提取 JSON。

- [ ] **Step 3: 实现循环预算**

最大四轮、每轮最多一个写提案、读取结果总量有界；所有轮次共享作业 deadline、取消信号、主备配置和正文大小上限。

- [ ] **Step 4: 验证**

Run: `cd server && uv run pytest tests/test_agent_provider.py tests/test_ai_usage.py tests/test_ai_fallback_routing.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

Run: `git add server/src/itime/ai server/tests/test_agent_provider.py && git commit -m "feat: 支持 Agent 工具调用循环"`

### Task 4: 实现领域提案构建器

**Files:**
- Create: `server/src/itime/ai/agent_proposals.py`
- Modify: `server/src/itime/planning/validation.py`
- Modify: `server/src/itime/planning/job_service.py`
- Test: `server/tests/test_agent_proposals.py`

- [ ] **Step 1: 写各领域提案校验测试**

覆盖任务新建/修改/完成/归档，日历新增/移动/取消，计划，专注开始，复盘动作和记忆增改停用；覆盖未知 ID、非法状态、跨账号、数量限制和 mixed 原子性。

- [ ] **Step 2: 实现规范化构建器**

每种工具参数先进入领域 Pydantic schema，再读取本人当前对象形成 before/after 和 expected version。计划提案复用 `normalize_plan_payload`，不复制时间约束。

- [ ] **Step 3: 验证**

Run: `cd server && uv run pytest tests/test_agent_proposals.py tests/test_planning_inputs.py tests/test_tasks.py tests/test_calendar_series.py tests/test_review_sessions.py -q`

Expected: PASS。

- [ ] **Step 4: 提交**

Run: `git add server/src/itime/ai/agent_proposals.py server/src/itime/planning server/tests/test_agent_proposals.py && git commit -m "feat: 生成类型化 Agent 提案"`

### Task 5: 接入会话 worker 与恢复

**Files:**
- Modify: `server/src/itime/ai/conversation_models.py`
- Modify: `server/src/itime/ai/conversation_service.py`
- Modify: `server/src/itime/ai/worker.py`
- Modify: `server/src/itime/ai/job_service.py`
- Test: `server/tests/test_ai_conversation_worker.py`
- Test: `server/tests/test_agent_conversation_worker.py`

- [ ] **Step 1: 写会话闭环失败测试**

模拟“明天上午学习两小时线性代数”，断言 worker 读取任务与日历、生成计划提案、消息引用提案且未修改计划；覆盖刷新恢复、取消、租约丢失、重试和额度终态。

- [ ] **Step 2: 扩展生成快照**

保存工具 schema 版本、工具调用摘要和结构化附件引用；事件增加 `tool_state`，但最终业务结构仍从普通 API 读取。

- [ ] **Step 3: 接入 Agent 循环**

普通文本结束为现有回复；读取工具继续当前作业；写工具创建提案并完成 assistant 消息。失败事务不得留下半个提案或扣额。

- [ ] **Step 4: 验证**

Run: `cd server && uv run pytest tests/test_agent_conversation_worker.py tests/test_ai_conversation_worker.py tests/test_ai_conversation_api.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

Run: `git add server/src/itime/ai server/tests/test_agent_conversation_worker.py server/tests/test_ai_conversation_worker.py server/tests/test_ai_conversation_api.py && git commit -m "feat: 在会话中编排 Agent 工具"`

### Task 6: 实现提案确认 API 与领域执行

**Files:**
- Create: `server/src/itime/ai/agent_proposal_service.py`
- Create: `server/src/itime/ai/agent_router.py`
- Modify: `server/src/itime/main.py`
- Test: `server/tests/test_agent_proposal_api.py`

- [ ] **Step 1: 写确认、拒绝和冲突测试**

覆盖列表、详情、确认、拒绝、重复操作、版本冲突、过期、mixed 回滚和双账号不可见。

- [ ] **Step 2: 实现 API**

提供 `GET /api/v1/ai/proposals`、`GET /{id}`、`POST /{id}/apply`、`POST /{id}/reject`。确认服务按固定锁顺序调用现有领域函数，并在同一事务写同步事件和操作结果。

- [ ] **Step 3: 验证**

Run: `cd server && uv run pytest tests/test_agent_proposal_api.py tests/test_ai_plan_proposal_api.py -q`

Expected: PASS。

- [ ] **Step 4: 提交**

Run: `git add server/src/itime/ai/agent_proposal_service.py server/src/itime/ai/agent_router.py server/src/itime/main.py server/tests/test_agent_proposal_api.py && git commit -m "feat: 增加 Agent 提案确认接口"`

### Task 7: 改造会话 Web

**Files:**
- Modify: `web/src/lib/conversations.ts`
- Create: `web/src/lib/agent-proposals.ts`
- Modify: `web/src/pages/AIPage.vue`
- Modify: `web/src/components/ConversationComposer.vue`
- Modify: `web/src/components/ConversationThread.vue`
- Replace: `web/src/components/ProposalMessageCard.vue`
- Test: `web/e2e/ai-agent.spec.ts`

- [ ] **Step 1: 写自然语言规划和提案确认 E2E**

输入“明天上午学习两小时线性代数”，断言无模式选择、消息流出现待确认提案、确认后日历可见；再覆盖调整、拒绝、冲突和普通问答。

- [ ] **Step 2: 移除模式控件**

消息请求不再传前端选择的 `response_mode` 和 planning 表单；旧 API 字段在服务端保留兼容期。composer 只保留附件、文本、发送和真实活动状态。

- [ ] **Step 3: 实现通用提案卡**

按 kind 展示关键变化，提供确认、调整、拒绝；复杂编辑路由到任务、日历、规划、专注或复盘页，返回后重新同步消息附件状态。

- [ ] **Step 4: 验证并提交**

Run: `cd web && pnpm build && pnpm exec playwright test e2e/ai-conversations.spec.ts e2e/ai-agent.spec.ts`

Expected: 三视口 PASS。

Run: `git add web/src/lib web/src/pages/AIPage.vue web/src/components/Conversation*.vue web/src/components/ProposalMessageCard.vue web/e2e/ai-agent.spec.ts && git commit -m "feat: 将 AI 会话改为 Agent 主入口"`

### Task 8: 完成真实 DeepSeek 与全链路验证

**Files:**
- Modify: `web/e2e/deepseek-live.spec.ts`
- Modify: `doc/验收/iTime-Web实施与验证.md`

- [ ] **Step 1: 增加真实模型场景**

覆盖普通问答、自动规划、任务提案、日历提案、复盘、记忆和误触发保护。使用本地忽略配置读取 Key，不写入测试输出或文档。

- [ ] **Step 2: 执行服务端完整验证**

Run: `cd server && uv run ruff check . && uv run pytest -q && uv build --no-sources`

Expected: 全部 PASS。

- [ ] **Step 3: 执行 Web 和 DeepSeek 验证**

Run: `cd web && pnpm build && pnpm test:e2e && pnpm test:e2e:deepseek`

Expected: Chromium 三视口与真实 DeepSeek 场景 PASS。

- [ ] **Step 4: 执行真实 Edge**

Run: `cd web && ITIME_E2E_BROWSER_CHANNEL=msedge pnpm exec playwright test e2e/ai-agent.spec.ts e2e/full-user-journey.spec.ts`

Expected: 真实 Edge 三视口 PASS。

- [ ] **Step 5: 记录证据并提交**

Run: `git add web/e2e/deepseek-live.spec.ts doc/验收/iTime-Web实施与验证.md && git commit -m "test: 验证 AI Agent 业务闭环"`
