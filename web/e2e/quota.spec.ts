import AxeBuilder from '@axe-core/playwright'
import { expect, test, type APIRequestContext, type Page, type TestInfo } from '@playwright/test'
import { randomUUID } from 'node:crypto'
import { mkdir } from 'node:fs/promises'

const evidence = '/tmp/itime-ui-evidence'
const password = 'Valid-quota-ui-password-123'
const runtimeErrors = new WeakMap<Page, string[]>()

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
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '验证邮箱', exact: true }).click()
  await page.goto('/login')
  await page.getByLabel('邮箱', { exact: true }).fill(address)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL('/ai')
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
  await expect(page.getByTestId('quota-available')).toHaveText(/CNY\s*1$/)
  await expect(page.getByTestId('quota-charged')).toHaveText(/CNY\s*0$/)
  await expect(page.getByTestId('quota-reserved')).toHaveText(/CNY\s*0$/)
  await expect(page.getByTestId('quota-base')).toHaveText(/CNY\s*1$/)
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
  await expect(page.getByTestId('quota-charged')).toHaveText(
    /CNY\s*0\.000135$/,
  )
  await expect(page.getByTestId('quota-available')).toHaveText(
    /CNY\s*0\.999865$/,
  )
  const ledger = page.getByRole('list', { name: '额度账本记录' })
  await expect(ledger.getByRole('listitem')).toHaveCount(1)
  await expect(ledger).toContainText('AI 使用')
  await expect(ledger).toContainText('供应商上报')
  await expect(ledger).toContainText('CNY -0.000135')

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
    /CNY\s*0\.000135$/,
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
    /CNY\s*0\.000135$/,
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
