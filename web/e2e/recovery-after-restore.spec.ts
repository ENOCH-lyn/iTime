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

interface RecoveryRunInput {
  run_id: string
  scenario: string
  service_version: string
  probes: {
    before: string
    after: string
  }
  deletions: {
    account_email: string
    objects: Record<string, { id: string, label: string }>
  }
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

async function loadInputs() {
  const accountFile = requiredEnvironment('ITIME_RECOVERY_ACCOUNT_FILE')
  const runFile = requiredEnvironment('ITIME_RECOVERY_RUN_FILE')
  const oldStateFile = requiredEnvironment('ITIME_RECOVERY_OLD_STORAGE_STATE')
  const primaryStateFile = requiredEnvironment(
    'ITIME_RECOVERY_PRIMARY_STORAGE_STATE',
  )
  for (const path of [
    accountFile,
    runFile,
    oldStateFile,
    primaryStateFile,
  ]) {
    expect((await stat(path)).mode & 0o777).toBe(0o600)
  }
  const credentials = JSON.parse(
    await readFile(accountFile, 'utf8'),
  ) as BrowserCredential[]
  const run = JSON.parse(
    await readFile(runFile, 'utf8'),
  ) as RecoveryRunInput
  expect(run.scenario).toBe('ui')
  expect(run.run_id).toBe(requiredEnvironment('ITIME_RECOVERY_RUN_ID'))
  expect(run.service_version).toMatch(/^\d+\.\d+\.\d+$/)
  if (credentials.length < 2) {
    throw new Error('恢复 UI 场景需要控制账号和已删除账号')
  }
  return {
    primary: credentials[0],
    deleted: credentials[1],
    run,
    oldStateFile,
    primaryStateFile,
  }
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
    if (response.url().includes('/api/') && response.status() >= 500) {
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

async function login(page: Page, credential: BrowserCredential) {
  await page.goto('/login')
  await page.getByLabel('邮箱', { exact: true }).fill(credential.email)
  await page.getByLabel('密码', { exact: true }).fill(credential.password)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL('/ai')
  await expect(page).toHaveTitle('AI 对话 · iTime')
}

async function navigate(
  page: Page,
  label: string,
  path: RegExp,
  title: string,
) {
  await page.getByRole('link', { name: label, exact: true }).click()
  await expect(page).toHaveURL(path)
  await expect(page).toHaveTitle(title)
  await assertPageHealth(page)
}

async function createTaskAndVerifySync(
  page: Page,
  browser: Browser,
  primaryStateFile: string,
) {
  const baseURL = requiredEnvironment('ITIME_RECOVERY_WEB_ORIGIN')
  const secondContext = await browser.newContext({
    baseURL,
    storageState: primaryStateFile,
    viewport: { width: 1200, height: 800 },
  })
  const second = await secondContext.newPage()
  watchRuntime(second)
  const title = `恢复后同步任务 ${randomUUID()}`
  try {
    await second.goto('/tasks')
    await expect(second).toHaveURL('/tasks')
    await expect(second).toHaveTitle('任务 · iTime')

    await page.getByRole('menuitem', { name: '任务', exact: true }).click()
    await expect(page).toHaveURL('/tasks')
    await page.getByRole('button', { name: '新建任务', exact: true }).click()
    await page.getByLabel('任务名称', { exact: true }).fill(title)
    await page.getByLabel('正文', { exact: false })
      .fill('恢复后的新写入必须可以同步到第二浏览器上下文。')
    await page.getByRole('button', { name: '创建任务', exact: true }).first().click()
    await expect(page.getByText('任务已创建并保存到服务端。')).toBeVisible()

    const started = Date.now()
    await expect(
      second.locator('.task-list li').filter({ hasText: title }),
    ).toBeVisible({ timeout: 4_800 })
    expect(Date.now() - started).toBeLessThanOrEqual(5_000)
    await assertPageHealth(second)
  } finally {
    await secondContext.close()
  }
}

async function saveEvidence(page: Page, info: TestInfo) {
  await mkdir(evidenceRoot, { recursive: true })
  const browserLabel = process.env.ITIME_RECOVERY_BROWSER_LABEL ?? 'chromium'
  const suffix = browserLabel === 'chromium' ? '' : `-${browserLabel}`
  const path = (
    `${evidenceRoot}/${info.project.name}${suffix}`
    + '-recovery-after-restore.png'
  )
  await page.screenshot({ path, fullPage: true, animations: 'disabled' })
  expect((await stat(path)).size).toBeGreaterThan(1_000)
}

test.beforeEach(async ({ page }) => watchRuntime(page))
test.afterEach(async ({ page }) => {
  expect(runtime.get(page)?.errors ?? []).toEqual([])
  expect(runtime.get(page)?.apiServerErrors ?? []).toEqual([])
})

test('恢复后撤销旧会话并保持删除语义', async ({
  browser,
}, info) => {
  test.skip(info.project.name !== 'desktop', '会话恢复语义只需验证一次')
  const { primary, deleted, run, oldStateFile } = await loadInputs()
  const baseURL = requiredEnvironment('ITIME_RECOVERY_WEB_ORIGIN')
  const oldContext = await browser.newContext({
    baseURL,
    storageState: oldStateFile,
  })
  const oldPage = await oldContext.newPage()
  watchRuntime(oldPage)
  try {
    await oldPage.goto('/tasks')
    await expect(oldPage).toHaveURL('/login')
    await expect(
      oldPage.getByRole('heading', { name: '欢迎回来' }),
    ).toBeVisible()
    await assertPageHealth(oldPage)
  } finally {
    await oldContext.close()
  }

  const freshContext = await browser.newContext({ baseURL })
  const freshPage = await freshContext.newPage()
  watchRuntime(freshPage)
  try {
    const deletedLogin = await freshContext.request.post(
      '/api/v1/auth/login',
      {
        data: {
          email: deleted.email,
          password: deleted.password,
          device_name: 'deleted recovery account',
        },
      },
    )
    expect(deletedLogin.status()).toBe(401)

    await login(freshPage, primary)
    await navigate(freshPage, '任务', /\/tasks$/, '任务 · iTime')
    await expect(
      freshPage.getByText(run.probes.before, { exact: true }),
    ).toBeVisible()
    await expect(
      freshPage.getByText(run.probes.after, { exact: true }),
    ).toHaveCount(0)
    for (const item of Object.values(run.deletions.objects)) {
      await expect(
        freshPage.getByText(item.label, { exact: true }),
      ).toHaveCount(0)
    }

    const deletedRoutes: Record<string, string> = {
      task: '/api/v1/tasks',
      difficulty: '/api/v1/difficulties',
      memory: '/api/v1/memories',
      'calendar-series': '/api/v1/calendar/series',
    }
    for (const [kind, item] of Object.entries(run.deletions.objects)) {
      const response = await freshPage.request.get(
        `${deletedRoutes[kind]}/${item.id}`,
      )
      expect(response.status()).toBe(410)
    }
  } finally {
    await freshContext.close()
  }
})

test('恢复后规划日历与专注页面可用', async ({ page }) => {
  await loadInputs()
  await page.goto('/ai')
  await expect(page).toHaveTitle('AI 对话 · iTime')
  await navigate(page, '规划', /\/plans(?:\?.*)?$/, '规划 · iTime')
  await navigate(page, '日历', /\/calendar(?:\?.*)?$/, '日历 · iTime')
  await navigate(page, '专注', /\/focus(?:\?.*)?$/, '专注 · iTime')
})

test('恢复后复盘统计与同步页面可用', async ({ page }) => {
  await loadInputs()
  await page.goto('/ai')
  await expect(page).toHaveTitle('AI 对话 · iTime')
  await navigate(page, '复盘', /\/reviews(?:\?.*)?$/, '复盘 · iTime')
  await navigate(page, '统计', /\/stats(?:\?.*)?$/, '统计 · iTime')
  await page.goto('/settings#sync')
  await expect(page).toHaveTitle('设置 · iTime')
  await expect(
    page.getByRole('dialog', { name: '同步与离线', exact: true }),
  ).toBeVisible()
  await assertPageHealth(page)
})

test('恢复后新写入可在五秒内同步', async ({ page, browser }, info) => {
  const { primaryStateFile } = await loadInputs()
  await page.goto('/ai')
  await expect(page).toHaveTitle('AI 对话 · iTime')
  await createTaskAndVerifySync(page, browser, primaryStateFile)
  await assertPageHealth(page)
  await saveEvidence(page, info)
})
