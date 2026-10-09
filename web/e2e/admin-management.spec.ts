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
  ai_endpoint: string
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
    await page.getByRole('button', { name: '保存注册策略', exact: true }).click()
    await expect(
      page.getByRole('region', { name: '注册方式' }).getByRole('status'),
    ).toContainText('注册策略已保存')
  }
}

async function setEmailVerification(page: Page, required: boolean) {
  await page.goto('/admin/registration')
  const control = page.getByRole('switch', { name: '要求邮箱验证' })
  if ((await control.isChecked()) !== required) {
    await control.setChecked(required)
    await page.getByRole('button', { name: '保存注册策略', exact: true }).click()
    await expect(
      page.getByRole('region', { name: '注册方式' }).getByRole('status'),
    ).toContainText('注册策略已保存')
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
  if ((page.viewportSize()?.width ?? 0) <= 700) {
    await expect(page.getByRole('button', {
      name: '打开导航',
      exact: true,
    })).toBeVisible()
    await expect(page.getByRole('heading', {
      name: '新对话',
      exact: true,
    })).toBeVisible()
  } else {
    await expect(page.getByRole('heading', { name: '今天想先理清哪件事？' })).toBeVisible()
  }
}

async function screenshot(page: Page, info: TestInfo, name: string) {
  await mkdir(evidence, { recursive: true })
  await page.screenshot({
    path: `${evidence}/${info.project.name}-admin-management-${name}.png`,
    fullPage: true,
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

test.beforeEach(async ({ page }) => {
  watchRuntime(page)
})

test.afterEach(async ({ page }) => {
  expect(runtimeErrors.get(page)).toEqual([])
})

test('关闭邮箱验证后新账号注册直接登录', async ({
  page,
  browser,
}, info) => {
  const data = await bootstrap(info)
  await loginAdmin(page, data.active['email-verification']!, data.password)
  await setRegistrationMode(page, 'open')
  await setEmailVerification(page, false)

  const userContext = await browser.newContext({
    baseURL: 'http://localhost:4173',
  })
  try {
    const userPage = await userContext.newPage()
    const email = `direct-ui-${randomUUID()}@example.com`
    await userPage.goto('/register')
    await userPage.getByLabel('邮箱', { exact: true }).fill(email)
    await userPage.getByLabel('密码', { exact: true }).fill(userPassword)
    await userPage.getByLabel('确认密码', { exact: true }).fill(userPassword)
    await userPage.getByRole('button', {
      name: '创建账号',
      exact: true,
    }).click()
    if ((userPage.viewportSize()?.width ?? 0) <= 700) {
      await expect(userPage.getByRole('heading', {
        name: '新对话',
        exact: true,
      })).toBeVisible()
    } else {
      await expect(userPage.getByRole('heading', {
        name: '今天想先理清哪件事？',
      })).toBeVisible()
    }
    await expect(userPage).toHaveURL('/ai')
    await screenshot(page, info, 'registration-no-email-verification')
  } finally {
    await userContext.close()
    await setEmailVerification(page, true)
  }
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
    const visibleRestriction = userPage.locator('.status:visible').filter({
      hasText: '此账号的 AI 功能已暂停',
    })
    await expect(visibleRestriction).toBeVisible()
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
    await expect(visibleRestriction).toBeVisible()

    await row.getByRole('button', { name: '恢复 AI', exact: true }).click()
    await page.getByRole('button', { name: '确认修改', exact: true }).click()
    await expect(page.getByRole('status')).toContainText('AI 访问已恢复')
    await userPage.reload()
    await expect(visibleRestriction).toHaveCount(0)
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  } finally {
    await userContext.close()
  }
})

test('注销中账号只读显示且不能由管理员恢复', async ({
  page,
  browser,
  request,
}, info) => {
  const data = await bootstrap(info)
  await loginAdmin(page, data.active['account-deletion']!, data.password)
  await setRegistrationMode(page, 'open')

  const email = `deleting-ui-${randomUUID()}@example.com`
  const userContext = await browser.newContext({
    baseURL: 'http://localhost:4173',
  })
  try {
    const userPage = await userContext.newPage()
    await registerAndLogin(userPage, request, email)
    const session = await (
      await userPage.request.get('/api/v1/auth/me')
    ).json()
    const requested = await userPage.request.post(
      '/api/v1/account-deletion',
      {
        headers: { 'X-CSRF-Token': session.csrf_token },
        data: {
          operation_id: randomUUID(),
          email_confirmation: email,
          current_password: userPassword,
        },
      },
    )
    expect(requested.status()).toBe(202)

    await page.goto('/admin/users')
    await page.getByLabel('账号状态', { exact: true })
      .selectOption('pending_deletion')
    await page.getByRole('button', { name: '筛选', exact: true }).click()
    const row = page.locator('.user-management-list li').filter({
      hasText: email,
    })
    await expect(row).toHaveCount(1)
    await expect(row.getByText('注销中', { exact: true })).toBeVisible()
    await expect(row.getByText('用户可在 7 天期限内自行撤销'))
      .toBeVisible()
    await expect(row.getByRole('button')).toHaveCount(0)
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
    expect(await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    )).toBe(true)
    await screenshot(page, info, 'account-deletion')
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
    await page.getByRole('button', { name: '保存注册策略', exact: true }).click()
    await stalePage.locator('.segmented-field label').filter({ hasText: '仅限邀请' }).click()
    await stalePage.getByRole('button', { name: '保存注册策略', exact: true }).click()
    await expect(stalePage.getByRole('alert')).toContainText('已被其他管理操作更新')
    await page.locator('.segmented-field label').filter({ hasText: '开放注册' }).click()
    await page.getByRole('button', { name: '保存注册策略', exact: true }).click()
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

test('分别创建、测试和发布截图、转写与对话配置', async ({
  page,
  browser,
  request,
}, info) => {
  const data = await bootstrap(info)
  await loginAdmin(page, data.active['ai-config']!, data.password)
  await page.goto('/admin/ai')
  await expect(page).toHaveTitle('AI 配置 · iTime 管理后台')
  await expect(page.getByRole('heading', { name: 'AI 配置', exact: true })).toBeVisible()

  const key = `sk-ui-${randomUUID()}`
  const model = `vision-ui-${info.project.name}-${randomUUID().slice(0, 8)}`
  await page.getByLabel('OpenAI 兼容端点', { exact: true }).fill(data.ai_endpoint)
  await page.getByLabel('模型', { exact: true }).fill(model)
  await page.getByLabel('API Key', { exact: true }).fill(key)
  await page.getByLabel('单次超时（秒）', { exact: true }).fill('10')
  await page.getByLabel('最大输出 Token', { exact: true }).fill('2048')
  await page.getByLabel('币种代码', { exact: true }).fill('cny')
  await page.getByLabel('输入价格（每百万 Token）', { exact: true }).fill('1.25')
  await page.getByLabel('缓存输入价格（每百万 Token）', { exact: true }).fill('0.5')
  await page.getByLabel('输出价格（每百万 Token）', { exact: true }).fill('4.75')
  await page.getByRole('button', { name: '保存新版本', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('已保存为草稿')
  await expect(page.getByLabel('API Key', { exact: true })).toHaveValue('')
  await expect(page.locator('body')).not.toContainText(key)

  const row = page.locator('.ai-config-list li').filter({ hasText: model })
  await expect(row.getByText('草稿', { exact: true })).toBeVisible()
  await expect(row).toContainText('CNY 输入 1.25 · 缓存输入 0.5 · 输出 4.75')
  await row.getByRole('button', { name: '测试', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('已通过合成图片测试')
  await expect(row.getByText('测试通过', { exact: true })).toBeVisible()
  await row.getByRole('button', { name: '发布', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('已发布')
  await expect(row.getByText('当前发布', { exact: true })).toBeVisible()

  const historical = page.locator('.ai-config-list li').filter({
    has: page.getByText('历史版本', { exact: true }),
  }).first()
  await historical.getByRole('button', { name: '回滚', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('已回滚到版本')
  await expect(page.locator('.ai-config-list').getByText('当前发布', { exact: true })).toHaveCount(1)
  await expect(row.getByText('历史版本', { exact: true })).toBeVisible()

  await page.locator('.ai-capability-switch label').filter({
    hasText: '语音转写',
  }).click()
  await expect(page.getByRole('heading', { name: '语音转写配置' })).toBeVisible()
  await expect(page.getByLabel('最大输出 Token', { exact: true })).toHaveCount(0)
  await expect(
    page.getByLabel('输入价格（每百万 Token）', { exact: true }),
  ).toHaveCount(0)
  const transcriptionModel = (
    `transcription-ui-${info.project.name}-${randomUUID().slice(0, 8)}`
  )
  await page.getByLabel('OpenAI 兼容端点', { exact: true }).fill(data.ai_endpoint)
  await page.getByLabel('模型', { exact: true }).fill(transcriptionModel)
  await page.getByLabel('API Key', { exact: true })
    .fill(`sk-transcription-ui-${randomUUID()}`)
  await page.getByLabel('单次超时（秒）', { exact: true }).fill('10')
  await page.getByLabel('币种代码', { exact: true }).fill('cny')
  await page.getByLabel('音频价格（每分钟）', { exact: true }).fill('0.006')
  await page.getByRole('button', { name: '保存新版本', exact: true }).click()
  const transcriptionRow = page.locator('.ai-config-list li').filter({
    hasText: transcriptionModel,
  })
  await expect(transcriptionRow.getByText('草稿', { exact: true })).toBeVisible()
  await expect(transcriptionRow).toContainText('CNY 音频 0.006 / 分钟')
  await expect(transcriptionRow).not.toContainText('百万 Token')
  await transcriptionRow.getByRole('button', { name: '测试', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('已通过合成音频测试')
  await transcriptionRow.getByRole('button', { name: '发布', exact: true }).click()
  await expect(transcriptionRow.getByText('当前发布', { exact: true })).toBeVisible()

  expect(await page.locator('vite-error-overlay').count()).toBe(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  await page.evaluate(() => scrollTo(0, 0))
  await screenshot(page, info, 'ai-config-published')

  await page.locator('.ai-capability-switch label').filter({
    hasText: '全局对话',
  }).click()
  await expect(page.getByRole('heading', { name: '全局对话配置' })).toBeVisible()
  const chatModel = `chat-ui-${info.project.name}-${randomUUID().slice(0, 8)}`
  await page.getByLabel('OpenAI 兼容端点', { exact: true }).fill(data.ai_endpoint)
  await page.getByLabel('模型', { exact: true }).fill(chatModel)
  await page.getByLabel('API Key', { exact: true }).fill(`sk-chat-ui-${randomUUID()}`)
  await page.getByLabel('单次超时（秒）', { exact: true }).fill('10')
  await page.getByLabel('最大输出 Token', { exact: true }).fill('2048')
  await page.getByRole('button', { name: '保存新版本', exact: true }).click()
  const chatRow = page.locator('.ai-config-list li').filter({ hasText: chatModel })
  await expect(chatRow.getByText('草稿', { exact: true })).toBeVisible()
  await chatRow.getByRole('button', { name: '测试', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('已通过合成文本测试')
  await chatRow.getByRole('button', { name: '发布', exact: true }).click()
  await expect(chatRow.getByText('当前发布', { exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: '调用顺序' })).toBeVisible()
  await expect(page.locator('.ai-route-primary')).toContainText(chatModel)

  const backupModels = [
    `chat-backup-one-${info.project.name}-${randomUUID().slice(0, 8)}`,
    `chat-backup-two-${info.project.name}-${randomUUID().slice(0, 8)}`,
  ]
  for (const backupModel of backupModels) {
    await page.getByLabel('模型', { exact: true }).fill(backupModel)
    await page.getByLabel('API Key', { exact: true })
      .fill(`sk-${backupModel}-${randomUUID()}`)
    await page.getByRole('button', { name: '保存新版本', exact: true }).click()
    const backupRow = page.locator('.ai-config-list li').filter({
      hasText: backupModel,
    })
    await expect(backupRow.getByText('草稿', { exact: true })).toBeVisible()
    await backupRow.getByRole('button', { name: '测试', exact: true }).click()
    await expect(backupRow.getByText('测试通过', { exact: true })).toBeVisible()
  }
  const listed = await (
    await page.request.get(
      '/api/v1/admin/ai-configs?capability=conversation&limit=100',
    )
  ).json()
  const chatConfigId = listed.items.find(
    (item: { model: string }) => item.model === chatModel,
  ).id as string
  const backupIds = backupModels.map(modelName => (
    listed.items.find((item: { model: string }) => item.model === modelName).id
  ))
  await page.getByLabel('备用 1', { exact: true }).selectOption(backupIds[0])
  await page.getByLabel('备用 2', { exact: true }).selectOption(backupIds[1])
  await page.getByRole('button', { name: '保存调用顺序', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('备用服务调用顺序已保存')
  const backupOneRow = page.locator('.ai-config-list li').filter({
    hasText: backupModels[0],
  })
  const backupTwoRow = page.locator('.ai-config-list li').filter({
    hasText: backupModels[1],
  })
  await expect(backupOneRow.getByText('备用 1', { exact: true })).toBeVisible()
  await expect(backupTwoRow.getByText('备用 2', { exact: true })).toBeVisible()

  await page.getByLabel('备用 1', { exact: true }).selectOption(backupIds[1])
  await page.getByLabel('备用 2', { exact: true }).selectOption(backupIds[0])
  await page.getByRole('button', { name: '保存调用顺序', exact: true }).click()
  await expect(backupTwoRow.getByText('备用 1', { exact: true })).toBeVisible()
  await expect(backupOneRow.getByText('备用 2', { exact: true })).toBeVisible()

  await backupOneRow.getByRole('button', { name: '发布', exact: true }).click()
  await expect(page.locator('.ai-route-primary')).toContainText(backupModels[0])
  await expect(backupOneRow.getByText('主服务', { exact: true })).toBeVisible()
  await expect(backupTwoRow.getByText('备用 1', { exact: true })).toBeVisible()

  await page.getByLabel('备用 1', { exact: true }).selectOption(chatConfigId)
  const adminSession = await (
    await page.request.get('/api/v1/admin/auth/me')
  ).json()
  const currentRoute = await (
    await page.request.get('/api/v1/admin/ai-configs/routing/conversation')
  ).json()
  const concurrentUpdate = await page.request.put(
    '/api/v1/admin/ai-configs/routing/conversation',
    {
      headers: { 'X-CSRF-Token': adminSession.csrf_token },
      data: {
        expected_routing_version: currentRoute.routing_version,
        fallback_config_ids: [],
      },
    },
  )
  expect(concurrentUpdate.status()).toBe(200)
  await page.getByRole('button', { name: '保存调用顺序', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText(
    '调用顺序已被其他管理员修改；当前选择仍保留，可基于最新版重试。',
  )
  await expect(page.getByLabel('备用 1', { exact: true })).toHaveValue(
    chatConfigId,
  )
  await page.getByRole('button', { name: '保存调用顺序', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('备用服务调用顺序已保存')
  await expect(chatRow.getByText('备用 1', { exact: true })).toBeVisible()
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  await screenshot(page, info, 'chat-fallback-route')

  await setRegistrationMode(page, 'open')
  const userContext = await browser.newContext({
    baseURL: 'http://localhost:4173',
    viewport: page.viewportSize(),
    isMobile: info.project.name === 'mobile',
    hasTouch: info.project.name !== 'desktop',
  })
  const userEmail = `fallback-ui-${randomUUID()}@example.com`
  try {
    const userPage = await userContext.newPage()
    watchRuntime(userPage)
    await registerAndLogin(userPage, request, userEmail)
    if ((userPage.viewportSize()?.width ?? 0) < 1024) {
      await userPage.getByRole('button', {
        name: '打开导航',
        exact: true,
      }).click()
    }
    await userPage.getByRole('button', { name: '新建会话', exact: true }).click()
    await userPage.getByLabel('消息', { exact: true }).fill('备用切换测试')
    await userPage.getByRole('button', { name: '发送消息', exact: true }).click()
    await expect(userPage.getByText('备用服务已接管并完成回复。')).toBeVisible({
      timeout: 15_000,
    })
    await expect(userPage.getByText('正在生成')).toHaveCount(0)
    await expect(userPage).toHaveTitle('AI 对话 · iTime')
    expect(await userPage.locator('vite-error-overlay').count()).toBe(0)
    expect(await userPage.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    )).toBe(true)
    expect((await new AxeBuilder({ page: userPage }).analyze()).violations)
      .toEqual([])
    await screenshot(userPage, info, 'fallback-user-response')
    expect(runtimeErrors.get(userPage)).toEqual([])
  } finally {
    await userContext.close()
  }

  await page.goto('/admin/costs')
  await page.getByLabel('用户邮箱', { exact: true }).fill(userEmail)
  await page.getByRole('button', { name: '筛选', exact: true }).click()
  const costList = page.getByRole('list', { name: 'AI 供应商成本' })
  await expect(costList.getByRole('listitem')).toHaveCount(2)
  const primaryCost = costList.getByRole('listitem').filter({
    hasText: backupModels[0],
  })
  const fallbackCost = costList.getByRole('listitem').filter({
    hasText: chatModel,
  })
  await expect(primaryCost).toContainText('等待重试')
  await expect(primaryCost).toContainText('尝试 1')
  await expect(fallbackCost).toContainText('成功')
  await expect(fallbackCost).toContainText('尝试 2')
  await screenshot(page, info, 'fallback-provider-costs')

  if (info.project.name === 'desktop') {
    await page.evaluate(() => localStorage.setItem(
      'itime-appearance',
      JSON.stringify({ theme: 'dark', fontSize: 'larger' }),
    ))
    await page.setViewportSize({ width: 320, height: 700 })
    await page.goto('/admin/ai')
    await page.locator('.ai-capability-switch label').filter({
      hasText: '全局对话',
    }).click()
    await expect(page.locator('.ai-route-primary')).toContainText(backupModels[0])
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
    await expect(page.locator('html')).toHaveAttribute('data-font-size', 'larger')
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
      .toBe(true)
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
    await screenshot(page, info, 'chat-fallback-route-320-large-dark')
    await page.locator('.ai-capability-switch label').filter({
      hasText: '语音转写',
    }).click()
    await expect(page.getByRole('heading', { name: '语音转写配置' })).toBeVisible()
    await expect(page.getByLabel('音频价格（每分钟）')).toBeVisible()
    await expect(page.getByLabel('最大输出 Token')).toHaveCount(0)
    expect(await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    )).toBe(true)
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
    await screenshot(page, info, 'transcription-config-320-large-dark')
  }
})
