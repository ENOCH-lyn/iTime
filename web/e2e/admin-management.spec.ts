import { test, expect, type APIRequestContext, type Page, type TestInfo } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { createHmac, randomUUID } from 'node:crypto'
import { mkdir, readFile } from 'node:fs/promises'

const evidence = '/tmp/itime-ui-evidence'
const userPassword = 'Valid-user-password-123'
const runtimeErrors = new WeakMap<Page, string[]>()

interface ActiveAdmin {
  email: string
  secret: string
  recovery_code: string
}
interface Bootstrap {
  project: string
  password: string
  active: Record<string, ActiveAdmin>
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

function totp(secret: string) {
  const counter = BigInt(Math.floor(Date.now() / 30_000))
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

async function loginAdmin(page: Page, admin: ActiveAdmin, password: string) {
  await page.goto('/admin/login')
  await page.getByLabel('管理员邮箱', { exact: true }).fill(admin.email)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByLabel('6 位验证码', { exact: true }).fill(totp(admin.secret))
  await page.getByRole('button', { name: '进入管理后台', exact: true }).click()
  await expect(page.getByRole('heading', { name: '账号安全' })).toBeVisible()
}

async function setRegistrationMode(page: Page, mode: 'open' | 'invite') {
  await page.goto('/admin/registration')
  const option = page.getByLabel(mode === 'open' ? '开放注册' : '仅限邀请')
  if (!(await option.isChecked())) {
    await page.locator('.segmented-field label').filter({
      hasText: mode === 'open' ? '开放注册' : '仅限邀请',
    }).click()
    await page.getByRole('button', { name: '保存注册方式', exact: true }).click()
    await expect(
      page.getByRole('region', { name: '注册方式' }).getByRole('status'),
    ).toContainText(
      mode === 'open' ? '开放注册已生效' : '邀请注册已生效',
    )
  }
}

async function mailLink(request: APIRequestContext, email: string) {
  let id = ''
  await expect.poll(async () => {
    const response = await request.get(
      `http://127.0.0.1:58025/api/v1/search?query=${encodeURIComponent(`to:${email}`)}`,
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

async function registerAndLogin(page: Page, request: APIRequestContext, email: string) {
  await page.goto('/register')
  await page.getByLabel('邮箱', { exact: true }).fill(email)
  await page.getByLabel('密码', { exact: true }).fill(userPassword)
  await page.getByLabel('确认密码', { exact: true }).fill(userPassword)
  await page.getByRole('button', { name: '创建账号', exact: true }).click()
  await expect(page.getByRole('heading', { name: '去邮箱看看吧' })).toBeVisible()
  await page.goto(await mailLink(request, email))
  await page.getByLabel('密码', { exact: true }).fill(userPassword)
  await page.getByRole('button', { name: '验证邮箱', exact: true }).click()
  await page.goto('/login')
  await page.getByLabel('邮箱', { exact: true }).fill(email)
  await page.getByLabel('密码', { exact: true }).fill(userPassword)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByRole('heading', { name: '账号与安全' })).toBeVisible()
}

async function screenshot(page: Page, info: TestInfo, name: string) {
  await mkdir(evidence, { recursive: true })
  await page.screenshot({
    path: `${evidence}/${info.project.name}-admin-management-${name}.png`,
    fullPage: true,
  })
}

test.beforeEach(async ({ page }) => {
  const errors: string[] = []
  runtimeErrors.set(page, errors)
  page.on('pageerror', error => errors.push(error.message))
  page.on('console', message => {
    if (
      ['error', 'warning'].includes(message.type())
      && !message.text().includes('Failed to load resource')
    ) errors.push(message.text())
  })
})

test.afterEach(async ({ page }) => {
  expect(runtimeErrors.get(page)).toEqual([])
})

test('切换邀请注册并用邮箱专属链接完成真实注册', async ({ page, browser }, info) => {
  const data = await bootstrap(info)
  await loginAdmin(page, data.active.registration!, data.password)
  await setRegistrationMode(page, 'invite')
  await expect(page).toHaveTitle('注册与邀请 · iTime 管理后台')
  await expect(page.getByRole('heading', { name: '注册与邀请' })).toBeVisible()
  expect(await page.locator('vite-error-overlay').count()).toBe(0)

  const email = `invited-ui-${randomUUID()}@example.com`
  await page.getByLabel('受邀邮箱', { exact: true }).fill(email)
  await page.getByLabel('有效小时数', { exact: true }).fill('24')
  await page.getByRole('button', { name: '创建邀请', exact: true }).click()
  await expect(
    page.getByRole('region', { name: '创建邀请' }).getByRole('status'),
  ).toContainText('邀请已创建')
  const link = await page.getByLabel('邀请链接', { exact: true }).inputValue()
  expect(link).toMatch(/^http:\/\/localhost:4173\/register#invite=[A-Za-z0-9_-]{43}$/)
  await screenshot(page, info, 'invitation-created')

  const userContext = await browser.newContext({ baseURL: 'http://localhost:4173' })
  try {
    const userPage = await userContext.newPage()
    await userPage.goto('/register')
    await expect(userPage.getByText('当前仅接受邀请注册，请打开管理员提供的邀请链接。')).toBeVisible()
    await expect(userPage.getByRole('button', { name: '创建账号', exact: true })).toBeDisabled()
    await userPage.goto(link)
    await expect(userPage).toHaveURL('/register')
    await expect(userPage.getByText('此注册将使用邮箱专属邀请。')).toBeVisible()
    await userPage.getByLabel('邮箱', { exact: true }).fill(`wrong-${email}`)
    await userPage.getByLabel('密码', { exact: true }).fill(userPassword)
    await userPage.getByLabel('确认密码', { exact: true }).fill(userPassword)
    await userPage.getByRole('button', { name: '创建账号', exact: true }).click()
    await expect(userPage.getByRole('alert')).toContainText('与此邮箱匹配')
    await userPage.getByLabel('邮箱', { exact: true }).fill(email)
    await userPage.getByRole('button', { name: '创建账号', exact: true }).click()
    await expect(userPage.getByRole('heading', { name: '去邮箱看看吧' })).toBeVisible()
    expect(
      await userPage.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage })),
    ).not.toContain('invite=')
  } finally {
    await userContext.close()
  }

  await page.getByRole('button', { name: '刷新邀请记录', exact: true }).click()
  const row = page.locator('.management-list li').filter({ hasText: email })
  await expect(row.getByText('已使用', { exact: true })).toBeVisible()
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await setRegistrationMode(page, 'open')
})

test('AI 冻结保留手动账号访问，停用账号立即撤销会话', async ({
  page,
  browser,
  request,
}, info) => {
  const data = await bootstrap(info)
  await loginAdmin(page, data.active.users!, data.password)
  await setRegistrationMode(page, 'open')

  const email = `managed-ui-${randomUUID()}@example.com`
  const userContext = await browser.newContext({ baseURL: 'http://localhost:4173' })
  try {
    const userPage = await userContext.newPage()
    await registerAndLogin(userPage, request, email)
    await page.goto('/admin/users')
    await page.getByLabel('搜索邮箱', { exact: true }).fill(email)
    await page.getByRole('button', { name: '筛选', exact: true }).click()
    const row = page.locator('.user-management-list li').filter({ hasText: email })
    await expect(row).toHaveCount(1)

    await row.getByRole('button', { name: '冻结 AI', exact: true }).click()
    await expect(page.getByRole('dialog')).toContainText('仍可登录和使用手动功能')
    await page.getByRole('button', { name: '确认修改', exact: true }).click()
    await expect(page.getByRole('status')).toContainText('AI 访问已冻结')
    await userPage.reload()
    await expect(userPage.getByText('此账号的 AI 功能已暂停')).toBeVisible()
    await screenshot(page, info, 'user-ai-frozen')

    await row.getByRole('button', { name: '停用账号', exact: true }).click()
    await expect(page.getByRole('dialog')).toContainText('现有登录和未使用邮件链接会立即失效')
    await page.getByRole('button', { name: '确认修改', exact: true }).click()
    await expect(page.getByRole('status')).toContainText('账号已停用')
    await userPage.reload()
    await expect(userPage).toHaveURL('/login')

    await row.getByRole('button', { name: '恢复账号', exact: true }).click()
    await page.getByRole('button', { name: '确认修改', exact: true }).click()
    await expect(page.getByRole('status')).toContainText('账号已恢复')
    await userPage.getByLabel('邮箱', { exact: true }).fill(email)
    await userPage.getByLabel('密码', { exact: true }).fill(userPassword)
    await userPage.getByRole('button', { name: '登录', exact: true }).click()
    await expect(userPage.getByText('此账号的 AI 功能已暂停')).toBeVisible()

    await row.getByRole('button', { name: '恢复 AI', exact: true }).click()
    await page.getByRole('button', { name: '确认修改', exact: true }).click()
    await expect(page.getByRole('status')).toContainText('AI 访问已恢复')
    await userPage.reload()
    await expect(userPage.getByText('此账号的 AI 功能已暂停')).toHaveCount(0)
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  } finally {
    await userContext.close()
  }
})

test('管理列表可从网络失败和版本冲突恢复并适配窄屏', async ({
  page,
  context,
}, info) => {
  const data = await bootstrap(info)
  await loginAdmin(page, data.active['management-errors']!, data.password)
  await page.route('**/api/v1/admin/users?**', route => route.abort('failed'))
  await page.goto('/admin/users')
  await expect(page.getByRole('alert')).toContainText('无法连接服务')
  await expect(page.getByRole('alert')).toContainText('本次操作未保存')
  await page.unroute('**/api/v1/admin/users?**')
  await page.getByRole('button', { name: '刷新列表', exact: true }).click()
  await expect(page.locator('.user-management-list li').first()).toBeVisible()
  await page.getByLabel('搜索邮箱', { exact: true }).fill(`missing-${randomUUID()}@example.com`)
  await page.getByRole('button', { name: '筛选', exact: true }).click()
  await expect(page.getByText('没有符合筛选条件的用户。')).toBeVisible()

  await setRegistrationMode(page, 'open')
  const stalePage = await context.newPage()
  try {
    await stalePage.goto('/admin/registration')
    await expect(stalePage.getByLabel('开放注册')).toBeChecked()
    await page.locator('.segmented-field label').filter({ hasText: '仅限邀请' }).click()
    await page.getByRole('button', { name: '保存注册方式', exact: true }).click()
    await stalePage.locator('.segmented-field label').filter({ hasText: '仅限邀请' }).click()
    await stalePage.getByRole('button', { name: '保存注册方式', exact: true }).click()
    await expect(stalePage.getByRole('alert')).toContainText('已被其他管理操作更新')
    await page.locator('.segmented-field label').filter({ hasText: '开放注册' }).click()
    await page.getByRole('button', { name: '保存注册方式', exact: true }).click()
  } finally {
    await stalePage.close()
  }

  await page.setViewportSize({ width: 320, height: 700 })
  await page.goto('/admin/registration')
  await page.getByRole('button', { name: '创建邀请', exact: true }).click()
  await expect(page.getByText('请输入完整的邮箱地址。')).toBeVisible()
  await expect(page.getByLabel('受邀邮箱', { exact: true })).toBeFocused()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  await screenshot(page, info, 'registration-320')
})
