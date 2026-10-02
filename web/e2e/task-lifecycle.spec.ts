import { test, expect, type APIRequestContext, type Page, type TestInfo } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { randomUUID } from 'node:crypto'
import { mkdir } from 'node:fs/promises'

const password = 'Valid-lifecycle-ui-password-123'
const evidence = '/tmp/itime-ui-evidence'
const runtimeErrors = new WeakMap<Page, string[]>()

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

async function screenshot(page: Page, info: TestInfo, name: string) {
  await mkdir(evidence, { recursive: true })
  await page.screenshot({
    path: `${evidence}/${info.project.name}-task-lifecycle-${name}.png`,
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
  const address = `task-lifecycle-ui-${randomUUID()}@example.com`
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
  await expect(page).toHaveURL('/tasks')
  await expect(page.getByRole('heading', { name: '任务', exact: true })).toBeVisible()
}

async function returnToList(page: Page) {
  const back = page.getByRole('button', { name: '返回任务列表', exact: true })
  if (await back.isVisible()) await back.click()
}

async function createTask(page: Page, title: string) {
  await returnToList(page)
  await page.getByRole('button', { name: '新建任务', exact: true }).click()
  await page.getByLabel('任务名称', { exact: true }).fill(title)
  await page.getByLabel('正文', { exact: false }).fill('归档和恢复后仍需保留的正文。')
  await page.getByRole('button', { name: '创建任务', exact: true }).first().click()
  await expect(page.getByText('任务已创建并保存到服务端。')).toBeVisible()
}

async function openTask(page: Page, title: string) {
  await returnToList(page)
  await page.locator('.task-list li').filter({ hasText: title }).getByRole('button').click()
  await expect(page.getByLabel('任务名称', { exact: true })).toHaveValue(title)
}

async function chooseLifecycle(page: Page, name: '活动' | '已归档' | '回收站') {
  await returnToList(page)
  await page.getByRole('group', { name: '任务范围' })
    .getByRole('button', { name, exact: true }).click()
  await expect(page.getByRole('heading', {
    name: name === '活动' ? '任务' : name,
    exact: true,
  })).toBeVisible()
}

async function assertPageHealth(page: Page) {
  expect(await page.locator('vite-error-overlay').count()).toBe(0)
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  expect(runtimeErrors.get(page)).toEqual([])
}

test.beforeEach(async ({ page }) => watchRuntime(page))
test.afterEach(async ({ page }) => expect(runtimeErrors.get(page)).toEqual([]))

test('任务可归档、只读查看、删除并在 30 天内恢复', async ({
  page,
  request,
}, info) => {
  await account(page, request)
  const title = '整理归档资料'
  await createTask(page, title)
  await page.getByRole('button', { name: '归档', exact: true }).click()
  await expect(page.getByText('任务已归档。')).toBeVisible()
  await expect(page.locator('.task-list li').filter({ hasText: title })).toHaveCount(0)

  await chooseLifecycle(page, '已归档')
  await expect(page.locator('.task-list li').filter({ hasText: title })).toBeVisible()
  await expect(page.getByRole('button', { name: '新建任务', exact: true })).toHaveCount(0)
  await openTask(page, title)
  await expect(page.getByText('此任务已归档')).toBeVisible()
  await expect(page.getByLabel('任务名称', { exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: '保存修改', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: '取消归档', exact: true }).click()
  await expect(page.getByText('任务已取消归档。')).toBeVisible()

  await chooseLifecycle(page, '活动')
  await openTask(page, title)
  page.once('dialog', async dialog => {
    expect(dialog.message()).toContain('保留 30 天')
    await dialog.accept()
  })
  await page.getByRole('button', { name: '移到回收站', exact: true }).click()
  await expect(page.getByText('任务已移到回收站，将保留 30 天。')).toBeVisible()

  await chooseLifecycle(page, '回收站')
  await openTask(page, title)
  await expect(page.getByText('此任务在回收站中')).toBeVisible()
  await expect(page.locator('.task-lifecycle-copy').getByText(/还剩 30 天/)).toBeVisible()
  await expect(page.getByLabel('正文', { exact: false }))
    .toHaveValue('归档和恢复后仍需保留的正文。')
  await expect(page.getByLabel('任务名称', { exact: true })).toBeDisabled()
  await assertPageHealth(page)
  await screenshot(page, info, 'trash-detail')

  if (info.project.name === 'desktop') {
    await page.evaluate(() => localStorage.setItem(
      'itime-appearance',
      JSON.stringify({ theme: 'light', fontSize: 'larger' }),
    ))
    await page.setViewportSize({ width: 320, height: 700 })
    await page.reload()
    await chooseLifecycle(page, '回收站')
    await openTask(page, title)
    await expect(page.getByText('此任务在回收站中')).toBeVisible()
    await assertPageHealth(page)
    await screenshot(page, info, 'trash-320-large-font')
  }

  await page.getByRole('button', { name: '恢复任务', exact: true }).click()
  await expect(page.getByText('任务已恢复到活动任务。')).toBeVisible()
  await chooseLifecycle(page, '活动')
  await expect(page.locator('.task-list li').filter({ hasText: title })).toBeVisible()
})

test('并发归档冲突可采用服务器状态且完成事实不变', async ({
  page,
  request,
  context,
}, info) => {
  await account(page, request)
  const title = '保留完成状态的任务'
  await createTask(page, title)
  await page.getByRole('button', { name: '标为完成', exact: true }).click()
  await expect(page.getByText('任务已明确标为完成。')).toBeVisible()

  const second = await context.newPage()
  watchRuntime(second)
  try {
    await second.goto('/tasks')
    await expect(second.getByRole('heading', { name: '任务', exact: true })).toBeVisible()
    const filter = second.getByRole('button', { name: '筛选任务', exact: true })
    const statusFilter = second.locator('#task-filters select').first()
    if (!(await statusFilter.isVisible())) {
      await filter.click()
    }
    await statusFilter.selectOption('completed')
    await openTask(second, title)

    await page.getByRole('button', { name: '归档', exact: true }).click()
    await expect(page.getByText('任务已归档。')).toBeVisible()
    await second.getByRole('button', { name: '归档', exact: true }).click()
    await expect(second.getByRole('alert')).toContainText('已在其他页面或设备更新')
    await expect(second.getByText(/服务器最新版/)).toBeVisible()
    await second.getByRole('button', { name: '改用服务器内容', exact: true }).click()
    await expect(second.getByText('此任务已归档')).toBeVisible()
    await expect(second.getByLabel('任务名称', { exact: true })).toBeDisabled()
    await expect(second.getByRole('button', { name: '取消归档', exact: true })).toBeVisible()
    await assertPageHealth(second)
    await screenshot(second, info, 'conflict-adopted')

    await second.getByRole('button', { name: '取消归档', exact: true }).click()
    await expect(second.getByText('任务已取消归档。')).toBeVisible()
    await chooseLifecycle(second, '活动')
    const secondFilter = second.getByRole('button', { name: '筛选任务', exact: true })
    const restoredStatusFilter = second.locator('#task-filters select').first()
    if (!(await restoredStatusFilter.isVisible())) {
      await secondFilter.click()
    }
    await restoredStatusFilter.selectOption('completed')
    await expect(second.locator('.task-list li').filter({ hasText: title })).toBeVisible()
  } finally {
    expect(runtimeErrors.get(second)).toEqual([])
    await second.close()
  }
})
