import AxeBuilder from '@axe-core/playwright'
import {
  expect,
  test,
  type APIRequestContext,
  type Page,
  type TestInfo,
} from '@playwright/test'
import { randomUUID } from 'node:crypto'
import { mkdir } from 'node:fs/promises'

const password = 'Valid-chat-first-visual-password-123'
const evidence = '/tmp/itime-ui-evidence'
const runtimeErrors = new WeakMap<Page, string[]>()

async function screenshot(page: Page, info: TestInfo, name: string) {
  await mkdir(evidence, { recursive: true })
  await page.screenshot({
    path: `${evidence}/${info.project.name}-chat-first-${name}.png`,
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
  const address = `chat-first-${randomUUID()}@example.com`
  await page.goto('/register')
  await page.getByLabel('邮箱', { exact: true }).fill(address)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByLabel('确认密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '创建账号', exact: true }).click()
  await expect(page.getByRole('heading', { name: '去邮箱看看吧' })).toBeVisible()
  await page.goto(await mailLink(request, address))
  await expect(page).toHaveURL('/ai')
}

async function session(page: Page) {
  const response = await page.request.get('/api/v1/auth/me')
  expect(response.ok()).toBeTruthy()
  return response.json() as Promise<{ csrf_token: string }>
}

async function seedConversations(page: Page) {
  const current = await session(page)
  const titles = Array.from({ length: 20 }, (_, index) => (
    index === 0
      ? '这是一个需要保留省略号但仍能通过 Tooltip 查看完整内容的超长会话标题'
      : index === 1
        ? 'UnbrokenTitle_ABCDEFGHIJKLMNOPQRSTUVWXYZ_0123456789'
        : index === 2
          ? 'English research notes and weekly planning'
          : index === 19
            ? '视觉验收长历史'
            : `会话 ${index + 1}`
  ))
  let selectedId = ''
  for (const [index, title] of titles.entries()) {
    const response = await page.request.post('/api/v1/ai/conversations', {
      headers: { 'X-CSRF-Token': current.csrf_token },
      data: { operation_id: randomUUID(), title },
    })
    expect(response.ok(), `create conversation ${index}`).toBeTruthy()
    if (index === titles.length - 1) {
      selectedId = (await response.json()).conversation.id
    }
  }
  return { selectedId, titles }
}

async function initializeTimeSettings(page: Page) {
  const current = await session(page)
  const response = await page.request.get('/api/v1/time-settings')
  const settings = (await response.json()).settings
  if (settings.initialized) return
  const saved = await page.request.put('/api/v1/time-settings', {
    headers: { 'X-CSRF-Token': current.csrf_token },
    data: {
      operation_id: randomUUID(),
      expected_version: settings.version,
      time_zone: 'Asia/Shanghai',
      week_starts_on: 1,
      activity_day_start_minute: 240,
    },
  })
  expect(saved.ok()).toBeTruthy()
}

async function sendAndFinish(page: Page, content: string) {
  const messages = page.locator('.conversation-messages > li')
  const before = await messages.count()
  await page.getByLabel('消息', { exact: true }).fill(content)
  await page.getByRole('button', { name: '发送消息', exact: true }).click()
  await expect(messages).toHaveCount(before + 2, { timeout: 15_000 })
  await expect(page.locator('.message-queued, .message-running')).toHaveCount(
    0,
    { timeout: 15_000 },
  )
}

async function newConversation(page: Page) {
  const heading = page.getByRole('heading', { name: '新对话', exact: true })
  if (await heading.isVisible()) return
  const create = page.getByRole('button', { name: '新建会话', exact: true })
  if (!(await create.isVisible())) {
    await page.getByRole('button', { name: '打开导航', exact: true }).click()
  }
  await create.click()
  await expect(heading).toBeVisible()
}

async function expectConversationTitles(page: Page, count: number) {
  const titles = page.locator('.conversation-title-text:visible')
  await expect(titles).toHaveCount(count)
  const geometry = await titles.evaluateAll(nodes => nodes.map(node => {
    const box = node.getBoundingClientRect()
    const row = node.closest('li')
    const menu = row?.querySelector<HTMLElement>('.conversation-row-menu')
    const menuBox = menu?.getBoundingClientRect()
    return {
      text: node.textContent?.trim() ?? '',
      width: box.width,
      height: box.height,
      menuLeft: menuBox?.left ?? Number.POSITIVE_INFINITY,
      titleRight: box.right,
    }
  }))
  expect(geometry.every(item => (
    item.text.length > 0
    && item.width > 48
    && item.height > 0
    && item.titleRight <= item.menuLeft
  ))).toBe(true)
}

async function expectNoAccidentalClipping(page: Page) {
  const accidentalClipping = await page.evaluate(() => (
    [...document.querySelectorAll<HTMLElement>('main *')]
      .filter(element => {
        const style = getComputedStyle(element)
        if (element.matches(
          [
            '.conversation-title-text',
            '.composer-attachment-name',
            '.conversation-viewport-shell',
            '.sr-only',
          ].join(', '),
        )) return false
        return (
          style.overflow === 'hidden'
          && (
            element.scrollWidth > element.clientWidth + 1
            || element.scrollHeight > element.clientHeight + 1
          )
        )
      })
      .map(element => ({
        tag: element.tagName,
        className: element.className,
        text: element.textContent?.trim().slice(0, 80),
      }))
  ))
  expect(accidentalClipping).toEqual([])
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

async function expectConversationGeometry(page: Page) {
  await page.locator('.conversation-message-viewport').evaluate(element => {
    element.scrollTop = element.scrollHeight
  })
  const geometry = await page.evaluate(() => {
    const viewport = document.querySelector<HTMLElement>(
      '.conversation-message-viewport',
    )!
    const composer = document.querySelector<HTMLElement>(
      '.conversation-composer',
    )!
    const last = document.querySelector<HTMLElement>(
      '.conversation-messages > li:last-child',
    )!
    const viewportBox = viewport.getBoundingClientRect()
    const composerBox = composer.getBoundingClientRect()
    const lastBox = last.getBoundingClientRect()
    return {
      viewportBottom: viewportBox.bottom,
      composerTop: composerBox.top,
      lastBottom: lastBox.bottom,
      rootFits: document.documentElement.scrollWidth <= innerWidth,
    }
  })
  expect(geometry.viewportBottom).toBeLessThanOrEqual(geometry.composerTop)
  expect(geometry.lastBottom).toBeLessThanOrEqual(geometry.composerTop - 16)
  expect(geometry.rootFits).toBe(true)
}

test.beforeEach(async ({ page }) => watchRuntime(page))
test.afterEach(async ({ page }) => {
  expect(runtimeErrors.get(page)).toEqual([])
})

test('二十个会话和三十条消息保持标题、滚动与排版完整', async ({
  page,
  request,
}, info) => {
  test.setTimeout(120_000)
  await account(page, request)
  const { selectedId } = await seedConversations(page)
  await page.reload()

  if ((page.viewportSize()?.width ?? 0) < 1024) {
    await page.getByRole('button', { name: '打开导航', exact: true }).click()
    await expect(page.getByRole('dialog', { name: '导航' })).toBeVisible()
    await expectConversationTitles(page, 20)
    if (info.project.name === 'tablet') {
      await screenshot(page, info, 'drawer')
    }
    await page.keyboard.press('Escape')
    await expect(page.getByRole('dialog', { name: '导航' })).toBeHidden()
  } else {
    await expectConversationTitles(page, 20)
  }

  await page.goto(`/ai?conversation=${selectedId}`)
  for (let index = 1; index <= 15; index += 1) {
    await sendAndFinish(page, `视觉历史消息 ${index}`)
  }
  await expect(page.locator('.conversation-messages > li')).toHaveCount(30)
  await expectConversationGeometry(page)
  await expectNoAccidentalClipping(page)
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  await screenshot(page, info, 'normal')
})

test('消息与输入区在长内容、八行、附件和窄高度下互不遮挡', async ({
  page,
  request,
}, info) => {
  await account(page, request)
  const input = page.getByLabel('消息', { exact: true })
  await input.fill('Markdown 视觉验收 **用户纯文本**')
  await input.press('Enter')
  await expect(page).toHaveURL(/\/ai\?conversation=/)
  await expect(page.getByRole('heading', { name: '学习安排' })).toBeVisible()
  await expectConversationGeometry(page)
  await expectNoAccidentalClipping(page)
  await screenshot(page, info, 'markdown')

  const messageCount = await page.locator('.conversation-messages > li').count()
  await input.fill('第一行')
  await input.press('Shift+Enter')
  await input.type('第二行')
  await expect(input).toHaveValue('第一行\n第二行')
  await expect(page.locator('.conversation-messages > li')).toHaveCount(messageCount)
  await expect(page.getByText(/\/12000$/)).toHaveCount(0)

  if (info.project.name === 'desktop') {
    await page.setViewportSize({ width: 320, height: 700 })
    await page.evaluate(() => localStorage.setItem(
      'itime-appearance',
      JSON.stringify({ theme: 'dark', fontSize: 'larger' }),
    ))
    await page.reload()
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
    await expect(page.locator('html')).toHaveAttribute('data-font-size', 'larger')
    await expect(page.getByRole('heading', { name: '学习安排' })).toBeVisible()
  }

  await input.fill(Array.from({ length: 8 }, (_, index) => (
    `第 ${index + 1} 行用于验证输入框高度变化`
  )).join('\n'))
  await expectConversationGeometry(page)

  await page.locator('.composer-secondary-actions input[type=file]').setInputFiles({
    name: 'schedule.png',
    mimeType: 'image/png',
    buffer: Buffer.from('not-uploaded-in-this-test'),
  })
  await expect(page.getByLabel('待发送附件')).toContainText('schedule.png')
  await expectConversationGeometry(page)
  await expectNoAccidentalClipping(page)

  if (info.project.name === 'desktop') {
    const textareaGeometry = await input.evaluate(element => ({
      clientHeight: element.clientHeight,
      scrollHeight: element.scrollHeight,
    }))
    expect(textareaGeometry.scrollHeight).toBeLessThanOrEqual(
      textareaGeometry.clientHeight + 1,
    )
    await expectConversationGeometry(page)
    await expectNoAccidentalClipping(page)
    await screenshot(page, info, '320-large-attachment')
  }

  await page.setViewportSize({
    width: page.viewportSize()?.width ?? 390,
    height: 500,
  })
  await input.focus()
  await expect(page.getByRole('button', { name: '发送消息' })).toBeVisible()
  await expectConversationGeometry(page)
  await expectNoAccidentalClipping(page)
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  await screenshot(page, info, 'composer-bottom')
})

test('任务提案与课表导入卡在消息流中保持完整', async ({
  page,
  request,
}, info) => {
  test.setTimeout(90_000)
  await account(page, request)
  await initializeTimeSettings(page)

  await page.getByLabel('消息', { exact: true })
    .fill('明天上午希望学习两小时线性代数')
  await page.getByRole('button', { name: '发送消息', exact: true }).click()
  const taskProposal = page.getByRole('region', { name: '任务计划提案' })
  await expect(taskProposal.getByText('学习线性代数', { exact: true }))
    .toBeVisible({ timeout: 15_000 })
  await expectConversationGeometry(page)
  await expectNoAccidentalClipping(page)
  await screenshot(page, info, 'proposals')

  await page.getByLabel('消息', { exact: true }).fill(
    '会话课表导入测试：请安排每周一、三上午的高等数学。',
  )
  await page.getByRole('button', { name: '发送消息', exact: true }).click()
  await expect(page.getByText('已整理为课表候选，请确认后导入。'))
    .toBeVisible({ timeout: 15_000 })
  const timetable = page.getByRole('region', { name: '课表导入' }).last()
  await expect(timetable.getByRole('button', { name: /确认导入 1 条/ }))
    .toBeVisible({ timeout: 15_000 })
  await expectConversationGeometry(page)
  await expectNoAccidentalClipping(page)
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  await screenshot(page, info, 'timetable')
})

test('取消与失败恢复状态不遮挡消息或输入区', async ({
  page,
  request,
}, info) => {
  test.setTimeout(90_000)
  await account(page, request)
  await page.getByLabel('消息', { exact: true }).fill('取消测试')
  await page.getByRole('button', { name: '发送消息', exact: true }).click()
  await expect(page.getByText('已收到这条消息。', { exact: true }))
    .toBeVisible({ timeout: 15_000 })
  await page.getByRole('button', { name: '取消生成', exact: true }).click()
  await expect(page.getByText('已取消，以上内容可能不完整')).toBeVisible()
  await expectConversationGeometry(page)
  await expectNoAccidentalClipping(page)
  await screenshot(page, info, 'cancelled')

  await newConversation(page)
  await page.getByLabel('消息', { exact: true }).fill('触发失败')
  await page.getByRole('button', { name: '发送消息', exact: true }).click()
  await expect(page.getByText('AI 服务暂时不可用，系统会自动重试。'))
    .toBeVisible({ timeout: 15_000 })
  await expect(page.getByRole('button', { name: '重新发送', exact: true }))
    .toBeVisible()
  await expectConversationGeometry(page)
  await expectNoAccidentalClipping(page)
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  await screenshot(page, info, 'failure')
})
