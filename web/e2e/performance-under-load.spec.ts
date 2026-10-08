import AxeBuilder from '@axe-core/playwright'
import {
  expect,
  test,
  type Browser,
  type Page,
  type TestInfo,
} from '@playwright/test'
import { randomUUID } from 'node:crypto'
import { mkdir, readFile, stat } from 'node:fs/promises'

interface BrowserCredential {
  email: string
  password: string
  dataset: string
}

interface RunManifest {
  run_id: string
  scenario: string
  service_version: string
}

interface RuntimeObservation {
  errors: string[]
  apiServerErrors: string[]
}

const evidenceRoot = '/tmp/itime-ui-evidence'
const runtime = new WeakMap<Page, RuntimeObservation>()

function requiredEnvironment(name: string) {
  const value = process.env[name]
  if (!value) throw new Error(`缺少 ${name}`)
  return value
}

async function loadRunInputs() {
  const accountFile = requiredEnvironment('ITIME_PERF_BROWSER_ACCOUNT_FILE')
  const manifestFile = requiredEnvironment('ITIME_PERF_RUN_MANIFEST_FILE')
  const accountMode = (await stat(accountFile)).mode & 0o777
  expect(accountMode).toBe(0o600)

  const credentials = JSON.parse(
    await readFile(accountFile, 'utf8'),
  ) as BrowserCredential[]
  const credential = credentials.find(item => item.dataset === 'browser')
    ?? [...credentials].reverse().find(item => item.dataset === 'standard')
  if (
    !credential
    || typeof credential.email !== 'string'
    || typeof credential.password !== 'string'
  ) {
    throw new Error('性能账号文件没有可用的 standard 账号')
  }
  const manifest = JSON.parse(
    await readFile(manifestFile, 'utf8'),
  ) as RunManifest
  expect(manifest.scenario).toBe('ui-load')
  expect(manifest.run_id).toBe(requiredEnvironment('ITIME_PERF_RUN_ID'))
  expect(manifest.service_version).toMatch(/^\d+\.\d+\.\d+$/)
  return { credential, manifest }
}

function assertLoadAlive() {
  const value = requiredEnvironment('ITIME_PERF_LOCUST_PID')
  expect(value).toMatch(/^[1-9]\d*$/)
  process.kill(Number(value), 0)
}

function watchRuntime(page: Page) {
  const observation: RuntimeObservation = {
    errors: [],
    apiServerErrors: [],
  }
  runtime.set(page, observation)
  page.on('pageerror', error => observation.errors.push(error.message))
  page.on('console', message => {
    if (
      ['error', 'warning'].includes(message.type())
      && !message.text().includes('Failed to load resource')
    ) {
      observation.errors.push(`${message.type()}: ${message.text()}`)
    }
  })
  page.on('response', response => {
    if (
      response.url().includes('/api/')
      && response.status() >= 500
    ) {
      observation.apiServerErrors.push(
        `${response.status()} ${response.request().method()} ${response.url()}`,
      )
    }
  })
}

async function assertPageHealth(page: Page) {
  const observation = runtime.get(page)
  expect(observation?.errors ?? []).toEqual([])
  expect(observation?.apiServerErrors ?? []).toEqual([])
  expect(await page.locator('vite-error-overlay').count()).toBe(0)
  expect(await page.evaluate(
    () => document.documentElement.scrollWidth <= window.innerWidth + 1,
  )).toBe(true)
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
}

async function assertReady(page: Page, manifest: RunManifest) {
  const response = await page.request.get('/api/ready')
  expect(response.status()).toBe(200)
  const body = await response.json() as {
    status: string
    version: string
  }
  expect(body).toEqual({
    status: 'ready',
    version: manifest.service_version,
  })
}

async function login(page: Page, credential: BrowserCredential) {
  await page.goto('/login')
  await page.getByLabel('邮箱', { exact: true }).fill(credential.email)
  await page.getByLabel('密码', { exact: true }).fill(credential.password)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL('/ai')
  await expect(page).toHaveTitle('AI 对话 · iTime')
}

async function timedNavigation(
  page: Page,
  label: string,
  expectedPath: RegExp,
  ready: () => Promise<void>,
) {
  const started = Date.now()
  await page.getByRole('link', { name: label, exact: true }).click()
  await expect(page).toHaveURL(expectedPath)
  await ready()
  expect(Date.now() - started).toBeLessThanOrEqual(5_000)
  await assertPageHealth(page)
}

async function openTask(page: Page, title: string) {
  const row = page.locator('.task-list li').filter({ hasText: title })
  await expect(row).toBeVisible()
  await row.getByRole('button').click()
  await expect(page.getByRole('heading', { name: '任务详情', exact: true }))
    .toBeVisible()
  await assertPageHealth(page)
}

async function visitCorePages(page: Page, taskTitle: string) {
  const back = page.getByRole('button', {
    name: '返回任务列表',
    exact: true,
  })
  if (await back.isVisible()) await back.click()
  await timedNavigation(page, '任务', /\/tasks$/, async () => {
    await expect(page.getByRole('heading', { name: '任务', exact: true }))
      .toBeVisible()
  })
  await openTask(page, taskTitle)
  await timedNavigation(page, '日历', /\/calendar(?:\?.*)?$/, async () => {
    await expect(page).toHaveTitle('日历 · iTime')
    await expect(page.getByText('正在读取日历…')).toHaveCount(0)
    await expect(page.getByRole('radio', { name: '周', exact: true }))
      .toBeChecked()
  })
  await timedNavigation(page, '规划', /\/plans(?:\?.*)?$/, async () => {
    await expect(page).toHaveTitle('规划 · iTime')
    await expect(page.getByText('正在读取任务与固定安排…')).toHaveCount(0)
    await expect(page.getByRole('heading', { name: '待规划任务', exact: true }))
      .toBeVisible()
  })
  await timedNavigation(page, '专注', /\/focus(?:\?.*)?$/, async () => {
    await expect(page).toHaveTitle('专注 · iTime')
    await expect(page.getByText('正在读取专注状态...')).toHaveCount(0)
    await expect(page.locator('.focus-toolbar h1'))
      .toBeVisible()
  })
  await timedNavigation(page, '复盘', /\/reviews(?:\?.*)?$/, async () => {
    await expect(page).toHaveTitle('复盘 · iTime')
    await expect(page.getByText('正在读取复盘...')).toHaveCount(0)
    await expect(page.locator('.review-page-heading h1')).toBeVisible()
  })
  await timedNavigation(page, '统计', /\/stats(?:\?.*)?$/, async () => {
    await expect(page).toHaveTitle('统计 · iTime')
    await expect(page.getByText('正在汇总当前事实...')).toHaveCount(0)
    await expect(page.getByRole('heading', { name: '周统计', exact: true }))
      .toBeVisible()
  })
}

async function secondDevice(
  browser: Browser,
  baseURL: string,
  credential: BrowserCredential,
) {
  const context = await browser.newContext({
    baseURL,
    viewport: { width: 1200, height: 800 },
  })
  const page = await context.newPage()
  watchRuntime(page)
  await login(page, credential)
  await page.getByRole('menuitem', { name: '任务', exact: true }).click()
  await expect(page).toHaveURL('/tasks')
  await expect(page.getByRole('heading', { name: '任务', exact: true }))
    .toBeVisible()
  return { context, page }
}

async function createTaskAndVerifySync(
  page: Page,
  browser: Browser,
  credential: BrowserCredential,
) {
  const baseURL = requiredEnvironment('ITIME_PERF_WEB_ORIGIN')
  const other = await secondDevice(browser, baseURL, credential)
  const title = `负载下跨设备任务 ${randomUUID()}`
  try {
    await page.getByRole('menuitem', { name: '任务', exact: true }).click()
    await expect(page).toHaveURL('/tasks')
    await page.getByRole('button', { name: '新建任务', exact: true }).click()
    await page.getByLabel('任务名称', { exact: true }).fill(title)
    await page.getByLabel('正文', { exact: false })
      .fill('由性能 UI 场景创建并通过同步事件在第二浏览器上下文核对。')
    const responsePromise = page.waitForResponse(response => (
      response.url().endsWith('/api/v1/tasks')
      && response.request().method() === 'POST'
    ))
    await page.getByRole('button', { name: '创建任务', exact: true }).first().click()
    expect((await responsePromise).status()).toBe(201)
    await expect(page.getByText('任务已创建并保存到服务端。')).toBeVisible()
    await expect(page.getByLabel('任务名称', { exact: true })).toHaveValue(title)

    const syncStarted = Date.now()
    const synced = other.page.locator('.task-list li').filter({ hasText: title })
    await expect(synced).toBeVisible({ timeout: 4_800 })
    expect(Date.now() - syncStarted).toBeLessThanOrEqual(5_000)
    await synced.getByRole('button').click()
    await expect(other.page.getByLabel('任务名称', { exact: true }))
      .toHaveValue(title)
    await assertPageHealth(other.page)
    return title
  } finally {
    await other.context.close()
  }
}

async function saveEvidence(page: Page, info: TestInfo) {
  await mkdir(evidenceRoot, { recursive: true })
  const browserLabel = process.env.ITIME_PERF_BROWSER_LABEL ?? 'chromium'
  const suffix = browserLabel === 'chromium' ? '' : `-${browserLabel}`
  const path = (
    `${evidenceRoot}/${info.project.name}${suffix}`
    + '-performance-under-load.png'
  )
  await page.screenshot({ path, fullPage: true, animations: 'disabled' })
  expect((await stat(path)).size).toBeGreaterThan(1_000)
}

test.beforeEach(async ({ page }) => watchRuntime(page))
test.afterEach(async ({ page }) => {
  expect(runtime.get(page)?.errors ?? []).toEqual([])
  expect(runtime.get(page)?.apiServerErrors ?? []).toEqual([])
})

test('持续负载下核心页面与跨设备同步保持可用', async ({
  page,
  browser,
}, info) => {
  const { credential, manifest } = await loadRunInputs()
  assertLoadAlive()
  await assertReady(page, manifest)
  await login(page, credential)
  const taskTitle = await createTaskAndVerifySync(page, browser, credential)
  await visitCorePages(page, taskTitle)
  await assertPageHealth(page)
  assertLoadAlive()
  await saveEvidence(page, info)
})
