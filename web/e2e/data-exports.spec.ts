import { execFile } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { mkdir } from 'node:fs/promises'
import { promisify } from 'node:util'
import AxeBuilder from '@axe-core/playwright'
import { expect, test, type APIRequestContext, type Page, type TestInfo } from '@playwright/test'

const password = 'Valid-data-export-password-123'
const runtimeErrors = new WeakMap<Page, string[]>()
const execFileAsync = promisify(execFile)

async function screenshot(page: Page, info: TestInfo, name: string) {
  await mkdir('/tmp/itime-ui-evidence', { recursive: true })
  await page.screenshot({
    path: `/tmp/itime-ui-evidence/${info.project.name}-data-export-${name}.png`,
    fullPage: true,
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
  const address = `data-export-ui-${randomUUID()}@example.com`
  await page.goto('/register')
  await page.getByLabel('邮箱', { exact: true }).fill(address)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByLabel('确认密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '创建账号', exact: true }).click()
  await page.goto(await mailLink(request, address))
  await expect(page).toHaveURL('/ai')
  const session = await (await page.request.get('/api/v1/auth/me')).json()
  return session.csrf_token as string
}

async function createTask(page: Page, csrf: string, marker: string) {
  const response = await page.request.post('/api/v1/tasks', {
    headers: { 'X-CSRF-Token': csrf },
    data: {
      operation_id: randomUUID(),
      title: marker,
      description: '用于验证完整数据包中的真实任务。',
      project_id: null,
      estimated_minutes: 45,
      priority: 2,
      labels: ['导出验证'],
      links: [],
      steps: [{ title: '解压并核对 JSON' }],
    },
  })
  expect(response.ok()).toBeTruthy()
}

async function unzipJson(path: string, entry: string) {
  const result = await execFileAsync('unzip', ['-p', path, entry], {
    encoding: 'utf8',
    maxBuffer: 20 * 1024 * 1024,
  })
  return JSON.parse(result.stdout)
}

function watchRuntime(page: Page) {
  const errors: string[] = []
  runtimeErrors.set(page, errors)
  page.on('pageerror', error => errors.push(error.message))
  page.on('console', message => {
    if (
      message.type() === 'error'
      && !message.text().includes('Failed to load resource')
    ) errors.push(message.text())
  })
  page.on('response', response => {
    if (response.url().includes('/api/v1/') && response.status() >= 500) {
      errors.push(`${response.status()} ${response.url()}`)
    }
  })
}

async function pageHealth(page: Page) {
  await expect(page).toHaveTitle('数据管理 · iTime')
  await expect(page.getByRole('heading', { name: '数据管理' })).toBeVisible()
  expect(await page.locator('vite-error-overlay').count()).toBe(0)
  expect(await page.evaluate(
    () => document.documentElement.scrollWidth <= window.innerWidth,
  )).toBe(true)
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
}

test.beforeEach(async ({ page }) => watchRuntime(page))
test.afterEach(async ({ page }) => {
  expect(runtimeErrors.get(page)).toEqual([])
})

test('data export survives unknown create response and downloads verified zip', async ({
  page,
  request,
}, info) => {
  const csrf = await account(page, request)
  const marker = `导出验证任务 ${randomUUID()}`
  await createTask(page, csrf, marker)
  await page.goto('/account/data')
  await expect(page.getByText('还没有生成过数据包。')).toBeVisible()

  let interceptCreate = true
  await page.route('**/api/v1/data-exports', async route => {
    if (interceptCreate && route.request().method() === 'POST') {
      interceptCreate = false
      await route.fetch()
      await route.abort('connectionreset')
      return
    }
    await route.continue()
  })
  await page.getByRole('button', { name: '生成完整数据包', exact: true }).click()
  await expect(page.getByRole('status')).toContainText(
    /已从服务器确认数据包(正在生成|生成成功)/,
  )
  await page.unroute('**/api/v1/data-exports')
  const row = page.getByRole('listitem').filter({ hasText: '完整数据包' }).first()
  await expect(row.getByText('可以下载', { exact: true })).toBeVisible({
    timeout: 20_000,
  })
  await page.reload()
  await expect(row.getByText('可以下载', { exact: true })).toBeVisible()

  const downloadPromise = page.waitForEvent('download')
  await row.getByRole('button', { name: '下载 ZIP', exact: true }).click()
  const download = await downloadPromise
  expect(download.suggestedFilename()).toMatch(
    /^itime-export-\d{8}-\d{6}Z\.zip$/,
  )
  const path = await download.path()
  expect(path).not.toBeNull()
  const exported = await unzipJson(path!, 'itime-data.json')
  const manifest = await unzipJson(path!, 'manifest.json')
  expect(exported.tasks.tasks).toContainEqual(expect.objectContaining({
    title: marker,
  }))
  expect(manifest.files['itime-data.json'].sha256).toMatch(/^[a-f0-9]{64}$/)
  const serialized = JSON.stringify(exported)
  expect(serialized).not.toMatch(/password_hash|token_hash|api_key_ciphertext/)
  expect(await page.evaluate(
    marker => JSON.stringify({ ...localStorage, ...sessionStorage }).includes(marker),
    marker,
  )).toBe(false)

  let failList = true
  await page.route('**/api/v1/data-exports?limit=20', async route => {
    if (failList) {
      failList = false
      await route.abort('connectionreset')
      return
    }
    await route.continue()
  })
  await page.getByRole('button', { name: '刷新导出记录' }).click()
  await expect(page.getByRole('alert')).toContainText('无法连接服务')
  await expect(row.getByText('可以下载', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '重新读取' }).click()
  await expect(page.getByRole('alert')).toHaveCount(0)
  await page.unroute('**/api/v1/data-exports?limit=20')

  await pageHealth(page)
  await screenshot(page, info, 'ready')

  if (info.project.name === 'mobile') {
    await page.evaluate(() => {
      localStorage.setItem('itime-appearance', JSON.stringify({
        theme: 'dark',
        fontSize: 'larger',
      }))
    })
    await page.setViewportSize({ width: 320, height: 700 })
    await page.reload()
    await expect(row.getByText('可以下载', { exact: true })).toBeVisible()
    await pageHealth(page)
    await screenshot(page, info, '320-large-dark')
  }
})

test('data export empty state and settings navigation remain accessible', async ({
  page,
  request,
}) => {
  await account(page, request)
  await page.goto('/account/data')
  await expect(page.getByRole('link', { name: '数据管理', exact: true }))
    .toHaveAttribute('aria-current', 'page')
  await expect(page.getByText('还没有生成过数据包。')).toBeVisible()
  await expect(page.getByRole('button', { name: '生成完整数据包' })).toBeEnabled()
  await pageHealth(page)
})
