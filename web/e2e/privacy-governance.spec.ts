import AxeBuilder from '@axe-core/playwright'
import { expect, test, type APIRequestContext, type Page, type TestInfo } from '@playwright/test'
import { createHmac, randomUUID } from 'node:crypto'
import { mkdir, readFile } from 'node:fs/promises'

const evidence = '/tmp/itime-ui-evidence'
const password = 'Valid-privacy-ui-password-123'
const runtimeErrors = new WeakMap<Page, string[]>()

interface ActiveAdmin {
  email: string
  secret: string
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
  const address = `privacy-ui-${randomUUID()}@example.com`
  await page.goto('/register')
  await page.getByLabel('邮箱', { exact: true }).fill(address)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByLabel('确认密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '创建账号', exact: true }).click()
  await page.goto(await mailLink(request, address))
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '验证邮箱', exact: true }).click()
  await page.goto('/login')
  await page.getByLabel('邮箱', { exact: true }).fill(address)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL('/ai')
  return address
}

async function loginAdmin(page: Page, admin: ActiveAdmin, adminPassword: string) {
  await page.goto('/admin/login')
  await page.getByLabel('管理员邮箱', { exact: true }).fill(admin.email)
  await page.getByLabel('密码', { exact: true }).fill(adminPassword)
  await page.getByLabel('6 位验证码', { exact: true }).fill(totp(admin.secret))
  await page.getByRole('button', { name: '进入管理后台', exact: true }).click()
  await expect(page.getByRole('heading', { name: '账号安全' })).toBeVisible()
}

async function screenshot(
  page: Page,
  info: TestInfo,
  name: string,
  fullPage = true,
) {
  await mkdir(evidence, { recursive: true })
  await page.screenshot({
    path: `${evidence}/${info.project.name}-privacy-governance-${name}.png`,
    fullPage,
  })
}

function wavBuffer(durationMs = 500) {
  const sampleRate = 16_000
  const samples = Math.floor(sampleRate * durationMs / 1000)
  const dataBytes = samples * 2
  const buffer = Buffer.alloc(44 + dataBytes)
  buffer.write('RIFF', 0, 'ascii')
  buffer.writeUInt32LE(36 + dataBytes, 4)
  buffer.write('WAVE', 8, 'ascii')
  buffer.write('fmt ', 12, 'ascii')
  buffer.writeUInt32LE(16, 16)
  buffer.writeUInt16LE(1, 20)
  buffer.writeUInt16LE(1, 22)
  buffer.writeUInt32LE(sampleRate, 24)
  buffer.writeUInt32LE(sampleRate * 2, 28)
  buffer.writeUInt16LE(2, 32)
  buffer.writeUInt16LE(16, 34)
  buffer.write('data', 36, 'ascii')
  buffer.writeUInt32LE(dataBytes, 40)
  return buffer
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
  page.on('response', response => {
    if (response.url().includes('/api/v1/') && response.status() >= 500) {
      errors.push(`${response.status()} ${response.url()}`)
    }
  })
})

test.afterEach(async ({ page }) => {
  expect(runtimeErrors.get(page)).toEqual([])
})

test('用户可关闭、恢复并在网络失败后对账质量改进设置', async ({
  page,
  request,
}, info) => {
  await account(page, request)
  await page.goto('/account/privacy')
  await expect(page).toHaveTitle('隐私与质量 · iTime')
  const qualitySwitch = page.getByRole('switch', { name: '参与质量改进' })
  await expect(qualitySwitch).toBeChecked()
  await qualitySwitch.uncheck()
  await page.getByRole('button', { name: '保存隐私设置', exact: true }).click()
  await expect(page.getByText('已退出质量改进；后续 AI 内容不会新增质量样本。')).toBeVisible()
  await page.reload()
  await expect(qualitySwitch).not.toBeChecked()

  await qualitySwitch.check()
  await page.route('**/api/v1/privacy-settings', async route => {
    if (route.request().method() === 'PUT') await route.abort('failed')
    else await route.continue()
  })
  await page.getByRole('button', { name: '保存隐私设置', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('无法连接服务')
  await expect(page.getByRole('alert')).toContainText('保存状态尚未确认')
  await expect(qualitySwitch).toBeChecked()
  await page.unroute('**/api/v1/privacy-settings')
  await page.getByRole('button', { name: '保存隐私设置', exact: true }).click()
  await expect(page.getByText('已参与质量改进；后续符合条件的 AI 内容可以进入质量样本。')).toBeVisible()
  await page.reload()
  await expect(qualitySwitch).toBeChecked()

  expect(await page.locator('vite-error-overlay').count()).toBe(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  await screenshot(page, info, 'privacy-setting')
})

test('管理员显式读取 AI 正文、提交质检并回查审计', async ({
  page,
  request,
}, info) => {
  const address = await account(page, request)
  await page.getByRole('button', { name: '新建会话', exact: true }).click()
  const prompt = `治理审计 ${randomUUID()}`
  await page.getByLabel('消息', { exact: true }).fill(prompt)
  await page.getByRole('button', { name: '发送消息', exact: true }).click()
  await expect(page.getByRole('list', { name: '会话消息' })).toContainText(
    '然后为第一项留出一段完整时间。',
  )
  await expect(page.getByText('正在生成')).toHaveCount(0)

  const data = await bootstrap(info)
  await loginAdmin(page, data.active.governance!, data.password)
  await page.goto('/admin/costs')
  await expect(page).toHaveTitle('供应商成本 · iTime 管理后台')
  await page.getByLabel('用户邮箱', { exact: true }).fill(address)
  await page.getByRole('button', { name: '筛选', exact: true }).click()
  const costRow = page.getByRole('list', { name: 'AI 供应商成本' })
    .getByRole('listitem').filter({ hasText: address })
  await expect(costRow).toContainText('供应商上报')
  await expect(costRow.locator('.cost-amount')).toHaveText(
    /^CNY 0\.[0-9]+$/,
  )
  await expect(costRow).toContainText(
    /输入 100\s*（缓存 10）\s*· 输出 20 Token/,
  )
  await expect(page.locator('body')).not.toContainText(prompt)
  await screenshot(page, info, 'provider-costs')

  await page.goto('/admin/logs')
  await expect(page).toHaveTitle('AI 日志 · iTime 管理后台')
  await page.getByLabel('用户邮箱', { exact: true }).fill(address)
  await page.getByRole('button', { name: '筛选', exact: true }).click()
  const logRow = page.getByRole('list', { name: 'AI 技术正文日志' })
    .getByRole('listitem').filter({ hasText: address })
  await expect(logRow).toBeVisible()
  await expect(page.locator('body')).not.toContainText(prompt)
  await logRow.getByRole('button', { name: '查看正文', exact: true }).click()
  const logDialog = page.getByRole('dialog', { name: 'AI 技术正文' })
  await expect(logDialog).toBeVisible()
  await expect(logDialog).not.toContainText(prompt)
  await logDialog.getByRole('button', { name: '审计并读取正文', exact: true }).click()
  await expect(logDialog).toContainText(prompt)
  await expect(logDialog).toContainText('先把今天最重要的事情列出来。')
  await screenshot(page, info, 'body-log-detail', false)
  await logDialog.locator('.dialog-actions').getByRole('button', { name: '关闭' }).click()

  await page.goto('/admin/quality')
  await expect(page).toHaveTitle('质量样本 · iTime 管理后台')
  await page.getByLabel('用户邮箱', { exact: true }).fill(address)
  await page.getByRole('button', { name: '筛选', exact: true }).click()
  const sampleRow = page.getByRole('list', { name: 'AI 质量样本' })
    .getByRole('listitem').filter({ hasText: address })
  await expect(sampleRow).toContainText('待质检')
  await sampleRow.getByRole('button', { name: '查看并质检', exact: true }).click()
  const qualityDialog = page.getByRole('dialog', { name: 'AI 质量样本' })
  await qualityDialog.getByLabel('本次访问用途').selectOption('manual_quality_review')
  await qualityDialog.getByRole('button', { name: '审计并读取正文', exact: true }).click()
  await expect(qualityDialog).toContainText(prompt)
  await qualityDialog.locator('.segmented-field label').filter({
    hasText: '需改进',
  }).click()
  await qualityDialog.getByLabel('评审备注（可选）').fill('回答需要更明确地区分事实与建议。')
  await qualityDialog.getByRole('button', { name: '保存质检结论', exact: true }).click()
  await expect(qualityDialog.getByText('质检结论已保存。')).toBeVisible()
  await screenshot(page, info, 'quality-reviewed', false)
  await qualityDialog.locator('.dialog-actions').getByRole('button', { name: '关闭' }).click()
  await expect(sampleRow).toContainText('需改进')

  await page.goto('/admin/audit')
  await expect(page).toHaveTitle('审计记录 · iTime 管理后台')
  await page.getByLabel('操作类型').selectOption('ai_body_log_accessed')
  await page.getByRole('button', { name: '筛选', exact: true }).click()
  const auditList = page.getByRole('list', { name: '管理审计记录' })
  await expect(auditList).toContainText('技术正文已读取')
  await expect(auditList).toContainText('技术排障')
  await page.getByLabel('操作类型').selectOption('ai_quality_sample_reviewed')
  await page.getByRole('button', { name: '筛选', exact: true }).click()
  await expect(auditList).toContainText('质量样本已评审')

  expect(await page.locator('vite-error-overlay').count()).toBe(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  await screenshot(page, info, 'audit-list')

  if (info.project.name === 'desktop') {
    await page.evaluate(() => localStorage.setItem(
      'itime-appearance',
      JSON.stringify({ theme: 'dark', fontSize: 'larger' }),
    ))
    await page.setViewportSize({ width: 320, height: 700 })
    await page.goto('/admin/quality')
    await expect(page.getByRole('heading', { name: '质量样本' })).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
    await screenshot(page, info, 'quality-320-large-dark')
  }
})

test('管理员可按语音转写筛选音频成本与治理内容', async ({
  page,
  request,
}, info) => {
  const address = await account(page, request)
  const auth = await (await page.request.get('/api/v1/auth/me')).json()
  const uploaded = await page.request.post(
    '/api/v1/voice-transcriptions?purpose=task_title&filename=voice.wav',
    {
      headers: {
        'Content-Type': 'audio/wav',
        'X-CSRF-Token': auth.csrf_token,
        'X-Operation-ID': randomUUID(),
      },
      data: wavBuffer(),
    },
  )
  expect(uploaded.status()).toBe(202)
  const accepted = await uploaded.json()
  await expect.poll(async () => {
    const response = await page.request.get(
      `/api/v1/voice-transcriptions/${accepted.transcription.id}`,
    )
    return (await response.json()).job.status
  }, { timeout: 15_000 }).toBe('succeeded')

  const data = await bootstrap(info)
  await loginAdmin(
    page,
    data.active['governance-transcription']!,
    data.password,
  )
  await page.goto('/admin/costs')
  await page.getByLabel('用户邮箱', { exact: true }).fill(address)
  await page.getByLabel('能力', { exact: true }).selectOption('transcription')
  await page.getByRole('button', { name: '筛选', exact: true }).click()
  const costRow = page.getByRole('list', { name: 'AI 供应商成本' })
    .getByRole('listitem').filter({ hasText: address })
  await expect(costRow).toContainText('语音转写')
  await expect(costRow).toContainText('平台实测')
  await expect(costRow).toContainText('音频 500 毫秒')
  await expect(costRow).toContainText('单价 0.006 / 分钟')
  await expect(costRow.locator('.cost-amount')).toHaveText('CNY 0.00005')

  await page.goto('/admin/logs')
  await page.getByLabel('用户邮箱', { exact: true }).fill(address)
  await page.getByLabel('能力', { exact: true }).selectOption('transcription')
  await page.getByRole('button', { name: '筛选', exact: true }).click()
  const logRow = page.getByRole('list', { name: 'AI 技术正文日志' })
    .getByRole('listitem').filter({ hasText: address })
  await expect(logRow).toContainText('语音转写')
  await logRow.getByRole('button', { name: '查看正文', exact: true }).click()
  const logDialog = page.getByRole('dialog', { name: 'AI 技术正文' })
  await logDialog.getByRole(
    'button',
    { name: '审计并读取正文', exact: true },
  ).click()
  await expect(logDialog).toContainText('omitted_by_retention_policy')
  await expect(logDialog).toContainText('周五前完成论文提纲')
  await expect(logDialog).not.toContainText('RIFF')
  await logDialog.locator('.dialog-actions')
    .getByRole('button', { name: '关闭' }).click()

  await page.goto('/admin/quality')
  await page.getByLabel('用户邮箱', { exact: true }).fill(address)
  await page.getByLabel('能力', { exact: true }).selectOption('transcription')
  await page.getByRole('button', { name: '筛选', exact: true }).click()
  const sampleRow = page.getByRole('list', { name: 'AI 质量样本' })
    .getByRole('listitem').filter({ hasText: address })
  await expect(sampleRow).toContainText('语音转写')
  await expect(sampleRow).toContainText('待质检')

  expect(await page.locator('vite-error-overlay').count()).toBe(0)
  expect(await page.evaluate(
    () => document.documentElement.scrollWidth <= innerWidth,
  )).toBe(true)
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  await screenshot(page, info, 'transcription-governance')
})
