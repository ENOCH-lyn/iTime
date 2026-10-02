import { test, expect, type Page, type TestInfo } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { createHmac } from 'node:crypto'
import { mkdir, readFile } from 'node:fs/promises'

const evidence = '/tmp/itime-ui-evidence'

interface ActiveAdmin {
  email: string
  secret: string
  recovery_code: string
}
interface Bootstrap {
  project: string
  password: string
  setup_email: string
  setup_link: string
  active: Record<'login' | 'recovery' | 'password' | 'layout', ActiveAdmin>
}

async function bootstrap(info: TestInfo) {
  const path = process.env.ITIME_E2E_BOOTSTRAP_FILE
  if (!path) throw new Error('missing ITIME_E2E_BOOTSTRAP_FILE')
  const entries = JSON.parse(await readFile(path, 'utf8')) as Bootstrap[]
  return entries.find(entry => entry.project === info.project.name)!
}
function decodeBase32(value: string) {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'
  let bits = ''
  for (const char of value.replace(/=+$/, '').toUpperCase()) {
    bits += alphabet.indexOf(char).toString(2).padStart(5, '0')
  }
  const bytes = []
  for (let index = 0; index + 8 <= bits.length; index += 8) {
    bytes.push(Number.parseInt(bits.slice(index, index + 8), 2))
  }
  return Buffer.from(bytes)
}
function totp(secret: string, offset = 0) {
  const counter = BigInt(Math.floor(Date.now() / 30_000) + offset)
  const buffer = Buffer.alloc(8)
  buffer.writeBigUInt64BE(counter)
  const digest = createHmac('sha1', decodeBase32(secret)).update(buffer).digest()
  const position = digest[digest.length - 1]! & 0x0f
  const number = (
    ((digest[position]! & 0x7f) << 24)
    | ((digest[position + 1]! & 0xff) << 16)
    | ((digest[position + 2]! & 0xff) << 8)
    | (digest[position + 3]! & 0xff)
  ) % 1_000_000
  return number.toString().padStart(6, '0')
}
function wrongCode(code: string) {
  return `${code.slice(0, -1)}${code.endsWith('0') ? '1' : '0'}`
}
async function screenshot(page: Page, info: TestInfo, name: string) {
  await mkdir(evidence, { recursive: true })
  await page.screenshot({
    path: `${evidence}/${info.project.name}-admin-${name}.png`,
    fullPage: true,
  })
}
async function login(
  page: Page,
  email: string,
  password: string,
  method: 'totp' | 'recovery',
  code: string,
) {
  await page.goto('/admin/login')
  await page.getByLabel('管理员邮箱', { exact: true }).fill(email)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.locator('.segmented-field label').filter({
    hasText: method === 'totp' ? '身份验证器' : '恢复码',
  }).click()
  const codeInput = method === 'totp'
    ? page.getByLabel('6 位验证码', { exact: true })
    : page.getByRole('textbox', { name: '恢复码', exact: true })
  await codeInput.fill(code)
  await page.getByRole('button', { name: '进入管理后台', exact: true }).click()
}

test.beforeEach(async ({ page }) => {
  const runtimeErrors: string[] = []
  page.on('pageerror', error => runtimeErrors.push(error.message))
  page.on('console', message => {
    if (['error', 'warning'].includes(message.type()) && !message.text().includes('Failed to load resource')) {
      runtimeErrors.push(message.text())
    }
  })
  ;(page as Page & { runtimeErrors?: string[] }).runtimeErrors = runtimeErrors
})
test.afterEach(async ({ page }) => {
  expect((page as Page & { runtimeErrors?: string[] }).runtimeErrors).toEqual([])
})

test('一次性链接完成 TOTP 设置并安全交付恢复码', async ({ page }, info) => {
  const data = await bootstrap(info)
  await page.goto(data.setup_link)
  await expect(page).toHaveURL('/admin/setup')
  await expect(page).toHaveTitle('设置双重验证 · iTime')
  await expect(page.getByRole('heading', { name: '设置双重验证' })).toBeVisible()
  await page.getByLabel('管理员密码', { exact: true }).fill('Wrong-admin-password-123')
  await page.getByRole('button', { name: '继续设置', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('邮箱、密码或验证码不正确')
  await page.getByLabel('管理员密码', { exact: true }).fill(data.password)
  await page.getByRole('button', { name: '继续设置', exact: true }).click()
  await expect(page.getByRole('heading', { name: '添加到身份验证器' })).toBeVisible()
  await expect(page.locator('.qr-frame svg')).toBeVisible()
  const groupedSecret = await page.locator('.secret-row code').innerText()
  const secret = groupedSecret.replace(/\s/g, '')
  expect(secret).toMatch(/^[A-Z2-7]{32}$/)
  await screenshot(page, info, 'setup')
  const current = totp(secret)
  await page.getByLabel('6 位验证码', { exact: true }).fill(wrongCode(current))
  await page.getByRole('button', { name: '完成设置', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('验证码不正确')
  await page.getByLabel('6 位验证码', { exact: true }).fill(current)
  await page.getByRole('button', { name: '完成设置', exact: true }).click()
  await expect(page.getByRole('heading', { name: '保存恢复码' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '保存恢复码' })).toBeFocused()
  await expect(page.locator('.skip-link')).toHaveCSS('clip-path', 'inset(50%)')
  await expect(page.locator('.recovery-code-list li')).toHaveCount(10)
  await expect(page.getByRole('button', { name: '进入管理后台', exact: true })).toBeDisabled()
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  const downloadPromise = page.waitForEvent('download')
  await page.getByRole('button', { name: '下载文本', exact: true }).click()
  expect((await downloadPromise).suggestedFilename()).toMatch(/^itime-admin-recovery-.+\.txt$/)
  await expect(page.locator('.skip-link')).toHaveCSS('clip-path', 'inset(50%)')
  await screenshot(page, info, 'recovery-codes')
  await page.getByLabel('我已将恢复码保存在安全的离线位置').check()
  await page.getByRole('button', { name: '进入管理后台', exact: true }).click()
  await expect(page).toHaveURL('/admin/security')
  await expect(page.getByText(data.setup_email, { exact: true })).toBeVisible()
})

test('TOTP 登录防重放，恢复码可作为一次性替代', async ({ page }, info) => {
  const data = await bootstrap(info)
  const admin = data.active.login
  const code = totp(admin.secret)
  await login(page, admin.email, data.password, 'totp', code)
  await expect(page.getByRole('heading', { name: '账号安全' })).toBeVisible()
  await expect(page.getByText('本次使用身份验证器登录')).toBeVisible()
  await screenshot(page, info, 'security')
  await page.getByRole('button', { name: '退出管理后台', exact: true }).click()
  await login(page, admin.email, data.password, 'totp', code)
  await expect(page.getByRole('alert')).toContainText('已经使用')
  await page.locator('.segmented-field label').filter({ hasText: '恢复码' }).click()
  await page.getByRole('textbox', { name: '恢复码', exact: true }).fill(admin.recovery_code)
  await page.getByRole('button', { name: '进入管理后台', exact: true }).click()
  await expect(page.getByText('本次使用恢复码登录')).toBeVisible()
})

test('恢复码轮换废止旧码，新码可完成登录', async ({ page }, info) => {
  const data = await bootstrap(info)
  const admin = data.active.recovery
  await login(page, admin.email, data.password, 'recovery', admin.recovery_code)
  await expect(page.getByRole('heading', { name: '账号安全' })).toBeVisible()
  await page.goto('/admin/recovery')
  await page.getByLabel('当前 6 位验证码', { exact: true }).fill(totp(admin.secret))
  await expect(page.getByRole('button', { name: '生成新的恢复码' })).toBeDisabled()
  await page.getByLabel('我知道所有旧恢复码会立即失效').check()
  await page.getByRole('button', { name: '生成新的恢复码' }).click()
  const newCode = await page.locator('.recovery-code-list code').first().innerText()
  await expect(page.locator('.recovery-code-list li')).toHaveCount(10)
  await page.getByLabel('我已将恢复码保存在安全的离线位置').check()
  await page.getByRole('button', { name: '完成', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('新的恢复码已生效')
  await page.getByRole('button', { name: '退出管理后台', exact: true }).click()
  await login(page, admin.email, data.password, 'recovery', admin.recovery_code)
  await expect(page.getByRole('alert')).toContainText('恢复码不正确或已经使用')
  await page.getByRole('textbox', { name: '恢复码', exact: true }).fill(newCode)
  await page.getByRole('button', { name: '进入管理后台', exact: true }).click()
  await expect(page.getByRole('heading', { name: '账号安全' })).toBeVisible()
})

test('修改管理密码撤销其他会话并保持当前会话', async ({ page, browser }, info) => {
  const data = await bootstrap(info)
  const admin = data.active.password
  await login(page, admin.email, data.password, 'totp', totp(admin.secret))
  const otherContext = await browser.newContext({ baseURL: 'http://localhost:4173' })
  try {
    const other = await otherContext.newPage()
    await login(other, admin.email, data.password, 'recovery', admin.recovery_code)
    await page.goto('/admin/password')
    await page.getByLabel('当前密码', { exact: true }).fill(data.password)
    await page.getByLabel('新密码', { exact: true }).fill('New-playwright-admin-password-456')
    await page.getByLabel('确认新密码', { exact: true }).fill('not-the-same-value')
    await page.getByRole('button', { name: '更新管理密码', exact: true }).click()
    await expect(page.getByText('两次输入的密码不一致。')).toBeVisible()
    await page.getByLabel('确认新密码', { exact: true }).fill('New-playwright-admin-password-456')
    await page.getByRole('button', { name: '更新管理密码', exact: true }).click()
    await expect(page.getByRole('status')).toContainText('其他普通和管理会话均已退出')
    await other.reload()
    await expect(other).toHaveURL('/admin/login')
    await page.getByRole('button', { name: '退出管理后台', exact: true }).click()
    const futureCode = totp(admin.secret, 1)
    await login(page, admin.email, data.password, 'totp', futureCode)
    await expect(page.getByRole('alert')).toContainText('邮箱、密码或验证码不正确')
    await page.getByLabel('密码', { exact: true }).fill('New-playwright-admin-password-456')
    await page.getByLabel('6 位验证码', { exact: true }).fill(futureCode)
    await page.getByRole('button', { name: '进入管理后台', exact: true }).click()
    await expect(page.getByRole('heading', { name: '账号安全' })).toBeVisible()
  } finally { await otherContext.close() }
})

test('未认证跳转、错误恢复、响应式与无障碍状态完整', async ({ page }, info) => {
  const data = await bootstrap(info)
  const admin = data.active.layout
  await page.goto('/admin/security')
  await expect(page).toHaveURL('/admin/login')
  await expect(page.getByRole('button', { name: '进入管理后台', exact: true })).toBeVisible()
  await login(page, admin.email, data.password, 'totp', wrongCode(totp(admin.secret)))
  await expect(page.getByRole('alert')).toContainText('验证码不正确')
  await expect(page.getByRole('alert')).toContainText('请求编号')
  await page.getByLabel('6 位验证码', { exact: true }).fill(totp(admin.secret))
  await page.getByRole('button', { name: '进入管理后台', exact: true }).click()
  await expect(page.getByRole('heading', { name: '账号安全' })).toBeVisible()
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await page.goto('/admin/appearance')
  await page.getByLabel('外观主题').selectOption('dark')
  await page.getByLabel('字号', { exact: true }).selectOption('larger')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await expect(page.locator('html')).toHaveAttribute('data-font-size', 'larger')
  await page.setViewportSize({ width: 320, height: 700 })
  await page.goto('/admin/recovery')
  await expect(page.getByRole('button', { name: '生成新的恢复码' })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  await screenshot(page, info, 'mobile-recovery')
})
