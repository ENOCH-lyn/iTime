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
    fullPage: true,
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
  await expect(importDialog.getByText(
    '订阅已移到回收站，将保留 30 天。',
  )).toBeVisible()
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
