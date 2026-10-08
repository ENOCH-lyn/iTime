# iTime Element Plus 与文案迁移实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将完整 Vue Web 的通用控件迁移到 Element Plus，并按确认规则精简全站文案。

**Architecture:** 通过 Vite 自动导入 Element Plus 组件和样式，根级 `ElConfigProvider` 统一中文语言与尺寸，主题桥接层复用现有 iTime token。领域组件保留布局和业务逻辑，内部按钮、表单、选择、上传、弹层、反馈和列表控件逐批替换。

**Tech Stack:** Vue 3、TypeScript、Vite、Element Plus、Lucide Vue、Playwright、axe

---

### Task 1: 建立组件库基座

**Files:**
- Modify: `web/package.json`
- Modify: `web/pnpm-lock.yaml`
- Modify: `web/vite.config.ts`
- Modify: `web/src/main.ts`
- Modify: `web/src/App.vue`
- Create: `web/src/element-plus-theme.css`
- Test: `web/e2e/ui-components.spec.ts`

- [ ] **Step 1: 写基础加载测试**

在 `ui-components.spec.ts` 登录后打开 `/ai`，断言根节点存在 Element Plus 配置类、页面无控制台错误且无水平溢出。

- [ ] **Step 2: 运行测试确认当前失败**

Run: `cd web && pnpm exec playwright test e2e/ui-components.spec.ts --project=desktop`

Expected: FAIL，页面尚未加载 Element Plus。

- [ ] **Step 3: 安装并配置依赖**

Run: `cd web && pnpm add element-plus @element-plus/icons-vue && pnpm add -D unplugin-auto-import unplugin-vue-components`

在 `vite.config.ts` 配置 `AutoImport` 与 `Components` 的 `ElementPlusResolver`；`App.vue` 使用 `ElConfigProvider` 和 `zh-cn` locale 包裹 `RouterView`。

- [ ] **Step 4: 建立主题桥接**

在 `element-plus-theme.css` 映射 `--el-color-primary`、文本、边框、背景、圆角、字号、控件高度、遮罩和深色变量，并在 `main.ts` 的现有全局样式之后加载。

- [ ] **Step 5: 构建和运行基础测试**

Run: `cd web && pnpm build && pnpm exec playwright test e2e/ui-components.spec.ts --project=desktop`

Expected: build 和测试 PASS。

- [ ] **Step 6: 提交**

Run: `git add web/package.json web/pnpm-lock.yaml web/vite.config.ts web/src/main.ts web/src/App.vue web/src/element-plus-theme.css web/e2e/ui-components.spec.ts && git commit -m "feat: 建立 Element Plus 界面基座"`

### Task 2: 迁移共享控件

**Files:**
- Modify: `web/src/components/FormField.vue`
- Modify: `web/src/components/StatusMessage.vue`
- Modify: `web/src/components/AppearanceControls.vue`
- Modify: `web/src/components/ProductNavigation.vue`
- Modify: `web/src/components/SyncStatusIndicator.vue`
- Modify: `web/src/style.css`
- Test: `web/e2e/ui-components.spec.ts`

- [ ] **Step 1: 扩充共享控件测试**

覆盖密码显示、字段错误、成功/失败提示、主题/字号切换、导航选中态、图标按钮名称和键盘焦点。

- [ ] **Step 2: 将表单和反馈迁移到 Element Plus**

`FormField` 使用 `ElFormItem`、`ElInput` 和 password reveal；`StatusMessage` 使用 `ElAlert`，保留保存状态、请求编号和重试事件；外观设置使用 `ElSelect`。

- [ ] **Step 3: 迁移导航控件**

导航使用 `ElMenu` 或 Element Plus 可访问菜单项，保留 RouterLink 路由语义和移动端水平定位；同步状态使用 `ElTooltip`、`ElBadge` 和图标按钮。

- [ ] **Step 4: 删除对应遗留基础样式**

只删除已无引用的 `.field`、`.input-wrap`、`.reveal`、通用状态和导航控件样式；领域布局类保留。

- [ ] **Step 5: 验证并提交**

Run: `cd web && pnpm build && pnpm exec playwright test e2e/ui-components.spec.ts --project=desktop --project=mobile`

Expected: PASS。

Run: `git add web/src/components web/src/style.css web/e2e/ui-components.spec.ts && git commit -m "refactor: 迁移共享界面控件"`

### Task 3: 迁移认证与公开页面

**Files:**
- Modify: `web/src/pages/AuthPage.vue`
- Modify: `web/src/pages/AdminAuthPage.vue`
- Modify: `web/src/pages/ProductPage.vue`
- Modify: `web/src/pages/DemoPage.vue`
- Modify: `web/src/pages/HelpPage.vue`
- Modify: `web/src/pages/TermsPage.vue`
- Modify: `web/src/pages/PrivacyPage.vue`
- Modify: `web/src/pages/NotFoundPage.vue`
- Modify: `web/src/public-site.css`
- Modify: `web/src/demo.css`
- Test: `web/e2e/auth.spec.ts`
- Test: `web/e2e/admin-auth.spec.ts`
- Test: `web/e2e/public.spec.ts`

- [ ] **Step 1: 固定现有核心流程断言**

保留注册、登录、验证、找回、管理员 TOTP、演示交互和协议访问断言；新增 Element Plus 表单和弹层存在性断言。

- [ ] **Step 2: 迁移认证表单**

使用 `ElForm`、`ElInput`、`ElButton`、`ElRadioGroup`、`ElAlert` 和 `ElResult`，保留自动完成、密码管理器、焦点与错误关联。

- [ ] **Step 3: 迁移公开页命令控件并精简文案**

保留品牌、真实时间网格和必要说明；删除重复副标题、功能自述和重复按钮文案。协议与隐私正文不因精简规则删减。

- [ ] **Step 4: 验证并提交**

Run: `cd web && pnpm build && pnpm exec playwright test e2e/auth.spec.ts e2e/admin-auth.spec.ts e2e/public.spec.ts`

Expected: 三视口 PASS。

Run: `git add web/src/pages web/src/public-site.css web/src/demo.css web/e2e && git commit -m "refactor: 迁移认证与公开页面控件"`

### Task 4: 迁移管理后台

**Files:**
- Modify: `web/src/pages/AdminPage.vue`
- Modify: `web/src/components/Admin*.vue`
- Modify: `web/src/style.css`
- Test: `web/e2e/admin-management.spec.ts`
- Test: `web/e2e/quota.spec.ts`
- Test: `web/e2e/privacy-governance.spec.ts`

- [ ] **Step 1: 增加后台表单、列表和弹层回归断言**

覆盖用户状态、注册策略、AI 配置测试发布、额度、成本、日志、质检、审计、安全设置和确认弹层。

- [ ] **Step 2: 迁移后台控件**

使用 `ElTabs`、`ElForm`、`ElSelect`、`ElSwitch`、`ElTable`、`ElPagination`、`ElDialog`、`ElDrawer`、`ElPopconfirm`、`ElTag` 和 `ElAlert`。手机宽度下表格使用详情抽屉，不压缩成不可读列。

- [ ] **Step 3: 精简后台文案**

删除每个分区重复的功能介绍；保留配置发布影响、Key 不回显、额度与正文访问审计等必要说明。

- [ ] **Step 4: 验证并提交**

Run: `cd web && pnpm build && pnpm exec playwright test e2e/admin-management.spec.ts e2e/quota.spec.ts e2e/privacy-governance.spec.ts`

Expected: 三视口 PASS。

Run: `git add web/src/pages/AdminPage.vue web/src/components/Admin*.vue web/src/style.css web/e2e && git commit -m "refactor: 迁移管理后台组件"`

### Task 5: 迁移账号设置

**Files:**
- Modify: `web/src/pages/AccountPage.vue`
- Modify: `web/src/components/AccountDeletionSettings.vue`
- Modify: `web/src/components/AdminPasswordSettings.vue`
- Modify: `web/src/components/AdminRecoverySettings.vue`
- Modify: `web/src/components/DataExportSettings.vue`
- Modify: `web/src/components/DeviceSettings.vue`
- Modify: `web/src/components/MemoryManager.vue`
- Modify: `web/src/components/PasswordSettings.vue`
- Modify: `web/src/components/PrivacySettings.vue`
- Modify: `web/src/components/QuotaOverview.vue`
- Modify: `web/src/components/RecoveryCodesPanel.vue`
- Modify: `web/src/components/SyncSettings.vue`
- Test: `web/e2e/account-deletion.spec.ts`
- Test: `web/e2e/data-exports.spec.ts`
- Test: `web/e2e/sync.spec.ts`

- [ ] **Step 1: 迁移账号导航、表单、开关、列表和危险操作弹层**

保留重新认证、待同步数量、注销提交期间不可关闭、导出状态恢复和请求编号。

- [ ] **Step 2: 精简设置页文案**

标题下不默认解释功能；隐私期限、注销后果、额度口径和同步冲突说明保留。

- [ ] **Step 3: 验证并提交**

Run: `cd web && pnpm build && pnpm exec playwright test e2e/account-deletion.spec.ts e2e/data-exports.spec.ts e2e/sync.spec.ts`

Expected: 三视口 PASS。

Run: `git add web/src/pages/AccountPage.vue web/src/components web/e2e && git commit -m "refactor: 迁移账号设置组件"`

### Task 6: 迁移 AI 与任务工作区

**Files:**
- Modify: `web/src/pages/AIPage.vue`
- Modify: `web/src/pages/TasksPage.vue`
- Modify: `web/src/components/Conversation*.vue`
- Modify: `web/src/components/ProposalMessageCard.vue`
- Modify: `web/src/components/Task*.vue`
- Modify: `web/src/components/ProjectDialog.vue`
- Modify: `web/src/components/VoiceTranscriptionControl.vue`
- Modify: `web/src/ai-workspace.css`
- Modify: `web/src/task-workspace.css`
- Test: `web/e2e/ai-conversations.spec.ts`
- Test: `web/e2e/tasks.spec.ts`
- Test: `web/e2e/task-lifecycle.spec.ts`
- Test: `web/e2e/voice-transcriptions.spec.ts`

- [ ] **Step 1: 迁移会话与任务基础控件**

使用 Element Plus 的输入、按钮、菜单、弹层、抽屉、标签、选择器、日期时间和上传控件；消息流、三栏布局和任务列表保持领域结构。

- [ ] **Step 2: 精简主要工作区文案**

移除模式说明、页面副标题和重复状态描述；保留 AI 冻结、归档、部分回复、版本冲突和未保存提示。

- [ ] **Step 3: 验证并提交**

Run: `cd web && pnpm build && pnpm exec playwright test e2e/ai-conversations.spec.ts e2e/tasks.spec.ts e2e/task-lifecycle.spec.ts e2e/voice-transcriptions.spec.ts`

Expected: 三视口 PASS。

Run: `git add web/src/pages/AIPage.vue web/src/pages/TasksPage.vue web/src/components web/src/ai-workspace.css web/src/task-workspace.css web/e2e && git commit -m "refactor: 迁移 AI 与任务工作区组件"`

### Task 7: 迁移规划、日历、专注、复盘与统计

**Files:**
- Modify: `web/src/pages/PlansPage.vue`
- Modify: `web/src/pages/CalendarPage.vue`
- Modify: `web/src/pages/FocusPage.vue`
- Modify: `web/src/pages/ReviewsPage.vue`
- Modify: `web/src/pages/StatisticsPage.vue`
- Modify: `web/src/components/Plan*.vue`
- Modify: `web/src/components/Calendar*.vue`
- Modify: `web/src/components/Focus*.vue`
- Modify: `web/src/components/DifficultyPanel.vue`
- Modify: `web/src/components/Review*.vue`
- Modify: `web/src/components/Statistics*.vue`
- Modify: `web/src/planning.css`
- Modify: `web/src/calendar.css`
- Modify: `web/src/focus.css`
- Modify: `web/src/reviews.css`
- Modify: `web/src/statistics.css`
- Test: `web/e2e/planning.spec.ts`
- Test: `web/e2e/calendar*.spec.ts`
- Test: `web/e2e/focus.spec.ts`
- Test: `web/e2e/reviews.spec.ts`
- Test: `web/e2e/statistics.spec.ts`

- [ ] **Step 1: 迁移领域工作区控件**

时间网格、计划时间轴、计时显示和统计图保持自研；所有命令、表单、选择、弹层、状态和上传控件迁移到 Element Plus。

- [ ] **Step 2: 精简页面说明**

保留日期、时区、未确认、冲突、执行事实和统计口径；删除重复解释功能的副标题。

- [ ] **Step 3: 验证并提交**

Run: `cd web && pnpm build && pnpm exec playwright test e2e/planning.spec.ts e2e/calendar.spec.ts e2e/calendar-imports.spec.ts e2e/calendar-lifecycle.spec.ts e2e/calendar-subscriptions.spec.ts e2e/focus.spec.ts e2e/reviews.spec.ts e2e/statistics.spec.ts`

Expected: 三视口 PASS。

Run: `git add web/src/pages web/src/components web/src/*.css web/e2e && git commit -m "refactor: 迁移核心业务工作区组件"`

### Task 8: 清理遗留基础控件并完成全站验证

**Files:**
- Modify: `web/src/style.css`
- Modify: `web/src/*.css`
- Modify: `doc/验收/iTime-Web实施与验证.md`
- Test: `web/e2e/*.spec.ts`

- [ ] **Step 1: 扫描遗留原生控件**

Run: `rg -n '<(button|input|select|textarea|dialog)\\b' web/src --glob '*.vue'`

Expected: 仅保留明确无法由 Element Plus 替代的语义输入，并在代码旁说明原因。

- [ ] **Step 2: 删除无引用样式**

使用 `rg` 逐项确认选择器无引用后删除 `.button`、`.field`、`.segmented-field`、原生 dialog 和重复响应式补丁。

- [ ] **Step 3: 执行完整验证**

Run: `cd web && pnpm build && pnpm test:e2e`

Expected: 全部 Chromium 三视口 PASS。

Run: `cd web && ITIME_E2E_BROWSER_CHANNEL=msedge pnpm test:e2e`

Expected: 真实 Edge 三视口 PASS。

- [ ] **Step 4: 更新证据并提交**

记录组件覆盖、构建、浏览器版本、视口、用例数量、控制台、axe 和遗留例外。

Run: `git add web doc/验收/iTime-Web实施与验证.md && git commit -m "test: 验证 Element Plus 全站迁移"`
