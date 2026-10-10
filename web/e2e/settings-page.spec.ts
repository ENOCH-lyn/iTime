import { expect, test, type APIRequestContext, type Page, type TestInfo } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { randomUUID } from 'node:crypto'
import { mkdir } from 'node:fs/promises'

const password = 'Valid-settings-password-123'
const evidence = '/tmp/itime-ui-evidence'

async function mailLink(request: APIRequestContext, address: string) {
  let id = ''
  await expect.poll(async () => {
    const response = await request.get(
      `http://127.0.0.1:58025/api/v1/search?query=${encodeURIComponent(`to:${address}`)}`,
    )
    const result = await response.json()
    id = result.messages.find(
      (message: { Subject: string }) => message.Subject.includes('验证邮箱'),
    )?.ID ?? ''
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
  const address = `settings-${randomUUID()}@example.com`
  await page.goto('/register')
  await page.getByLabel('邮箱', { exact: true }).fill(address)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByLabel('确认密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '创建账号', exact: true }).click()
  await page.goto(await mailLink(request, address))
  await expect(page).toHaveURL('/ai')
  return address
}

async function login(page: Page, address: string) {
  await page.goto('/login')
  await page.getByLabel('邮箱', { exact: true }).fill(address)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL('/ai')
}

async function createTask(page: Page, title: string) {
  const session = await (await page.request.get('/api/v1/auth/me')).json()
  const response = await page.request.post('/api/v1/tasks', {
    headers: { 'X-CSRF-Token': session.csrf_token },
    data: {
      operation_id: randomUUID(),
      title,
      description: '设置同步冲突视觉验收。',
      project_id: null,
      estimated_minutes: 30,
      priority: 1,
      labels: [],
      links: [],
      steps: [{ title: '离线步骤' }],
    },
  })
  expect(response.ok()).toBeTruthy()
  return {
    session,
    task: (await response.json()).task as {
      id: string
      version: number
      steps: { id: string }[]
    },
  }
}

async function screenshot(
  page: Page,
  info: TestInfo,
  name: string,
  fullPage = true,
) {
  await mkdir(evidence, { recursive: true })
  await page.screenshot({
    path: `${evidence}/${info.project.name}-settings-${name}.png`,
    fullPage,
    animations: 'disabled',
  })
}

test('设置单页分组并兼容旧深链接', async ({ page, request }, info) => {
  const address = await account(page, request)
  await page.goto('/settings')
  await expect(page).toHaveTitle('设置 · iTime')
  await expect(page.getByRole('heading', { name: '设置', exact: true })).toBeVisible()
  for (const name of ['账号与安全', '偏好', 'AI 与隐私', '数据与同步', '账号操作']) {
    await expect(page.getByRole('heading', { name, exact: true })).toBeVisible()
  }
  await expect(page.locator('#account').getByText(address, { exact: true })).toBeVisible()
  await expect(page.getByRole('navigation', { name: '个人设置' })).toHaveCount(0)
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  await screenshot(page, info, 'normal')

  await page.goto('/account/password')
  await expect(page).toHaveURL('/settings#password')
  await expect(page.getByRole('dialog', { name: '修改密码' })).toBeVisible()
  await page.getByRole('dialog', { name: '修改密码' })
    .getByRole('button', { name: '关闭' }).click()

  await page.goto('/account/devices')
  await expect(page).toHaveURL('/settings#devices')
  await expect(page.getByRole('dialog', { name: '登录设备' })).toBeVisible()
  await page.getByRole('dialog', { name: '登录设备' })
    .getByRole('button', { name: '关闭' }).click()

  await page.goto('/account/data')
  await expect(page).toHaveURL('/settings#data')
  await expect(page.locator('#data-export-settings')).toBeVisible()
})

test('设置页在 320px 最大字号下无裁切', async ({ page, request }, info) => {
  await account(page, request)
  await page.setViewportSize({ width: 320, height: 700 })
  await page.evaluate(() => {
    localStorage.setItem('itime-appearance', JSON.stringify({
      theme: 'dark',
      fontSize: 'larger',
    }))
  })
  await page.goto('/settings')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await expect(page.locator('html')).toHaveAttribute('data-font-size', 'larger')
  await expect(page.getByRole('navigation', { name: '设置导航' })).toBeVisible()
  await expect(page.getByRole('link', { name: '返回 AI 对话' })).toBeVisible()
  expect(await page.evaluate(
    () => document.documentElement.scrollWidth <= window.innerWidth,
  )).toBe(true)
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  await screenshot(page, info, '320-large-dark')
  if (info.project.name === 'mobile') {
    await page.getByRole('heading', { name: '注销账号', exact: true })
      .scrollIntoViewIfNeeded()
    await screenshot(page, info, 'danger', false)
  }
})

test('设置页展开真实同步冲突和恢复操作', async ({
  page,
  request,
  browser,
}, info) => {
  test.skip(info.project.name !== 'desktop', '桌面固定证据只生成一次')
  const address = await account(page, request)
  const marker = `设置冲突 ${randomUUID()}`
  const { session, task } = await createTask(page, marker)
  const secondContext = await browser.newContext({
    baseURL: 'http://localhost:4173',
    viewport: { width: 1440, height: 1000 },
  })
  try {
    const second = await secondContext.newPage()
    await login(second, address)
    await second.goto('/tasks')
    await second.getByText(marker, { exact: true }).click()
    await secondContext.setOffline(true)
    await second.getByLabel('完成步骤 离线步骤').click()
    await expect(second.getByLabel(/1 项待同步/)).toBeVisible()

    const removed = await page.request.delete(
      `/api/v1/tasks/${task.id}/steps/${task.steps[0]!.id}`,
      {
        headers: { 'X-CSRF-Token': session.csrf_token },
        data: {
          operation_id: randomUUID(),
          expected_task_version: task.version,
        },
      },
    )
    expect(removed.ok()).toBeTruthy()

    await secondContext.setOffline(false)
    await expect(second.getByLabel(/1 项冲突/)).toBeVisible({
      timeout: 8_000,
    })
    await second.getByLabel(/1 项冲突/).click()
    await expect(second).toHaveURL('/settings#sync')
    const drawer = second.getByRole('dialog', { name: '同步与离线' })
    await expect(drawer.getByRole('heading', { name: '同步冲突' })).toBeVisible()
    expect(await second.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    )).toBe(true)
    expect((await new AxeBuilder({ page: second }).analyze()).violations)
      .toEqual([])
    await screenshot(second, info, 'sync-conflict', false)
    await drawer.getByRole('button', { name: '丢弃此操作' }).click()
    await expect(second.getByText('已同步', { exact: true })).toBeVisible()
  } finally {
    await secondContext.close()
  }
})
