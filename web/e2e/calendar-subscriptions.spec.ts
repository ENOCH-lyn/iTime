import AxeBuilder from '@axe-core/playwright'
import { expect, test, type APIRequestContext, type Page, type TestInfo } from '@playwright/test'
import { randomUUID } from 'node:crypto'
import { mkdir, readFile } from 'node:fs/promises'

const password = 'Valid-calendar-subscription-ui-password-123'
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

async function screenshot(page: Page, info: TestInfo, name: string) {
  await mkdir(evidence, { recursive: true })
  await page.screenshot({
    path: `${evidence}/${info.project.name}-calendar-subscription-${name}.png`,
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
  const address = `calendar-subscription-ui-${randomUUID()}@example.com`
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
  await page.getByRole('link', { name: '日历', exact: true }).click()
  await expect(page).toHaveURL('/calendar')
}

async function csrf(page: Page) {
  const response = await page.request.get('/api/v1/auth/me')
  expect(response.ok()).toBeTruthy()
  return (await response.json()).csrf_token as string
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

async function openSubscriptions(page: Page) {
  await page.getByRole('button', { name: '导入日历', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '导入日历' })
  await expect(dialog).toBeVisible()
  await dialog.locator('.import-mode-switch label').filter({ hasText: '订阅 URL' }).click()
  await expect(dialog.getByRole('heading', { name: '日历订阅' })).toBeVisible()
  return dialog
}

async function createSubscription(
  page: Page,
  dialog: ReturnType<Page['getByRole']>,
  origin: string,
  scenario: string,
  name: string,
) {
  await dialog.getByLabel('名称', { exact: true }).first().fill(name)
  await dialog.getByLabel('ICS 订阅地址').fill(
    `${origin}/calendar/${randomUUID()}/private-token.ics?scenario=${scenario}&token=hidden`,
  )
  await dialog.getByLabel('刷新周期（分钟）').first().fill('15')
  await dialog.getByRole('button', { name: '创建订阅', exact: true }).click()
  await expect(dialog.getByText('订阅已创建，首次刷新已进入队列。')).toBeVisible()
  await expect.poll(async () => {
    const response = await page.request.get('/api/v1/calendar/subscriptions')
    const body = await response.json()
    return body.items.find(
      (item: { name: string }) => item.name === name,
    )?.refresh_status
  }, { timeout: 15_000 }).toBe('idle')
  const detail = dialog.getByRole('region', { name })
  await expect(detail.getByText('已就绪', { exact: true })).toBeVisible({
    timeout: 15_000,
  })
  await expect(dialog.getByText('订阅课程', { exact: true }).last()).toBeVisible()
  await expect(dialog.getByText(/private-token/)).toHaveCount(0)
}

test.beforeEach(async ({ page }) => watchRuntime(page))
test.afterEach(async ({ page }) => expect(runtimeErrors.get(page)).toEqual([]))

test('订阅创建、304、暂停恢复并可从日历返回来源', async ({
  page,
  request,
}, info) => {
  const { calendar_origin: origin } = await bootstrap(info)
  await account(page, request)
  let dialog = await openSubscriptions(page)
  await createSubscription(page, dialog, origin, 'basic', '课程订阅')

  await dialog.getByRole('button', { name: '立即刷新', exact: true }).click()
  await expect(dialog.getByText('无变化', { exact: true }).first()).toBeVisible({
    timeout: 15_000,
  })
  await dialog.getByRole('button', { name: '暂停', exact: true }).click()
  await expect(dialog.getByText('暂停只停止后续刷新')).toBeVisible()
  await expect(dialog.getByRole('button', { name: '立即刷新' })).toBeDisabled()
  await dialog.getByRole('button', { name: '恢复', exact: true }).click()
  await expect(dialog.getByText('订阅已恢复，刷新已进入队列。')).toBeVisible()
  await expect(dialog.getByText('已就绪', { exact: true }).first()).toBeVisible({
    timeout: 15_000,
  })
  expect((await new AxeBuilder({ page }).include('.subscription-manager').analyze()).violations)
    .toEqual([])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    .toBe(true)
  await screenshot(page, info, 'ready')

  await dialog.getByRole('button', { name: '完成', exact: true }).click()
  await page.getByLabel('跳转日期').fill('2026-10-06')
  await expect(page.getByText('正在读取日历…')).toHaveCount(0)
  const allDayButton = page.getByRole('button', { name: /全天纪念日/ }).first()
  await expect(allDayButton).toBeVisible()
  await allDayButton.click()
  const occurrencePanel = page.locator('.occurrence-panel')
  await expect(occurrencePanel.getByText('2026-10-06 全天', { exact: true }))
    .toBeVisible()
  await expect(occurrencePanel.getByText(/至 2026-10-07/)).toHaveCount(0)
  if (info.project.name !== 'desktop') {
    await occurrencePanel.getByRole('button', { name: '返回日历' }).click()
  }
  const eventButton = page.getByRole('button', { name: /订阅课程/ }).first()
  await expect(eventButton).toBeVisible()
  await eventButton.click()
  await expect(page.getByText('ICS 订阅', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '编辑整个系列' })).toHaveCount(0)
  const manageSource = page.getByRole('button', { name: '管理来源与本地覆盖' })
  await manageSource.click()
  dialog = page.getByRole('dialog', { name: '导入日历' })
  await expect(dialog.getByLabel('事件本地名称')).toBeVisible()
  await expect(dialog.getByText(/private-token/)).toHaveCount(0)
  await dialog.getByRole('button', { name: '完成', exact: true }).click()
  await expect(manageSource).toBeFocused()

  if (info.project.name === 'desktop') {
    await page.evaluate(() => localStorage.setItem(
      'itime-appearance',
      JSON.stringify({ theme: 'light', fontSize: 'larger' }),
    ))
    await page.setViewportSize({ width: 320, height: 700 })
    await page.reload()
    dialog = await openSubscriptions(page)
    await expect(dialog.getByText('课程订阅', { exact: true }).first()).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
      .toBe(true)
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
    await screenshot(page, info, '320-large-font')
  }
})

test('本地覆盖保留来源差异，刷新失败保留旧日历', async ({
  page,
  request,
}, info) => {
  const { calendar_origin: origin } = await bootstrap(info)
  await account(page, request)
  const dialog = await openSubscriptions(page)
  await createSubscription(page, dialog, origin, 'update', '会变化的订阅')

  await dialog.getByRole('button', { name: /订阅课程/ }).first().click()
  await dialog.getByText('本地覆盖名称').click()
  await dialog.getByLabel('事件本地名称').fill('我的课程名称')
  const subscriptions = await (
    await page.request.get('/api/v1/calendar/subscriptions')
  ).json()
  const source = subscriptions.items.find(
    (item: { name: string }) => item.name === '会变化的订阅',
  )
  const sourceDetail = await (
    await page.request.get(`/api/v1/calendar/subscriptions/${source.id}`)
  ).json()
  const sourceEvent = sourceDetail.events[0]
  const concurrentOverride = await page.request.put(
    `/api/v1/calendar/subscriptions/${source.id}/events/${sourceEvent.id}/overrides`,
    {
      headers: { 'X-CSRF-Token': await csrf(page) },
      data: {
        operation_id: randomUUID(),
        expected_version: 0,
        values: { location: '服务器地点' },
        reset_fields: [],
      },
    },
  )
  expect(concurrentOverride.ok()).toBeTruthy()
  await dialog.getByRole('button', { name: '保存字段覆盖' }).click()
  await expect(dialog.getByText('此事件覆盖已在其他页面变化，当前填写仍保留。'))
    .toBeVisible()
  await expect(dialog.getByLabel('事件本地名称')).toHaveValue('我的课程名称')
  await dialog.getByLabel('事件本地名称').fill('我的课程名称-冲突后')
  await dialog.getByRole('button', { name: '基于最新版重试', exact: true }).click()
  await expect(dialog.getByText('事件本地字段已保存。')).toBeVisible()

  await dialog.getByRole('button', { name: '立即刷新', exact: true }).click()
  await expect(dialog.getByText('来源在本地覆盖后发生了变化')).toBeVisible({
    timeout: 15_000,
  })
  await expect(dialog.getByText(/当前来源：来源课程更新/)).toBeVisible()
  await expect(dialog.getByText(/本地值：我的课程名称-冲突后/)).toBeVisible()
  await screenshot(page, info, 'source-difference')

  await dialog.getByRole('button', { name: '重置为来源', exact: true }).first().click()
  await expect(dialog.getByLabel('事件本地名称')).toHaveValue('来源课程更新')

  await dialog.getByLabel('名称', { exact: true }).first().fill('失败保留来源')
  await dialog.getByLabel('ICS 订阅地址').fill(
    `${origin}/calendar/${randomUUID()}/private.ics?scenario=failure&token=hidden`,
  )
  await dialog.getByRole('button', { name: '创建订阅', exact: true }).click()
  await expect.poll(async () => {
    const response = await page.request.get('/api/v1/calendar/subscriptions')
    const body = await response.json()
    return body.items.find(
      (item: { name: string }) => item.name === '失败保留来源',
    )?.refresh_status
  }, { timeout: 15_000 }).toBe('idle')
  await expect(
    dialog.getByRole('region', { name: '失败保留来源' })
      .getByText('已就绪', { exact: true }),
  ).toBeVisible()
  await dialog.getByRole('button', { name: '立即刷新', exact: true }).click()
  await expect.poll(async () => {
    const response = await page.request.get('/api/v1/calendar/subscriptions')
    const body = await response.json()
    return body.items.find(
      (item: { name: string }) => item.name === '失败保留来源',
    )?.refresh_status
  }, { timeout: 15_000 }).toBe('error')
  await expect(dialog.getByText('最近刷新失败', { exact: true })).toBeVisible({
    timeout: 15_000,
  })
  await expect(dialog.getByText(/上次成功刷新的 1 条事件仍保留/)).toBeVisible()
  expect((await new AxeBuilder({ page }).include('.subscription-manager').analyze()).violations)
    .toEqual([])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    .toBe(true)
  await screenshot(page, info, 'failure-retained')

  await dialog.getByRole('button', { name: '完成', exact: true }).click()
  await page.getByLabel('跳转日期').fill('2026-10-06')
  await expect(page.getByText('正在读取日历…')).toHaveCount(0)
  await expect(page.getByRole('button', { name: /订阅课程/ }).first()).toBeVisible()
})
