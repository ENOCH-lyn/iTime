import { navigateToProduct } from './helpers/product-navigation'
import AxeBuilder from '@axe-core/playwright'
import { expect, test, type APIRequestContext, type Page, type TestInfo } from '@playwright/test'
import { randomUUID } from 'node:crypto'
import { mkdir } from 'node:fs/promises'

const password = 'Valid-planning-ui-password-123'
const evidence = '/tmp/itime-ui-evidence'
const runtimeErrors = new WeakMap<Page, string[]>()

function isoDate(value: Date) {
  return value.toISOString().slice(0, 10)
}

function futureDate(days: number) {
  const value = new Date()
  value.setUTCDate(value.getUTCDate() + days)
  return isoDate(value)
}

function addDays(value: string, days: number) {
  const result = new Date(`${value}T12:00:00Z`)
  result.setUTCDate(result.getUTCDate() + days)
  return isoDate(result)
}

async function screenshot(page: Page, info: TestInfo, name: string) {
  await mkdir(evidence, { recursive: true })
  await page.screenshot({
    path: `${evidence}/${info.project.name}-planning-${name}.png`,
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
  const address = `planning-ui-${randomUUID()}@example.com`
  await page.goto('/register')
  await page.getByLabel('邮箱', { exact: true }).fill(address)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByLabel('确认密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '创建账号', exact: true }).click()
  await expect(page.getByRole('heading', { name: '去邮箱看看吧' })).toBeVisible()
  await page.goto(await mailLink(request, address))
  await expect(page).toHaveURL('/ai')
  return initializeTimeSettings(page)
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

async function createPlanningMemory(page: Page, csrf: string) {
  const response = await page.request.post('/api/v1/memories', {
    headers: { 'X-CSRF-Token': csrf },
    data: {
      operation_id: randomUUID(),
      kind: 'preference',
      title: '规划时段',
      content: '计划时优先安排上午。',
    },
  })
  expect(response.ok()).toBeTruthy()
}

async function createTask(
  page: Page,
  csrf: string,
  title: string,
  estimatedMinutes: number,
) {
  const response = await page.request.post('/api/v1/tasks', {
    headers: { 'X-CSRF-Token': csrf },
    data: {
      operation_id: randomUUID(),
      title,
      description: `${title}的规划测试正文`,
      project_id: null,
      estimated_minutes: estimatedMinutes,
      priority: 2,
      labels: ['规划'],
      links: [],
      steps: [
        { title: `${title}第一步` },
        { title: `${title}第二步` },
      ],
    },
  })
  expect(response.ok()).toBeTruthy()
  return (await response.json()).task
}

async function createFixedOccurrence(
  page: Page,
  csrf: string,
  targetDate: string,
) {
  const response = await page.request.post('/api/v1/calendar/series', {
    headers: { 'X-CSRF-Token': csrf },
    data: {
      operation_id: randomUUID(),
      kind: 'fixed',
      term_id: null,
      name: '固定课程',
      description: '规划页固定占用测试',
      location: '教学楼',
      busy: true,
      time_zone: null,
      start_date: targetDate,
      starts_at: '11:00:00',
      ends_at: '12:00:00',
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
}

async function createCompetingPlanVersion(
  page: Page,
  csrf: string,
  targetDate: string,
) {
  const params = new URLSearchParams({
    scope: 'week',
    date: targetDate,
    density: 'balanced',
    buffer: '15',
    availability_start: '08:00',
    availability_end: '22:00',
  })
  const contextResponse = await page.request.get(
    `/api/v1/planning/context?${params}`,
  )
  expect(contextResponse.ok()).toBeTruthy()
  const current = (await contextResponse.json()).context.current_plan
  expect(current).toBeTruthy()
  const response = await page.request.post('/api/v1/plans/versions', {
    headers: { 'X-CSRF-Token': csrf },
    data: {
      operation_id: randomUUID(),
      week_start_date: current.week_start_date,
      expected_plan_version: current.version,
      density: current.density,
      buffer_minutes: current.buffer_minutes,
      availability_start: current.availability_start,
      availability_end: current.availability_end,
      blocks: current.blocks.map((block: {
        task_id: string
        occurrence_key: string | null
        local_date: string
        local_starts_at: string
        local_ends_at: string
        step_ids: string[]
      }) => ({
        task_id: block.task_id,
        occurrence_key: block.occurrence_key,
        local_date: block.local_date,
        starts_at: block.local_starts_at,
        ends_at: block.local_ends_at,
        step_ids: block.step_ids,
      })),
      unscheduled_items: current.unscheduled_items.map((item: {
        task_id: string
        occurrence_key: string | null
        remaining_minutes: number
        reason: string
        explanation: string
      }) => ({
        task_id: item.task_id,
        occurrence_key: item.occurrence_key,
        remaining_minutes: item.remaining_minutes,
        reason: item.reason,
        explanation: item.explanation,
      })),
    },
  })
  expect(response.ok()).toBeTruthy()
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

async function viewportOverflow(page: Page) {
  return page.evaluate(() => {
    if (document.documentElement.scrollWidth <= innerWidth) return []
    return [
      {
        element: 'document',
        left: 0,
        right: document.documentElement.scrollWidth,
        width: document.documentElement.scrollWidth,
      },
      ...[...document.querySelectorAll<HTMLElement>('body *')]
      .filter(element => {
        const style = getComputedStyle(element)
        return style.display !== 'none'
          && style.visibility !== 'hidden'
          && style.opacity !== '0'
      })
      .map(element => {
      const rect = element.getBoundingClientRect()
      return {
        element: `${element.tagName.toLowerCase()}${element.id ? `#${element.id}` : ''}${element.className ? `.${String(element.className).trim().replace(/\s+/g, '.')}` : ''}`,
        left: Math.round(rect.left),
        right: Math.round(rect.right),
        width: Math.round(rect.width),
      }
    })
    .filter(item => item.width > 0 && (item.left < -1 || item.right > innerWidth + 1))
      .slice(0, 19),
    ]
  })
}

async function goToPlan(page: Page, targetDate: string) {
  await page.goto(`/plans?scope=day&date=${targetDate}`)
  await expect(page).toHaveTitle('规划 · iTime')
  await expect(page.getByRole('heading', { name: '待规划任务', exact: true })).toBeVisible()
  await expect(page.getByText('正在读取任务与固定安排…')).toHaveCount(0)
}

test.beforeEach(async ({ page }) => watchRuntime(page))
test.afterEach(async ({ page }) => {
  expect(runtimeErrors.get(page)).toEqual([])
})

test('生成、离页恢复、确认和手工版本形成完整规划流程', async ({
  page,
  request,
}, info) => {
  const targetDate = futureDate(14)
  const csrf = await account(page, request)
  await createPlanningMemory(page, csrf)
  const tasks = await Promise.all([
    createTask(page, csrf, '完成项目提纲', 60),
    createTask(page, csrf, '整理实验数据', 45),
    createTask(page, csrf, '阅读补充资料', 30),
  ])
  await createFixedOccurrence(page, csrf, targetDate)
  await goToPlan(page, targetDate)

  for (const task of tasks) {
    await expect(page.getByText(task.title, { exact: true }).first()).toBeVisible()
  }
  await expect(page.getByText('固定课程', { exact: true })).toBeVisible()
  await expect(page.locator('.plan-task-list input:checked')).toHaveCount(3)

  await page.getByLabel('补充要求').fill('离页恢复测试')
  await page.getByRole('button', { name: '生成计划提案', exact: true }).click()
  await navigateToProduct(page, '任务')
  await expect(page).toHaveURL('/tasks')
  await navigateToProduct(page, '规划')
  await expect(page.getByText('这是预览，确认后才会写入计划')).toBeVisible({
    timeout: 15_000,
  })
  await expect(page.getByText('已按长期记忆调整计划。', { exact: true }))
    .toBeVisible()
  await expect(page.locator('.plan-block')).toHaveCount(2)
  await expect(page.getByRole('heading', { name: '暂未排入', exact: true })).toBeVisible()
  for (const task of tasks) {
    await expect(page.getByText(task.title, { exact: true }).last()).toBeVisible()
  }

  await page.getByRole('button', { name: '确认并写入计划', exact: true }).click()
  await expect(page.getByText('提案已写入计划，当前为第 1 版。')).toBeVisible()
  await page.getByRole('button', { name: '查看当前计划', exact: true }).click()
  await expect(page.getByText('当前计划', { exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: '第 1 版', exact: true })).toBeVisible()

  const blocksResponse = await page.request.get(
    `/api/v1/planning/blocks?start=${targetDate}&end=${addDays(targetDate, 1)}`,
  )
  expect(blocksResponse.ok()).toBeTruthy()
  expect((await blocksResponse.json()).items).toHaveLength(2)

  await page.getByRole('button', { name: '手工调整', exact: true }).click()
  const firstBlock = page.locator('.plan-block').first()
  const originalFirstTitle = await firstBlock.locator('.plan-block-copy > strong').innerText()
  await firstBlock.getByRole('button', { name: /^下移 / }).click()
  await expect(page.locator('.plan-block').first().locator('.plan-block-copy > strong'))
    .not.toHaveText(originalFirstTitle)

  const editableBlock = page.locator('.plan-block').first()
  await editableBlock.locator('summary').click()
  await editableBlock.getByRole('checkbox').nth(1).check()
  await createCompetingPlanVersion(page, csrf, targetDate)
  await page.getByRole('button', { name: '保存新版本', exact: true }).click()
  await expect(page.getByText('此周计划已在其他页面或设备更新，请刷新后比较再保存。')).toBeVisible()
  await expect(page.getByText(/^请求编号：/)).toBeVisible()
  await page.getByRole('button', { name: '重新读取', exact: true }).click()
  await expect(page.getByRole('alert')).toHaveCount(0)
  await expect(page.getByRole('heading', { name: '第 2 版', exact: true }))
    .toBeVisible()
  await expect(page.getByText('有未保存调整', { exact: true })).toBeVisible()
  const saveVersion = page.getByRole('button', { name: '保存新版本', exact: true })
  await expect(saveVersion).toBeEnabled()
  await saveVersion.click()
  await expect(page.getByText('手工调整已保存为第 3 版。')).toBeVisible()
  await expect(page.getByRole('heading', { name: '第 3 版', exact: true })).toBeVisible()

  await page.getByLabel('查看计划版本').selectOption('1')
  await expect(page.getByText('历史版本 · 只读', { exact: true })).toBeVisible()
  await page.getByLabel('查看计划版本').selectOption('current')
  await expect(page.getByText('当前计划', { exact: true })).toBeVisible()

  expect(await page.locator('vite-error-overlay').count()).toBe(0)
  expect(await viewportOverflow(page)).toEqual([])
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  await screenshot(page, info, 'confirmed-manual-version')

  if (info.project.name === 'desktop') {
    await page.evaluate(() => localStorage.setItem(
      'itime-appearance',
      JSON.stringify({ theme: 'dark', fontSize: 'larger' }),
    ))
    await page.setViewportSize({ width: 320, height: 700 })
    await page.reload()
    await expect(page.getByText('当前计划', { exact: true })).toBeVisible()
    expect(await viewportOverflow(page)).toEqual([])
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
    await screenshot(page, info, '320-large-dark')
  }

  await navigateToProduct(page, '日历')
  await expect(page).toHaveURL('/calendar')
  await page.getByLabel('跳转日期').fill(targetDate)
  await expect(page.getByText('正在读取日历…')).toHaveCount(0)
  const planEvent = page.locator('.event-plan:visible').first()
  await expect(planEvent).toBeVisible()
  const projectedTitle = await planEvent.locator('strong').innerText()
  await planEvent.click()
  const detail = page.locator('.occurrence-panel')
  await expect(detail.getByText('计划块', { exact: true })).toBeVisible()
  await expect(detail.getByRole('heading', { name: projectedTitle, exact: true })).toBeVisible()
  await expect(detail.getByText('手工调整', { exact: true })).toBeVisible()
  await expect(detail.getByText('第 3 版', { exact: true })).toBeVisible()
  await expect(detail.getByRole('button', { name: '标为完成', exact: true })).toHaveCount(0)
  await expect(detail.getByRole('link', { name: '前往规划页', exact: true })).toBeVisible()
  expect(await viewportOverflow(page)).toEqual([])
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  await screenshot(page, info, 'calendar-projection')
})

test('生成可以取消且提案可以明确拒绝', async ({ page, request }, info) => {
  test.skip(true, 'S2 AI 工具链接管规划生成后恢复')
  void page; void request; void info
})
