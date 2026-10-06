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
  await expect(page).toHaveURL('/ai')
  await page.getByRole('link', { name: '日历', exact: true }).click()
  await expect(page).toHaveURL('/calendar')
}

async function csrf(page: Page) {
  const response = await page.request.get('/api/v1/auth/me')
  expect(response.ok()).toBeTruthy()
  return (await response.json()).csrf_token as string
}

async function setTimeZone(page: Page, value: string) {
  const sessionCsrf = await csrf(page)
  const response = await page.request.get('/api/v1/time-settings')
  expect(response.ok()).toBeTruthy()
  const settings = (await response.json()).settings
  if (settings.time_zone === value) return sessionCsrf
  const updated = await page.request.put('/api/v1/time-settings', {
    headers: { 'X-CSRF-Token': sessionCsrf },
    data: {
      operation_id: randomUUID(),
      expected_version: settings.version,
      time_zone: value,
      week_starts_on: 1,
      activity_day_start_minute: 240,
    },
  })
  expect(updated.ok()).toBeTruthy()
  await page.reload()
  return sessionCsrf
}

async function createConfirmedPlan(page: Page, sessionCsrf: string) {
  const taskResponse = await page.request.post('/api/v1/tasks', {
    headers: { 'X-CSRF-Token': sessionCsrf },
    data: {
      operation_id: randomUUID(),
      title: '来源冲突测试任务',
      description: '已确认计划不能被订阅刷新静默移动',
      project_id: null,
      estimated_minutes: 60,
      priority: 2,
      labels: [],
      links: [],
      steps: [{ title: '保持当前计划块' }],
    },
  })
  expect(taskResponse.ok()).toBeTruthy()
  const task = (await taskResponse.json()).task
  const planResponse = await page.request.post('/api/v1/plans/versions', {
    headers: { 'X-CSRF-Token': sessionCsrf },
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
        starts_at: '09:00:00',
        ends_at: '10:00:00',
        step_ids: [task.steps[0].id],
      }],
      unscheduled_items: [],
    },
  })
  expect(planResponse.ok()).toBeTruthy()
  return (await planResponse.json()).plan
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
  const eventName = scenario === 'plan-conflict' ? '原固定课程' : '订阅课程'
  await expect(dialog.getByText(eventName, { exact: true }).last()).toBeVisible()
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

test('订阅单次例外可取消、重置、移动并跨来源刷新保留', async ({
  page,
  request,
}, info) => {
  test.setTimeout(90_000)
  const { calendar_origin: origin } = await bootstrap(info)
  await account(page, request)
  await setTimeZone(page, 'Asia/Shanghai')
  let dialog = await openSubscriptions(page)
  await createSubscription(
    page,
    dialog,
    origin,
    'occurrence-exception',
    '单次例外来源',
  )
  await dialog.getByRole('button', { name: '完成', exact: true }).click()
  await page.getByLabel('跳转日期').fill('2026-10-06')
  await expect(page.getByText('正在读取日历…')).toHaveCount(0)

  let eventButton = page.getByRole('button', { name: /订阅课程/ }).first()
  await expect(eventButton).toBeVisible()
  await eventButton.click()
  const panel = page.locator('.occurrence-panel')
  await expect(panel.getByRole('button', { name: '取消本次' })).toBeVisible()
  await expect(panel.getByRole('button', { name: '调整本次时间' })).toBeVisible()
  await expect(panel.getByRole('button', { name: '覆盖本次内容' })).toBeVisible()
  await panel.getByRole('button', { name: '取消本次' }).click()
  await expect(panel.getByText('本次订阅安排已取消，其他实例和来源均未改变。'))
    .toBeVisible()
  await expect(panel.getByText('已取消', { exact: true }).first()).toBeVisible()
  await expect(panel.getByRole('button', { name: '重置为来源' })).toBeVisible()

  await panel.getByRole('button', { name: '重置为来源' }).click()
  await expect(panel.getByText('本次订阅安排已恢复为当前来源内容。'))
    .toBeVisible()
  await expect(panel.getByRole('button', { name: '取消本次' })).toBeVisible()

  await panel.getByRole('button', { name: '调整本次时间' }).click()
  await panel.getByLabel('开始日期').fill('2026-10-07')
  await panel.getByLabel('开始', { exact: true }).fill('14:00')
  await panel.getByLabel('结束', { exact: true }).fill('15:00')
  await panel.getByRole('button', { name: '保存本次' }).click()
  await expect(panel.getByText('本次安排已调课。')).toBeVisible()
  await expect(panel.getByText(/10月7日.*14:00.*15:00/)).toBeVisible()
  await expect(panel.getByText('已调课', { exact: true })).toBeVisible()

  await panel.getByRole('button', { name: '管理来源与本地覆盖' }).click()
  dialog = page.getByRole('dialog', { name: '导入日历' })
  await dialog.getByRole('button', { name: '立即刷新', exact: true }).click()
  await expect(dialog.getByText('来源课程更新', { exact: true }).last()).toBeVisible({
    timeout: 15_000,
  })
  await dialog.getByRole('button', { name: '完成', exact: true }).click()
  await expect(panel.getByText(/10月7日.*14:00.*15:00/)).toBeVisible()
  await expect(panel.getByRole('heading', { name: '来源课程更新' })).toBeVisible()

  await panel.getByRole('button', { name: '管理来源与本地覆盖' }).click()
  dialog = page.getByRole('dialog', { name: '导入日历' })
  await dialog.getByRole('button', { name: '立即刷新', exact: true }).click()
  await expect.poll(async () => {
    const response = await page.request.get('/api/v1/calendar/subscriptions')
    const body = await response.json()
    return body.items.find(
      (item: { name: string }) => item.name === '单次例外来源',
    )?.generation
  }, { timeout: 15_000 }).toBe(3)
  await dialog.getByRole('button', { name: '完成', exact: true }).click()
  await expect(panel.getByText('来源已取消，本地移动仍保留')).toBeVisible()
  await expect(panel.getByText(/10月7日.*14:00.*15:00/)).toBeVisible()

  expect((await new AxeBuilder({ page }).include('.occurrence-panel').analyze()).violations)
    .toEqual([])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    .toBe(true)
  await screenshot(page, info, 'occurrence-exception')

  if (info.project.name === 'desktop') {
    const allDayButton = page.getByRole('button', { name: /全天纪念日/ }).first()
    await expect(allDayButton).toBeVisible()
    await allDayButton.click()
    await panel.getByRole('button', { name: '调整本次时间' }).click()
    await expect(panel.getByLabel('开始', { exact: true })).toHaveCount(0)
    await panel.getByLabel('开始日期').fill('2026-10-09')
    await panel.getByLabel('结束日期（不含）').fill('2026-10-10')
    await panel.getByRole('button', { name: '保存本次' }).click()
    await expect(panel.getByText('2026-10-09 全天', { exact: true })).toBeVisible()

    await page.evaluate(() => localStorage.setItem(
      'itime-appearance',
      JSON.stringify({ theme: 'dark', fontSize: 'larger' }),
    ))
    await page.setViewportSize({ width: 320, height: 700 })
    await page.goto('/calendar?date=2026-10-07')
    eventButton = page.getByRole('button', { name: /来源课程更新/ }).first()
    await expect(eventButton).toBeVisible()
    await eventButton.click()
    await expect(page.getByText('来源已取消，本地移动仍保留')).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
      .toBe(true)
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
    await screenshot(page, info, 'occurrence-exception-320-dark-large')
  }
})

test('订阅来源更新后已确认计划可查看并明确保留', async ({
  page,
  request,
}, info) => {
  const { calendar_origin: origin } = await bootstrap(info)
  await account(page, request)
  const sessionCsrf = await setTimeZone(page, 'Asia/Shanghai')
  let dialog = await openSubscriptions(page)
  await createSubscription(
    page,
    dialog,
    origin,
    'plan-conflict',
    '计划冲突来源',
  )
  const originalPlan = await createConfirmedPlan(page, sessionCsrf)
  const sourceRegion = dialog.getByRole('region', { name: '计划冲突来源' })
  await sourceRegion.getByRole('button', { name: '立即刷新', exact: true }).click()
  await expect(
    dialog.getByRole('region', { name: '最近刷新' })
      .getByText('已更新', { exact: true }).first(),
  ).toBeVisible({ timeout: 15_000 })
  await dialog.getByRole('button', { name: '完成', exact: true }).click()

  await page.goto('/plans?scope=week&date=2026-10-06')
  await expect(page.getByRole('heading', {
    name: '订阅来源更新后，当前计划需复核',
  })).toBeVisible()
  await expect(page.getByText('第 1 版计划没有被自动改动。')).toBeVisible()
  const conflict = page.getByRole('region', { name: '计划冲突来源的计划变化' })
  await expect(conflict.getByText('1 处变化 · 与 1 个计划块重叠')).toBeVisible()
  await conflict.getByText('查看变化时段').click()
  await expect(conflict.getByText(/11:00.*09:30/)).toBeVisible()
  await expect(page.locator('.plan-block')).toHaveCount(1)
  expect((await new AxeBuilder({ page }).include('.plan-source-conflicts').analyze()).violations)
    .toEqual([])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    .toBe(true)
  await page.evaluate(() => scrollTo(0, 0))
  await screenshot(page, info, 'plan-source-conflict')

  await conflict.getByRole('link', { name: '在日历查看' }).click()
  await expect(page).toHaveURL(/\/calendar\?.*date=2026-10-06/)
  dialog = page.getByRole('dialog', { name: '导入日历' })
  await expect(dialog).toBeVisible()
  await expect(dialog.getByText('计划冲突来源', { exact: true }).first()).toBeVisible()
  await dialog.getByRole('button', { name: '完成', exact: true }).click()
  const planEvent = page.getByRole('button', {
    name: /09:00–10:00 来源冲突测试任务，存在时间冲突/,
  }).first()
  await expect(planEvent).toBeVisible()
  await planEvent.click()
  await expect(page.getByText('订阅来源变化后与此计划块重叠。')).toBeVisible()
  await page.getByRole('link', { name: '前往规划页' }).click()

  await expect(page.getByRole('heading', {
    name: '订阅来源更新后，当前计划需复核',
  })).toBeVisible()
  await page.getByRole('button', { name: '保留当前计划', exact: true }).click()
  await expect(page.getByText('已保留第 1 版计划，来源冲突已处理。')).toBeVisible()
  await expect(page.getByRole('heading', {
    name: '订阅来源更新后，当前计划需复核',
  })).toHaveCount(0)
  const currentPlan = await (
    await page.request.get(`/api/v1/plans/${originalPlan.id}`)
  ).json()
  expect(currentPlan.plan.version).toBe(1)
  expect(currentPlan.plan.current_version.blocks).toEqual(
    originalPlan.current_version.blocks,
  )

  await page.goto('/calendar?date=2026-10-06')
  dialog = await openSubscriptions(page)
  await dialog.getByRole('button', { name: /移动后的固定课程/ }).first().click()
  await dialog.getByRole('button', { name: '立即刷新', exact: true }).click()
  await expect(
    dialog.getByRole('region', { name: '最近刷新' })
      .getByText('已更新', { exact: true }).first(),
  ).toBeVisible({ timeout: 15_000 })
  await dialog.getByRole('button', { name: '完成', exact: true }).click()
  await page.goto('/plans?scope=week&date=2026-10-06')
  await expect(page.getByRole('heading', {
    name: '订阅来源更新后，当前计划需复核',
  })).toBeVisible()
  await expect(page.getByText('1 处变化 · 未与计划块重叠')).toBeVisible()

  if (info.project.name === 'desktop') {
    await page.evaluate(() => localStorage.setItem(
      'itime-appearance',
      JSON.stringify({ theme: 'dark', fontSize: 'larger' }),
    ))
    await page.setViewportSize({ width: 320, height: 700 })
    await page.reload()
    await expect(page.getByRole('heading', {
      name: '订阅来源更新后，当前计划需复核',
    })).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
      .toBe(true)
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
    await screenshot(page, info, 'plan-source-conflict-320-dark-large')
  }

  await page.getByRole('button', {
    name: '按最新来源重新生成',
    exact: true,
  }).click()
  await expect(page.getByText('这是预览，确认后才会写入计划')).toBeVisible({
    timeout: 15_000,
  })
  await page.getByRole('button', { name: '确认并写入计划', exact: true }).click()
  await expect(page.getByText('提案已写入计划，当前为第 2 版。')).toBeVisible()
  await page.getByRole('button', { name: '查看当前计划', exact: true }).click()
  await expect(page.getByRole('heading', {
    name: '订阅来源更新后，当前计划需复核',
  })).toHaveCount(0)
})
