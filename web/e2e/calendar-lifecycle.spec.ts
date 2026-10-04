import AxeBuilder from '@axe-core/playwright'
import { expect, test, type APIRequestContext, type Page, type TestInfo } from '@playwright/test'
import { randomUUID } from 'node:crypto'
import { mkdir, readFile } from 'node:fs/promises'

const password = 'Valid-calendar-lifecycle-password-123'
const evidence = '/tmp/itime-ui-evidence'
const runtimeErrors = new WeakMap<Page, string[]>()

async function bootstrap(info: TestInfo) {
  const path = process.env.ITIME_E2E_BOOTSTRAP_FILE
  if (!path) throw new Error('missing ITIME_E2E_BOOTSTRAP_FILE')
  const values = JSON.parse(await readFile(path, 'utf8')) as {
    project: string
    calendar_origin: string
  }[]
  const value = values.find(item => item.project === info.project.name)
  if (!value) throw new Error(`missing bootstrap for ${info.project.name}`)
  return value
}

function isoDate(date: Date) {
  return date.toISOString().slice(0, 10)
}

function futureDate(days: number) {
  const date = new Date()
  date.setUTCDate(date.getUTCDate() + days)
  return isoDate(date)
}

function addDays(value: string, days: number) {
  const date = new Date(`${value}T12:00:00Z`)
  date.setUTCDate(date.getUTCDate() + days)
  return isoDate(date)
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
  const address = `calendar-lifecycle-ui-${randomUUID()}@example.com`
  await page.goto('/register')
  await page.getByLabel('邮箱', { exact: true }).fill(address)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByLabel('确认密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '创建账号', exact: true }).click()
  await expect(page.getByRole('heading', { name: '去邮箱看看吧' })).toBeVisible()
  await page.goto(await mailLink(request, address))
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '验证邮箱', exact: true }).click()
  await page.getByRole('link', { name: '前往登录' }).click()
  await page.getByLabel('邮箱', { exact: true }).fill(address)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL('/ai')
}

async function csrf(page: Page) {
  const response = await page.request.get('/api/v1/auth/me')
  expect(response.ok()).toBeTruthy()
  return (await response.json()).csrf_token as string
}

async function createSeries(page: Page, date: string) {
  const token = await csrf(page)
  const response = await page.request.post('/api/v1/calendar/series', {
    headers: { 'X-CSRF-Token': token },
    data: {
      operation_id: randomUUID(),
      kind: 'fixed',
      term_id: null,
      name: '回收测试安排',
      description: '删除后可恢复',
      location: '图书馆',
      busy: true,
      time_zone: null,
      start_date: date,
      starts_at: '09:00:00',
      ends_at: '10:00:00',
      end_day_offset: 0,
      start_period_number: null,
      end_period_number: null,
      ambiguous_utc_offset_minutes: null,
      recurrence: {
        frequency: 'daily',
        interval: 1,
        weekdays: [],
        month: null,
        day_of_month: null,
        until: null,
        count: 3,
        week_numbers: [],
      },
    },
  })
  expect(response.ok()).toBeTruthy()
  return (await response.json()).series as {
    id: string
    name: string
    version: number
  }
}

interface SubscriptionSummary {
  id: string
  name: string
  lifecycle_status: 'active' | 'paused'
  refresh_status: 'idle' | 'queued' | 'running' | 'error'
  generation: number
  version: number
}

async function createSubscription(
  page: Page,
  origin: string,
  scenario: string,
  name: string,
) {
  const token = await csrf(page)
  const response = await page.request.post('/api/v1/calendar/subscriptions', {
    headers: { 'X-CSRF-Token': token },
    data: {
      operation_id: randomUUID(),
      name,
      url: `${origin}/calendar/${randomUUID()}/private.ics?scenario=${scenario}&token=hidden`,
      refresh_interval_minutes: 60,
    },
  })
  expect(response.ok()).toBeTruthy()
  let subscription: SubscriptionSummary | undefined
  await expect.poll(async () => {
    const current = await page.request.get('/api/v1/calendar/subscriptions')
    const body = await current.json()
    subscription = body.items.find(
      (item: SubscriptionSummary) => item.name === name,
    )
    return subscription?.refresh_status
  }, { timeout: 15_000 }).toBe('idle')
  return subscription!
}

async function setTimeZone(page: Page, value: string) {
  const token = await csrf(page)
  const response = await page.request.get('/api/v1/time-settings')
  expect(response.ok()).toBeTruthy()
  const settings = (await response.json()).settings
  if (settings.time_zone === value) return token
  const updated = await page.request.put('/api/v1/time-settings', {
    headers: { 'X-CSRF-Token': token },
    data: {
      operation_id: randomUUID(),
      expected_version: settings.version,
      time_zone: value,
      week_starts_on: 1,
      activity_day_start_minute: 240,
    },
  })
  expect(updated.ok()).toBeTruthy()
  return token
}

async function addSeriesExceptionAndAddition(
  page: Page,
  series: { id: string; name: string; version: number },
  date: string,
) {
  const token = await csrf(page)
  const sourceDate = addDays(date, 1)
  const exception = await page.request.post(
    `/api/v1/calendar/series/${series.id}/exceptions`,
    {
      headers: { 'X-CSRF-Token': token },
      data: {
        operation_id: randomUUID(),
        expected_series_version: series.version,
        occurrence_key: `${sourceDate}T09:00:00`,
        action: 'cancelled',
        moved_start_date: null,
        moved_starts_at: null,
        moved_ends_at: null,
        moved_end_day_offset: null,
        moved_ambiguous_utc_offset_minutes: null,
        override_name: null,
        override_description: null,
        override_location: null,
        override_busy: null,
      },
    },
  )
  expect(exception.ok()).toBeTruthy()
  const afterException = (await exception.json()).series
  const addition = await page.request.post(
    `/api/v1/calendar/series/${series.id}/additions`,
    {
      headers: { 'X-CSRF-Token': token },
      data: {
        operation_id: randomUUID(),
        expected_series_version: afterException.version,
        source_occurrence_key: `${sourceDate}T09:00:00`,
        status: 'scheduled',
        local_start_date: addDays(date, 3),
        starts_at: '15:00:00',
        ends_at: '16:00:00',
        end_day_offset: 0,
        time_zone: null,
        ambiguous_utc_offset_minutes: null,
        override_name: '恢复后的补课安排',
        override_description: '恢复聚合后仍然存在',
        override_location: '补课教室',
        override_busy: true,
      },
    },
  )
  expect(addition.ok()).toBeTruthy()
  return (await addition.json()).series as typeof series
}

async function createConfirmedPlan(page: Page, token: string) {
  const taskResponse = await page.request.post('/api/v1/tasks', {
    headers: { 'X-CSRF-Token': token },
    data: {
      operation_id: randomUUID(),
      title: '恢复来源冲突任务',
      description: '来源恢复不能静默移动此计划块',
      project_id: null,
      estimated_minutes: 30,
      priority: 2,
      labels: [],
      links: [],
      steps: [{ title: '保持原计划时间' }],
    },
  })
  expect(taskResponse.ok()).toBeTruthy()
  const task = (await taskResponse.json()).task
  const planResponse = await page.request.post('/api/v1/plans/versions', {
    headers: { 'X-CSRF-Token': token },
    data: {
      operation_id: randomUUID(),
      week_start_date: '2026-10-05',
      expected_plan_version: 0,
      density: 'balanced',
      buffer_minutes: 0,
      availability_start: '08:00:00',
      availability_end: '22:00:00',
      blocks: [{
        task_id: task.id,
        occurrence_key: null,
        local_date: '2026-10-06',
        starts_at: '11:15:00',
        ends_at: '11:45:00',
        step_ids: [task.steps[0].id],
      }],
      unscheduled_items: [],
    },
  })
  expect(planResponse.ok()).toBeTruthy()
  return (await planResponse.json()).plan
}

async function trashSeries(
  page: Page,
  series: { id: string; version: number },
) {
  const response = await page.request.delete(`/api/v1/calendar/series/${series.id}`, {
    headers: { 'X-CSRF-Token': await csrf(page) },
    data: {
      operation_id: randomUUID(),
      expected_version: series.version,
    },
  })
  expect(response.ok()).toBeTruthy()
  return (await response.json()).series as typeof series
}

async function restoreSeries(
  page: Page,
  series: { id: string; version: number },
) {
  const response = await page.request.post(
    `/api/v1/calendar/series/${series.id}/restore`,
    {
      headers: { 'X-CSRF-Token': await csrf(page) },
      data: {
        operation_id: randomUUID(),
        expected_version: series.version,
      },
    },
  )
  expect(response.ok()).toBeTruthy()
  return (await response.json()).series as typeof series
}

async function pauseSubscription(
  page: Page,
  subscription: SubscriptionSummary,
) {
  const response = await page.request.post(
    `/api/v1/calendar/subscriptions/${subscription.id}/pause`,
    {
      headers: { 'X-CSRF-Token': await csrf(page) },
      data: {
        operation_id: randomUUID(),
        expected_version: subscription.version,
      },
    },
  )
  expect(response.ok()).toBeTruthy()
  return (await response.json()).subscription as SubscriptionSummary
}

async function trashSubscription(
  page: Page,
  subscription: SubscriptionSummary,
) {
  const response = await page.request.delete(
    `/api/v1/calendar/subscriptions/${subscription.id}`,
    {
      headers: { 'X-CSRF-Token': await csrf(page) },
      data: {
        operation_id: randomUUID(),
        expected_version: subscription.version,
      },
    },
  )
  expect(response.ok()).toBeTruthy()
  return (await response.json()).subscription as SubscriptionSummary
}

async function goCalendar(page: Page, date: string) {
  await page.goto('/calendar')
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
  await page.getByLabel('跳转日期').fill(date)
  await expect(page.getByText('正在读取日历…')).toHaveCount(0)
}

async function eventButton(page: Page, date: string, text: string) {
  const daySelector = (page.viewportSize()?.width ?? 0) <= 840
    ? '.agenda-day'
    : '.calendar-day'
  return page.locator(daySelector).filter({
    has: page.locator(`time[datetime="${date}"]`),
  }).getByRole('button').filter({ hasText: text })
}

async function screenshot(page: Page, info: TestInfo, name: string) {
  await mkdir(evidence, { recursive: true })
  await page.screenshot({
    path: `${evidence}/${info.project.name}-calendar-lifecycle-${name}.png`,
    fullPage: false,
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

function consumeExpectedApiFailure(page: Page, path: string) {
  const errors = runtimeErrors.get(page) ?? []
  const index = errors.findIndex(error => (
    error.startsWith('500 ') && error.includes(path)
  ))
  expect(index).toBeGreaterThanOrEqual(0)
  errors.splice(index, 1)
}

test.beforeEach(async ({ page }) => watchRuntime(page))
test.afterEach(async ({ page }) => expect(runtimeErrors.get(page)).toEqual([]))

test('日历安排可直接移入回收站并立即退出普通日历', async ({
  page,
  request,
}, info) => {
  const date = futureDate(14)
  await account(page, request)
  await createSeries(page, date)
  await goCalendar(page, date)

  await (await eventButton(page, date, '回收测试安排')).click()
  await expect(
    page.getByRole('heading', { name: '回收测试安排' }),
  ).toBeVisible()
  page.once('dialog', dialog => dialog.accept())
  await page.getByRole('button', { name: '移到回收站', exact: true }).click()
  await expect(
    page.getByText('安排已移到回收站，将保留 30 天。'),
  ).toBeVisible()
  await expect(await eventButton(page, date, '回收测试安排')).toHaveCount(0)
  await expect(page.locator('html')).not.toHaveAttribute('data-vite-error')
  expect(await page.locator('body').evaluate(
    element => element.scrollWidth <= element.clientWidth,
  )).toBeTruthy()
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  await screenshot(page, info, 'direct-delete')
})

test('暂停与删除订阅保持不同语义并停止活动来源', async ({
  page,
  request,
}, info) => {
  const runtime = await bootstrap(info)
  await account(page, request)
  const token = await csrf(page)
  const created = await page.request.post('/api/v1/calendar/subscriptions', {
    headers: { 'X-CSRF-Token': token },
    data: {
      operation_id: randomUUID(),
      name: '回收测试订阅',
      url: `${runtime.calendar_origin}/calendar/${randomUUID()}/private.ics?token=hidden`,
      refresh_interval_minutes: 60,
    },
  })
  expect(created.ok()).toBeTruthy()
  await goCalendar(page, futureDate(14))
  await page.getByRole('button', { name: '导入日历', exact: true }).click()
  const importDialog = page.getByRole('dialog', { name: '导入日历' })
  await importDialog.locator('.import-mode-switch label').filter({
    hasText: '订阅 URL',
  }).click()
  await importDialog.getByRole('button', {
    name: /回收测试订阅/,
  }).click()
  await expect(
    importDialog.getByRole('heading', { name: '回收测试订阅' }),
  ).toBeVisible()
  await expect(importDialog.getByText(
    '暂停只停止后续刷新，当前日历内容和本地字段覆盖仍会保留。',
  )).toHaveCount(0)

  page.once('dialog', async dialog => {
    expect(dialog.message()).toContain('立即从普通日历隐藏')
    expect(dialog.message()).toContain('30 天')
    await dialog.accept()
  })
  await importDialog.getByRole('button', {
    name: '移到回收站',
    exact: true,
  }).click()
  const deleteSuccess = importDialog.getByText(
    '订阅已移到回收站，将保留 30 天。',
  )
  const deleteError = importDialog.getByRole('alert')
  await expect(deleteSuccess.or(deleteError)).toBeVisible()
  if (await deleteError.isVisible()) {
    await expect(deleteError).toContainText('此订阅已在其他页面或设备更新')
    await expect(deleteError).toContainText('请求编号')
    await importDialog.getByRole('button', { name: '重新读取' }).click()
    await expect(deleteError).toHaveCount(0)
    page.once('dialog', dialog => dialog.accept())
    await importDialog.getByRole('button', {
      name: '移到回收站',
      exact: true,
    }).click()
  }
  await expect(deleteSuccess).toBeVisible()
  await expect(importDialog.getByText('回收测试订阅')).toHaveCount(0)

  const active = await page.request.get(
    '/api/v1/calendar/subscriptions?lifecycle=active',
  )
  expect((await active.json()).items).toHaveLength(0)
  const trashed = await page.request.get(
    '/api/v1/calendar/subscriptions?lifecycle=trashed',
  )
  expect((await trashed.json()).items).toHaveLength(1)
  await screenshot(page, info, 'subscription-direct-delete')
})

test('统一回收站只读展示并恢复安排、活动订阅和暂停订阅', async ({
  page,
  request,
}, info) => {
  test.setTimeout(90_000)
  const { calendar_origin: origin } = await bootstrap(info)
  const date = futureDate(14)
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await account(page, request)

  let series = await createSeries(page, date)
  series = await addSeriesExceptionAndAddition(page, series, date)
  const active = await createSubscription(
    page,
    origin,
    'basic',
    '恢复后刷新订阅',
  )
  const paused = await pauseSubscription(
    page,
    await createSubscription(page, origin, 'basic', '保持暂停订阅'),
  )
  await trashSeries(page, series)
  await trashSubscription(page, active)
  await trashSubscription(page, paused)
  await goCalendar(page, date)

  const trigger = page.getByRole('button', {
    name: '日历回收站',
    exact: true,
  })
  await trigger.focus()
  await page.keyboard.press('Enter')
  let trash = page.getByRole('dialog', { name: '日历回收站' })
  await expect(trash).toBeVisible()
  await expect(
    trash.getByRole('heading', { name: '回收测试安排', exact: true }),
  ).toBeVisible()
  await expect(trash.getByText(/将在 .* 清理/)).toBeVisible()
  await expect(trash.getByText('删除后可恢复', { exact: true })).toBeVisible()
  await expect(trash.getByText('图书馆', { exact: true })).toBeVisible()
  await expect(trash.getByText(/每天.*3 次/)).toBeVisible()
  await expect(trash.getByRole('button', { name: /永久删除/ })).toHaveCount(0)
  await expect(trash.getByRole('button', { name: /编辑/ })).toHaveCount(0)

  await trash.getByRole('button', {
    name: '恢复回收测试安排',
    exact: true,
  }).click()
  await expect(trash.getByText('安排已恢复到日历。', { exact: true })).toBeVisible()
  await expect(trash.getByText('回收测试安排', { exact: true })).toHaveCount(0)
  await expect(trash.getByText('回收站中没有安排。', { exact: true })).toBeVisible()

  await trash.locator('.calendar-trash-scope label').filter({
    hasText: '订阅',
  }).click()
  await expect(
    trash.getByRole('button', { name: /查看订阅 恢复后刷新订阅/ }),
  ).toBeVisible()
  await trash.getByRole('button', { name: /查看订阅 恢复后刷新订阅/ }).click()
  await expect(
    trash.getByRole('heading', { name: active.name, exact: true }),
  ).toBeVisible()
  await expect(trash.getByText('删除前状态：使用中', { exact: true })).toBeVisible()
  await expect(trash.getByText(/private\.ics|token=hidden/)).toHaveCount(0)
  await expect(
    trash.getByRole('button', { name: '立即刷新', exact: true }),
  ).toHaveCount(0)
  await expect(
    trash.getByRole('button', { name: '暂停', exact: true }),
  ).toHaveCount(0)
  await trash.getByRole('button', {
    name: `恢复${active.name}`,
    exact: true,
  }).click()
  await expect(
    trash.getByText('订阅已恢复并重新进入刷新队列。', { exact: true }),
  ).toBeVisible()
  await expect.poll(async () => {
    const response = await page.request.get('/api/v1/calendar/subscriptions')
    const body = await response.json()
    return body.items.find(
      (item: SubscriptionSummary) => item.id === active.id,
    )
  }, { timeout: 15_000 }).toMatchObject({
    lifecycle_status: 'active',
    refresh_status: 'idle',
    generation: active.generation + 1,
  })

  await trash.getByRole('button', { name: /查看订阅 保持暂停订阅/ }).click()
  await expect(trash.getByText('删除前状态：已暂停', { exact: true })).toBeVisible()
  expect((await new AxeBuilder({ page }).include('.calendar-trash-dialog').analyze()).violations)
    .toEqual([])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    .toBe(true)

  if (info.project.name === 'desktop') {
    await page.evaluate(() => localStorage.setItem(
      'itime-appearance',
      JSON.stringify({ theme: 'dark', fontSize: 'larger' }),
    ))
    await page.setViewportSize({ width: 320, height: 700 })
    await page.reload()
    trash = page.getByRole('dialog', { name: '日历回收站' })
    await page.getByRole('button', { name: '日历回收站', exact: true }).click()
    await trash.locator('.calendar-trash-scope label').filter({
      hasText: '订阅',
    }).click()
    await trash.getByRole('button', { name: /查看订阅 保持暂停订阅/ }).click()
    await expect(trash.getByText('删除前状态：已暂停', { exact: true })).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
      .toBe(true)
    expect((await new AxeBuilder({ page }).include('.calendar-trash-dialog').analyze()).violations)
      .toEqual([])
    await screenshot(page, info, 'trash-320-dark-large')
  } else {
    await screenshot(page, info, 'trash')
  }

  await trash.getByRole('button', {
    name: `恢复${paused.name}`,
    exact: true,
  }).click()
  await expect(
    trash.getByText('订阅已恢复，仍保持暂停。', { exact: true }),
  ).toBeVisible()
  await expect.poll(async () => {
    const response = await page.request.get('/api/v1/calendar/subscriptions')
    const body = await response.json()
    return body.items.find(
      (item: SubscriptionSummary) => item.id === paused.id,
    )
  }).toMatchObject({
    lifecycle_status: 'paused',
    refresh_status: 'idle',
    generation: paused.generation + 1,
  })
  await page.waitForTimeout(2_100)
  const pausedDetail = await (
    await page.request.get(`/api/v1/calendar/subscriptions/${paused.id}`)
  ).json()
  expect(pausedDetail.subscription.generation).toBe(paused.generation + 1)

  await trash.getByRole('button', {
    name: '关闭日历回收站',
    exact: true,
  }).click()
  await expect(trigger).toBeFocused()
  await page.getByLabel('跳转日期').fill(date)
  await expect(page.getByText('正在读取日历…')).toHaveCount(0)
  await expect(await eventButton(page, date, '回收测试安排')).toBeVisible()
  await page.locator('.calendar-view-switch label').filter({ hasText: '月' }).click()
  await expect(
    await eventButton(page, addDays(date, 1), '回收测试安排'),
  ).toBeVisible()
  await expect(
    await eventButton(page, addDays(date, 3), '恢复后的补课安排'),
  ).toBeVisible()
  await expect(page.locator('html')).not.toHaveAttribute('data-vite-error')
})

test('回收站保留旧列表并支持网络失败和版本冲突恢复', async ({
  page,
  request,
  context,
}) => {
  test.setTimeout(90_000)
  const date = futureDate(16)
  await page.route('**/api/v1/sync/changes?**', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      events: [],
      next_cursor: 'calendar-lifecycle-test',
      has_more: false,
      reset_required: false,
      reset_reason: null,
      server_time: new Date().toISOString(),
    }),
  }))
  await account(page, request)
  const series = await createSeries(page, date)
  const deleted = await trashSeries(page, series)
  await goCalendar(page, date)

  let failLoads = true
  await page.route('**/api/v1/calendar/series?**', async route => {
    const url = new URL(route.request().url())
    if (url.searchParams.get('lifecycle') === 'trashed' && failLoads) {
      await route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({
          error: {
            code: 'service_unavailable',
            message: '回收站暂时无法读取。',
            request_id: 'calendar-trash-load-failure',
            saved: 'not_saved',
            retryable: true,
            details: {},
          },
        }),
      })
      return
    }
    await route.continue()
  })

  const trigger = page.getByRole('button', { name: '日历回收站', exact: true })
  await trigger.click()
  const trash = page.getByRole('dialog', { name: '日历回收站' })
  await expect(trash.getByRole('alert')).toContainText('回收站暂时无法读取')
  await expect(trash.getByText('请求编号：calendar-trash-load-failure')).toBeVisible()
  consumeExpectedApiFailure(page, 'lifecycle=trashed')
  failLoads = false
  await trash.getByRole('button', { name: '重新读取' }).click()
  await expect(
    trash.getByRole('heading', { name: series.name, exact: true }),
  ).toBeVisible()

  failLoads = true
  await trash.getByRole('button', { name: '刷新日历回收站' }).click()
  await expect(trash.getByRole('alert')).toContainText('回收站暂时无法读取')
  await expect(
    trash.getByRole('heading', { name: series.name, exact: true }),
  ).toBeVisible()
  consumeExpectedApiFailure(page, 'lifecycle=trashed')
  failLoads = false
  await trash.getByRole('button', { name: '重新读取' }).click()

  await context.setOffline(true)
  await trash.getByRole('button', {
    name: `恢复${series.name}`,
    exact: true,
  }).click()
  await expect(trash.getByRole('alert')).toContainText('无法连接服务')
  await expect(trash.getByText('保存状态尚未确认')).toBeVisible()
  await context.setOffline(false)

  const restoredElsewhere = await restoreSeries(page, deleted)
  await trashSeries(page, restoredElsewhere)
  await trash.getByRole('button', { name: '重试恢复' }).click()
  await expect(trash.getByRole('alert')).toContainText(
    '此日历系列已在其他页面或设备更新',
  )
  await expect(trash.getByText(/请求编号：/)).toBeVisible()
  await trash.getByRole('button', { name: '读取最新版' }).click()
  await expect(
    trash.getByText('已读取服务器最新版，可以再次恢复。', { exact: true }),
  ).toBeVisible()
  await trash.getByRole('button', {
    name: `恢复${series.name}`,
    exact: true,
  }).click()
  await expect(trash.getByText('安排已恢复到日历。', { exact: true })).toBeVisible()
  await expect(page.locator('html')).not.toHaveAttribute('data-vite-error')
})

test('恢复订阅与当前计划重叠时仅提示复核且不移动计划', async ({
  page,
  request,
}, info) => {
  const { calendar_origin: origin } = await bootstrap(info)
  await account(page, request)
  const token = await setTimeZone(page, 'Asia/Shanghai')
  const paused = await pauseSubscription(
    page,
    await createSubscription(page, origin, 'plan-conflict', '恢复冲突来源'),
  )
  await trashSubscription(page, paused)
  const originalPlan = await createConfirmedPlan(page, token)
  await goCalendar(page, '2026-10-06')

  await page.getByRole('button', { name: '日历回收站', exact: true }).click()
  const trash = page.getByRole('dialog', { name: '日历回收站' })
  await trash.locator('.calendar-trash-scope label').filter({
    hasText: '订阅',
  }).click()
  await expect(
    trash.getByRole('heading', { name: '恢复冲突来源', exact: true }),
  ).toBeVisible()
  await trash.getByRole('button', {
    name: '恢复恢复冲突来源',
    exact: true,
  }).click()
  await expect(
    trash.getByText('订阅已恢复，仍保持暂停。', { exact: true }),
  ).toBeVisible()
  await trash.getByRole('button', {
    name: '关闭日历回收站',
    exact: true,
  }).click()

  await expect(page.getByText(/1 个来源与计划块重叠/)).toBeVisible()
  await page.getByRole('link', { name: '打开规划处理' }).click()
  await expect(page.getByRole('heading', {
    name: '订阅来源更新后，当前计划需复核',
  })).toBeVisible()
  await expect(
    page.getByText(`第 ${originalPlan.version} 版计划没有被自动改动。`),
  ).toBeVisible()
  await expect(page.locator('.plan-block')).toHaveCount(1)
  await expect(page.locator('.plan-block').getByText(/11:15.*11:45/)).toBeVisible()
  expect((await new AxeBuilder({ page }).include('.plan-source-conflicts').analyze()).violations)
    .toEqual([])
})
