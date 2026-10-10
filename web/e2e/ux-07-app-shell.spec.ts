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

const password = 'Valid-ux-07-app-shell-password-123'
const evidence = '/tmp/itime-ui-evidence'
const runtimeErrors = new WeakMap<Page, string[]>()

const pages = [
  { path: '/ai', label: 'AI' },
  { path: '/tasks', label: '任务' },
  { path: '/calendar', label: '日历' },
  { path: '/plans', label: '规划' },
  { path: '/reviews', label: '复盘' },
  { path: '/me', label: '我的' },
] as const

async function mailLink(request: APIRequestContext, address: string) {
  let id = ''
  await expect.poll(async () => {
    const response = await request.get(
      `http://127.0.0.1:58025/api/v1/search?query=${encodeURIComponent(`to:${address}`)}`,
    )
    const result = await response.json()
    id = result.messages.find(
      (message: { Subject: string }) => message.Subject.includes('验证邮箱'),
    )?.ID ?? ''
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
  const address = `ux-07-${randomUUID()}@example.com`
  await page.goto('/register')
  await page.getByLabel('邮箱', { exact: true }).fill(address)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByLabel('确认密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '创建账号', exact: true }).click()
  await expect(page.getByRole('heading', { name: '去邮箱看看吧' })).toBeVisible()
  await page.goto(await mailLink(request, address))
  await expect(page).toHaveURL('/ai')
}

async function screenshot(page: Page, info: TestInfo, name: string) {
  await mkdir(evidence, { recursive: true })
  await page.screenshot({
    path: `${evidence}/${info.project.name}-ux-07-${name}.png`,
    animations: 'disabled',
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

async function expectAppShellPresence(page: Page) {
  if ((page.viewportSize()?.width ?? 0) < 961) {
    const trigger = page.getByRole('button', { name: '打开导航', exact: true })
    await expect(trigger).toBeVisible()
    await trigger.click()
    const dialog = page.getByRole('dialog', { name: '导航', exact: true })
    await expect(dialog).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(dialog).toBeHidden()
  } else {
    await expect(page.locator('aside.app-shell-primary')).toBeVisible()
  }
}

async function expectRootFits(page: Page) {
  expect(await page.evaluate(
    () => document.documentElement.scrollWidth <= window.innerWidth + 1,
  )).toBe(true)
}

test.beforeEach(async ({ page }) => watchRuntime(page))
test.afterEach(async ({ page }) => {
  expect(runtimeErrors.get(page)).toEqual([])
})

test('全站 6 个页面展示统一的 AppShell', async ({ page, request }, info) => {
  test.setTimeout(180_000)
  await account(page, request)

  for (const entry of pages) {
    await page.goto(entry.path)
    if (entry.path === '/ai') {
      await expect(
        page.getByRole('heading', { name: '新对话', exact: true }),
      ).toBeVisible({ timeout: 15_000 })
    } else {
      await page.waitForLoadState('networkidle')
    }
    await expectAppShellPresence(page)
    await expectRootFits(page)
    const violations = (await new AxeBuilder({ page })
      .disableRules(['color-contrast'])
      .analyze()).violations
    expect(violations, `${entry.path} 存在 Axe 违规`).toEqual([])
    await screenshot(page, info, entry.path.replace(/[/]/g, '-'))
  }
})

test('规划页引导跳转 AI 对话，复盘页进入只读态并引导 AI', async ({ page, request }) => {
  test.setTimeout(120_000)
  await account(page, request)

  await page.goto('/plans')
  await page.waitForLoadState('networkidle')
  const planHero = page.getByRole('link', {
    name: /规划交给 AI 对话|交给 AI 对话|帮我生成本周规划/,
  }).first()
  await expect(planHero).toBeVisible({ timeout: 15_000 })
  await planHero.click()
  await expect(page).toHaveURL(/\/ai(?:\?|$)/)

  await page.goto('/reviews')
  await page.waitForLoadState('networkidle')
  const reviewHero = page.getByRole('link', {
    name: /去 AI 说复盘|复盘今天|让 AI 带你复盘/,
  }).first()
  await expect(reviewHero).toBeVisible({ timeout: 15_000 })
  await reviewHero.click()
  await expect(page).toHaveURL(/\/ai(?:\?|$)/)
})

test('旧路由 /focus 与 /stats 分别回退到 /ai 与 /me/stats', async ({ page, request }) => {
  test.setTimeout(60_000)
  await account(page, request)

  await page.goto('/focus')
  await expect(page).toHaveURL(/\/ai(?:\?|$)/)

  await page.goto('/stats')
  await expect(page).toHaveURL(/\/me\/stats(?:[?#/]|$)/)
})
