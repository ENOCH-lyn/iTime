import { test, expect, type APIRequestContext, type Page, type TestInfo } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { randomUUID } from 'node:crypto'
import { mkdir } from 'node:fs/promises'

const password = 'Valid-calendar-ui-password-123'
const evidence = '/tmp/itime-ui-evidence'
const runtimeErrors = new WeakMap<Page, string[]>()

function isoDate(date: Date) {
  return date.toISOString().slice(0, 10)
}

function futureDate(days: number) {
  const date = new Date()
  date.setUTCDate(date.getUTCDate() + days)
  return isoDate(date)
}

function futureDateBeforeWeekEnd(days: number) {
  const value = futureDate(days)
  return new Date(`${value}T12:00:00Z`).getUTCDay() === 0
    ? addDays(value, 1)
    : value
}

function addDays(value: string, days: number) {
  const date = new Date(`${value}T12:00:00Z`)
  date.setUTCDate(date.getUTCDate() + days)
  return isoDate(date)
}

function mondayOnOrBefore(value: string) {
  const date = new Date(`${value}T12:00:00Z`)
  const weekday = date.getUTCDay() || 7
  date.setUTCDate(date.getUTCDate() - weekday + 1)
  return isoDate(date)
}

function weekdayLabel(value: string) {
  return ['日', '一', '二', '三', '四', '五', '六'][
    new Date(`${value}T12:00:00Z`).getUTCDay()
  ]
}

async function screenshot(page: Page, info: TestInfo, name: string) {
  await mkdir(evidence, { recursive: true })
  await page.screenshot({
    path: `${evidence}/${info.project.name}-calendar-${name}.png`,
    fullPage: true,
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
  const address = `calendar-ui-${randomUUID()}@example.com`
  await page.goto('/register')
  await page.getByLabel('邮箱', { exact: true }).fill(address)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByLabel('确认密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '创建账号', exact: true }).click()
  await expect(page.getByRole('heading', { name: '去邮箱看看吧' })).toBeVisible()
  await page.goto(await mailLink(request, address))
  await expect(page).toHaveURL('/ai')
  await page.getByRole('menuitem', { name: '任务', exact: true }).click()
  await expect(page).toHaveURL('/tasks')
}

async function csrf(page: Page) {
  const response = await page.request.get('/api/v1/auth/me')
  expect(response.ok()).toBeTruthy()
  return (await response.json()).csrf_token as string
}

async function createSeries(
  page: Page,
  fields: Partial<Record<string, unknown>> = {},
) {
  const token = await csrf(page)
  const response = await page.request.post('/api/v1/calendar/series', {
    headers: { 'X-CSRF-Token': token },
    data: {
      operation_id: randomUUID(),
      kind: 'fixed',
      term_id: null,
      name: '每日研讨',
      description: '用于日历单次操作测试',
      location: '研讨室',
      busy: true,
      time_zone: null,
      start_date: futureDate(14),
      starts_at: '13:00:00',
      ends_at: '14:00:00',
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
        count: 4,
        week_numbers: [],
      },
      ...fields,
    },
  })
  expect(response.ok()).toBeTruthy()
  return (await response.json()).series
}

async function goCalendar(page: Page, date: string) {
  await page.goto('/calendar')
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
  await page.getByLabel('跳转日期').fill(date)
  const daySelector = (page.viewportSize()?.width ?? 0) <= 840 ? '.agenda-day' : '.calendar-day'
  await expect(page.locator(daySelector).filter({
    has: page.locator(`time[datetime="${date}"]`),
  })).toBeVisible()
  await expect(page.getByText('正在读取日历…')).toHaveCount(0)
}

async function eventButton(page: Page, date: string, text: string) {
  const mobile = (page.viewportSize()?.width ?? 0) <= 840
  const day = page.locator(mobile ? '.agenda-day' : '.calendar-day').filter({
    has: page.locator(`time[datetime="${date}"]`),
  })
  return day.getByRole('button').filter({ hasText: text })
}

async function createTaskWithDueDate(
  page: Page,
  title: string,
  date: string,
  recurring = false,
) {
  await page.getByRole('button', { name: '新建任务', exact: true }).click()
  await page.getByLabel('任务名称', { exact: true }).fill(title)
  await page.getByRole('button', { name: '创建任务', exact: true }).first().click()
  await expect(page.getByText('任务已创建并保存到服务端。')).toBeVisible()
  await page.getByLabel('截止类型').selectOption('datetime')
  await page.getByLabel('截止日期', { exact: true }).fill(date)
  await page.getByLabel('截止时刻', { exact: true }).fill('18:30')
  if (recurring) {
    await page.getByLabel('重复方式').selectOption('daily')
    await page.getByLabel('共').check()
    await page.getByLabel('重复次数').fill('3')
  }
  await page.getByRole('button', { name: '保存日期与重复', exact: true }).click()
  await expect(page.getByText('日期与重复设置已保存。')).toBeVisible()
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
    if (
      response.url().includes('/api/v1/')
      && response.status() >= 500
    ) errors.push(`${response.status()} ${response.url()}`)
  })
}

test.beforeEach(async ({ page }) => watchRuntime(page))
test.afterEach(async ({ page }) => expect(runtimeErrors.get(page)).toEqual([]))

test('学期课程、固定安排和任务截止在统一日历中展示并标出冲突', async ({
  page,
  request,
}, info) => {
  const date = futureDate(14)
  const monday = mondayOnOrBefore(date)
  await account(page, request)
  await createTaskWithDueDate(page, '提交课程报告', date)
  await page.getByRole('menuitem', { name: '日历', exact: true }).click()
  await page.getByLabel('跳转日期').fill(date)

  await page.getByRole('button', { name: '时间与学期', exact: true }).click()
  const settingsDialog = page.getByRole('dialog', { name: '时间与学期' })
  await settingsDialog.getByRole('button', { name: '新建学期', exact: true }).click()
  await settingsDialog.getByLabel('学期名称').fill('2030 秋季学期')
  await settingsDialog.getByLabel('开始日期').fill(monday)
  await settingsDialog.getByLabel('结束日期').fill(addDays(monday, 90))
  await settingsDialog.getByLabel('第一周周一').fill(monday)
  await settingsDialog.getByLabel('节次 1 名称').fill('第一节')
  await settingsDialog.getByLabel('节次 1 开始时间').fill('08:00')
  await settingsDialog.getByLabel('节次 1 结束时间').fill('08:45')
  await settingsDialog.getByRole('button', { name: '添加节次', exact: true }).click()
  await settingsDialog.getByLabel('节次 2 名称').fill('第二节')
  await settingsDialog.getByLabel('节次 2 开始时间').fill('08:45')
  await settingsDialog.getByLabel('节次 2 结束时间').fill('09:30')
  await settingsDialog.getByRole('button', { name: '保存学期与节次', exact: true }).click()
  await expect(settingsDialog.getByText('学期与节次已保存。')).toBeVisible()
  await settingsDialog.getByRole('button', { name: '完成', exact: true }).click()

  await page.getByRole('button', { name: '新建安排', exact: true }).first().click()
  let seriesDialog = page.getByRole('dialog', { name: '新建安排' })
  await seriesDialog.getByLabel('名称', { exact: true }).fill('分布式系统')
  await seriesDialog.getByLabel('地点', { exact: false }).fill('A101')
  await seriesDialog.getByLabel('首个实例日期').fill(date)
  const courseWeekday = seriesDialog.getByLabel(weekdayLabel(date), { exact: true })
  if (!(await courseWeekday.isChecked())) {
    await seriesDialog.getByText(weekdayLabel(date), { exact: true }).click()
  }
  await seriesDialog.getByLabel('有效周次').fill('1-4')
  await seriesDialog.getByLabel('时间填写方式').selectOption('period')
  await expect(seriesDialog.getByLabel('开始节次')).toBeVisible()
  await seriesDialog.getByLabel('开始节次').selectOption('1')
  await seriesDialog.getByLabel('结束节次').selectOption('2')
  await seriesDialog.getByRole('button', { name: '创建安排', exact: true }).click()
  await expect(page.getByText('新安排已创建。')).toBeVisible()

  await page.getByRole('button', { name: '时间与学期', exact: true }).click()
  await settingsDialog.getByRole('button', { name: '编辑2030 秋季学期', exact: true }).click()
  await settingsDialog.getByLabel('节次 1 开始时间').fill('10:00')
  await settingsDialog.getByLabel('节次 1 结束时间').fill('10:45')
  await settingsDialog.getByLabel('节次 2 开始时间').fill('10:45')
  await settingsDialog.getByLabel('节次 2 结束时间').fill('11:30')
  await settingsDialog.getByRole('button', { name: '保存学期与节次', exact: true }).click()
  await expect(settingsDialog.getByText('学期与节次已保存。')).toBeVisible()
  await settingsDialog.getByRole('button', { name: '完成', exact: true }).click()

  await (await eventButton(page, date, '分布式系统')).click()
  await page.getByRole('button', { name: '编辑整个系列', exact: true }).click()
  const courseEditor = page.getByRole('dialog', { name: '编辑整个系列' })
  await expect(courseEditor.getByText(/不会静默套用新节次/)).toBeVisible()
  await expect(courseEditor.getByLabel('开始时刻')).toHaveValue('08:00')
  await expect(courseEditor.getByLabel('结束时刻')).toHaveValue('09:30')
  await courseEditor.getByRole('button', { name: '保存整个系列', exact: true }).click()
  await expect(page.locator('.occurrence-panel').getByText('系列修改已保存。')).toBeVisible()

  await page.getByRole('button', { name: '新建安排', exact: true }).first().click()
  seriesDialog = page.getByRole('dialog', { name: '新建安排' })
  await seriesDialog.getByText('固定安排', { exact: true }).click()
  await seriesDialog.getByLabel('名称', { exact: true }).fill('项目讨论')
  await seriesDialog.getByLabel('地点', { exact: false }).fill('线上')
  await seriesDialog.getByLabel('开始日期').fill(date)
  await seriesDialog.getByLabel('开始时刻').fill('08:30')
  await seriesDialog.getByLabel('结束时刻').fill('09:15')
  await seriesDialog.getByRole('button', { name: '创建安排', exact: true }).click()
  await expect(page.getByText(/检测到 1 处时间冲突/)).toBeVisible()

  await expect(await eventButton(page, date, '分布式系统')).toBeVisible()
  await expect(await eventButton(page, date, '项目讨论')).toBeVisible()
  await expect(await eventButton(page, date, '提交课程报告')).toBeVisible()
  const course = await eventButton(page, date, '分布式系统')
  await course.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByText('这段时间与其他占用安排重叠')).toBeVisible()
  if ((page.viewportSize()?.width ?? 0) <= 840) {
    await expect(page.locator('.occurrence-header h1')).toBeFocused()
    await page.getByRole('button', { name: '返回日历' }).click()
    await expect(course).toBeFocused()
  }
  await page.locator('.calendar-view-switch').getByText('月', { exact: true }).click()
  await expect(await eventButton(page, date, '提交课程报告')).toBeVisible()
  await page.getByLabel('跳转日期').fill('')
  await page.locator('.calendar-title h1').click()
  await expect(page.getByLabel('跳转日期')).toHaveValue(date)
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  expect(await page.locator('vite-error-overlay').count()).toBe(0)
  await screenshot(page, info, 'unified-month')
})

test('重复任务实例可创建关联补做并独立完成', async ({
  page,
  request,
}, info) => {
  const start = futureDateBeforeWeekEnd(14)
  const makeupDate = addDays(start, 1)
  await account(page, request)
  await createTaskWithDueDate(page, '重复任务补做测试', start, true)
  await goCalendar(page, start)

  await (await eventButton(page, start, '重复任务补做测试')).click()
  const panel = page.locator('.occurrence-panel')
  await panel.getByRole('button', { name: '安排补做', exact: true }).click()
  await panel.getByLabel('补做日期').fill(makeupDate)
  await panel.getByLabel('补做时刻').fill('20:15')
  await screenshot(page, info, 'task-makeup-form')
  await panel.getByRole('button', { name: '创建补做', exact: true }).click()
  await expect(panel.getByText('补做任务已创建，原实例保持不变。')).toBeVisible()

  const initialResponse = await page.request.get(
    `/api/v1/task-occurrences?start=${start}&end=${addDays(makeupDate, 1)}`,
  )
  expect(initialResponse.ok()).toBeTruthy()
  const initialItems = (await initialResponse.json()).items as Array<{
    occurrence_key: string
    kind: 'base' | 'makeup'
    due_date: string
    due_time: string | null
    status: 'open' | 'completed' | 'skipped'
    makeup_for_key: string | null
  }>
  const original = initialItems.find(item => item.kind === 'base' && item.due_date === start)
  const makeup = initialItems.find(item => item.kind === 'makeup')
  expect(original).toBeTruthy()
  expect(makeup).toMatchObject({
    due_date: makeupDate,
    due_time: '20:15:00',
    status: 'open',
    makeup_for_key: original?.occurrence_key,
  })

  if ((page.viewportSize()?.width ?? 0) <= 840) {
    await page.getByRole('button', { name: '返回日历' }).click()
  }
  await (await eventButton(page, makeupDate, '20:15')).click()
  await expect(panel.getByText(`关联补做 · 原实例 ${start}`, { exact: true })).toBeVisible()
  await expect(panel.getByRole('button', { name: '安排补做', exact: true })).toHaveCount(0)
  await panel.getByRole('button', { name: '标为完成', exact: true }).click()
  await expect(panel.getByText('任务实例已完成。')).toBeVisible()

  const completedResponse = await page.request.get(
    `/api/v1/task-occurrences?start=${start}&end=${addDays(makeupDate, 1)}`,
  )
  expect(completedResponse.ok()).toBeTruthy()
  const completedItems = (await completedResponse.json()).items as typeof initialItems
  expect(completedItems.find(item => item.occurrence_key === original?.occurrence_key)?.status)
    .toBe('open')
  expect(completedItems.find(item => item.kind === 'makeup')?.status).toBe('completed')

  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  expect(await page.locator('vite-error-overlay').count()).toBe(0)
  await screenshot(page, info, 'task-makeup-completed')

  if (info.project.name === 'desktop') {
    await page.evaluate(() => localStorage.setItem(
      'itime-appearance',
      JSON.stringify({ theme: 'dark', fontSize: 'larger' }),
    ))
    await page.setViewportSize({ width: 320, height: 700 })
    await page.reload()
    await page.getByLabel('跳转日期').fill(start)
    await (await eventButton(page, start, '18:30')).click()
    await panel.getByRole('button', { name: '安排补做', exact: true }).click()
    await expect(panel.getByRole('heading', { name: '安排关联补做' })).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
    await screenshot(page, info, 'task-makeup-form-320-large-dark')
  }
})

test('单次取消、调课、关联补课及补课恢复均保留原系列', async ({
  page,
  request,
}, info) => {
  const start = futureDateBeforeWeekEnd(14)
  await account(page, request)
  const created = await createSeries(page, { start_date: start, busy: false })
  await goCalendar(page, start)

  await (await eventButton(page, start, '每日研讨')).click()
  const panel = page.locator('.occurrence-panel')
  await page.getByRole('button', { name: '取消本次', exact: true }).click()
  await expect(panel.getByText('本次安排已取消，其他实例未受影响。')).toBeVisible()
  await page.getByRole('button', { name: '恢复本次', exact: true }).click()
  await expect(panel.getByText('本次安排已恢复。')).toBeVisible()
  const restored = await page.request.get(
    `/api/v1/calendar/occurrences?start=${start}&end=${addDays(start, 1)}`,
  )
  const restoredItem = (await restored.json()).items[0]
  expect(restoredItem.status).toBe('scheduled')
  expect(restoredItem.busy).toBe(false)
  await page.getByRole('button', { name: '取消本次', exact: true }).click()
  await expect(panel.getByText('本次安排已取消，其他实例未受影响。')).toBeVisible()
  await page.getByRole('button', { name: '安排补课', exact: true }).click()
  await panel.getByLabel('日期', { exact: true }).fill(addDays(start, 1))
  await panel.getByLabel('开始', { exact: true }).fill('13:30')
  await panel.getByLabel('结束', { exact: true }).fill('14:30')
  await panel.getByRole('button', { name: '创建补课', exact: true }).click()
  await expect(panel.getByText(/补课已关联到本次原安排/)).toBeVisible()

  if ((page.viewportSize()?.width ?? 0) <= 840) {
    await page.getByRole('button', { name: '返回日历' }).click()
  }
  const second = addDays(start, 1)
  await (await eventButton(page, second, '13:30')).click()
  await expect(page.getByRole('button', { name: '取消补课', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '取消补课', exact: true }).click()
  await expect(panel.getByText('本次补课已取消。')).toBeVisible()
  await page.getByRole('button', { name: '恢复补课', exact: true }).click()
  await expect(panel.getByText('本次补课已恢复。')).toBeVisible()

  if ((page.viewportSize()?.width ?? 0) <= 840) {
    await page.getByRole('button', { name: '返回日历' }).click()
  }
  await (await eventButton(page, second, '13:00')).click()
  await expect(page.getByRole('button', { name: '调整本次时间', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '调整本次时间', exact: true }).click()
  await panel.getByLabel('开始', { exact: true }).fill('16:00')
  await panel.getByLabel('结束', { exact: true }).fill('17:00')
  await panel.getByRole('button', { name: '保存本次', exact: true }).click()
  await expect(panel.getByText('本次安排已调课。')).toBeVisible()

  const response = await page.request.get(
    `/api/v1/calendar/occurrences?start=${start}&end=${addDays(start, 5)}`,
  )
  const items = (await response.json()).items
  expect(items).toHaveLength(5)
  expect(items.find((item: { occurrence_key: string }) =>
    item.occurrence_key === `${start}T13:00:00`).status).toBe('cancelled')
  expect(items.some((item: {
    occurrence_type: string
    status: string
    source_occurrence_key: string
  }) => (
    item.occurrence_type === 'addition'
    && item.status === 'scheduled'
    && item.source_occurrence_key === `${start}T13:00:00`
  ))).toBeTruthy()
  expect((await page.request.get(`/api/v1/calendar/series/${created.id}`)).ok()).toBeTruthy()
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  await screenshot(page, info, 'exceptions')
})

test('系列并发冲突保留草稿，刷新版本后可拆分本次及以后', async ({
  page,
  request,
  context,
}, info) => {
  const start = futureDateBeforeWeekEnd(21)
  await account(page, request)
  const original = await createSeries(page, { name: '晨间复习', start_date: start })
  await goCalendar(page, start)
  await (await eventButton(page, start, '晨间复习')).click()
  await page.getByRole('button', { name: '编辑整个系列', exact: true }).click()
  await expect(page.getByRole('heading', { name: '编辑整个系列' })).toBeVisible()
  const firstDialog = page.getByRole('dialog', { name: '编辑整个系列' })

  const secondPage = await context.newPage()
  watchRuntime(secondPage)
  try {
    await goCalendar(secondPage, start)
    await (await eventButton(secondPage, start, '晨间复习')).click()
    await secondPage.getByRole('button', { name: '编辑整个系列', exact: true }).click()
    const secondDialog = secondPage.getByRole('dialog', { name: '编辑整个系列' })
    await secondDialog.getByLabel('名称', { exact: true }).fill('晨间复习已同步')
    await secondDialog.getByLabel('地点', { exact: false }).fill('云端会议室')
    await secondDialog.getByRole('button', { name: '保存整个系列', exact: true }).click()
    await expect(secondPage.locator('.occurrence-panel').getByText('系列修改已保存。')).toBeVisible()

    await firstDialog.getByLabel('名称', { exact: true }).fill('本地保留的草稿')
    await firstDialog.getByRole('button', { name: '保存整个系列', exact: true }).click()
    await expect(firstDialog.getByRole('alert')).toContainText('已在其他页面或设备更新')
    await expect(firstDialog.getByLabel('名称', { exact: true })).toHaveValue('本地保留的草稿')
    await expect(firstDialog.getByText(/服务器最新版 · 版本 2/)).toBeVisible()
    await expect(firstDialog.getByText('地点：云端会议室', { exact: true })).toBeVisible()
    await expect(firstDialog.getByRole('button', { name: /明确覆盖服务器内容/ })).toBeVisible()
    await firstDialog.getByRole('button', { name: '取消', exact: true }).click()
  } finally {
    expect(runtimeErrors.get(secondPage)).toEqual([])
    runtimeErrors.delete(secondPage)
    await secondPage.close()
  }

  if ((page.viewportSize()?.width ?? 0) <= 840) {
    await page.getByRole('button', { name: '返回日历' }).click()
  }
  await page.getByRole('button', { name: '刷新日历', exact: true }).click()
  const splitDate = addDays(start, 1)
  await (await eventButton(page, splitDate, '晨间复习已同步')).click()
  await page.getByRole('button', { name: '修改本次及以后', exact: true }).click()
  const splitDialog = page.getByRole('dialog', { name: '修改本次及以后' })
  await splitDialog.getByLabel('名称', { exact: true }).fill('午后复习')
  await splitDialog.getByLabel('开始时刻').fill('15:00')
  await splitDialog.getByLabel('结束时刻').fill('16:00')
  await splitDialog.getByRole('button', { name: '保存本次及以后', exact: true }).click()
  await expect(page.getByText('已从本次开始创建新的后继系列。')).toBeVisible()

  const response = await page.request.get(
    `/api/v1/calendar/occurrences?start=${start}&end=${addDays(start, 5)}`,
  )
  const items = (await response.json()).items
  expect(items).toHaveLength(4)
  expect(items.find((item: { local_start_date: string }) =>
    item.local_start_date === start).name).toBe('晨间复习已同步')
  expect(items.find((item: { local_start_date: string }) =>
    item.local_start_date === splitDate).name).toBe('午后复习')
  const seriesResponse = await page.request.get('/api/v1/calendar/series?limit=100')
  expect((await seriesResponse.json()).items.filter((item: { id: string; predecessor_id: string | null }) =>
    item.id === original.id || item.predecessor_id === original.id)).toHaveLength(2)
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  await screenshot(page, info, 'split-series')
})

test('夏令时无效时刻被拒绝，跨日安排按保存时区展示', async ({
  page,
  request,
}, info) => {
  const dstDate = '2027-03-14'
  await account(page, request)
  await createSeries(page, {
    name: '上海晨会',
    time_zone: 'Asia/Shanghai',
    start_date: '2027-03-15',
    starts_at: '09:00:00',
    ends_at: '10:00:00',
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
  })
  await goCalendar(page, dstDate)
  await page.getByRole('button', { name: '时间与学期', exact: true }).click()
  const settingsDialog = page.getByRole('dialog', { name: '时间与学期' })
  await settingsDialog.getByLabel('IANA 时区').fill('America/New_York')
  await settingsDialog.getByRole('button', { name: '保存时区', exact: true }).click()
  await expect(settingsDialog.getByText(/当前时区已更新/)).toBeVisible()
  await settingsDialog.getByRole('button', { name: '完成', exact: true }).click()
  await expect(await eventButton(page, dstDate, '21:00')).toContainText('上海晨会')

  await page.getByRole('button', { name: '新建安排', exact: true }).first().click()
  const seriesDialog = page.getByRole('dialog', { name: '新建安排' })
  await seriesDialog.getByLabel('名称', { exact: true }).fill('DST 跳时测试')
  await seriesDialog.getByLabel('开始日期').fill(dstDate)
  await seriesDialog.getByLabel('开始时刻').fill('02:30')
  await seriesDialog.getByLabel('结束时刻').fill('03:30')
  await seriesDialog.getByRole('button', { name: '创建安排', exact: true }).click()
  await expect(seriesDialog.getByRole('alert')).toContainText('夏令时')
  await seriesDialog.getByLabel('名称', { exact: true }).fill('跨午夜维护')
  await seriesDialog.getByLabel('开始时刻').fill('23:30')
  await seriesDialog.getByLabel('结束时刻').fill('00:30')
  await seriesDialog.getByLabel('跨至次日结束').check()
  await seriesDialog.getByRole('button', { name: '创建安排', exact: true }).click()
  await expect(page.getByText('新安排已创建。')).toBeVisible()

  await page.getByRole('button', { name: '新建安排', exact: true }).first().click()
  const ambiguousDialog = page.getByRole('dialog', { name: '新建安排' })
  await ambiguousDialog.getByLabel('名称', { exact: true }).fill('回拨时刻安排')
  await ambiguousDialog.getByLabel('开始日期').fill('2027-11-07')
  await ambiguousDialog.getByLabel('开始时刻').fill('01:30')
  await ambiguousDialog.getByLabel('结束时刻').fill('02:30')
  await ambiguousDialog.getByRole('button', { name: '创建安排', exact: true }).click()
  await expect(ambiguousDialog.getByRole('alert')).toContainText('UTC 偏移')
  await ambiguousDialog.getByLabel('重复时刻的 UTC 偏移').selectOption('-240')
  await ambiguousDialog.getByRole('button', { name: '创建安排', exact: true }).click()
  await expect(page.getByText('新安排已创建。')).toBeVisible()

  await expect(await eventButton(page, dstDate, '跨午夜维护')).toBeVisible()
  const response = await page.request.get(
    `/api/v1/calendar/occurrences?start=${dstDate}&end=${addDays(dstDate, 2)}`,
  )
  const occurrence = (await response.json()).items.find(
    (item: { name: string }) => item.name === '跨午夜维护',
  )
  expect(occurrence.time_zone).toBe('America/New_York')
  expect(occurrence.local_end_date).toBe(addDays(dstDate, 1))
  const ambiguousResponse = await page.request.get(
    '/api/v1/calendar/occurrences?start=2027-11-07&end=2027-11-08',
  )
  const ambiguousOccurrence = (await ambiguousResponse.json()).items.find(
    (item: { name: string }) => item.name === '回拨时刻安排',
  )
  expect(ambiguousOccurrence.start_at).toBe('2027-11-07T05:30:00Z')
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await screenshot(page, info, 'timezone-overnight')

  if (info.project.name === 'desktop') {
    await page.evaluate(() => localStorage.setItem(
      'itime-appearance',
      JSON.stringify({ theme: 'light', fontSize: 'larger' }),
    ))
    await page.setViewportSize({ width: 320, height: 700 })
    await page.reload()
    await page.getByLabel('跳转日期').fill(dstDate)
    await expect(await eventButton(page, dstDate, '跨午夜维护')).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
    await screenshot(page, info, '320-large-font')
  }
})
