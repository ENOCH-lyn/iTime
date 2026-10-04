# iTime M3 任务实例补做 Web 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补齐重复任务基础实例的关联补做 Web 入口、恢复语义和真实 Chromium 验证，使现有服务端
`POST /api/v1/tasks/{task_id}/occurrences/makeup` 成为用户可达功能。

**Architecture:** 不新增数据模型或服务端状态机。前端在 `task-schedules.ts` 封装既有端点，
`CalendarOccurrencePanel.vue` 只对重复任务的 `kind=base` 实例展示“安排补做”，复用现有表单、
操作 ID、DST 偏移和日历刷新链路；`kind=makeup` 实例显示来源且不能继续创建链式补做。

**Tech Stack:** Vue 3、TypeScript、Lucide、FastAPI 既有 API、Playwright Chromium、axe。

---

## 范围与交互合同

- 日期型任务输入补做日期，不发送时刻或 UTC 偏移。
- 定时任务输入补做日期和本地时刻；缺少任一字段先在客户端拒绝。
- 表单首次打开沿用当前实例日期/时刻，用户明确修改后提交，不推断“下一天”。
- 提交成功后刷新统一日历，原实例状态保持不变，新实例显示为“关联补做”，并可独立完成、跳过、
  重新打开或调期。
- 响应未知或普通失败时保留输入和 `operation_id`；重试相同内容复用服务端幂等结果。DST 重复
  时刻显示既有偏移选择；版本冲突保留表单，由刷新后的任务版本重新提交。
- 非重复任务、补做实例、课程/固定安排和计划块不显示任务补做入口。课程“补课”保持既有合同。

## Task 1：先建立失败的真实浏览器用例

**Files:**
- Modify: `web/e2e/calendar.spec.ts`

- [x] **Step 1: 扩展任务创建 helper**

让 helper 可创建带截止时刻、每日重复和有限次数的任务；保存后进入统一日历。

```ts
await page.getByLabel('截止类型').selectOption('datetime')
await page.getByLabel('截止日期', { exact: true }).fill(start)
await page.getByLabel('截止时刻', { exact: true }).fill('18:30')
await page.getByLabel('重复方式').selectOption('daily')
await page.getByLabel('重复次数').fill('3')
```

- [x] **Step 2: 编写补做链路测试**

测试必须从真实页面选择基础任务实例，创建另一天/时刻的补做，再从日历打开新实例并核对来源：

```ts
await page.getByRole('button', { name: '安排补做', exact: true }).click()
await panel.getByLabel('补做日期').fill(makeupDate)
await panel.getByLabel('补做时刻').fill('20:15')
await panel.getByRole('button', { name: '创建补做', exact: true }).click()
await expect(panel.getByText('补做任务已创建，原实例保持不变。')).toBeVisible()
```

随后通过 `GET /api/v1/task-occurrences` 断言：

```ts
expect(items.find(item => item.kind === 'base')?.status).toBe('open')
expect(items.find(item => item.kind === 'makeup')).toMatchObject({
  status: 'completed',
  makeup_for_key: originalKey,
})
```

每个 Playwright 项目都执行创建、打开和独立完成；desktop 另切到 320×700、最大字号深色，检查
表单、来源、按钮、axe、控制台和根级横向溢出并保存截图。

- [x] **Step 3: 运行测试确认失败**

```bash
cd web
pnpm exec playwright test e2e/calendar.spec.ts \
  --grep "重复任务实例可创建关联补做"
```

Expected: 三个项目均因找不到“安排补做”失败，证明测试触达当前缺口。

## Task 2：实现前端 API 与详情交互

**Files:**
- Modify: `web/src/lib/task-schedules.ts`
- Modify: `web/src/components/CalendarOccurrencePanel.vue`

- [x] **Step 1: 封装现有补做端点**

在 `task-schedules.ts` 新增：

```ts
export async function createTaskMakeup(
  occurrence: TaskOccurrence,
  expectedTaskVersion: number,
  operationId: string,
  dueDate: string,
  dueTime: string | null,
  ambiguousUtcOffsetMinutes: number | null = null,
) {
  return api<{ operation_id: string; task: TaskDetail; occurrence: TaskOccurrence }>(
    `/tasks/${occurrence.task_id}/occurrences/makeup`,
    {
      operation_id: operationId,
      expected_task_version: expectedTaskVersion,
      original_occurrence_key: occurrence.occurrence_key,
      due_date: dueDate,
      due_time: dueTime,
      ambiguous_utc_offset_minutes: ambiguousUtcOffsetMinutes,
    },
  )
}
```

- [x] **Step 2: 增加任务补做表单状态与提交**

在 `CalendarOccurrencePanel.vue`：

- 新增 `CalendarPlus` 图标、`task-makeup` action 和 `{ date, time }` 草稿。
- 仅当 `task.kind === 'base'` 且 `taskDetail.schedule.recurrence.frequency !== 'none'` 时展示入口。
- 日期型隐藏时刻并发送 `null`；定时型校验时刻。
- 指纹包含 task、来源 key、`taskDetail.version`、日期、时刻和 DST offset；失败不清空草稿与
  操作号。版本冲突时重新读取 `TaskDetail`，保留草稿，下一次提交使用最新任务版本。
- 成功后关闭表单并触发现有 `changed`，由页面重读 task occurrence。

```ts
const canCreateTaskMakeup = computed(() => (
  task.value?.kind === 'base'
  && taskDetail.value?.schedule.recurrence.frequency !== 'none'
))
```

- [x] **Step 3: 展示补做来源和防止链式补做**

`kind=makeup` 的详情增加来源事实：

```vue
<div v-if="task?.kind === 'makeup' && task.makeup_for_key">
  <dt>来源</dt>
  <dd>关联补做 · 原实例 {{ task.makeup_for_key.slice(0, 10) }}</dd>
</div>
```

补做实例不渲染“安排补做”；原实例完成/跳过状态不因创建补做改变。

- [x] **Step 4: 构建并跑专项**

```bash
cd web
pnpm build
pnpm exec playwright test e2e/calendar.spec.ts \
  --grep "重复任务实例可创建关联补做"
```

Expected: TypeScript/Vite 构建通过，测试在 desktop、mobile、tablet 共 3 项通过。

- [x] **Step 5: 提交功能**

```bash
git add web/src/lib/task-schedules.ts \
  web/src/components/CalendarOccurrencePanel.vue \
  web/e2e/calendar.spec.ts
git commit -m "feat: 支持任务实例补做"
```

## Task 3：回归、文档与运行态收口

**Files:**
- Modify: `doc/需求/iTime-需求追踪矩阵.md`
- Modify: `doc/设计/iTime-详细设计文档.md`
- Modify: `doc/设计/iTime-任务核心服务设计.md`
- Modify: `doc/测试/iTime-测试方案与测试用例.md`
- Modify: `doc/验收/iTime-产品验收清单.md`
- Modify: `doc/验收/iTime-Web实施与验证.md`
- Modify: `doc/计划/iTime-M3任务实例补做Web实施计划.md`

- [x] **Step 1: 执行受影响服务端和完整日历 Chromium**

```bash
cd server
uv run pytest -q tests/test_task_schedules.py tests/test_task_lifecycle.py \
  tests/test_statistics_api.py tests/test_data_export_snapshot.py
uv run ruff check .

cd ../web
pnpm build
pnpm exec playwright test e2e/calendar.spec.ts
```

Expected: 任务补做、生命周期、统计和导出服务回归通过；日历全部场景在三种视口通过。

- [x] **Step 2: 执行全量回归**

```bash
cd server
uv run pytest -q

cd ../web
pnpm test:e2e
```

Expected: 服务端及 Chromium 全量通过；只允许测试中明确说明为 desktop-only 的 skip。

- [x] **Step 3: 核对环境与证据**

```bash
curl --fail --silent http://127.0.0.1:8000/api/ready
curl --fail --silent http://localhost:5173/calendar
git diff --check
```

确认 `/tmp/itime-ui-evidence/*calendar-task-makeup*` 截图可读、无重叠或溢出，且
`itime_test_%` 测试数据库残留为 0。

- [x] **Step 4: 同步状态文档并提交**

把“任务补做仅服务端实现”的描述更新为实际 API、UI、动态证据及剩余语音/AI 校对边界，不把本
功能通过扩大成整个 Web 首版完成。

```bash
git add doc/需求/iTime-需求追踪矩阵.md \
  doc/设计/iTime-详细设计文档.md \
  doc/设计/iTime-任务核心服务设计.md \
  doc/测试/iTime-测试方案与测试用例.md \
  doc/验收/iTime-产品验收清单.md \
  doc/验收/iTime-Web实施与验证.md \
  doc/计划/iTime-M3任务实例补做Web实施计划.md
git commit -m "docs: 记录任务实例补做证据"
```

## 自检

- 规格覆盖：来源关联、原实例不变、日期/定时、DST、幂等、独立状态和不可链式补做均有步骤。
- 类型一致：复用现有 `TaskOccurrence`、`TaskDetail` 和服务端响应，不新增平行模型。
- 范围约束：不修改服务端状态机，不新增删除动作，不把课程补课与任务补做混用。
- 占位扫描：计划无占位项或未定义函数；实际结果只在执行后写入验收记录。
