import AxeBuilder from '@axe-core/playwright'
import { expect, test, type APIRequestContext, type Page, type TestInfo } from '@playwright/test'
import { randomUUID } from 'node:crypto'
import { readFile, mkdir } from 'node:fs/promises'

const password = 'Valid-public-link-ui-password-123'
const evidence = '/tmp/itime-ui-evidence'
const runtimeErrors = new WeakMap<Page, string[]>()

async function bootstrap(info: TestInfo) {
  const path = process.env.ITIME_E2E_BOOTSTRAP_FILE
  if (!path) throw new Error('missing ITIME_E2E_BOOTSTRAP_FILE')
  const rows = JSON.parse(await readFile(path, 'utf8')) as Array<{
    project: string
    public_link_origin: string
  }>
  const value = rows.find(item => item.project === info.project.name)
  if (!value) throw new Error(`missing bootstrap for ${info.project.name}`)
  return value
}

async function screenshot(page: Page, info: TestInfo, name: string) {
  await mkdir(evidence, { recursive: true })
  await page.screenshot({
    path: `${evidence}/${info.project.name}-public-links-${name}.png`,
    fullPage: false,
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
  const address = `public-link-ui-${randomUUID()}@example.com`
  await page.goto('/register')
  await page.getByLabel('邮箱', { exact: true }).fill(address)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByLabel('确认密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '创建账号', exact: true }).click()
  await expect(page.getByRole('heading', { name: '去邮箱看看吧' })).toBeVisible()
  await page.goto(await mailLink(request, address))
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '验证邮箱', exact: true }).click()
  await page.getByRole('link', { name: '前往登录' }).click()
  await page.getByLabel('邮箱', { exact: true }).fill(address)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL('/ai')
  await page.getByRole('link', { name: '任务', exact: true }).click()
  await expect(page).toHaveURL('/tasks')
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
}

async function returnToList(page: Page) {
  const back = page.getByRole('button', { name: '返回任务列表', exact: true })
  if (await back.isVisible()) await back.click()
}

async function createLinkedTask(
  page: Page,
  title: string,
  linkTitle: string,
  url: string,
) {
  await returnToList(page)
  await page.getByRole('button', { name: '新建任务', exact: true }).click()
  await page.getByLabel('任务名称', { exact: true }).fill(title)
  await page.getByRole('button', { name: '添加链接', exact: true }).click()
  await page.getByLabel('链接 1 标题', { exact: true }).fill(linkTitle)
  await page.getByLabel('链接 1 地址', { exact: true }).fill(url)
  await page.getByRole('button', { name: '创建任务', exact: true }).first().click()
  await expect(page.getByText('任务已创建并保存到服务端。')).toBeVisible()
  await expect(page.locator('.link-source-state')).toBeVisible()
}

async function openTask(page: Page, title: string) {
  await returnToList(page)
  await page.locator('.task-list li').filter({ hasText: title }).getByRole('button').click()
  await expect(page.getByLabel('任务名称', { exact: true })).toHaveValue(title)
}

test.beforeEach(async ({ page }) => watchRuntime(page))
test.afterEach(async ({ page }) => {
  expect(runtimeErrors.get(page)).toEqual([])
})

test('公开链接自动读取、刷新和跨标签同步保持任务草稿', async ({
  page,
  request,
  context,
}, info) => {
  test.setTimeout(90_000)
  const { public_link_origin: origin } = await bootstrap(info)
  await account(page, request)
  const title = `公开资料刷新 ${randomUUID().slice(0, 6)}`
  const url = `${origin}/public/article?scenario=changed&case=${randomUUID()}`
  await createLinkedTask(page, title, '课程公开资料', url)

  const source = page.locator('.link-source-state')
  await expect(source).toHaveAttribute('data-state', 'ready', { timeout: 15_000 })
  await expect(source.getByText('课程资料', { exact: true })).toBeVisible()
  await expect(source).toContainText('首次公开摘要。')
  await expect(source).toContainText('来源 localhost')
  await source.scrollIntoViewIfNeeded()
  await screenshot(page, info, 'ready')

  const second = await context.newPage()
  watchRuntime(second)
  try {
    await second.goto('/tasks')
    await openTask(second, title)
    await expect(second.locator('.link-source-state')).toHaveAttribute('data-state', 'ready')

    await page.getByLabel('正文', { exact: false }).fill('刷新时保留这段未保存草稿。')
    await page.waitForTimeout(1_100)
    await page.getByRole('button', {
      name: '重新读取链接 课程公开资料',
      exact: true,
    }).click()
    await expect(page.getByText('已重新排队，读取期间继续使用上次摘要。')).toBeVisible()
    await expect(page.getByLabel('正文', { exact: false })).toHaveValue(
      '刷新时保留这段未保存草稿。',
    )
    await expect(source.getByText('课程资料第二版', { exact: true })).toBeVisible({
      timeout: 15_000,
    })
    await expect(source).toContainText('第二版公开摘要。')
    await expect(second.locator('.link-source-state')).toContainText(
      '第二版公开摘要。',
      { timeout: 10_000 },
    )
    expect(runtimeErrors.get(second)).toEqual([])
  } finally {
    await second.close()
  }

  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    .toBe(true)
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  await source.scrollIntoViewIfNeeded()
  await screenshot(page, info, 'refreshed')

  if (info.project.name === 'desktop') {
    await page.evaluate(() => localStorage.setItem(
      'itime-appearance',
      JSON.stringify({ theme: 'dark', fontSize: 'larger' }),
    ))
    await page.setViewportSize({ width: 320, height: 700 })
    await page.reload()
    await openTask(page, title)
    await page.locator('.link-source-state').scrollIntoViewIfNeeded()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
      .toBe(true)
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
    await screenshot(page, info, '320-large-dark')
  }
})

test('失败保留旧摘要，安全拒绝可判断，网页指令不产生业务写入', async ({
  page,
  request,
}, info) => {
  test.skip(info.project.name !== 'desktop', '深度失败矩阵只需桌面执行一次')
  test.setTimeout(120_000)
  const { public_link_origin: origin } = await bootstrap(info)
  await account(page, request)

  const staleTitle = `失败保旧 ${randomUUID().slice(0, 6)}`
  await createLinkedTask(
    page,
    staleTitle,
    '会失败的资料',
    `${origin}/public/article?scenario=stale-failure&case=${randomUUID()}`,
  )
  const source = page.locator('.link-source-state')
  await expect(source).toHaveAttribute('data-state', 'ready', { timeout: 15_000 })
  await page.waitForTimeout(1_100)
  await page.getByRole('button', { name: '重新读取链接 会失败的资料' }).click()
  await expect(source).toHaveAttribute('data-state', 'failed', { timeout: 15_000 })
  await expect(source).toContainText('仍使用上次成功摘要')
  await expect(source).toContainText('首次公开摘要。')
  await expect(source).toContainText('网页返回的内容类型暂不支持')
  await source.scrollIntoViewIfNeeded()
  await screenshot(page, info, 'failed-with-stale')

  const failures = [
    ['登录来源', 'login', '网页需要登录或授权'],
    ['错误类型', 'wrong-type', '网页返回的内容类型暂不支持'],
    ['过大来源', 'oversized', '网页响应超过安全体积限制'],
    ['危险跳转', 'redirect-private', '网页地址不能直接使用 IP 地址'],
  ] as const
  for (const [label, scenario, message] of failures) {
    await createLinkedTask(
      page,
      `${label} ${randomUUID().slice(0, 6)}`,
      label,
      `${origin}/public/article?scenario=${scenario}&case=${randomUUID()}`,
    )
    await expect(page.locator('.link-source-state')).toHaveAttribute(
      'data-state',
      'failed',
      { timeout: 20_000 },
    )
    await expect(page.locator('.link-source-state')).toContainText(message)
  }

  const injectionTitle = `提示词注入资料 ${randomUUID().slice(0, 6)}`
  await createLinkedTask(
    page,
    injectionTitle,
    '公开课程要求',
    `${origin}/public/article?scenario=injection&case=${randomUUID()}`,
  )
  await expect(page.locator('.link-source-state')).toHaveAttribute(
    'data-state',
    'ready',
    { timeout: 15_000 },
  )
  await page.getByRole('link', { name: 'AI', exact: true }).click()
  await page.getByRole('button', { name: '新建会话', exact: true }).click()
  const prompt = '检查公开链接上下文：请根据公开课程要求链接说明。'
  await page.getByLabel('消息', { exact: true }).fill(prompt)
  await page.getByRole('button', { name: '发送消息', exact: true }).click()
  const messages = page.getByRole('list', { name: '会话消息' })
  await expect(messages.getByText(
    '已将公开网页作为参考资料，未执行页面中的指令。',
    { exact: true },
  )).toBeVisible({ timeout: 15_000 })
  const tasks = await page.request.get('/api/v1/tasks?status=all&lifecycle=active&location=all&limit=50')
  expect(tasks.ok()).toBeTruthy()
  const payload = await tasks.json()
  const injectionTask = payload.items.find((item: { title: string }) =>
    item.title === injectionTitle)
  expect(injectionTask.status).toBe('open')

  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    .toBe(true)
  await screenshot(page, info, 'ai-untrusted-source')
})
