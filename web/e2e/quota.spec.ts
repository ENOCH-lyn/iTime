import AxeBuilder from '@axe-core/playwright'
import { expect, test, type APIRequestContext, type Page, type TestInfo } from '@playwright/test'
import { createHmac, randomUUID } from 'node:crypto'
import { mkdir, readFile } from 'node:fs/promises'

const evidence = '/tmp/itime-ui-evidence'
const password = 'Valid-quota-ui-password-123'
const runtimeErrors = new WeakMap<Page, string[]>()

function parseNanos(value: string) {
  const [whole, fraction = ''] = value.split('.')
  return BigInt(whole) * 1_000_000_000n
    + BigInt(fraction.padEnd(9, '0'))
}

function formatNanos(value: bigint) {
  const whole = value / 1_000_000_000n
  const fraction = (value % 1_000_000_000n)
    .toString()
    .padStart(9, '0')
    .replace(/0+$/, '')
  return `${whole}${fraction ? `.${fraction}` : ''}`
}

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

async function loginAdmin(page: Page, admin: ActiveAdmin, adminPassword: string) {
  await page.goto('/admin/login')
  await page.getByLabel('管理员邮箱', { exact: true }).fill(admin.email)
  await page.getByLabel('密码', { exact: true }).fill(adminPassword)
  await page.getByLabel('6 位验证码', { exact: true }).fill(totp(admin.secret))
  await page.getByRole('button', { name: '进入管理后台', exact: true }).click()
  await expect(page.getByRole('heading', { name: '账号安全' })).toBeVisible()
}

async function screenshot(page: Page, info: TestInfo, name: string) {
  await mkdir(evidence, { recursive: true })
  await page.screenshot({
    path: `${evidence}/${info.project.name}-quota-${name}.png`,
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
  const address = `quota-ui-${randomUUID()}@example.com`
  await page.goto('/register')
  await page.getByLabel('邮箱', { exact: true }).fill(address)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByLabel('确认密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '创建账号', exact: true }).click()
  await page.goto(await mailLink(request, address))
  await expect(page).toHaveURL('/ai')
  return address
}

async function newConversation(page: Page) {
  await page.getByRole('button', { name: '新建会话', exact: true }).click()
  await expect(
    page.getByRole('heading', { name: '新对话', exact: true }),
  ).toBeVisible()
}

async function send(page: Page, content: string) {
  await page.getByLabel('消息', { exact: true }).fill(content)
  await page.getByRole('button', { name: '发送消息', exact: true }).click()
  await expect(
    page.getByRole('list', { name: '会话消息' })
      .getByText(content, { exact: true }),
  ).toBeVisible()
}

test.beforeEach(async ({ page }) => {
  const errors: string[] = []
  runtimeErrors.set(page, errors)
  page.on('pageerror', error => errors.push(error.message))
  page.on('console', message => {
    if (
      ['error', 'warning'].includes(message.type())
      && !message.text().includes('Failed to load resource')
    ) {
      errors.push(message.text())
    }
  })
  page.on('response', response => {
    if (
      response.url().includes('/api/v1/')
      && response.status() >= 500
    ) {
      errors.push(`${response.status()} ${response.url()}`)
    }
  })
})

test.afterEach(async ({ page }) => {
  expect(runtimeErrors.get(page)).toEqual([])
})

test('用户额度显示成功扣额并在取消后释放预留', async ({
  page,
  request,
}, info) => {
  await account(page, request)
  let firstQuotaRead = true
  let savedBrowserTimeZone = false
  await page.route('**/api/v1/quota', async route => {
    if (firstQuotaRead && route.request().method() === 'GET') {
      firstQuotaRead = false
      await route.fulfill({
        status: 409,
        contentType: 'application/json',
        body: JSON.stringify({
          error: {
            code: 'time_settings_required',
            message: '请先保存当前使用的时区，再查看 AI 额度。',
            request_id: randomUUID(),
            saved: 'not_saved',
            retryable: false,
            details: {},
          },
        }),
      })
      return
    }
    await route.continue()
  })
  await page.route('**/api/v1/time-settings', async route => {
    if (route.request().method() === 'PUT') {
      savedBrowserTimeZone = true
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          settings: {
            initialized: true,
            time_zone: 'Asia/Shanghai',
            week_starts_on: 1,
            activity_day_start_minute: 240,
            version: 1,
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          },
        }),
      })
      return
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        settings: {
          initialized: false,
          time_zone: null,
          week_starts_on: 1,
          activity_day_start_minute: 240,
          version: 0,
          created_at: null,
          updated_at: null,
        },
      }),
    })
  })
  await page.goto('/account/quota')
  await expect(page).toHaveTitle('AI 额度 · iTime')
  await expect(page.getByRole('heading', { name: 'AI 额度' })).toBeVisible()
  await expect.poll(() => savedBrowserTimeZone).toBe(true)
  await page.unroute('**/api/v1/quota')
  await page.unroute('**/api/v1/time-settings')
  const initialBase = (
    await page.getByTestId('quota-base').innerText()
  ).replace(/\s+/g, '')
  const initialBaseAmount = initialBase.replace(/^CNY/, '')
  await expect(page.getByTestId('quota-available')).toHaveText(initialBase)
  await expect(page.getByTestId('quota-charged')).toHaveText(/CNY\s*0$/)
  await expect(page.getByTestId('quota-reserved')).toHaveText(/CNY\s*0$/)
  await expect(page.getByTestId('quota-base')).toHaveText(initialBase)
  await expect(page.getByText('当前没有处理中的额度预留。')).toBeVisible()
  await expect(page.getByText('暂无额度账本记录。')).toBeVisible()

  await page.goto('/ai')
  await newConversation(page)
  await send(page, '额度成功结算')
  await expect(
    page.getByRole('list', { name: '会话消息' })
      .getByText('然后为第一项留出一段完整时间。'),
  ).toBeVisible()
  await expect(page.getByText('正在生成')).toHaveCount(0)

  await page.goto('/account/quota')
  const chargedAmount = (
    await page.getByTestId('quota-charged').innerText()
  ).replace(/\s+/g, '').replace(/^CNY/, '')
  expect(parseNanos(chargedAmount)).toBeGreaterThan(0n)
  await expect.poll(async () => (
    await page.getByTestId('quota-available').innerText()
  ).replace(/\s+/g, '')).toBe(
    `CNY${formatNanos(parseNanos(initialBaseAmount) - parseNanos(chargedAmount))}`,
  )
  const ledger = page.getByRole('list', { name: '额度账本记录' })
  await expect(ledger.getByRole('listitem')).toHaveCount(1)
  await expect(ledger).toContainText('AI 使用')
  await expect(ledger).toContainText('供应商上报')
  await expect(ledger).toContainText(`CNY -${chargedAmount}`)

  await page.goto('/ai')
  await newConversation(page)
  await send(page, '取消测试')
  const conversationUrl = page.url()
  await page.goto('/account/quota')
  await expect.poll(async () =>
    page.getByTestId('quota-reserved').textContent(),
  ).not.toMatch(/CNY\s*0$/)
  await expect(
    page.getByRole('list', { name: '处理中额度预留' }).getByRole('listitem'),
  ).toHaveCount(1)

  await page.goto(conversationUrl)
  await expect(
    page.getByRole('list', { name: '会话消息' })
      .getByText('已收到这条消息。', { exact: true }),
  ).toBeVisible()
  await page.getByRole('button', { name: '取消生成', exact: true }).click()
  await expect(page.getByText('已取消，以上内容可能不完整')).toBeVisible()

  await page.goto('/account/quota')
  await expect(page.getByTestId('quota-reserved')).toHaveText(/CNY\s*0$/)
  await expect(page.getByTestId('quota-charged')).toHaveText(
    new RegExp(`CNY\\s*${chargedAmount.replace('.', '\\.')}$`),
  )
  await expect(page.getByRole('list', { name: '额度账本记录' })
    .getByRole('listitem')).toHaveCount(1)

  await page.goto('/ai')
  await newConversation(page)
  await send(page, '触发失败')
  await page.goto('/account/quota')
  await expect.poll(async () =>
    page.getByTestId('quota-reserved').textContent(),
  ).not.toMatch(/CNY\s*0$/)
  await expect.poll(async () => {
    const response = await page.request.get('/api/v1/quota')
    return (await response.json()).current.reserved
  }, { timeout: 20_000 }).toBe('0')
  await page.reload()
  await expect(page.getByTestId('quota-reserved')).toHaveText(/CNY\s*0$/)
  await expect(page.getByTestId('quota-charged')).toHaveText(
    new RegExp(`CNY\\s*${chargedAmount.replace('.', '\\.')}$`),
  )
  await expect(page.getByRole('list', { name: '额度账本记录' })
    .getByRole('listitem')).toHaveCount(1)

  await page.getByLabel('记录类型').selectOption('usage_charge')
  await page.getByRole('button', { name: '筛选', exact: true }).click()
  await expect(ledger.getByRole('listitem')).toHaveCount(1)
  expect(await page.evaluate(
    () => document.documentElement.scrollWidth <= innerWidth,
  )).toBe(true)
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  await screenshot(page, info, 'user-ledger')

  if (info.project.name === 'desktop') {
    await page.evaluate(() => localStorage.setItem(
      'itime-appearance',
      JSON.stringify({ theme: 'dark', fontSize: 'larger' }),
    ))
    await page.setViewportSize({ width: 320, height: 700 })
    await page.reload()
    await expect(page.getByRole('heading', { name: 'AI 额度' })).toBeVisible()
    expect(await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    )).toBe(true)
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
    await screenshot(page, info, 'user-320-large-dark')
  }
})

test('后台发布额度策略、调整用户并完成未知结果对账', async ({
  page,
  browser,
  request,
}, info) => {
  const userContext = await browser.newContext({
    baseURL: 'http://localhost:4173',
  })
  const nextPolicyAmount = {
    desktop: '1.25',
    mobile: '1.5',
    tablet: '1.75',
  }[info.project.name] ?? '2'
  try {
    const userPage = await userContext.newPage()
    const email = await account(userPage, request)
    const data = await bootstrap(info)
    await loginAdmin(page, data.active.quota!, data.password)
    await page.goto('/admin/quota')
    await expect(page).toHaveTitle('AI 额度 · iTime 管理后台')
    const policyTab = page.getByRole('tab', { name: '策略' })
    await expect(policyTab).toHaveAttribute(
      'aria-selected',
      'true',
    )

    await policyTab.focus()
    await policyTab.press('ArrowRight')
    const adjustmentTab = page.getByRole('tab', { name: '用户调整' })
    await expect(adjustmentTab).toBeFocused()
    await expect(adjustmentTab).toHaveAttribute('aria-selected', 'true')
    await page.getByLabel('用户邮箱', { exact: true }).fill(email)
    await page.getByRole('button', { name: '搜索', exact: true }).click()
    const userRow = page.getByRole('list', { name: '额度用户搜索结果' })
      .getByRole('listitem')
      .filter({ hasText: email })
    await expect(userRow).toHaveCount(1)
    await userRow.getByRole('button', { name: '选择', exact: true }).click()
    const baseBefore = (
      await page.getByTestId('admin-quota-base').innerText()
    ).replace(/\s+/g, '')
    await expect(page.getByTestId('admin-quota-available')).toHaveText(
      baseBefore,
    )

    await page.getByLabel('调整金额', { exact: true }).fill('0.100000001')
    await page.getByLabel('调整原因', { exact: true }).fill('浏览器未知结果补偿')
    const reviewAdjustment = page.getByRole('button', {
      name: '核对并调整',
      exact: true,
    })
    await expect(reviewAdjustment).toBeEnabled()
    await reviewAdjustment.click()
    const adjustmentDialog = page.getByRole('dialog', { name: '确认额度调整' })
    await expect(adjustmentDialog).toContainText('本次变化')
    await expect(adjustmentDialog).toContainText('+0.100000001')

    let savedBeforeAbort = false
    await page.route('**/api/v1/admin/quota/users/*/adjustments', async route => {
      const response = await route.fetch()
      expect(response.status()).toBe(201)
      savedBeforeAbort = true
      await route.abort('failed')
    }, { times: 1 })
    await adjustmentDialog.getByRole('button', {
      name: '确认调整',
      exact: true,
    }).click()
    await expect(page.getByText('已从账本确认额度调整保存成功。')).toBeVisible()
    expect(savedBeforeAbort).toBe(true)
    await expect(page.getByTestId('admin-quota-available')).not.toHaveText(
      baseBefore,
    )

    await page.getByRole('button', { name: '查看此用户账本' }).click()
    const adminLedger = page.getByRole('list', { name: '用户额度账本' })
    await expect(adminLedger).toContainText(email)
    await expect(adminLedger).toContainText('浏览器未知结果补偿')
    await expect(adminLedger).toContainText('管理员增加')

    await page.getByRole('tab', { name: '策略' }).click()
    await page.getByLabel('每日基础额度').fill(nextPolicyAmount)
    await page.getByRole('button', { name: '保存新版本', exact: true }).click()
    await expect(page.getByRole('status')).toContainText('已保存为草稿')
    const policyRow = page.getByRole('list', { name: '额度策略历史' })
      .getByRole('listitem')
      .filter({ hasText: `CNY ${nextPolicyAmount} / 活动日` })
      .first()
    await expect(policyRow.getByText('草稿', { exact: true })).toBeVisible()
    await policyRow.getByRole('button', { name: '发布', exact: true }).click()
    const policyDialog = page.getByRole('dialog', { name: '发布此额度策略？' })
    await expect(policyDialog).toContainText(`CNY ${nextPolicyAmount}`)
    await policyDialog.getByRole('button', { name: '确认发布' }).click()
    await expect(page.getByRole('status')).toContainText('只影响新活动日')
    await expect(policyRow.getByText('当前发布', { exact: true })).toBeVisible()

    await page.getByRole('tab', { name: '用户调整' }).click()
    const adjustmentSection = page.getByRole('region', {
      name: '调整活动日额度',
    })
    await adjustmentSection.getByRole('button', {
      name: '刷新',
      exact: true,
    }).click()
    await expect(page.getByTestId('admin-quota-base')).toHaveText(baseBefore)

    const newUserContext = await browser.newContext({
      baseURL: 'http://localhost:4173',
    })
    try {
      const newUserPage = await newUserContext.newPage()
      await account(newUserPage, request)
      await newUserPage.goto('/account/quota')
      await expect(newUserPage.getByTestId('quota-base')).toHaveText(
        new RegExp(`CNY\\s*${nextPolicyAmount}$`),
      )
    } finally {
      await newUserContext.close()
    }

    const availableText = (
      await page.getByTestId('admin-quota-available').innerText()
    ).replace(/^CNY\s*/, '')
    await page.locator('.quota-adjustment-mode label').filter({
      hasText: '扣减',
    }).click()
    await page.getByLabel('调整金额', { exact: true }).fill(availableText)
    await page.getByLabel('调整原因', { exact: true }).fill('额度用尽验证')
    await page.getByRole('button', { name: '核对并调整', exact: true }).click()
    await expect(adjustmentDialog).toContainText('调整后可用')
    await expect(adjustmentDialog).toContainText(/CNY\s+0/)
    await adjustmentDialog.getByRole('button', {
      name: '确认调整',
      exact: true,
    }).click()
    await expect(page.getByRole('status')).toContainText('额度调整已保存')
    await expect(page.getByTestId('admin-quota-available')).toHaveText(
      /CNY\s*0$/,
    )

    await userPage.goto('/account/quota')
    await expect(userPage.getByTestId('quota-base')).toHaveText(baseBefore)
    await expect(userPage.getByTestId('quota-available')).toHaveText(/CNY\s*0$/)
    await userPage.goto('/ai')
    await newConversation(userPage)
    await userPage.getByLabel('消息', { exact: true }).fill('额度不足验证')
    await userPage.getByRole('button', {
      name: '发送消息',
      exact: true,
    }).click()
    await expect(userPage.getByRole('alert')).toContainText(
      '今日 AI 额度不足，本次请求未接收。',
    )

    await page.getByRole('button', { name: '查看此用户账本' }).click()
    await expect(adminLedger.getByRole('listitem')).toHaveCount(2)
    await page.getByLabel('类型', { exact: true }).selectOption('admin_debit')
    await page.getByRole('button', { name: '筛选', exact: true }).click()
    await expect(adminLedger.getByRole('listitem')).toHaveCount(1)
    await expect(adminLedger).toContainText('额度用尽验证')

    expect(await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    )).toBe(true)
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
    await screenshot(page, info, 'admin-ledger')

    if (info.project.name === 'desktop') {
      await page.evaluate(() => localStorage.setItem(
        'itime-appearance',
        JSON.stringify({ theme: 'dark', fontSize: 'larger' }),
      ))
      await page.setViewportSize({ width: 320, height: 700 })
      await page.reload()
      await expect(page.getByRole('heading', { name: 'AI 额度' })).toBeVisible()
      expect(await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      )).toBe(true)
      expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
      await screenshot(page, info, 'admin-320-large-dark')
    }
  } finally {
    await userContext.close()
  }
})
