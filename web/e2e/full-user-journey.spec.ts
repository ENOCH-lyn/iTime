import AxeBuilder from '@axe-core/playwright'
import {
  expect,
  test,
  type APIRequestContext,
  type Browser,
  type Page,
  type TestInfo,
} from '@playwright/test'
import { randomUUID } from 'node:crypto'
import { mkdir } from 'node:fs/promises'

const password = 'Valid-full-journey-password-123'
const evidence = '/tmp/itime-ui-evidence'
const runtimeErrors = new WeakMap<Page, string[]>()

function currentActivityDate(timeZone: string) {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-CA', {
      timeZone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      hourCycle: 'h23',
    }).formatToParts(new Date()).map(part => [part.type, part.value]),
  )
  const date = new Date(Date.UTC(
    Number(parts.year),
    Number(parts.month) - 1,
    Number(parts.day),
  ))
  if (Number(parts.hour) < 4) date.setUTCDate(date.getUTCDate() - 1)
  return date.toISOString().slice(0, 10)
}

async function screenshot(page: Page, info: TestInfo, name: string) {
  await mkdir(evidence, { recursive: true })
  await page.screenshot({
    path: `${evidence}/${info.project.name}-full-journey-${name}.png`,
    fullPage: true,
    animations: 'disabled',
  })
}

function watchRuntime(page: Page) {
  const errors: string[] = []
  runtimeErrors.set(page, errors)
  page.on('pageerror', error => errors.push(error.message))
  page.on('console', message => {
    if (
      ['error', 'warning'].includes(message.type())
      && !message.text().includes('Failed to load resource')
    ) errors.push(message.text())
  })
  page.on('response', response => {
    if (response.url().includes('/api/v1/') && response.status() >= 500) {
      errors.push(`${response.status()} ${response.url()}`)
    }
  })
}

async function assertPageHealth(page: Page) {
  expect(runtimeErrors.get(page) ?? []).toEqual([])
  expect(await page.locator('vite-error-overlay').count()).toBe(0)
  expect(await page.evaluate(
    () => document.documentElement.scrollWidth <= window.innerWidth,
  )).toBe(true)
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
}

async function mailLink(request: APIRequestContext, address: string) {
  let id = ''
  await expect.poll(async () => {
    const response = await request.get(
      `http://127.0.0.1:58025/api/v1/search?query=${encodeURIComponent(`to:${address}`)}`,
    )
    const result = await response.json()
    id = result.messages.find((message: { Subject: string }) =>
      message.Subject.includes('验证邮箱'))?.ID ?? ''
    return id
  }, { timeout: 12_000 }).not.toBe('')
  const message = await (
    await request.get(`http://127.0.0.1:58025/api/v1/message/${id}`)
  ).json()
  return message.Text.match(
    /http:\/\/localhost:4173\/verify-email#token=[A-Za-z0-9_-]{43}/,
  )[0] as string
}

async function login(page: Page, email: string) {
  await page.goto('/login')
  await page.getByLabel('邮箱', { exact: true }).fill(email)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL('/ai')
}

async function initializeTimeSettings(page: Page) {
  const sessionResponse = await page.request.get('/api/v1/auth/me')
  expect(sessionResponse.ok()).toBeTruthy()
  const session = await sessionResponse.json()
  const settingsResponse = await page.request.get('/api/v1/time-settings')
  expect(settingsResponse.ok()).toBeTruthy()
  const settings = (await settingsResponse.json()).settings
  const response = await page.request.put('/api/v1/time-settings', {
    headers: { 'X-CSRF-Token': session.csrf_token },
    data: {
      operation_id: randomUUID(),
      expected_version: settings.version,
      time_zone: 'Asia/Shanghai',
      week_starts_on: 1,
      activity_day_start_minute: 240,
    },
  })
  expect(response.ok()).toBeTruthy()
  return session.csrf_token as string
}

async function registerAndLogin(page: Page, request: APIRequestContext) {
  const email = `journey-${randomUUID()}@example.com`
  await page.goto('/register')
  await page.getByLabel('邮箱', { exact: true }).fill(email)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByLabel('确认密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '创建账号', exact: true }).click()
  await expect(page.getByRole('heading', { name: '去邮箱看看吧' })).toBeVisible()
  await page.goto(await mailLink(request, email))
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '验证邮箱', exact: true }).click()
  await expect(page.getByRole('heading', { name: '邮箱已验证' })).toBeVisible()
  await login(page, email)
  await initializeTimeSettings(page)
  return { email }
}

async function returnToTaskList(page: Page) {
  const back = page.getByRole('button', { name: '返回任务列表', exact: true })
  if (await back.isVisible()) await back.click()
}

async function createProjectAndTaskThroughUI(page: Page) {
  const taskTitle = '完成跨模块验收'
  await page.getByRole('link', { name: '任务', exact: true }).click()
  await expect(page).toHaveURL('/tasks')
  await expect(page.getByRole('heading', { name: '任务', exact: true })).toBeVisible()

  await page.getByRole('button', { name: '新建项目', exact: true }).click()
  await page.getByLabel('名称', { exact: true }).fill('Web 首版验收')
  await page.getByLabel('说明', { exact: false }).fill('贯通首版核心用户旅程。')
  await page.getByRole('button', { name: '创建', exact: true }).click()
  await expect(page.getByText('项目已创建。')).toBeVisible()

  await page.getByRole('button', { name: '新建任务', exact: true }).click()
  await page.getByLabel('任务名称', { exact: true }).fill(taskTitle)
  await page.getByLabel('正文', { exact: false }).fill('从规划、执行到复盘核对同一份服务端事实。')
  await page.getByLabel('项目或目标', { exact: true })
    .selectOption({ label: 'Web 首版验收' })
  await page.getByLabel('优先级', { exact: true }).selectOption('3')
  await page.getByLabel('预计用时', { exact: false }).fill('60')
  await page.getByRole('textbox', { name: '标签 逗号分隔', exact: true })
    .fill('验收, 跨模块')
  await page.getByRole('button', { name: '添加步骤', exact: true }).click()
  await page.getByLabel('步骤 1', { exact: true }).fill('准备验收数据')
  await page.getByRole('button', { name: '添加步骤', exact: true }).click()
  await page.getByLabel('步骤 2', { exact: true }).fill('核对完整结果')

  const responsePromise = page.waitForResponse(response => (
    response.url().endsWith('/api/v1/tasks')
    && response.request().method() === 'POST'
  ))
  await page.getByRole('button', { name: '创建任务', exact: true }).first().click()
  const response = await responsePromise
  expect(response.ok()).toBeTruthy()
  await expect(page.getByText('任务已创建并保存到服务端。')).toBeVisible()
  await expect(page.getByText('0/2 已完成')).toBeVisible()
  return (await response.json()).task as {
    id: string
    title: string
    status: string
  }
}

async function openTaskThroughUI(page: Page, title: string) {
  await page.goto('/tasks')
  await returnToTaskList(page)
  const item = page.locator('.task-list li').filter({ hasText: title })
  await expect(item).toBeVisible()
  await item.getByRole('button').click()
  await expect(page.getByLabel('任务名称', { exact: true })).toHaveValue(title)
}

async function generateAndConfirmPlan(page: Page, date: string, title: string) {
  await page.goto(`/plans?scope=day&date=${date}`)
  await expect(page).toHaveTitle('规划 · iTime')
  await expect(page.getByRole('heading', { name: '待规划任务', exact: true }))
    .toBeVisible()
  await expect(page.getByText('正在读取任务与固定安排…')).toHaveCount(0)
  await expect(page.getByText(title, { exact: true }).first()).toBeVisible()
  await expect(page.locator('.plan-task-list input:checked')).toHaveCount(1)

  await page.getByRole('button', { name: '生成计划提案', exact: true }).click()
  await expect(page.getByText('这是预览，确认后才会写入计划'))
    .toBeVisible({ timeout: 15_000 })
  await expect(page.locator('.plan-block')).toHaveCount(1)
  await page.getByRole('button', { name: '确认并写入计划', exact: true }).click()
  await expect(page.getByText('提案已写入计划，当前为第 1 版。')).toBeVisible()
  await page.getByRole('button', { name: '查看当前计划', exact: true }).click()
  await expect(page.getByText('当前计划', { exact: true })).toBeVisible()
  await expect(page.locator('.plan-block')).toHaveCount(1)
}

async function startPlannedFocusFromCalendar(
  page: Page,
  date: string,
  taskTitle: string,
) {
  await page.getByRole('link', { name: '日历', exact: true }).click()
  await expect(page).toHaveURL('/calendar')
  await page.getByLabel('跳转日期').fill(date)
  await expect(page.getByText('正在读取日历…')).toHaveCount(0)
  const planEvent = page.locator('.event-plan:visible').filter({
    hasText: taskTitle,
  }).first()
  await expect(planEvent).toBeVisible()
  await planEvent.click()
  const detail = page.locator('.occurrence-panel')
  await expect(detail.getByText('计划块', { exact: true })).toBeVisible()
  const focusLink = detail.getByRole('link', { name: '开始专注', exact: true })
  await expect(focusLink).toHaveAttribute('href', /^\/focus\?block=/)
  await focusLink.click()
  await expect(page).toHaveURL(/\/focus\?block=/)
  await page.getByRole('button', { name: '开始专注', exact: true }).click()
  await expect(page.getByRole('heading', { name: taskTitle, exact: true }))
    .toBeVisible()
}

async function completeExecutionThroughUI(page: Page) {
  await page.getByLabel('准备验收数据').check()
  await expect(page.getByText('1 / 2 步', { exact: true })).toBeVisible()
  await page.getByRole('tab', { name: '困难', exact: true }).click()
  await page.getByLabel('遇到了什么困难？')
    .fill('跨模块状态需要逐项核对。')
  await page.getByRole('button', { name: '记录困难', exact: true }).click()
  await expect(page.getByText('困难已记录。')).toBeVisible()
  await expect(page.locator('.focus-time')).not.toHaveText('00:00:00', {
    timeout: 3_000,
  })
  await page.getByRole('button', { name: '结束', exact: true }).click()
  await expect(page.getByText('本次专注已结束。')).toBeVisible()
  await page.getByRole('tab', { name: '步骤', exact: true }).click()
  await expect(page.getByText('任务未完成', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '完成任务', exact: true }).click()
  await expect(page.getByText('关联任务已完成。')).toBeVisible()
  await expect(page.getByText('任务已完成', { exact: true })).toBeVisible()
}

async function expectReviewFacts(page: Page, taskTitle: string) {
  const metrics = page.locator('.review-fact-metrics')
  await expect(metrics.getByText('计划块').locator('..').getByRole('definition'))
    .toHaveText('1')
  await expect(metrics.getByText('完成任务').locator('..').getByRole('definition'))
    .toHaveText('1')
  await expect(metrics.getByText('困难').locator('..').getByRole('definition'))
    .toHaveText('1')
  await page.getByText('查看记录明细').click()
  await expect(page.getByText(taskTitle, { exact: true }).first()).toBeVisible()
  await expect(
    page.locator('.review-fact-columns section')
      .filter({ has: page.getByRole('heading', { name: '专注与困难' }) })
      .getByText(taskTitle, { exact: true }),
  ).toBeVisible()
  await expect(page.getByText('跨模块状态需要逐项核对。', { exact: true }))
    .toBeVisible()
}

async function expectSecondDeviceResult(
  page: Page,
  date: string,
  taskTitle: string,
) {
  await page.goto(`/stats?scope=day&date=${date}`)
  await expect(page).toHaveTitle('统计 · iTime')
  await expect(page.getByRole('heading', { name: '日统计' })).toBeVisible()
  const planning = page.locator('.statistics-metric-group[data-kind="planning"]')
  const tasks = page.locator('.statistics-metric-group[data-kind="tasks"]')
  const focus = page.locator('.statistics-metric-group[data-kind="focus"]')
  await expect(planning.getByText('计划块').locator('..').getByRole('definition'))
    .toHaveText('1')
  await expect(tasks.getByText('1 项完成', { exact: true })).toBeVisible()
  await expect(focus.getByText('会话').locator('..').getByRole('definition'))
    .toHaveText('1')
  await expect(focus.getByText('已结束').locator('..').getByRole('definition'))
    .toHaveText('1')

  await page.goto(`/reviews?date=${date}`)
  await expect(page.getByText(
    '今天完成了核心事项，也留下了可继续优化的线索。',
  )).toBeVisible()
  await page.getByText('查看记录明细').click()
  await expect(
    page.locator('.review-fact-columns').getByText(taskTitle, { exact: true }).first(),
  ).toBeVisible()
}

async function secondDevice(browser: Browser, email: string) {
  const context = await browser.newContext({
    baseURL: 'http://localhost:4173',
    viewport: { width: 1200, height: 800 },
  })
  const page = await context.newPage()
  watchRuntime(page)
  await login(page, email)
  return { context, page }
}

test.beforeEach(async ({ page }) => watchRuntime(page))
test.afterEach(async ({ page }) => {
  expect(runtimeErrors.get(page)).toEqual([])
})

test('注册到复盘的完整闭环可在另一在线端查看', async ({
  page,
  request,
  browser,
}, info) => {
  test.setTimeout(90_000)
  const date = currentActivityDate('Asia/Shanghai')
  const account = await registerAndLogin(page, request)
  const task = await createProjectAndTaskThroughUI(page)
  const other = await secondDevice(browser, account.email)

  try {
    await openTaskThroughUI(other.page, task.title)
    await generateAndConfirmPlan(page, date, task.title)
    await startPlannedFocusFromCalendar(page, date, task.title)
    await completeExecutionThroughUI(page)

    await expect(
      other.page.getByRole('button', { name: '重新打开', exact: true }),
    ).toBeVisible({ timeout: 5_000 })

    const storedTaskResponse = await page.request.get(`/api/v1/tasks/${task.id}`)
    expect(storedTaskResponse.ok()).toBeTruthy()
    expect((await storedTaskResponse.json()).task.status).toBe('completed')

    await page.goto(`/reviews?date=${date}`)
    await expect(page).toHaveTitle('复盘 · iTime')
    await expect(page.getByRole('heading', { name: '事实概览' })).toBeVisible()
    await expectReviewFacts(page, task.title)
    await page.getByLabel('补充事实').fill('完整闭环已按步骤核对。')
    await page.getByRole('button', { name: '保存事实' }).click()
    await expect(page.getByText('补充事实已保存。')).toBeVisible()
    await page.getByRole('button', { name: '跳过，生成报告' }).click()
    await expect(page.getByText(
      '今天完成了核心事项，也留下了可继续优化的线索。',
    )).toBeVisible({ timeout: 15_000 })

    await assertPageHealth(page)
    await screenshot(page, info, 'review-complete')

    await expectSecondDeviceResult(other.page, date, task.title)
    await assertPageHealth(other.page)
    await screenshot(other.page, info, 'second-device-result')
    expect(runtimeErrors.get(other.page)).toEqual([])
  } finally {
    await other.context.close()
  }
})
