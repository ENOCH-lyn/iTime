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
      minute: '2-digit',
      second: '2-digit',
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

function localDateTime(value: Date, timeZone: string) {
  const parts = zonedParts(value, timeZone)
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}:${parts.second}`
}

async function currentActivityFixture(page: Page, timeZone: string) {
  while (true) {
    const now = new Date()
    const parts = zonedParts(now, timeZone)
    const localSeconds = (
      Number(parts.hour) * 3600
      + Number(parts.minute) * 60
      + Number(parts.second)
    )
    const secondsIntoActivityDay = (
      localSeconds - 4 * 3600 + 24 * 3600
    ) % (24 * 3600)
    if (secondsIntoActivityDay >= 3 && secondsIntoActivityDay <= 24 * 3600 - 30) {
      const endedAt = new Date(now.getTime() - 1000)
      const startedAt = new Date(endedAt.getTime() - 1000)
      return {
        activityDate: activityDate(timeZone, endedAt),
        localStartedAt: localDateTime(startedAt, timeZone),
        localEndedAt: localDateTime(endedAt, timeZone),
      }
    }
    await page.waitForTimeout(3000)
  }
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

async function createManualFocus(
  page: Page,
  csrf: string,
  interval: { localStartedAt: string; localEndedAt: string },
) {
  const response = await page.request.post('/api/v1/focus-sessions/manual', {
    headers: { 'X-CSRF-Token': csrf },
    data: {
      operation_id: randomUUID(),
      title: '统计补记',
      task_id: null,
      occurrence_key: null,
      local_started_at: interval.localStartedAt,
      local_ended_at: interval.localEndedAt,
      time_zone: 'Asia/Shanghai',
      start_utc_offset_minutes: null,
      end_utc_offset_minutes: null,
      effective_duration_seconds: 1,
    },
  })
  expect(response.ok()).toBeTruthy()
}

async function createAbandonedFocus(page: Page, csrf: string) {
  const startedResponse = await page.request.post('/api/v1/focus-sessions', {
    headers: { 'X-CSRF-Token': csrf },
    data: {
      operation_id: randomUUID(),
      mode: 'stopwatch',
      target_seconds: null,
      title: '中止的短专注',
      task_id: null,
      occurrence_key: null,
      scheduled_block_id: null,
    },
  })
  expect(startedResponse.ok()).toBeTruthy()
  const started = (await startedResponse.json()).focus
  await page.waitForTimeout(1100)
  const abandoned = await page.request.post(
    `/api/v1/focus-sessions/${started.id}/transitions`,
    {
      headers: { 'X-CSRF-Token': csrf },
      data: {
        operation_id: randomUUID(),
        expected_version: started.version,
        action: 'abandon',
        client_recorded_at: null,
      },
    },
  )
  expect(abandoned.ok()).toBeTruthy()
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

test('personal statistics separate plan task and focus facts', async ({
  page,
  request,
}, info) => {
  test.setTimeout(90_000)
  const activity = await currentActivityFixture(page, 'Asia/Shanghai')
  const csrf = await account(page, request)
  const targetDate = activity.activityDate
  const task = await createTask(page, csrf)
  await createPlan(page, csrf, task, targetDate)
  await completeTask(page, csrf, task)
  await createManualFocus(page, csrf, activity)
  await createAbandonedFocus(page, csrf)

  await page.goto(`/stats?scope=week&date=${targetDate}`)
  await expect(page).toHaveTitle('统计 · iTime')
  await expect(page.getByRole('heading', { name: '周统计' })).toBeVisible()
  await expect(page.getByText('活动日于当地 04:00 切换')).toBeVisible()

  const planning = page.locator('.statistics-metric-group[data-kind="planning"]')
  await expect(planning.getByText('1 小时', { exact: true })).toBeVisible()
  await expect(planning.getByText('计划块').locator('..').getByRole('definition'))
    .toHaveText('1')

  const tasks = page.locator('.statistics-metric-group[data-kind="tasks"]')
  await expect(tasks.getByText('1 项完成', { exact: true })).toBeVisible()
  await expect(tasks.getByText('跳过实例').locator('..').getByRole('definition'))
    .toHaveText('0')

  const focus = page.locator('.statistics-metric-group[data-kind="focus"]')
  await expect(focus.getByText('补记').locator('..').getByRole('definition'))
    .toHaveText('1')
  await expect(focus.getByText('已放弃').locator('..').getByRole('definition'))
    .toHaveText('1')
  await expect(page.getByRole('heading', { name: '计划分钟' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '任务结果' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '专注分钟' })).toBeVisible()

  await page.getByRole('tab', { name: '日', exact: true }).click()
  await expect(page).toHaveURL(new RegExp(`/stats\\?scope=day&date=${targetDate}`))
  await expect(page.getByRole('heading', { name: '日统计' })).toBeVisible()
  await page.getByRole('tab', { name: '月', exact: true }).click()
  await expect(page).toHaveURL(/scope=month/)
  await expect(page.getByRole('heading', { name: '月统计' })).toBeVisible()
  await page.getByRole('tab', { name: '周', exact: true }).click()
  await expect(page.getByRole('heading', { name: '周统计' })).toBeVisible()

  let failNext = true
  await page.route('**/api/v1/statistics?*', route => {
    if (failNext) {
      failNext = false
      return route.abort('connectionreset')
    }
    return route.continue()
  })
  await page.getByRole('button', { name: '刷新统计' }).click()
  await expect(page.getByRole('alert')).toContainText('无法连接服务')
  await expect(page.getByText(/仍显示 .* 计算的结果/)).toBeVisible()
  await expect(planning.getByText('1 小时', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '重新读取' }).click()
  await expect(page.getByRole('alert')).toHaveCount(0)

  await page.getByText('查看统计口径').click()
  await expect(page.getByText('计划只读取当前计划版本')).toBeVisible()
  await pageHealth(page)
  await screenshot(page, info, 'populated')

  if (info.project.name === 'mobile') {
    await page.evaluate(() => {
      localStorage.setItem('itime-appearance', JSON.stringify({
        theme: 'dark',
        fontSize: 'larger',
      }))
    })
    await page.setViewportSize({ width: 320, height: 700 })
    await page.reload()
    await expect(page.getByRole('heading', { name: '周统计' })).toBeVisible()
    await pageHealth(page)
    await screenshot(page, info, '320-large-dark')
  }
})

test('empty historical range remains neutral and current navigation is bounded', async ({
  page,
  request,
}) => {
  await account(page, request)
  await page.goto('/stats?scope=day&date=2020-01-01')
  await expect(page.getByRole('heading', { name: '日统计' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '这个范围还没有记录' }))
    .toBeVisible()
  await expect(
    page.getByText('计划、任务结果和专注事实都会在保存后显示在这里。'),
  ).toBeVisible()
  await page.getByRole('button', { name: '回到当前' }).click()
  await expect(page).not.toHaveURL(/date=2020-01-01/)
  await expect(page.getByRole('button', { name: '下一范围' })).toBeDisabled()
  await pageHealth(page)
})
