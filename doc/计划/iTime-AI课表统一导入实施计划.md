# iTime AI 课表统一导入实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 AI 会话和日历通过同一服务导入图片、XLSX、ICS、公开网页和文字课表，并在统一候选校对后确认写入。

**Architecture:** 扩展现有 `CalendarImportBatch` 和截图识别作业，而不新建第二套日历候选。确定性文件直接解析；图片、网页正文和文字进入同一 AI 结构化输出与候选规范化；所有来源最终复用现有候选编辑和确认事务。

**Tech Stack:** FastAPI、SQLAlchemy、PostgreSQL、受限 HTTP、OpenAI-compatible multimodal/tool output、Vue 3、Element Plus、Playwright

---

### Task 1: 扩展导入来源模型

**Files:**
- Create: `server/src/itime/migrations/versions/0039_calendar_ai_import_sources.py`
- Modify: `server/src/itime/calendar/models.py`
- Modify: `server/src/itime/calendar/import_schemas.py`
- Modify: `server/src/itime/calendar/import_service.py`
- Test: `server/tests/test_calendar_ai_import_models.py`

- [x] **Step 1: 写迁移和模型失败测试**

覆盖 `image|public_url|text` 来源、会话/消息可选关联、来源显示摘要、批次状态、本人复合约束和临时正文清理字段。

- [x] **Step 2: 实现迁移与兼容映射**

保留旧 `xlsx|ics|screenshot` 数据可读；新 API 统一返回 `xlsx_file|ics_file|image|public_url|text`，旧截图映射为 image。

- [x] **Step 3: 验证**

Run: `cd server && uv run alembic upgrade head && uv run pytest tests/test_calendar_ai_import_models.py tests/test_calendar_imports.py tests/test_calendar_screenshot_imports.py -q`

Expected: PASS。

- [x] **Step 4: 提交**

Run: `git add server/src/itime/migrations server/src/itime/calendar server/tests/test_calendar_ai_import_models.py && git commit -m "feat: 扩展课表导入来源模型"`

### Task 2: 统一图片、网页和文字 AI 解析

**Files:**
- Modify: `server/src/itime/ai/screenshot_provider.py`
- Create: `server/src/itime/calendar/ai_import_provider.py`
- Modify: `server/src/itime/calendar/screenshot_service.py`
- Test: `server/tests/test_calendar_ai_import_provider.py`

- [ ] **Step 1: 写统一输出测试**

覆盖图片、网页正文和文字输入生成相同 `ScreenshotRecognitionOutput` 等价结构，包含课程名称、时间、重复、地点、原文、置信度和缺失字段。

- [ ] **Step 2: 提取通用工具 schema**

图片 provider 只负责多模态消息，通用 provider 负责结构化工具 schema、输出大小、JSON 解析和错误分类；模型正文不得作为候选。

- [ ] **Step 3: 增加提示注入隔离**

网页和用户文本明确标记为不可信资料；模型不能调用未公布工具、扩大日期范围或生成未知学期/节次 ID。

- [ ] **Step 4: 验证并提交**

Run: `cd server && uv run pytest tests/test_calendar_ai_import_provider.py tests/test_calendar_screenshot_imports.py tests/test_ai_usage.py -q`

Expected: PASS。

Run: `git add server/src/itime/ai/screenshot_provider.py server/src/itime/calendar/ai_import_provider.py server/src/itime/calendar/screenshot_service.py server/tests/test_calendar_ai_import_provider.py && git commit -m "feat: 统一 AI 课表解析"`

### Task 3: 实现公开网页受限读取

**Files:**
- Create: `server/src/itime/calendar/import_fetcher.py`
- Modify: `server/src/itime/network/restricted_http.py`
- Test: `server/tests/test_calendar_import_fetcher.py`

- [ ] **Step 1: 写受限抓取测试**

使用本地受控 HTTP 服务覆盖 HTML、纯文本、ICS、重定向、循环、私网、超大正文、不支持类型、压缩炸弹、超时和 DNS 变化。

- [ ] **Step 2: 实现抓取器**

复用现有 SSRF 与重定向校验；只接受 HTTPS 公开地址，测试环境允许显式白名单；不携带 Cookie、Authorization、系统代理或用户代理凭据。

- [ ] **Step 3: 确定性 ICS 优先**

响应为完整 ICS 时直接进入现有解析器；HTML/纯文本只提取有界可见正文后进入 AI。

- [ ] **Step 4: 验证并提交**

Run: `cd server && uv run pytest tests/test_calendar_import_fetcher.py tests/test_calendar_subscription_fetcher.py tests/test_public_links.py -q`

Expected: PASS。

Run: `git add server/src/itime/calendar/import_fetcher.py server/src/itime/network/restricted_http.py server/tests/test_calendar_import_fetcher.py && git commit -m "feat: 增加课表网页受限读取"`

### Task 4: 建立统一导入 API 和作业

**Files:**
- Create: `server/src/itime/calendar/ai_import_router.py`
- Modify: `server/src/itime/calendar/screenshot_router.py`
- Modify: `server/src/itime/ai/worker.py`
- Modify: `server/src/itime/main.py`
- Test: `server/tests/test_calendar_ai_import_api.py`
- Test: `server/tests/test_calendar_ai_import_worker.py`

- [ ] **Step 1: 写三来源 API 测试**

覆盖 multipart 图片、JSON URL、JSON 文字、会话关联、操作重放、AI 冻结、额度不足、并发上限和跨账号不可见。

- [ ] **Step 2: 实现统一入口**

提供 `POST /api/v1/calendar/imports/ai`：图片上传创建临时媒体，网址创建受限抓取作业，文字直接冻结输入；三者创建同类 AI 作业并返回批次和 job。

- [ ] **Step 3: 接入 worker**

worker 成功后调用现有 `create_candidates`，批次进入 `needs_review`；失败、取消或超时释放额度并清理无恢复价值的临时内容。

- [ ] **Step 4: 验证**

Run: `cd server && uv run pytest tests/test_calendar_ai_import_api.py tests/test_calendar_ai_import_worker.py tests/test_calendar_imports.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

Run: `git add server/src/itime/calendar server/src/itime/ai/worker.py server/src/itime/main.py server/tests/test_calendar_ai_import_api.py server/tests/test_calendar_ai_import_worker.py && git commit -m "feat: 增加统一 AI 课表导入接口"`

### Task 5: 接入 Agent 提案

**Files:**
- Modify: `server/src/itime/ai/agent_tools.py`
- Modify: `server/src/itime/ai/agent_proposals.py`
- Modify: `server/src/itime/ai/conversation_service.py`
- Test: `server/tests/test_agent_timetable_import.py`

- [ ] **Step 1: 写会话导入闭环测试**

覆盖图片附件、公开 URL 和文字描述触发 `propose_timetable_import`，消息关联唯一批次，确认前日历为空。

- [ ] **Step 2: 实现会话关联**

附件先创建媒体或文件来源，发送消息时绑定来源；Agent 工具创建或关联导入批次，并把 `import_batch_id` 写入 assistant 消息附件。

- [ ] **Step 3: 验证并提交**

Run: `cd server && uv run pytest tests/test_agent_timetable_import.py tests/test_agent_conversation_worker.py -q`

Expected: PASS。

Run: `git add server/src/itime/ai server/tests/test_agent_timetable_import.py && git commit -m "feat: 在 Agent 会话中接入课表导入"`

### Task 6: 重构日历导入界面

**Files:**
- Modify: `web/src/lib/calendar-imports.ts`
- Modify: `web/src/components/CalendarImportDialog.vue`
- Modify: `web/src/components/CalendarScreenshotImport.vue`
- Modify: `web/src/components/CalendarImportCandidateEditor.vue`
- Modify: `web/src/pages/CalendarPage.vue`
- Test: `web/e2e/calendar-imports.spec.ts`

- [ ] **Step 1: 写图片、网址和文字入口 E2E**

从日历打开导入，分别提交三类来源，断言处理状态、候选编辑、确认结果、恢复和终态只读。

- [ ] **Step 2: 实现统一来源切换**

使用 `ElSegmented`、`ElUpload`、`ElInput`、`ElDialog`/`ElDrawer`；XLSX/ICS 保留确定性上传，图片/网址/文字调用统一 AI API。

- [ ] **Step 3: 复用候选编辑器**

所有来源进入 `CalendarImportCandidateEditor`，统一有效、需补充、无效、重复、确认和跳过状态。

- [ ] **Step 4: 验证并提交**

Run: `cd web && pnpm build && pnpm exec playwright test e2e/calendar-imports.spec.ts`

Expected: 三视口 PASS。

Run: `git add web/src/lib/calendar-imports.ts web/src/components/Calendar*.vue web/src/pages/CalendarPage.vue web/e2e/calendar-imports.spec.ts && git commit -m "feat: 统一日历课表导入界面"`

### Task 7: 接入会话附件与导入卡

**Files:**
- Modify: `web/src/lib/conversations.ts`
- Modify: `web/src/components/ConversationComposer.vue`
- Modify: `web/src/components/ConversationThread.vue`
- Create: `web/src/components/TimetableImportMessageCard.vue`
- Test: `web/e2e/ai-timetable-import.spec.ts`

- [ ] **Step 1: 写会话图片、URL 和文字导入 E2E**

断言无需选择模式即可上传或发送，消息内出现导入批次，调整打开统一编辑器，确认后跳转日历可见。

- [ ] **Step 2: 实现附件菜单**

使用 Element Plus 上传和下拉菜单；发送前显示可移除附件，禁止超大小或不支持类型，消息提交期间不重复上传。

- [ ] **Step 3: 实现导入消息卡**

显示来源、课程数量、需补充和重复数量；提供确认、调整、取消和“在日历查看”。

- [ ] **Step 4: 验证并提交**

Run: `cd web && pnpm build && pnpm exec playwright test e2e/ai-timetable-import.spec.ts e2e/ai-agent.spec.ts e2e/calendar-imports.spec.ts`

Expected: 三视口 PASS。

Run: `git add web/src/lib web/src/components web/e2e/ai-timetable-import.spec.ts && git commit -m "feat: 支持会话导入课表"`

### Task 8: 真实模型与完整回归

**Files:**
- Modify: `web/e2e/deepseek-live.spec.ts`
- Modify: `doc/测试/iTime-测试方案与测试用例.md`
- Modify: `doc/验收/iTime-Web实施与验证.md`

- [ ] **Step 1: 增加真实 DeepSeek 图片与文字课表**

使用固定测试图片和自然语言描述，断言候选内容、修改、确认和日历投影；网页使用本地受控服务，不向生产站点写入。

- [ ] **Step 2: 执行服务端验证**

Run: `cd server && uv run ruff check . && uv run pytest -q && uv build --no-sources`

Expected: 全部 PASS。

- [ ] **Step 3: 执行 Web 验证**

Run: `cd web && pnpm build && pnpm test:e2e && pnpm test:e2e:deepseek`

Expected: Chromium 三视口和真实 DeepSeek PASS。

- [ ] **Step 4: 执行真实 Edge**

Run: `cd web && ITIME_E2E_BROWSER_CHANNEL=msedge pnpm exec playwright test e2e/ai-timetable-import.spec.ts e2e/calendar-imports.spec.ts e2e/full-user-journey.spec.ts`

Expected: 真实 Edge 三视口 PASS。

- [ ] **Step 5: 记录证据并提交**

Run: `git add web/e2e/deepseek-live.spec.ts doc/测试/iTime-测试方案与测试用例.md doc/验收/iTime-Web实施与验证.md && git commit -m "test: 验证 AI 课表统一导入"`
