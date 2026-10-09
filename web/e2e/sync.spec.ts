import { randomUUID } from 'node:crypto'
import { mkdir } from 'node:fs/promises'
import AxeBuilder from '@axe-core/playwright'
import {
  expect,
  test,
  type APIRequestContext,
  type Page,
  type TestInfo,
} from '@playwright/test'

const password = 'Valid-sync-ui-password-123'
const runtimeErrors = new WeakMap<Page, string[]>()

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

async function screenshot(page: Page, info: TestInfo, name: string) {
  await mkdir('/tmp/itime-ui-evidence', { recursive: true })
  await page.screenshot({
    path: `/tmp/itime-ui-evidence/${info.project.name}-sync-${name}.png`,
    fullPage: true,
    animations: 'disabled',
  })
}

test.beforeEach(async ({ page }) => watchRuntime(page))
test.afterEach(async ({ page }) => {
  expect(runtimeErrors.get(page)).toEqual([])
})

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
  const address = `sync-ui-${randomUUID()}@example.com`
  await page.goto('/register')
  await page.getByLabel('邮箱', { exact: true }).fill(address)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByLabel('确认密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '创建账号', exact: true }).click()
  await page.goto(await mailLink(request, address))
  await expect(page).toHaveURL('/ai')
  const session = await (await page.request.get('/api/v1/auth/me')).json()
  return { ...session, email: address }
}

async function login(page: Page, email: string) {
  await page.goto('/login')
  await page.getByLabel('邮箱', { exact: true }).fill(email)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL('/ai')
}

async function createTask(page: Page, csrf: string, title: string) {
  const response = await page.request.post('/api/v1/tasks', {
    headers: { 'X-CSRF-Token': csrf },
    data: {
      operation_id: randomUUID(),
      title,
      description: '验证离线读取缓存。',
      project_id: null,
      estimated_minutes: 30,
      priority: 1,
      labels: [],
      links: [],
      steps: [{ title: '离线可见步骤' }],
    },
  })
  expect(response.ok()).toBeTruthy()
  return (await response.json()).task
}

function futureDate(days: number) {
  const value = new Date()
  value.setDate(value.getDate() + days)
  return [
    value.getFullYear(),
    String(value.getMonth() + 1).padStart(2, '0'),
    String(value.getDate()).padStart(2, '0'),
  ].join('-')
}

async function makeTaskRecurring(
  page: Page,
  csrf: string,
  task: { id: string; version: number },
  date: string,
) {
  const response = await page.request.put(`/api/v1/tasks/${task.id}/schedule`, {
    headers: { 'X-CSRF-Token': csrf },
    data: {
      operation_id: randomUUID(),
      expected_task_version: task.version,
      due_kind: 'date',
      due_date: date,
      due_time: null,
      time_zone: null,
      ambiguous_utc_offset_minutes: null,
      splittable: true,
      minimum_block_minutes: 15,
      recurrence: {
        frequency: 'daily',
        interval: 1,
        weekdays: [],
        month: null,
        day_of_month: null,
        until: null,
        count: 2,
        week_numbers: [],
      },
    },
  })
  expect(response.ok()).toBeTruthy()
}

async function createCalendarSeries(page: Page, csrf: string, date: string) {
  const response = await page.request.post('/api/v1/calendar/series', {
    headers: { 'X-CSRF-Token': csrf },
    data: {
      operation_id: randomUUID(),
      kind: 'fixed',
      term_id: null,
      name: '跨设备回收安排',
      description: '由同步事件刷新日历与回收站',
      location: '同步测试地点',
      busy: true,
      time_zone: null,
      start_date: date,
      starts_at: '13:00:00',
      ends_at: '14:00:00',
      end_day_offset: 0,
      start_period_number: null,
      end_period_number: null,
      ambiguous_utc_offset_minutes: null,
      recurrence: {
        frequency: 'none',
        interval: 1,
        weekdays: [],
        month: null,
        day_of_month: null,
        until: null,
        count: null,
        week_numbers: [],
      },
    },
  })
  expect(response.ok()).toBeTruthy()
  return (await response.json()).series as {
    id: string
    version: number
  }
}

async function jumpToDate(page: Page, date: string) {
  await page.goto(`/calendar?date=${date}`)
  await expect(page.getByText('正在读取日历…')).toHaveCount(0)
}

test('offline cache is encrypted and never hides an HTTP failure', async ({
  page,
  request,
  context,
}) => {
  const session = await account(page, request)
  const marker = `离线缓存任务 ${randomUUID()}`
  await createTask(page, session.csrf_token, marker)
  await page.goto('/tasks')
  await expect(page.getByText(marker, { exact: true })).toBeVisible()

  await expect.poll(async () => page.evaluate(async () => {
    const open = indexedDB.open('itime-sync-cache')
    const database = await new Promise<IDBDatabase>((resolve, reject) => {
      open.onsuccess = () => resolve(open.result)
      open.onerror = () => reject(open.error)
    })
    const transaction = database.transaction('responses', 'readonly')
    const request = transaction.objectStore('responses').getAll()
    const rows = await new Promise<unknown[]>((resolve, reject) => {
      request.onsuccess = () => resolve(request.result)
      request.onerror = () => reject(request.error)
    })
    database.close()
    return rows.length
  })).toBeGreaterThan(0)
  expect(await page.evaluate(async markerText => {
    const open = indexedDB.open('itime-sync-cache')
    const database = await new Promise<IDBDatabase>((resolve, reject) => {
      open.onsuccess = () => resolve(open.result)
      open.onerror = () => reject(open.error)
    })
    const transaction = database.transaction('responses', 'readonly')
    const request = transaction.objectStore('responses').getAll()
    const rows = await new Promise<unknown[]>((resolve, reject) => {
      request.onsuccess = () => resolve(request.result)
      request.onerror = () => reject(request.error)
    })
    database.close()
    return JSON.stringify(rows).includes(markerText)
  }, marker)).toBe(false)

  await page.getByRole('button', { name: '已归档', exact: true }).click()
  await expect(page.getByRole('heading', { name: '已归档' })).toBeVisible()
  await expect(page.locator('.task-list li')).toHaveCount(0)
  await context.setOffline(true)
  await page.getByRole('button', { name: '活动', exact: true }).click()
  await expect(page.getByText(marker, { exact: true })).toBeVisible()

  await context.setOffline(false)
  await page.route('**/api/v1/tasks?**', route => route.fulfill({
    status: 503,
    contentType: 'application/json',
    body: JSON.stringify({
      error: {
        code: 'service_unavailable',
        message: '同步测试服务错误。',
        request_id: 'sync-cache-http-error',
        saved: 'not_saved',
        retryable: true,
        details: {},
      },
    }),
  }))
  await page.getByRole('button', { name: '已归档', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('同步测试服务错误')
})

test('sync coordinator reports events offline state and cursor recovery', async ({
  page,
  request,
  context,
}, info) => {
  const session = await account(page, request)
  await page.goto('/settings#sync')
  await expect(page.getByRole('dialog', { name: '同步与离线' })).toBeVisible()
  await expect(page.getByText('已同步', { exact: true })).toBeVisible()

  const marker = `协调器任务 ${randomUUID()}`
  await createTask(page, session.csrf_token, marker)
  await expect.poll(async () => (
    page.getByTestId('sync-latest-change').textContent()
  ), { timeout: 5_000 }).not.toBe('暂无变更')

  await page.goto('/tasks')
  await expect(page.getByLabel(/同步：已同步/)).toBeVisible()
  await context.setOffline(true)
  await expect(page.getByText('当前离线。显示最近同步的数据。')).toBeVisible()
  await expect(page.getByLabel(/同步：离线/)).toBeVisible()
  await context.setOffline(false)
  await page.getByLabel(/同步：/).click()
  await expect(page).toHaveURL('/settings#sync')
  await expect(page.getByText('已同步', { exact: true })).toBeVisible()

  await page.evaluate(userId => {
    sessionStorage.setItem(`itime-sync-cursor:${userId}`, 'invalid.cursor')
  }, session.user.id)
  await page.reload()
  await expect(page.getByText('同步基线已重新建立。')).toBeVisible()
  await expect(page.getByText('已同步', { exact: true })).toBeVisible()
  expect(await page.evaluate(
    () => document.documentElement.scrollWidth <= window.innerWidth,
  )).toBe(true)
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  await screenshot(page, info, 'status')
  if (info.project.name === 'mobile') {
    await page.setViewportSize({ width: 320, height: 700 })
    await page.evaluate(() => localStorage.setItem(
      'itime-appearance',
      JSON.stringify({ theme: 'dark', fontSize: 'larger' }),
    ))
    await page.reload()
    await expect(page.getByRole('dialog', { name: '同步与离线' })).toBeVisible()
    expect(await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    )).toBe(true)
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
    await screenshot(page, info, 'status-320-large-dark')
  }
})

test('cross-device task refresh and offline step queue converge', async ({
  page,
  request,
  browser,
}) => {
  const session = await account(page, request)
  const secondContext = await browser.newContext()
  const second = await secondContext.newPage()
  try {
    await login(second, session.email)
    await second.goto('/tasks')
    const marker = `跨设备任务 ${randomUUID()}`
    const task = await createTask(page, session.csrf_token, marker)
    await expect(
      second.getByText(marker, { exact: true }),
    ).toBeVisible({ timeout: 5_000 })

    await second.getByText(marker, { exact: true }).click()
    await expect(
      second.getByLabel('完成步骤 离线可见步骤'),
    ).toBeVisible()
    await secondContext.setOffline(true)
    await second.getByLabel('完成步骤 离线可见步骤').click()
    await expect(second.getByLabel(/1 项待同步/)).toBeVisible()
    await expect(
      second.getByLabel('重新打开步骤 离线可见步骤'),
    ).toBeChecked()

    await secondContext.setOffline(false)
    await expect(second.getByLabel(/同步：已同步/)).toBeVisible({
      timeout: 8_000,
    })
    await expect.poll(async () => {
      const response = await second.request.get(`/api/v1/tasks/${task.id}`)
      const current = (await response.json()).task
      return current.steps[0].completed
    }).toBe(true)

    const current = (
      await (await page.request.get(`/api/v1/tasks/${task.id}`)).json()
    ).task
    const completed = await page.request.put(`/api/v1/tasks/${task.id}/completion`, {
      headers: { 'X-CSRF-Token': session.csrf_token },
      data: {
        operation_id: randomUUID(),
        expected_version: current.version,
        completed: true,
      },
    })
    expect(completed.ok()).toBeTruthy()
    await expect(
      second.getByRole('button', { name: '重新打开', exact: true }),
    ).toBeVisible({ timeout: 5_000 })
  } finally {
    await secondContext.close()
  }
})

test('calendar lifecycle sync refreshes an open trash dialog and restored calendar', async ({
  page,
  request,
  browser,
}) => {
  const session = await account(page, request)
  const date = futureDate(12)
  const series = await createCalendarSeries(page, session.csrf_token, date)
  await jumpToDate(page, date)
  await expect(page.getByRole('button', { name: /跨设备回收安排/ })).toBeVisible()

  const secondContext = await browser.newContext()
  const second = await secondContext.newPage()
  watchRuntime(second)
  try {
    await login(second, session.email)
    await jumpToDate(second, date)
    await expect(second.getByLabel(/同步：已同步/)).toBeVisible({
      timeout: 8_000,
    })
    await second.getByRole('button', {
      name: '日历回收站',
      exact: true,
    }).click()
    const trash = second.getByRole('dialog', { name: '日历回收站' })
    await expect(trash.getByText('回收站中没有安排。', { exact: true })).toBeVisible()

    const removed = await page.request.delete(
      `/api/v1/calendar/series/${series.id}`,
      {
        headers: { 'X-CSRF-Token': session.csrf_token },
        data: {
          operation_id: randomUUID(),
          expected_version: series.version,
        },
      },
    )
    expect(removed.ok()).toBeTruthy()
    await expect(
      trash.getByRole('heading', {
        name: '跨设备回收安排',
        exact: true,
      }),
    ).toBeVisible({ timeout: 5_000 })
    await expect(
      page.getByRole('button', { name: /跨设备回收安排/ }),
    ).toHaveCount(0, { timeout: 5_000 })

    await trash.getByRole('button', {
      name: '恢复跨设备回收安排',
      exact: true,
    }).click()
    await expect(
      page.getByRole('button', { name: /跨设备回收安排/ }),
    ).toBeVisible({ timeout: 5_000 })
    expect(runtimeErrors.get(second)).toEqual([])
  } finally {
    await secondContext.close()
  }
})

test('offline step conflict stays visible until explicitly discarded', async ({
  page,
  request,
  browser,
}, info) => {
  const session = await account(page, request)
  const marker = `冲突任务 ${randomUUID()}`
  const task = await createTask(page, session.csrf_token, marker)
  const secondContext = await browser.newContext()
  const second = await secondContext.newPage()
  try {
    await login(second, session.email)
    await second.goto('/tasks')
    await second.getByText(marker, { exact: true }).click()
    await secondContext.setOffline(true)
    await second.getByLabel('完成步骤 离线可见步骤').click()
    await expect(second.getByLabel(/1 项待同步/)).toBeVisible()

    const removed = await page.request.delete(
      `/api/v1/tasks/${task.id}/steps/${task.steps[0].id}`,
      {
        headers: { 'X-CSRF-Token': session.csrf_token },
        data: {
          operation_id: randomUUID(),
          expected_task_version: task.version,
        },
      },
    )
    expect(removed.ok()).toBeTruthy()

    await secondContext.setOffline(false)
    await expect(second.getByLabel(/1 项冲突/)).toBeVisible({
      timeout: 8_000,
    })
    await second.getByLabel(/1 项冲突/).click()
    await expect(
      second.getByRole('heading', { name: '同步冲突' }),
    ).toBeVisible()
    if (info.project.name === 'desktop') {
      await screenshot(second, info, 'conflict')
    }
    await second.getByRole('button', { name: '丢弃此操作' }).click()
    await expect(second.getByText('已同步', { exact: true })).toBeVisible()
  } finally {
    await secondContext.close()
  }
})

test('recurring occurrence step queues offline without changing the template', async ({
  page,
  request,
  context,
}) => {
  const session = await account(page, request)
  const marker = `重复实例同步 ${randomUUID()}`
  const task = await createTask(page, session.csrf_token, marker)
  const date = futureDate(2)
  await makeTaskRecurring(page, session.csrf_token, task, date)

  await page.goto('/calendar')
  await page.getByLabel('跳转日期').fill(date)
  const daySelector = (page.viewportSize()?.width ?? 0) <= 840
    ? '.agenda-day'
    : '.calendar-day'
  const day = page.locator(daySelector).filter({
    has: page.locator(`time[datetime="${date}"]`),
  })
  await day.getByRole('button').filter({ hasText: marker }).click()
  await expect(page.getByRole('heading', { name: '本次步骤' })).toBeVisible()

  await context.setOffline(true)
  await page.getByLabel('离线可见步骤', { exact: true }).click()
  await expect(page.getByLabel(/1 项待同步/)).toBeVisible()
  await context.setOffline(false)
  await expect(page.getByLabel(/同步：已同步/)).toBeVisible({
    timeout: 8_000,
  })

  await expect.poll(async () => {
    const response = await page.request.get(
      `/api/v1/task-occurrences?start=${date}&end=${futureDate(4)}`,
    )
    const items = (await response.json()).items
    return items[0]?.completed_step_ids.length
  }).toBe(1)
  const template = await (
    await page.request.get(`/api/v1/tasks/${task.id}`)
  ).json()
  expect(template.task.steps[0].completed).toBe(false)
})

test('explicit logout confirms and clears account-local sync data', async ({
  page,
  request,
}) => {
  const session = await account(page, request)
  const marker = `退出清理任务 ${randomUUID()}`
  await createTask(page, session.csrf_token, marker)
  await page.goto('/tasks')
  await page.getByText(marker, { exact: true }).click()
  await page.route(
    '**/api/v1/sync/tasks/**/completion',
    route => route.abort('failed'),
  )
  await page.getByLabel('完成步骤 离线可见步骤').click()
  await expect(page.getByLabel(/1 项待同步/)).toBeVisible()

  page.once('dialog', dialog => dialog.dismiss())
  await page.getByRole('button', { name: '退出登录' }).click()
  await expect(page).toHaveURL('/tasks')
  await expect(page.getByLabel(/1 项待同步/)).toBeVisible()

  page.once('dialog', dialog => dialog.accept())
  await page.getByRole('button', { name: '退出登录' }).click()
  await expect(page).toHaveURL('/login')
  await expect.poll(async () => page.evaluate(async userId => {
    const localKeys = Object.keys(localStorage)
      .filter(key => key.includes(userId))
    const open = indexedDB.open('itime-sync-cache')
    const database = await new Promise<IDBDatabase>((resolve, reject) => {
      open.onsuccess = () => resolve(open.result)
      open.onerror = () => reject(open.error)
    })
    const transaction = database.transaction('responses', 'readonly')
    const index = transaction.objectStore('responses').index('account_id')
    const request = index.count(IDBKeyRange.only(userId))
    const cacheCount = await new Promise<number>((resolve, reject) => {
      request.onsuccess = () => resolve(request.result)
      request.onerror = () => reject(request.error)
    })
    database.close()
    return localKeys.length + cacheCount
  }, session.user.id)).toBe(0)
})
