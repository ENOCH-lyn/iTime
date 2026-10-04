# iTime M7 完整 Web 闭环实施计划

> **执行要求：** 按任务顺序使用测试驱动方式实施；每个独立测试能力或产品修复分别提交，完成后执行专项和全量回归。

**目标：** 用真实 PostgreSQL、HTTP、Mailpit、AI worker 和浏览器完成 `E2E-01`，证明同一用户可以从注册一直走到另一在线端查看规划、执行、困难、复盘和统计结果。

**架构：** 不新增业务 API，也不通过路由拦截伪造核心业务响应。主浏览器通过可见 UI 完成账号、任务、规划确认、专注、困难、任务完成和复盘；第二个独立浏览器上下文登录同一账号，验证同步刷新及最终服务端事实。Playwright 继续默认使用仓库 Chromium，同时允许通过环境变量选择本机 Chrome/Edge channel，以便同一场景复用到真实浏览器兼容验证。

**技术栈：** Vue 3、Playwright、axe-core、FastAPI、PostgreSQL、Mailpit、现有隔离 Web 测试环境和确定性 OpenAI 兼容测试上游。

---

## 范围与不变量

- 对应需求：`REQ-WEB-01`、`REQ-TASK-01/02`、`REQ-PLAN-01/02`、`REQ-EXEC-01`、`REQ-DIFF-01`、`REQ-REVIEW-01`、`REQ-STATS-01`、`REQ-SYNC-01`、`E2E-01`。
- 主流程：注册与邮箱验证 → UI 创建项目和任务 → AI 生成计划预览 → 明确确认 → 从日历计划块开始专注 → 记录困难 → 结束专注并明确完成任务 → 生成复盘报告 → 第二在线端查看同步结果和统计。
- AI 提案确认前不得出现当前计划；结束专注不得自动完成任务；只有点击“完成任务”后才记录完成事实。
- 第二端使用独立浏览器上下文和独立会话 Cookie，不复制主端 storage state。
- 核心业务请求不得通过 `page.route()` 返回伪造成功；所有状态来自隔离 API、数据库和真实 worker。
- 每个视口检查 Vite 错误层、相关 API 5xx、控制台异常、根级横向溢出和 axe。
- 测试数据只进入随机测试库；退出后数据库和子进程必须清理。

## Task 1：增加可选真实浏览器通道

**Files:**
- Modify: `web/playwright.config.ts`

- [ ] **Step 1: 在测试配置中读取可选 channel**

保留默认 Chromium 行为；仅在显式提供 `ITIME_E2E_BROWSER_CHANNEL=chrome` 或
`ITIME_E2E_BROWSER_CHANNEL=msedge` 时把 channel 传给三个既有项目。

```ts
const browserChannel = process.env.ITIME_E2E_BROWSER_CHANNEL

const browserUse = browserChannel ? { channel: browserChannel } : {}

projects: [
  {
    name: 'desktop',
    use: {
      ...devices['Desktop Chrome'],
      ...browserUse,
      viewport: { width: 1440, height: 1000 },
    },
  },
]
```

- [ ] **Step 2: 验证默认 Chromium 配置仍可列出测试**

Run:

```bash
cd web
pnpm exec playwright test --list
```

Expected: 配置成功加载，既有 `desktop`、`mobile`、`tablet` 项目和全部测试均可列出。

- [ ] **Step 3: 验证真实 Chrome channel**

Run:

```bash
cd web
ITIME_E2E_BROWSER_CHANNEL=chrome pnpm exec playwright test \
  e2e/public.spec.ts --project=desktop --grep "公开首页"
```

Expected: 本机 Google Chrome 启动，指定公共页面冒烟场景通过。

- [ ] **Step 4: 提交浏览器验证基础**

```bash
git add web/playwright.config.ts
git commit -m "test: 支持真实浏览器兼容验证"
```

## Task 2：建立跨模块完整用户旅程

**Files:**
- Create: `web/e2e/full-user-journey.spec.ts`

- [ ] **Step 1: 写注册、登录、时间和质量检查辅助函数**

测试文件提供以下本地辅助函数，不把新测试耦合到其他 spec 的模块私有实现：

```ts
async function registerAndLogin(page: Page, request: APIRequestContext) {
  const email = `journey-${randomUUID()}@example.com`
  await page.goto('/register')
  await page.getByLabel('邮箱', { exact: true }).fill(email)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByLabel('确认密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '创建账号', exact: true }).click()
  await page.goto(await mailLink(request, email))
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '验证邮箱', exact: true }).click()
  await login(page, email)
  return { email, csrf: await initializeTimeSettings(page) }
}
```

同时实现：

- `activityDate('Asia/Shanghai')`：按当地 04:00 计算当前活动日。
- `watchRuntime(page)`：收集 `pageerror`、console error/warning 和 `/api/v1/` 的 5xx。
- `assertPageHealth(page)`：断言无错误层、无根级横向溢出、axe 无违规。
- `screenshot(page, info, name)`：写入 `/tmp/itime-ui-evidence/<project>-full-journey-*.png`。

- [ ] **Step 2: 先写完整主流程断言**

核心测试使用 `test.setTimeout(90_000)`，并按以下顺序只操作真实页面：

```ts
test('注册到复盘的完整闭环可在另一在线端查看', async ({
  page,
  request,
  browser,
}, info) => {
  const activityDate = currentActivityDate('Asia/Shanghai')
  const account = await registerAndLogin(page, request)

  await createProjectAndTaskThroughUI(page, {
    project: 'Web 首版验收',
    task: '完成跨模块验收',
    estimate: 60,
    steps: ['准备验收数据', '核对完整结果'],
  })

  const secondContext = await browser.newContext()
  const second = await secondContext.newPage()
  await login(second, account.email)
  await openTaskThroughUI(second, '完成跨模块验收')

  await page.goto(`/plans?scope=day&date=${activityDate}`)
  await page.getByRole('button', { name: '生成计划提案', exact: true }).click()
  await expect(page.getByText('这是预览，确认后才会写入计划'))
    .toBeVisible({ timeout: 15_000 })
  await page.getByRole('button', { name: '确认并写入计划', exact: true }).click()

  await startPlannedFocusFromCalendar(page, activityDate)
  await page.getByLabel('准备验收数据').check()
  await page.getByRole('tab', { name: '困难', exact: true }).click()
  await page.getByLabel('遇到了什么困难？')
    .fill('跨模块状态需要逐项核对。')
  await page.getByRole('button', { name: '记录困难', exact: true }).click()
  await page.getByRole('button', { name: '结束', exact: true }).click()
  await page.getByRole('button', { name: '完成任务', exact: true }).click()

  await expect(second.getByRole('button', { name: '重新打开', exact: true }))
    .toBeVisible({ timeout: 5_000 })

  await page.goto(`/reviews?date=${activityDate}`)
  await expectReviewFacts(page, {
    plannedBlocks: 1,
    completedTasks: 1,
    difficulties: 1,
  })
  await page.getByLabel('补充事实').fill('完整闭环已按步骤核对。')
  await page.getByRole('button', { name: '保存事实' }).click()
  await page.getByRole('button', { name: '跳过，生成报告' }).click()
  await expect(page.getByText(
    '今天完成了核心事项，也留下了可继续优化的线索。',
  )).toBeVisible({ timeout: 15_000 })

  await expectSecondDeviceResult(second, activityDate)
})
```

`createProjectAndTaskThroughUI`、`openTaskThroughUI`、
`startPlannedFocusFromCalendar`、`expectReviewFacts` 和
`expectSecondDeviceResult` 必须继续使用角色、标签或稳定业务 class 定位器，并断言：

- 项目与任务创建成功，两个步骤和预计 60 分钟持久化。
- 规划提案确认后出现当前第 1 版和一个计划块。
- 日历计划块保留任务标题，并带到 `/focus?block=...`。
- 专注结束后仍显示“任务未完成”，明确完成后服务端任务状态才为 `completed`。
- 复盘事实包含 1 个计划块、1 个完成任务、1 个专注条目和 1 条困难。
- 第二端在 5 秒内看到任务完成；随后在统计页看到计划块 1、任务完成 1、专注会话 1，并在复盘页读到已生成报告。

- [ ] **Step 3: 运行 desktop，收集首个真实失败**

Run:

```bash
cd web
pnpm exec playwright test e2e/full-user-journey.spec.ts --project=desktop
```

Expected: 若跨模块状态、选择器或时序存在缺陷，测试在对应用户步骤失败并保留截图；不得通过降低断言、增加固定长等待或伪造响应绕过。

- [ ] **Step 4: 修复测试自身问题或产品集成缺陷**

选择器只等待可观察状态，异步 AI 最长等待复用现有 15 秒合同，跨端同步最长等待 5 秒。若失败属于产品代码，则先在所属模块增加最窄回归测试、修复并单独提交，再回到本场景。

- [ ] **Step 5: 运行三种视口和质量检查**

Run:

```bash
cd web
pnpm exec playwright test e2e/full-user-journey.spec.ts
```

Expected: `3 passed`；desktop、mobile、tablet 都完成同一真实闭环，且 axe、控制台、错误层和溢出检查通过。

- [ ] **Step 6: 提交完整闭环测试**

```bash
git add web/e2e/full-user-journey.spec.ts
git commit -m "test: 验证完整 Web 用户闭环"
```

## Task 3：真实浏览器、全量回归与证据收口

**Files:**
- Modify: `doc/测试/iTime-测试方案与测试用例.md`
- Modify: `doc/需求/iTime-需求追踪矩阵.md`
- Modify: `doc/验收/iTime-产品验收清单.md`
- Modify: `doc/验收/iTime-Web实施与验证.md`
- Modify: `doc/计划/iTime-开发总计划.md`
- Modify: `README.md`

- [ ] **Step 1: 在真实 Chrome 重跑完整闭环**

Run:

```bash
cd web
ITIME_E2E_BROWSER_CHANNEL=chrome pnpm exec playwright test \
  e2e/full-user-journey.spec.ts --project=desktop
```

Expected: `1 passed`，记录 Google Chrome 实际版本、视口和命令。

- [ ] **Step 2: 在真实 Edge 重跑完整闭环**

先用 `ls -d "/Applications/Microsoft Edge.app"` 确认本机状态。仅在本地测试环境安装浏览器，
不改项目依赖；安装后运行：

```bash
cd web
ITIME_E2E_BROWSER_CHANNEL=msedge pnpm exec playwright test \
  e2e/full-user-journey.spec.ts --project=desktop
```

Expected: `1 passed`。若当前环境不能安装 Edge，证据必须明确记录“未执行”及原始原因，不得用
Chromium 结果替代 Edge。

- [ ] **Step 3: 执行静态、构建、专项与全量回归**

Run:

```bash
cd server
uv run ruff check .
uv build --no-sources
uv run pytest -q

cd ../web
pnpm build
pnpm exec playwright test e2e/full-user-journey.spec.ts
pnpm test:e2e
```

Expected: 全部命令通过；测试结束后 `itime_test_%` 数据库、孤立 worker 和临时测试文件残留为
0。

- [ ] **Step 4: 更新需求和验收证据**

文档只把 `E2E-01` 和实际执行过的浏览器标为通过；保留以下真实缺口：

- 学期、项目、AI 会话的删除范围仍未确认，当前只验证无删除旁路。
- 其他业务对象提案范围仍待审计或设计。
- 未执行的 Edge、实体设备、正式第三方模型质量、性能、备份恢复和生产部署继续标为未完成。

实施记录包含提交 SHA、测试日期、数据库/worker/浏览器环境、完整命令、通过数量、场景步骤、
截图路径、失败及修复说明。

- [ ] **Step 5: 提交 M7-A 证据**

```bash
git add README.md doc/测试/iTime-测试方案与测试用例.md \
  doc/需求/iTime-需求追踪矩阵.md doc/验收/iTime-产品验收清单.md \
  doc/验收/iTime-Web实施与验证.md doc/计划/iTime-开发总计划.md
git commit -m "docs: 记录完整 Web 闭环证据"
git status --short --branch
```

Expected: 文档提交后工作树干净；测试代码、截图、日志、凭据和测试数据库没有被误提交。
