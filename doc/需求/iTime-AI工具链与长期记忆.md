# iTime AI 工具链与长期记忆

版本：0.1；更新：2026-10-10。本文承接[范围与 AI 融合重构 v2](iTime-范围与AI融合重构v2.md) 第 3 节与 [ADR-014](../架构/iTime-ADR架构决策文档.md#adr-014ai-function-tools-与长期记忆)，定稿 S2 阶段的工具 schema、权限矩阵、预览-确认闭环、长期记忆语义与服务端编排协议。原规格中与本文冲突的条目以本文为准。

## 1. 定稿基线

1. **工具调用取代上下文注入**：废弃 `conversation_service` 中 `select_relevant_context` 等"把过去对话片段塞进 prompt"的通道（代码保留直至 S2 实现验证通过再移除）；模型以 function tools 为唯一业务数据入口。
2. **三类权限**：只读工具（AI 直接执行）、写预览工具（AI 产生 proposal、UI 卡片展示、用户点确认后服务端在事务中应用）、长期记忆工具（显式写、可召回、可增删改）。
3. **规划/复盘不固定模板**：规划页只保留 CRUD/展示；复盘页只保留结果展示；生成和多轮追问全部在 AI 对话里完成。system prompt 层只写"合理追问原则"，不写死问题序列，由模型按当日事实组织追问顺序。
4. **长期记忆复用 `memories` 表**：不新建 `ai_memory` 表。现表字段 `kind/title/content/source_type/version/软删除/30 天回收` 已满足；S2 增量加 `tags String[]` 与 `last_used_at TIMESTAMPTZ`，`source_type` 枚举增加 `ai_tool`。既有的 `review_proposal` 建议路径不动。
5. **新对话首条消息自动召回**：服务端编排，不是模型任意拉取；取 Top-K 条拼入 system prompt，上限明确。
6. **单次对话资源硬限界**：工具调用次数、累计 token、单工具调用超时、并发度均在 `AIConfigVersion` 里按版本配置，不写死常量。

## 2. 工具 schema 清单

每个工具的入参、校验、权限、副作用、幂等 key、速率/预算在下表定稿；JSON Schema 的字段细节在 `doc/设计/iTime-AI工具链与长期记忆.md` 落地。

### 2.1 只读工具（AI 直接执行，无需确认）

| 名称 | 入参 | 响应 | 校验 | 速率 |
| --- | --- | --- | --- | --- |
| `get_today` | 无 | `{"activity_date": "YYYY-MM-DD", "iana_tz": "...", "current_time": "ISO8601"}` | 从用户 time_settings 读 | 单次对话 10 次 |
| `get_time_settings` | 无 | `{"iana_tz", "activity_day_start": "04:00", "week_start": "monday"}` | 读 user_profile | 单次对话 5 次 |
| `get_calendar_events` | `date_from, date_to`(ISO date, 跨度 ≤ 93 天) | 事件数组 + 下页游标 | 用户所有权；跨度上限；重复事件走 `query_occurrences` 即时展开 | 单次对话 30 次 |
| `get_event` | `event_id` | 事件详情，含状态/卡点/实际用时字段 | 用户所有权 | 20 次 |
| `list_tasks` | `filter: {status, due_from, due_to, tag, keyword, cursor, limit}` (limit ≤ 50) | 任务数组 + 下页游标 | 用户所有权 | 30 次 |
| `get_task` | `task_id` | 任务详情含 steps/子任务 | 用户所有权 | 20 次 |
| `search_tasks` | `query`(≤ 200 字), `limit`(≤ 20) | 任务数组 | 中文/英文全文；仅返回未软删 | 20 次 |
| `list_plans` | `filter: {kind, period_from, period_to}` | 规划数组 | 用户所有权 | 20 次 |
| `get_plan` | `plan_id` | 规划详情含 blocks | 用户所有权 | 20 次 |
| `list_reviews` | `date_from, date_to` | 复盘摘要数组 | 用户所有权 | 20 次 |
| `get_review` | `review_id` | 复盘详情含补充事实 | 用户所有权 | 20 次 |
| `list_difficulties` | `date_from, date_to` | 困难数组 | 用户所有权 | 20 次 |
| `search_memory` | `query`(≤ 200 字), `limit`(≤ 10) | 命中记忆数组 `{id, version, kind, title, content, tags, last_used_at}` | 仅活跃（未软删）记忆；更新 `last_used_at` | 20 次 |
| `list_memories` | `filter: {kind, tags, lifecycle: active\|trashed}` | 记忆数组 + 下页游标 | 用户所有权 | 10 次 |

**只读响应中的"未知"必须显式**：缺失/软删/超范围时返回 `{"missing": [...]}` 字段而不是无提示的空数组；AI 据此追问而不是编造。

### 2.2 写预览工具（AI 生成 proposal，UI 卡片，用户点确认后在事务中应用）

每个写工具调用都要返回 `{"proposal_id": "...", "preview": {...}, "requires_confirmation": true}`，服务端把 proposal 入对应模块的 `*_proposals` 或新建的 `ai_action_proposals` 表（具体位置由 S2 详设决定），客户端通过现有 proposal 应用通道走确认。幂等 key = `(owner_id, conversation_id, message_id, tool_name, operation_id)`。

| 名称 | 入参要点 | 落地动作 | 版本冲突策略 |
| --- | --- | --- | --- |
| `propose_plan` | `kind, period_from, period_to, blocks[], rationale` | 新建 planning Proposal | 走现有 planning 版本检查 |
| `confirm_plan` | `proposal_id, expected_version` | 应用 planning 提案 | 既有逻辑；版本不符 409 |
| `update_plan_block` | `plan_id, block_id, patch, expected_version` | 新建 planning Proposal 的子变更 | 规划版本冲突返回预览失败 |
| `list_plan_blocks` | `plan_id` | 只读；归入只读组 | - |
| `create_task` | `title, due_at?, estimated_minutes?, tags?, steps?, project_id?` | 新建任务 proposal | 任务 UI 侧覆盖版本冲突 |
| `update_task` | `task_id, patch, expected_version` | 任务字段 patch 预览 | 返回当前 version 让用户重新确认 |
| `complete_task` | `task_id, actual_minutes?, note?, expected_version` | 标记完成 | 版本冲突重走预览 |
| `start_review` | `date` | 创建或拉起当日 ReviewSession；返回当日事实快照（由后端编排拉 `get_calendar_events` / `list_tasks` / `list_difficulties` 的只读结果拼合） | 幂等；同日已有返回现有 session |
| `write_review_report` | `date, summary, highlights[], blockers[], next_focus[], supplemental_facts?` | 预览 review 报告；用户确认后写入 `review_report_versions` | 既有版本冲突走 409 |
| `save_memory` | `kind, title, content, tags?, source_conversation_id` | 预览→确认后 insert 到 `memories`, `source_type='ai_tool'` | 新增记录无版本冲突；重复内容由 UI 提示合并 |
| `update_memory` | `memory_id, kind?, title?, content?, tags?, expected_version` | 预览→确认后更新 | 既有 version 检查 |
| `trash_memory` | `memory_id, expected_version` | 预览→确认后软删（进 30 天回收） | 既有逻辑 |
| `create_difficulty` | `date, severity, title, note, related_task_id?` | 预览困难 | - |

**预览-确认闭环**：
1. AI 调写工具 → 服务端返回 `proposal_id` + 卡片渲染数据。
2. 对话流通过 SSE 新事件 `tool_proposal_pending` 推送到前端；前端在消息内渲染卡片，带「确认 / 修改 / 拒绝」按钮。
3. 用户点"确认"：前端调 `POST /api/v1/ai/proposals/{proposal_id}:apply`，服务端在事务中应用、更新 proposal 状态、SSE 推 `tool_proposal_applied`。
4. 用户点"拒绝"或超 7 天未处理：proposal 进 `rejected` / `expired`；AI 下一轮对话读取状态并自然回复。
5. 失败/取消按 ADR-008 不扣用户额度；审计记录按 REQ-AUDIT 既有口径。

### 2.3 工具调用预算与硬限界

单次对话（即单条用户消息触发的一轮 AI 响应）内：
- 工具调用总次数 ≤ `AIConfigVersion.tool_call_budget`（S2 默认建议 20，上限按版本可改）；
- 累计 token（prompt + 响应 + 工具 I/O）≤ `AIConfigVersion.token_budget`；
- 单工具调用超时：只读 10s、写预览 15s；
- 工具调用之间不允许并发（避免资源竞态和不可预期副作用）；
- 工具调用链路计入 `ai_jobs.tool_calls` JSONB 字段，每条含 `name, args_hash, duration_ms, status`，审计留 7 天与正文日志同步销毁。

超限时返回 `tool_budget_exceeded`，模型在 system prompt 层被告知"预算用尽，请总结现状并询问用户要不要继续"，不自动打断对话。

## 3. 权限矩阵

| 工具组 | 调用主体 | 用户确认 | 读取范围 | 写入范围 |
| --- | --- | --- | --- | --- |
| 只读 | 服务端代执行 | 否 | 调用用户自己的数据 | 无（含 `search_memory` 更新 `last_used_at`，视作读副作用） |
| 写预览 | 服务端代执行 → 返回 proposal | 必须 | 调用用户自己的数据 | 无（proposal 不是真正写）|
| 用户确认后应用 | 用户显式触发 | 已满足 | - | 调用用户自己的数据，走既有领域 service |
| 管理员 | **不可通过 AI 工具触发** | - | - | 管理员操作走后台独立入口 |

模型拿不到任何用户 ID、session cookie、平台 Key；工具 I/O 全由服务端注入上下文。

## 4. 长期记忆语义

### 4.1 字段扩展（基于现有 `memories` 表）

增量字段（S2 迁移）：
- `tags: String[]`（PostgreSQL `TEXT[]`，默认空数组，上限 10 个 tag，每个 ≤ 24 字符）；
- `last_used_at: TIMESTAMPTZ`（nullable，由 `search_memory` 命中或新对话首次召回时更新）；
- `source_type` 枚举追加 `'ai_tool'`，`source_conversation_id UUID NULL`（指向触发保存的会话；FK 可选，便于审计）；

既有 `source_type IN ('review_proposal', 'manual')` 的 check constraint 需要在迁移中改为 `IN ('review_proposal', 'manual', 'ai_tool')`，并允许 `ai_tool` 的 `source_proposal_id IS NULL` 但 `source_conversation_id IS NOT NULL`。

### 4.2 CRUD 与召回

- **CRUD**：复用 [`memory_service.create_memory` / `update_memory` / `trash_memory` / `restore_memory`](../../server/src/itime/review/memory_service.py)；新增 `save_memory` function tool 走相同通路但 source_type 固定为 `ai_tool`。
- **召回**：
  - `search_memory(query, limit)`：先用关键词命中 title/content/tags（PostgreSQL `pg_trgm` 或 ILIKE 回退，具体由设计稿定），按 `last_used_at DESC, updated_at DESC` 排序，`limit ≤ 10`；命中后批量更新 `last_used_at`。
  - **冷启动**：如果 query 为空或命中为 0，fallback 到 `ORDER BY updated_at DESC LIMIT 5` 的"最近记忆"。
- **自动召回**：对话 service 在"新会话的第一条用户消息"处 **强制调一次** `search_memory(query=第一条消息内容, limit=5)`，把结果通过现有 `memory_system_message` 拼成 system message 注入 prompt。之后由模型自行调用 `search_memory` 二次查询；不再做"每条消息都自动召回"。

### 4.3 隐私/审计/生命周期

- 用户在 `/me/长期记忆` 可见、可增删改、可导出（走既有数据导出通道）。
- 删除 → 软删 + 30 天回收，与 REQ-MEM-01 一致。
- 记忆**不参与质检训练**（REQ-PRIV-02），`governance_service.create_quality_sample_if_enabled` 的采样范围不扩展到 `memories` 表。
- 账号注销按 REQ-DATA-02：pending_deletion 期间不删，7 天撤销期后由注销 worker 清理。
- 超级管理员查看记忆内容记审计，与正文访问审计规则一致。
- `search_memory` 命中更新 `last_used_at` 不生成独立 SyncFeed 事件（否则噪声太大），但 CRUD 事件保留。

## 5. 对话编排变更

### 5.1 流程重构

现 `conversation_service.py` 中"用户消息入列 → 组装 ChatCompletion messages → 单次模型调用 → 回填 assistant message"的单轮链路改为**工具调用循环**：

1. 入列用户消息并开 ai_job。
2. 系统 message 由以下几段拼成（按序）：
   - 基础 system prompt（含"合理追问原则"：引用上下文日历/任务/困难，必要时追问，不编造数据，不自建固定模板）；
   - `search_memory` 自动召回结果（首条消息）；
   - 当前时间上下文（`get_today` 结果，避免模型用自己的时间）。
3. 调模型，若响应含 `tool_calls`：
   - 校验工具名在白名单；校验参数 schema；校验预算未超；
   - 执行只读工具 → 把结果作为 `tool` 消息回灌模型；
   - 执行写预览工具 → 建 proposal、SSE 推 `tool_proposal_pending`、同时把 proposal_id 作为 `tool` 消息回灌模型；
   - 回到第 3 步；
4. 响应不含 `tool_calls` → 作为 assistant 最终消息落库、SSE 推 `message_completed`、结算额度。
5. 任何一步失败：按 ADR-005 的失败/重试规则；预算超限返回 `tool_budget_exceeded` 并把这一事实作为 system 提示让模型优雅收尾。

### 5.2 关键代码位置

- 新增 `server/src/itime/ai/tools/{__init__.py, registry.py, readonly.py, write_previews.py, confirm_router.py}`；
- `conversation_service.py` 的 `_build_messages` 系函数迁移到 `tools/registry.py` 的 prompt 组装模块；
- 预算与审计字段加到 `AIJob.tool_calls JSONB`（新增迁移）；
- SSE 事件扩展到 `AIJobEvent` 的 `event_type` 枚举：`tool_called`、`tool_proposal_pending`、`tool_proposal_applied`、`tool_proposal_rejected`、`tool_budget_exceeded`。

### 5.3 复盘工作流（合理追问，不是固定模板）

system prompt 层写入下列**原则**而非问题序列：

> 当用户表达"复盘今天"、"回顾今天"或类似意图时：
> 1. 先调 `get_today` 得到活动日；再调 `get_calendar_events`、`list_tasks(status=today)`、`list_difficulties(date_from=today, date_to=today)` 拉取当日事实；
> 2. 用一两句话向用户复述今天的事实（已完成任务 / 未完成任务 / 卡住的事项 / 日历上发生的事件），**不要先问"今天完成了什么"**（事实可查）；
> 3. 对事实中不明确的部分追问（例如事件没标状态、任务 estimated_minutes 和 actual_minutes 差距大、某困难未解决）；
> 4. 根据用户回答，可能再调 `search_memory` 看是否有既往规律可对照；
> 5. 当信息足够形成 `summary + highlights + blockers + next_focus` 时，调 `write_review_report` 走预览-确认，不要静默写入；
> 6. 用户确认后，如果用户表示"想保存这个经验"，再调 `save_memory`。

模型在追问轮数、顺序和问法上自由选择，不强制固定序列。

## 6. 前端影响面

- `/ai` 页面：消息流新增**工具调用轨迹**（折叠展示："AI 正在查看 10 月 7 日的日历…"）；工具预览卡片（任务/规划/复盘/记忆）内置「确认 / 修改 / 拒绝」按钮，调 `/api/v1/ai/proposals/{id}:apply|reject`。
- `/me/长期记忆` 新增 tab：列表、搜索、创建、编辑、软删、从回收站恢复、导出；复用现有 memory API。
- 规划页、复盘页：按 S1 已改的只读态保留，不新增生成按钮；跳转 AI 的入口文案在 `/plans`、`/reviews` 空态卡片顶部。
- 新增前端类型 `AiToolCall`、`AiProposalPreview`、`AiMemory`；SSE 客户端处理 5 个新事件。

## 7. 分阶段落地（对应 S2 的 3 个提交）

| 提交 | 范围 | 验收 |
| --- | --- | --- |
| **S2-1 后端：记忆扩展与 API** | `memories` 表加 tags / last_used_at / source_type('ai_tool') / source_conversation_id 迁移；`memory_service` 加 `search_memory_by_query`、`touch_last_used_at`；新增 `POST /api/v1/memory:search`；pytest 覆盖命中/未命中/软删/软删不召回/`last_used_at` 更新 | 迁移 upgrade/downgrade 可逆；pytest 全绿 |
| **S2-2 后端：function tools 骨架** | `server/src/itime/ai/tools/*`；注册只读工具 + 写预览工具；接入 `conversation_service` 为可选分支（按 `AIConfigVersion.use_tool_calling` 开关），不动旧路径；pytest 覆盖每工具成功/权限失败/幂等/预算超限 | pytest 全绿；旧对话测试不回归 |
| **S2-3 后端：编排切换 + SSE 事件** | `conversation_service` 默认走工具调用循环；移除 `select_relevant_context` 的旧注入（保留代码可通过 `AIConfigVersion` 回滚）；`AIJob.tool_calls` 迁移；SSE 5 个新事件；真实 DeepSeek 回归一次 | 真实 DeepSeek 调用通过；SSE 事件被前端接收 |

前端在 S2-E 单独 PR 落地，覆盖 `/ai` 工具轨迹、预览卡片、`/me/长期记忆` tab，Playwright 覆盖"AI 建任务预览确认写入"、"AI 保存记忆→新会话自动召回"、"AI 复盘读取当日事实并 `write_review_report`"。

## 8. 挂起项（S3 前再定稿）

- 工具调用预算的具体默认数值（20/50/100 哪个合适）：S2-2 实测后在 `AIConfigVersion` seed 里设默认。
- `search_memory` 的召回算法：首版 ILIKE + tags 命中 + `last_used_at DESC`；是否接入 pg_trgm 索引或向量检索（embedding）在 S3 评估。
- `tool_proposal_pending` 卡片在手机视口的降级展示：S2-E 实现时在 Playwright mobile viewport 中真机校验。
- 自动归纳记忆（对话结束时 AI 自动总结沉淀为 memory）：首版明确**不做**，仅手动显式 `save_memory`；S3 评估。
