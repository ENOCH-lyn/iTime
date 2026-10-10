import { randomUUID } from 'node:crypto'
import { mkdir } from 'node:fs/promises'
import AxeBuilder from '@axe-core/playwright'
import { expect, test, type APIRequestContext, type Page, type TestInfo } from '@playwright/test'

const password = 'Valid-statistics-password-123'
const runtimeErrors = new WeakMap<Page, string[]>()

function isoDate(value: Date) {
  return value.toISOString().slice(0, 10)
}

function weekStart(value: string) {
  const result = new Date(`${value}T12:00:00Z`)
  const weekday = result.getUTCDay() || 7
  result.setUTCDate(result.getUTCDate() - weekday + 1)
  return isoDate(result)
}

function zonedParts(value: Date, timeZone: string) {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-CA', {
      timeZone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      hourCycle: 'h23',
    }).formatToParts(value).map(part => [part.type, part.value]),
  )
  return parts
}

function activityDate(timeZone: string, value = new Date()) {
  const parts = zonedParts(value, timeZone)
  const activityDay = new Date(Date.UTC(
    Number(parts.year),
    Number(parts.month) - 1,
    Number(parts.day),
  ))
  if (Number(parts.hour) < 4) {
    activityDay.setUTCDate(activityDay.getUTCDate() - 1)
  }
  return isoDate(activityDay)
}

async function screenshot(page: Page, info: TestInfo, name: string) {
  await mkdir('/tmp/itime-ui-evidence', { recursive: true })
  await page.screenshot({
    path: `/tmp/itime-ui-evidence/${info.project.name}-statistics-${name}.png`,
    fullPage: true,
    animations: 'disabled',
  })
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

async function account(page: Page, request: APIRequestContext) {
  const address = `statistics-ui-${randomUUID()}@example.com`
  await page.goto('/register')
  await page.getByLabel('邮箱', { exact: true }).fill(address)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByLabel('确认密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '创建账号', exact: true }).click()
  await page.goto(await mailLink(request, address))
  await expect(page).toHaveURL('/ai')

  const sessionResponse = await page.request.get('/api/v1/auth/me')
  expect(sessionResponse.ok()).toBeTruthy()
  const session = await sessionResponse.json()
  const settingsResponse = await page.request.get('/api/v1/time-settings')
  expect(settingsResponse.ok()).toBeTruthy()
  const settings = (await settingsResponse.json()).settings
  const update = await page.request.put('/api/v1/time-settings', {
    headers: { 'X-CSRF-Token': session.csrf_token },
    data: {
      operation_id: randomUUID(),
      expected_version: settings.version,
      time_zone: 'Asia/Shanghai',
      week_starts_on: 1,
      activity_day_start_minute: 240,
    },
  })
  expect(update.ok()).toBeTruthy()
  return session.csrf_token as string
}

async function createTask(page: Page, csrf: string) {
  const response = await page.request.post('/api/v1/tasks', {
    headers: { 'X-CSRF-Token': csrf },
    data: {
      operation_id: randomUUID(),
      title: '统计验证任务',
      description: '用于验证个人统计当前事实。',
      project_id: null,
      estimated_minutes: 60,
      priority: 2,
      labels: ['统计'],
      links: [],
      steps: [],
    },
  })
  expect(response.ok()).toBeTruthy()
  return (await response.json()).task
}

async function createPlan(
  page: Page,
  csrf: string,
  task: { id: string },
  targetDate: string,
) {
  const response = await page.request.post('/api/v1/plans/versions', {
    headers: { 'X-CSRF-Token': csrf },
    data: {
      operation_id: randomUUID(),
      week_start_date: weekStart(targetDate),
      expected_plan_version: 0,
      density: 'balanced',
      buffer_minutes: 15,
      availability_start: '08:00:00',
      availability_end: '22:00:00',
      blocks: [{
        task_id: task.id,
        occurrence_key: null,
        local_date: targetDate,
        starts_at: '09:00:00',
        ends_at: '10:00:00',
        step_ids: [],
      }],
      unscheduled_items: [],
    },
  })
  expect(response.ok()).toBeTruthy()
}

async function completeTask(
  page: Page,
  csrf: string,
  task: { id: string; version: number },
) {
  const response = await page.request.put(
    `/api/v1/tasks/${task.id}/completion`,
    {
      headers: { 'X-CSRF-Token': csrf },
      data: {
        operation_id: randomUUID(),
        expected_version: task.version,
        completed: true,
      },
    },
  )
  expect(response.ok()).toBeTruthy()
}

function watchRuntime(page: Page) {
  const errors: string[] = []
  runtimeErrors.set(page, errors)
  page.on('pageerror', error => errors.push(error.message))
  page.on('console', message => {
    if (
      message.type() === 'error'
      && !message.text().includes('Failed to load resource')
    ) errors.push(message.text())
  })
}

async function pageHealth(page: Page) {
  expect(await page.locator('vite-error-overlay').count()).toBe(0)
  expect(await page.evaluate(
    () => document.documentElement.scrollWidth <= window.innerWidth,
  )).toBe(true)
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
}

test.beforeEach(async ({ page }) => watchRuntime(page))
test.afterEach(async ({ page }) => {
  expect(runtimeErrors.get(page)).toEqual([])
})

test('个人统计在我的页面展示计划与任务事实', async ({
  page,
  request,
}, info) => {
  test.setTimeout(90_000)
  const targetDate = activityDate('Asia/Shanghai')
  const csrf = await account(page, request)
  const task = await createTask(page, csrf)
  await createPlan(page, csrf, task, targetDate)
  await completeTask(page, csrf, task)

  await page.goto('/me/stats')
  await expect(page).toHaveTitle('我的 · iTime')
  await expect(page.getByRole('heading', { name: '周统计', exact: true }))
    .toBeVisible()

  const planning = page.locator('.statistics-metric-group[data-kind="planning"]')
  await expect(planning.getByText('1 小时', { exact: true })).toBeVisible()
  await expect(planning.getByText('计划块').locator('..').getByRole('definition'))
    .toHaveText('1')

  const tasks = page.locator('.statistics-metric-group[data-kind="tasks"]')
  await expect(tasks.getByText('1 项完成', { exact: true })).toBeVisible()
  await expect(tasks.getByText('跳过实例').locator('..').getByRole('definition'))
    .toHaveText('0')

  await pageHealth(page)
  await screenshot(page, info, 'populated')
})

test('/stats 旧路径仍然跳转到我的页面', async ({ page, request }) => {
  await account(page, request)
  await page.goto('/stats')
  await expect(page).toHaveURL('/me/stats')
  await expect(page).toHaveTitle('我的 · iTime')
  await expect(page.getByRole('heading', { name: '周统计', exact: true }))
    .toBeVisible()
  await pageHealth(page)
})
