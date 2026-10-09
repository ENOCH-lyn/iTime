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
  await screenshot(page, info, 'markdown')

  const messageCount = await page.locator('.conversation-messages > li').count()
  await input.fill('第一行')
  await input.press('Shift+Enter')
  await input.type('第二行')
  await expect(input).toHaveValue('第一行\n第二行')
  await expect(page.locator('.conversation-messages > li')).toHaveCount(messageCount)
  await expect(page.getByText(/\/12000$/)).toHaveCount(0)

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

  await page.setViewportSize({
    width: page.viewportSize()?.width ?? 390,
    height: 500,
  })
  await input.focus()
  await expect(page.getByRole('button', { name: '发送消息' })).toBeVisible()
  await expectConversationGeometry(page)
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  await screenshot(page, info, 'composer-bottom')
})
