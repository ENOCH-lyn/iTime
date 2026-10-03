import { test, expect, type Page, type APIRequestContext, type TestInfo } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { mkdir } from 'node:fs/promises'
import { randomUUID } from 'node:crypto'

const password = 'Valid-test-password-123'
const replacement = 'New-valid-password-456'
const evidence = '/tmp/itime-ui-evidence'
const email = () => `ui-${randomUUID()}@example.com`
const runtimeErrors = new WeakMap<Page, string[]>()

async function screenshot(page: Page, info: TestInfo, name: string) {
  await mkdir(evidence, { recursive: true })
  await page.screenshot({ path: `${evidence}/${info.project.name}-${name}.png`, fullPage: true })
}
async function mailLink(request: APIRequestContext, address: string, purpose = 'verify') {
  let id = ''
  await expect.poll(async () => {
    const response = await request.get(`http://127.0.0.1:58025/api/v1/search?query=${encodeURIComponent(`to:${address}`)}`)
    const result = await response.json()
    const message = result.messages.find((m: { Subject: string }) => m.Subject.includes(purpose === 'verify' ? '验证邮箱' : '重设密码'))
    id = message?.ID ?? ''
    return id
  }, { timeout: 12_000 }).not.toBe('')
  const result = await (await request.get(`http://127.0.0.1:58025/api/v1/message/${id}`)).json()
  return result.Text.match(/http:\/\/localhost:4173\/[a-z-]+#token=[A-Za-z0-9_-]{43}/)[0] as string
}
async function register(page: Page, address: string) {
  await page.goto('/register')
  await page.getByLabel('邮箱', { exact: true }).fill(address)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByLabel('确认密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '创建账号', exact: true }).click()
  await expect(page.getByRole('heading', { name: '去邮箱看看吧' })).toBeVisible()
}
async function expectAIHome(page: Page) {
  await expect(page).toHaveURL('/ai')
  if ((page.viewportSize()?.width ?? 0) <= 700) {
    await expect(page.getByRole('button', { name: '新建会话', exact: true })).toBeVisible()
  } else {
    await expect(page.getByRole('heading', { name: '今天想先理清哪件事？' })).toBeVisible()
  }
}
async function login(page: Page, address: string, secret = password) {
  await page.goto('/login')
  await page.getByLabel('邮箱', { exact: true }).fill(address)
  await page.getByLabel('密码', { exact: true }).fill(secret)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expectAIHome(page)
}
async function account(page: Page, request: APIRequestContext) {
  const address = email()
  await register(page, address)
  const link = await mailLink(request, address)
  await page.goto(link)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '验证邮箱', exact: true }).click()
  await expect(page.getByRole('heading', { name: '邮箱已验证' })).toBeVisible()
  await login(page, address)
  return address
}

test.beforeEach(async ({ page }) => {
  const exceptions: string[] = []
  runtimeErrors.set(page, exceptions)
  page.on('pageerror', error => exceptions.push(error.message))
  page.on('console', message => {
    if (['error', 'warning'].includes(message.type()) && !message.text().includes('Failed to load resource')) {
      exceptions.push(message.text())
    }
  })
})
test.afterEach(async ({ page }) => { expect(runtimeErrors.get(page)).toEqual([]) })

test('注册校验、真实邮件验证、登录与刷新保持身份', async ({ page, request, context }, info) => {
  const address = email()
  await page.goto('/register')
  const browserTimeZone = await page.evaluate(() => Intl.DateTimeFormat().resolvedOptions().timeZone)
  await page.getByRole('button', { name: '创建账号', exact: true }).click()
  await expect(page.getByText('请输入完整的邮箱地址。')).toBeVisible()
  await expect(page.getByLabel('邮箱', { exact: true })).toBeFocused()
  await register(page, address)
  const link = await mailLink(request, address)
  await page.goto('/login')
  await page.getByLabel('邮箱', { exact: true }).fill(address)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('邮箱尚未验证')
  await expect(page.getByRole('alert')).toContainText('请求编号')
  await page.goto(link)
  await expect(page).toHaveURL('/verify-email')
  await page.getByLabel('密码', { exact: true }).fill(replacement)
  await page.getByRole('button', { name: '验证邮箱', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('邮箱或密码不正确')
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '验证邮箱', exact: true }).click()
  await expect(page.getByRole('heading', { name: '邮箱已验证' })).toBeVisible()
  await login(page, address)
  await page.reload()
  await expectAIHome(page)
  const timeSettings = await page.evaluate(async () => {
    const response = await fetch('/api/v1/time-settings')
    return response.json()
  })
  expect(timeSettings.settings).toMatchObject({
    initialized: true,
    time_zone: browserTimeZone,
    week_starts_on: 1,
    activity_day_start_minute: 240,
  })
  await screenshot(page, info, 'account')
  const cookie = (await context.cookies()).find(c => c.name === 'itime_session')!
  expect(cookie.httpOnly).toBe(true)
  expect(cookie.path).toBe('/api')
  expect(await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }))).not.toMatch(/token|csrf|Valid-test/)
  await page.goto(link)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '验证邮箱', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('已过期、已使用或已被新链接替换')
  await screenshot(page, info, 'used-link')
})

test('重新发送、缺失令牌与邮件成功状态的恢复入口', async ({ page, request }) => {
  const address = email()
  await page.goto('/verify-email')
  await expect(page.getByRole('alert')).toContainText('链接不完整')
  await expect(page.getByRole('button', { name: '验证邮箱', exact: true })).toBeDisabled()
  await register(page, address)
  const oldLink = await mailLink(request, address)
  const resendButton = page.getByRole('button', { name: '重新发送邮件', exact: true })
  await expect(resendButton).toBeEnabled()
  await resendButton.click()
  await expect(page.getByRole('status')).toContainText('已受理重新发送')
  await page.goto(oldLink)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '验证邮箱', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('已被新链接替换')
  await page.getByRole('link', { name: '重新发送验证邮件', exact: true }).click()
  await expect(page.getByLabel('邮箱', { exact: true })).toBeVisible()
})

test('找回密码使用真实邮件，旧设备退出，新密码可登录', async ({ page, request, browser }) => {
  const address = await account(page, request)
  const other = await browser.newContext({ baseURL: 'http://localhost:4173' })
  try {
    const second = await other.newPage()
    await login(second, address)
    await page.goto('/forgot-password')
    await page.getByLabel('邮箱', { exact: true }).fill(address)
    await page.getByRole('button', { name: '发送重设邮件', exact: true }).click()
    await expect(page.getByRole('heading', { name: '去邮箱看看吧' })).toBeVisible()
    await page.goto(await mailLink(request, address, 'reset'))
    await page.getByLabel('新密码', { exact: true }).fill(replacement)
    await page.getByLabel('确认密码', { exact: true }).fill(password)
    await page.getByRole('button', { name: '重设密码', exact: true }).click()
    await expect(page.getByText('两次输入的密码不一致。')).toBeVisible()
    await page.getByLabel('确认密码', { exact: true }).fill(replacement)
    await page.getByRole('button', { name: '重设密码', exact: true }).click()
    await expect(page.getByRole('heading', { name: '密码已重设' })).toBeVisible()
    await second.reload()
    await expect(second).toHaveURL('/login')
    await login(page, address, replacement)
  } finally { await other.close() }
})

test('改密保留及撤销其他会话，当前会话轮换后继续操作', async ({ page, request, browser }) => {
  const address = await account(page, request)
  const other = await browser.newContext({ baseURL: 'http://localhost:4173' })
  try {
    const second = await other.newPage()
    await login(second, address)
    await page.goto('/account/password')
    await page.getByLabel('当前密码', { exact: true }).fill(password)
    await page.getByLabel('新密码', { exact: true }).fill(replacement)
    await page.getByLabel('确认新密码', { exact: true }).fill(replacement)
    await page.getByLabel('同时退出其他登录设备').uncheck()
    await page.getByRole('button', { name: '更新密码', exact: true }).click()
    await expect(page.getByRole('status')).toContainText('其他设备的登录已保留')
    await second.reload()
    await expectAIHome(second)
    await page.getByLabel('当前密码', { exact: true }).fill(replacement)
    await page.getByLabel('新密码', { exact: true }).fill(password)
    await page.getByLabel('确认新密码', { exact: true }).fill(password)
    await page.getByLabel('同时退出其他登录设备').check()
    await page.getByRole('button', { name: '更新密码', exact: true }).click()
    await expect(page.getByRole('status')).toContainText('其他设备已退出')
    await second.reload()
    await expect(second).toHaveURL('/login')
    await page.getByRole('button', { name: '退出登录', exact: true }).click()
    await expect(page).toHaveURL('/login')
  } finally { await other.close() }
})

test('设备列表、确认取消与远程退出、当前设备退出', async ({ page, request, browser }, info) => {
  const address = await account(page, request)
  const other = await browser.newContext({ baseURL: 'http://localhost:4173' })
  try {
    const second = await other.newPage()
    await login(second, address)
    await page.goto('/account/devices')
    await expect(page.locator('.device-list li')).toHaveCount(2)
    await screenshot(page, info, 'devices')
    await page.getByRole('button', { name: /^退出设备/ }).click()
    await expect(page.getByRole('dialog')).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.getByRole('dialog')).not.toBeVisible()
    await expect(page.locator('.device-list li')).toHaveCount(2)
    await page.getByRole('button', { name: /^退出设备/ }).click()
    await page.getByRole('button', { name: '确认退出', exact: true }).click()
    await expect(page.locator('.device-list li')).toHaveCount(1)
    await second.reload()
    await expect(second).toHaveURL('/login')
    await page.getByRole('button', { name: '退出当前设备', exact: true }).click()
    await page.getByRole('button', { name: '确认退出', exact: true }).click()
    await expect(page).toHaveURL('/login')
  } finally { await other.close() }
})

test('断网保留输入，恢复网络后完成真实登录', async ({ page, request, context }, info) => {
  const address = await account(page, request)
  await page.getByRole('button', { name: '退出登录', exact: true }).click()
  await page.getByLabel('邮箱', { exact: true }).fill(address)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await context.setOffline(true)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('无法连接服务')
  await expect(page.getByRole('alert')).toContainText('保存状态尚未确认')
  await expect(page.getByLabel('邮箱', { exact: true })).toHaveValue(address)
  await expect(page.getByLabel('密码', { exact: true })).toHaveValue(password)
  await screenshot(page, info, 'offline')
  await context.setOffline(false)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expectAIHome(page)
})

test('多标签改密遇到旧身份响应延迟，后续写入仍使用新会话', async ({ page, request, context }) => {
  await account(page, request)
  const second = await context.newPage()
  await second.goto('/account/password')
  await expect(second.getByLabel('当前密码', { exact: true })).toBeVisible()
  await page.goto('/account')
  await expect(page.getByRole('heading', { name: '账号与安全' })).toBeVisible()
  let release!: () => void
  let captured!: () => void
  const held = new Promise<void>(resolve => { release = resolve })
  const received = new Promise<void>(resolve => { captured = resolve })
  let intercepted = false
  await page.route('**/api/v1/auth/me', async route => {
    if (intercepted) { await route.continue(); return }
    intercepted = true
    const response = await route.fetch()
    captured()
    await held
    await route.fulfill({ response })
  })
  try {
    await page.getByRole('link', { name: '修改密码', exact: true }).first().click()
    await received
    await second.getByLabel('当前密码', { exact: true }).fill(password)
    await second.getByLabel('新密码', { exact: true }).fill(replacement)
    await second.getByLabel('确认新密码', { exact: true }).fill(replacement)
    await second.getByRole('button', { name: '更新密码', exact: true }).click()
    await expect(second.getByRole('status')).toContainText('密码已更新')
    release()
    await page.getByLabel('当前密码', { exact: true }).fill(replacement)
    await page.getByLabel('新密码', { exact: true }).fill(password)
    await page.getByLabel('确认新密码', { exact: true }).fill(password)
    await page.getByRole('button', { name: '更新密码', exact: true }).click()
    await expect(page.getByRole('status')).toContainText('密码已更新')
  } finally { release(); await second.close() }
})

test('账号服务初次不可达可恢复，超时不显示虚假成功', async ({ page, request }) => {
  await page.route('**/api/v1/auth/policy', route => route.abort('failed'))
  await page.goto('/login')
  await expect(page.getByRole('alert')).toContainText('无法连接服务')
  await page.unroute('**/api/v1/auth/policy')
  await page.getByRole('button', { name: '重新连接', exact: true }).click()
  await expect(page.getByLabel('邮箱', { exact: true })).toBeVisible()
  const address = await account(page, request)
  await page.getByRole('button', { name: '退出登录', exact: true }).click()
  await page.getByLabel('邮箱', { exact: true }).fill(address)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.clock.install()
  let arrived!: () => void
  let release!: () => void
  const requestArrived = new Promise<void>(resolve => { arrived = resolve })
  const released = new Promise<void>(resolve => { release = resolve })
  await page.route('**/api/v1/auth/login', async route => {
    arrived()
    await released
    await route.abort()
  })
  try {
    await page.getByRole('button', { name: '登录', exact: true }).click()
    await requestArrived
    await page.clock.fastForward(15_100)
    await expect(page.getByRole('alert')).toContainText('等待响应超时')
    await expect(page.getByRole('alert')).toContainText('保存状态尚未确认')
    await expect(page.getByLabel('密码', { exact: true })).toHaveValue(password)
  } finally { release(); await page.unrouteAll({ behavior: 'wait' }) }
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expectAIHome(page)
})

test('主题、字号、键盘焦点、视口布局与无障碍检查', async ({ page, request }, info) => {
  await page.goto('/login')
  await expect(page).toHaveTitle('欢迎回来 · iTime')
  await expect(page.getByRole('button', { name: '登录', exact: true })).toBeVisible()
  expect(await page.locator('vite-error-overlay').count()).toBe(0)
  await page.getByLabel('外观主题').selectOption('light')
  await screenshot(page, info, 'login-light')
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '显示密码', exact: true }).click()
  await expect(page.getByLabel('密码', { exact: true })).toHaveAttribute('type', 'text')
  await page.getByRole('button', { name: '隐藏密码', exact: true }).click()
  await page.getByLabel('外观主题').selectOption('dark')
  await page.reload()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await screenshot(page, info, 'login-dark')
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  await page.getByLabel('邮箱', { exact: true }).focus()
  await page.keyboard.press('Tab')
  await expect(page.getByLabel('密码', { exact: true })).toBeFocused()
  await account(page, request)
  for (const path of ['/account/password', '/account/devices', '/account/appearance']) {
    await page.goto(path)
    await expect(page.getByRole('button', { name: '退出登录', exact: true })).toBeVisible()
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  }
  await page.getByLabel('字号', { exact: true }).selectOption('larger')
  await page.getByLabel('外观主题').selectOption('system')
  await page.emulateMedia({ colorScheme: 'light', reducedMotion: 'reduce' })
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
  await page.reload()
  await expect(page.locator('html')).toHaveAttribute('data-font-size', 'larger')
  await page.setViewportSize({ width: 320, height: 700 })
  await page.goto('/account/password')
  await expect(page.getByRole('button', { name: '更新密码', exact: true })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await screenshot(page, info, '320-large-font')
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  await page.goto('/does-not-exist')
  await expect(page.getByRole('heading', { name: '这个页面不在这里' })).toBeVisible()
  await page.getByRole('link', { name: '返回 iTime' }).click()
  await expectAIHome(page)
})
