import AxeBuilder from '@axe-core/playwright'
import { expect, test, type APIRequestContext, type Page, type TestInfo } from '@playwright/test'
import { randomUUID } from 'node:crypto'
import { mkdir } from 'node:fs/promises'

const password = 'Valid-ai-conversation-ui-password-123'
const evidence = '/tmp/itime-ui-evidence'
const runtimeErrors = new WeakMap<Page, string[]>()

async function screenshot(page: Page, info: TestInfo, name: string) {
  await mkdir(evidence, { recursive: true })
  await page.screenshot({
    path: `${evidence}/${info.project.name}-ai-conversation-${name}.png`,
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
  const address = `ai-chat-ui-${randomUUID()}@example.com`
  await page.goto('/register')
  await page.getByLabel('邮箱', { exact: true }).fill(address)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByLabel('确认密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '创建账号', exact: true }).click()
  await expect(page.getByRole('heading', { name: '去邮箱看看吧' })).toBeVisible()
  await page.goto(await mailLink(request, address))
  await expect(page).toHaveURL('/ai')
  await expect(page).toHaveTitle('AI 对话 · iTime')
}

async function planningSetup(page: Page) {
  const sessionResponse = await page.request.get('/api/v1/auth/me')
  expect(sessionResponse.ok()).toBeTruthy()
  const session = await sessionResponse.json()
  const settingsResponse = await page.request.get('/api/v1/time-settings')
  const settings = (await settingsResponse.json()).settings
  const saved = await page.request.put('/api/v1/time-settings', {
    headers: { 'X-CSRF-Token': session.csrf_token },
    data: {
      operation_id: randomUUID(),
      expected_version: settings.version,
      time_zone: 'Asia/Shanghai',
      week_starts_on: 1,
      activity_day_start_minute: 240,
    },
  })
  expect(saved.ok()).toBeTruthy()
  const task = await page.request.post('/api/v1/tasks', {
    headers: { 'X-CSRF-Token': session.csrf_token },
    data: {
      operation_id: randomUUID(),
      title: '完成对话规划任务',
      description: '从 AI 对话生成结构化规划提案。',
      project_id: null,
      estimated_minutes: 60,
      priority: 2,
      labels: ['规划'],
      links: [],
      steps: [{ title: '整理范围' }, { title: '确认输出' }],
    },
  })
  expect(task.ok()).toBeTruthy()
}

function futureDate(days: number) {
  const date = new Date()
  date.setUTCDate(date.getUTCDate() + days)
  return date.toISOString().slice(0, 10)
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

async function newConversation(page: Page) {
  await page.getByRole('button', { name: '新建会话', exact: true }).click()
  await expect(page.getByRole('heading', { name: '新对话', exact: true })).toBeVisible()
}

function conversationButton(page: Page, title: string) {
  return page.locator('.conversation-select').filter({ hasText: title })
}

async function returnToConversationList(page: Page) {
  const back = page.getByRole('button', { name: '返回会话列表', exact: true })
  if (await back.isVisible()) await back.click()
}

async function send(page: Page, content: string) {
  await page.getByLabel('消息', { exact: true }).fill(content)
  await page.getByRole('button', { name: '发送消息', exact: true }).click()
  await expect(
    page.getByRole('list', { name: '会话消息' }).getByText(content, { exact: true }),
  ).toBeVisible()
}

test.beforeEach(async ({ page }) => watchRuntime(page))
test.afterEach(async ({ page }) => {
  expect(runtimeErrors.get(page)).toEqual([])
})

test('多会话生成、改名、归档和恢复保持持久状态', async ({
  page,
  request,
}, info) => {
  await account(page, request)
  if ((page.viewportSize()?.width ?? 0) <= 700) {
    await expect(page.getByRole('button', { name: '新建会话', exact: true })).toBeVisible()
  } else {
    await expect(page.getByRole('heading', { name: '今天想先理清哪件事？' })).toBeVisible()
  }
  await newConversation(page)
  const firstPrompt = '帮我整理今天的学习安排'
  await page.getByLabel('消息', { exact: true }).fill(firstPrompt)
  await page.getByLabel('消息', { exact: true }).press('Control+Enter')
  const messageList = page.getByRole('list', { name: '会话消息' })
  await expect(messageList.getByText(firstPrompt, { exact: true })).toBeVisible()
  await expect(messageList.getByText('先把今天最重要的事情列出来。')).toBeVisible()
  await expect(messageList.getByText('然后为第一项留出一段完整时间。')).toBeVisible()
  await expect(page.getByText('正在生成')).toHaveCount(0)

  await returnToConversationList(page)
  await page.getByRole('button', { name: '新建会话', exact: true }).click()
  await expect(page.getByRole('heading', { name: '新对话', exact: true })).toBeVisible()
  await returnToConversationList(page)
  await page.getByRole('button', { name: '重命名 新对话' }).click()
  const rename = page.getByRole('dialog', { name: '重命名会话' })
  await rename.getByLabel('会话名称', { exact: true }).fill('论文讨论')
  await rename.getByRole('button', { name: '保存名称', exact: true }).click()
  await conversationButton(page, '论文讨论').click()
  await expect(page.getByRole('heading', { name: '论文讨论', exact: true })).toBeVisible()

  await returnToConversationList(page)
  page.once('dialog', dialog => dialog.accept())
  await page.getByRole('button', { name: '归档 论文讨论' }).click()
  if ((page.viewportSize()?.width ?? 0) <= 700) {
    await expect(page.getByRole('button', { name: '已归档', exact: true })).toBeVisible()
  } else {
    await expect(page.getByRole('heading', { name: '今天想先理清哪件事？' })).toBeVisible()
  }
  await page.getByRole('button', { name: '已归档', exact: true }).click()
  await expect(conversationButton(page, '论文讨论')).toBeVisible()
  page.once('dialog', dialog => dialog.accept())
  await page.getByRole('button', { name: '重新打开 论文讨论' }).click()
  const visibleStatus = (page.viewportSize()?.width ?? 0) <= 700
    ? page.locator('.conversation-navigation-status')
    : page.locator('.ai-workspace-status')
  await expect(visibleStatus.getByText('会话已重新打开。')).toBeVisible()
  await page.getByRole('button', { name: '进行中', exact: true }).click()
  await conversationButton(page, '帮我整理今天的学习安排').click()
  await expect(messageList.getByText(firstPrompt, { exact: true })).toBeVisible()
  await expect(messageList.getByText('先把今天最重要的事情列出来。')).toBeVisible()

  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    .toBe(true)
  await screenshot(page, info, 'multi-session')

  if (info.project.name === 'desktop') {
    await page.evaluate(() => localStorage.setItem(
      'itime-appearance',
      JSON.stringify({ theme: 'dark', fontSize: 'larger' }),
    ))
    await page.setViewportSize({ width: 320, height: 700 })
    await page.goto('/ai')
    await conversationButton(page, '帮我整理今天的学习安排').click()
    await expect(
      page.getByRole('list', { name: '会话消息' }).getByText(firstPrompt, { exact: true }),
    ).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
      .toBe(true)
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
    await screenshot(page, info, '320-large-dark')
  }
})

test('对话显式生成结构化计划并通过提案卡进入确认页', async ({
  page,
  request,
}, info) => {
  await account(page, request)
  await planningSetup(page)
  await newConversation(page)

  await page.locator('.composer-mode label').filter({ hasText: '生成计划' }).click()
  await page.getByLabel('计划日期', { exact: true }).fill(futureDate(14))
  await page.getByLabel('消息', { exact: true }).fill('请安排这项任务，并保留清晰的检查步骤。')
  await page.getByRole('button', { name: '生成计划', exact: true }).click()

  const messageList = page.getByRole('list', { name: '会话消息' })
  await expect(messageList.getByText('计划草案已生成。', { exact: true })).toBeVisible({
    timeout: 15_000,
  })
  const proposal = messageList.getByRole('region', { name: '规划提案' })
  await expect(proposal.getByText('尚未写入计划', { exact: true })).toBeVisible()
  await expect(proposal.getByText('完成对话规划任务', { exact: false })).toBeVisible()
  await expect(messageList).not.toContainText('submit_itime_plan')
  await screenshot(page, info, 'planning-card')

  const destination = await proposal.getByRole('link', { name: '查看并确认' }).getAttribute('href')
  expect(destination).toContain('/plans')
  expect(destination).toContain('proposal=')
  await proposal.getByRole('link', { name: '查看并确认' }).click()
  await expect(page).toHaveURL(/\/plans\?.*proposal=/)
  await expect(page.getByText('这是预览，确认后才会写入计划')).toBeVisible()

  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    .toBe(true)
  await screenshot(page, info, 'planning-proposal')
})

test('离开页面后作业继续并从服务端恢复完整回复', async ({
  page,
  request,
}) => {
  await account(page, request)
  await newConversation(page)
  const prompt = '离页恢复：请帮我安排今天'
  await send(page, prompt)
  await expect(
    page.getByRole('list', { name: '会话消息' })
      .getByText('先把今天最重要的事情列出来。'),
  ).toBeVisible()
  const conversationId = new URL(page.url()).searchParams.get('conversation')
  expect(conversationId).toBeTruthy()

  await returnToConversationList(page)
  await page.getByRole('link', { name: '任务', exact: true }).click()
  await expect(page).toHaveURL('/tasks')
  await page.getByRole('link', { name: 'AI', exact: true }).click()
  await conversationButton(page, prompt).click()
  const restoredMessages = page.getByRole('list', { name: '会话消息' })
  await expect(
    restoredMessages.getByText('然后为第一项留出一段完整时间。'),
  ).toBeVisible()
  await expect(restoredMessages.getByText(prompt, { exact: true })).toHaveCount(1)

  const messages = await (
    await page.request.get(
      `/api/v1/ai/conversations/${conversationId}/messages?limit=50`,
    )
  ).json()
  expect(messages.items).toHaveLength(2)
  expect(messages.items[1].job.attempt_count).toBe(1)
})

test('取消保留已生成片段，失败状态可重新发送', async ({
  page,
  request,
}, info) => {
  await account(page, request)
  await newConversation(page)
  await send(page, '取消测试')
  await expect(
    page.getByRole('list', { name: '会话消息' })
      .getByText('已收到这条消息。', { exact: true }),
  ).toBeVisible()
  await page.getByRole('button', { name: '取消生成', exact: true }).click()
  await expect(page.getByText('已取消，以上内容可能不完整')).toBeVisible()
  await page.waitForTimeout(1800)
  await expect(page.getByText('取消后不应出现。')).toHaveCount(0)

  await returnToConversationList(page)
  await page.getByRole('button', { name: '新建会话', exact: true }).click()
  await send(page, '触发失败')
  await expect(page.getByText('AI 服务暂时不可用，系统会自动重试。'))
    .toBeVisible({ timeout: 15_000 })
  await page.getByRole('button', { name: '重新发送', exact: true }).click()
  await expect(page.getByLabel('消息', { exact: true })).toHaveValue('触发失败')
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    .toBe(true)
  await screenshot(page, info, 'cancel-and-failure')
})

test('困难链接只填普通对话草稿，用户发送后才创建作业', async ({
  page,
  request,
}, info) => {
  await account(page, request)
  const session = await (await page.request.get('/api/v1/auth/me')).json()
  const currentSettings = (await (
    await page.request.get('/api/v1/time-settings')
  ).json()).settings
  const settings = await page.request.put('/api/v1/time-settings', {
    headers: { 'X-CSRF-Token': session.csrf_token },
    data: {
      operation_id: randomUUID(),
      expected_version: currentSettings.version,
      time_zone: 'Asia/Shanghai',
      week_starts_on: 1,
      activity_day_start_minute: 240,
    },
  })
  expect(settings.ok()).toBeTruthy()
  const created = await page.request.post('/api/v1/difficulties', {
    headers: { 'X-CSRF-Token': session.csrf_token },
    data: {
      operation_id: randomUUID(),
      task_id: null,
      focus_session_id: null,
      occurrence_key: null,
      content: '公式推导到第三步时无法理解变量替换。',
      category: '理解',
      emotion: '困惑',
      occurred_at: null,
    },
  })
  expect(created.ok()).toBeTruthy()
  const difficulty = (await created.json()).difficulty
  let messageRequests = 0
  page.on('request', current => {
    if (
      current.method() === 'POST'
      && /\/api\/v1\/ai\/conversations\/[^/]+\/messages$/.test(current.url())
    ) messageRequests += 1
  })

  await page.goto(`/ai?difficulty=${difficulty.id}`)
  await expect(page).toHaveURL(/\/ai\?conversation=/)
  await expect(page.getByLabel('消息', { exact: true })).toHaveValue(
    '我遇到了这个困难：\n公式推导到第三步时无法理解变量替换。\n分类：理解\n感受：困惑',
  )
  await expect(
    page.locator('.ai-main').getByText('困难已放入草稿，发送前可以继续修改。'),
  ).toBeVisible()
  expect(messageRequests).toBe(0)
  const before = await page.request.get('/api/v1/ai/jobs?capability=conversation&limit=20')
  expect((await before.json()).items).toHaveLength(0)

  await page.getByRole('button', { name: '发送消息', exact: true }).click()
  await expect.poll(() => messageRequests).toBe(1)
  await expect.poll(async () => {
    const after = await page.request.get(
      '/api/v1/ai/jobs?capability=conversation&limit=20',
    )
    return (await after.json()).items.length
  }).toBe(1)
  await expect(
    page.getByRole('list', { name: '会话消息' })
      .getByText(/公式推导到第三步时无法理解变量替换。/),
  ).toBeVisible()
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  await screenshot(page, info, 'difficulty-draft-sent')
})
