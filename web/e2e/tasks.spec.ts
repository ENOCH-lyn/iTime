import { test, expect, type APIRequestContext, type Page, type TestInfo } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { randomUUID } from 'node:crypto'
import { mkdir } from 'node:fs/promises'

const password = 'Valid-task-ui-password-123'
const evidence = '/tmp/itime-ui-evidence'
const runtimeErrors = new WeakMap<Page, string[]>()

async function screenshot(page: Page, info: TestInfo, name: string) {
  await mkdir(evidence, { recursive: true })
  await page.screenshot({
    path: `${evidence}/${info.project.name}-tasks-${name}.png`,
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
  const address = `task-ui-${randomUUID()}@example.com`
  await page.goto('/register')
  await page.getByLabel('邮箱', { exact: true }).fill(address)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByLabel('确认密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '创建账号', exact: true }).click()
  await expect(page.getByRole('heading', { name: '去邮箱看看吧' })).toBeVisible()
  await page.goto(await mailLink(request, address))
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '验证邮箱', exact: true }).click()
  await expect(page.getByRole('heading', { name: '邮箱已验证' })).toBeVisible()
  await page.getByRole('link', { name: '前往登录' }).click()
  await page.getByLabel('邮箱', { exact: true }).fill(address)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL('/tasks')
  await expect(page.getByRole('heading', { name: '任务', exact: true })).toBeVisible()
  return address
}

async function returnToList(page: Page) {
  const back = page.getByRole('button', { name: '返回任务列表', exact: true })
  if (await back.isVisible()) await back.click()
}

async function createSimpleTask(page: Page, title: string) {
  await returnToList(page)
  await page.getByRole('button', { name: '新建任务', exact: true }).click()
  await page.getByLabel('任务名称', { exact: true }).fill(title)
  await page.getByRole('button', { name: '创建任务', exact: true }).first().click()
  await expect(page.getByText('任务已创建并保存到服务端。')).toBeVisible()
  await returnToList(page)
  await expect(page.locator('.task-list li').filter({ hasText: title })).toBeVisible()
}

async function openTask(page: Page, title: string) {
  await returnToList(page)
  await page.locator('.task-list li').filter({ hasText: title }).getByRole('button').click()
  await expect(page.getByLabel('任务名称', { exact: true })).toHaveValue(title)
}

async function showFilters(page: Page) {
  const status = page.getByLabel('状态')
  if (!(await status.isVisible())) {
    await page.getByRole('button', { name: '筛选任务', exact: true }).click()
  }
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

test.beforeEach(async ({ page }) => watchRuntime(page))
test.afterEach(async ({ page }) => expect(runtimeErrors.get(page)).toEqual([]))

test('项目、任务、步骤、链接、明确完成、搜索和持久化形成完整流程', async ({
  page,
  request,
}, info) => {
  await account(page, request)
  await expect(page.getByText('这里还没有任务')).toBeVisible()
  expect(await page.locator('vite-error-overlay').count()).toBe(0)

  await page.getByRole('button', { name: '新建项目', exact: true }).click()
  await page.getByLabel('名称', { exact: true }).fill('学习冲刺')
  await page.getByLabel('说明', { exact: false }).fill('集中整理本周的重要学习任务。')
  await page.getByRole('button', { name: '创建', exact: true }).click()
  await expect(page.getByText('项目已创建。')).toBeVisible()
  await page.getByRole('button', { name: '编辑学习冲刺', exact: true }).click()
  await page.getByLabel('名称', { exact: true }).fill('论文冲刺')
  await page.getByRole('button', { name: '保存修改', exact: true }).click()
  await expect(page.getByText('项目已保存。')).toBeVisible()

  await page.getByRole('button', { name: '新建任务', exact: true }).click()
  await page.getByLabel('任务名称', { exact: true }).fill('完成论文提纲')
  await page.getByLabel('正文', { exact: false }).fill('先整理研究问题，再核对参考文献。')
  await page.getByLabel('项目或目标', { exact: true }).selectOption({ label: '论文冲刺' })
  await page.getByLabel('优先级', { exact: true }).selectOption('3')
  await page.getByLabel('预计用时', { exact: false }).fill('90')
  await page.getByRole('textbox', { name: '标签 逗号分隔', exact: true }).fill('学习, 深度工作, 学习')
  await page.getByRole('button', { name: '添加步骤', exact: true }).click()
  await page.getByLabel('步骤 1', { exact: true }).fill('列出研究问题')
  await page.getByRole('button', { name: '添加步骤', exact: true }).click()
  await page.getByLabel('步骤 2', { exact: true }).fill('整理章节结构')
  await page.getByRole('button', { name: '添加链接', exact: true }).click()
  await page.getByLabel('链接 1 标题', { exact: true }).fill('写作规范')
  await page.getByLabel('链接 1 地址', { exact: true }).fill('https://example.com/style')
  await page.getByRole('button', { name: '创建任务', exact: true }).first().click()
  await expect(page.getByText('任务已创建并保存到服务端。')).toBeVisible()
  await expect(page.getByLabel('任务名称', { exact: true })).toHaveValue('完成论文提纲')
  await expect(page.getByText('0/2 已完成')).toBeVisible()

  await page.getByLabel('步骤 2', { exact: true }).fill('整理章节结构与图表')
  await page.getByRole('checkbox', { name: '完成步骤 列出研究问题', exact: true }).check()
  await expect(page.getByText('步骤已勾选；任务不会自动完成。')).toBeVisible()
  await expect(page.getByLabel('步骤 2', { exact: true })).toHaveValue('整理章节结构与图表')
  await page.getByRole('button', { name: '保存步骤 整理章节结构', exact: true }).click()
  await expect(page.getByText('步骤已保存。')).toBeVisible()
  await expect(page.getByRole('button', { name: '标为完成', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '上移步骤 整理章节结构与图表', exact: true }).click()
  await expect(page.getByText('步骤顺序已更新。')).toBeVisible()
  await page.getByRole('button', { name: '标为完成', exact: true }).click()
  await expect(page.getByText('任务已明确标为完成。')).toBeVisible()
  await expect(page.getByRole('button', { name: '重新打开', exact: true })).toBeVisible()

  await page.getByLabel('正文', { exact: false }).fill('已核对研究问题，并补充三篇参考文献。')
  await page.getByRole('button', { name: '保存修改', exact: true }).first().click()
  await expect(page.getByText('修改已保存。')).toBeVisible()
  await page.reload()
  await expect(page.getByRole('heading', { name: '任务', exact: true })).toBeVisible()
  await showFilters(page)
  await page.getByLabel('状态').selectOption('completed')
  await page.getByLabel('搜索任务').fill('参考文献')
  await page.getByRole('button', { name: '搜索', exact: true }).click()
  await expect(page.locator('.task-list li').filter({ hasText: '完成论文提纲' })).toBeVisible()
  await openTask(page, '完成论文提纲')
  await expect(page.getByLabel('正文', { exact: false })).toHaveValue('已核对研究问题，并补充三篇参考文献。')
  await expect(page.getByLabel('链接 1 地址', { exact: true })).toHaveValue('https://example.com/style')
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await screenshot(page, info, 'complete-detail')
})

test('批量版本冲突由真实第二页面触发且整批不部分保存', async ({
  page,
  request,
  context,
}) => {
  await account(page, request)
  await createSimpleTask(page, '批量任务甲')
  await createSimpleTask(page, '批量任务乙')

  const second = await context.newPage()
  watchRuntime(second)
  try {
    await second.goto('/tasks')
    await expect(second.getByRole('heading', { name: '任务', exact: true })).toBeVisible()
    await openTask(second, '批量任务甲')
    await second.getByRole('button', { name: '标为完成', exact: true }).click()
    await expect(second.getByText('任务已明确标为完成。')).toBeVisible()

    await page.getByRole('checkbox', { name: '选择任务 批量任务甲', exact: true }).check()
    await page.getByRole('checkbox', { name: '选择任务 批量任务乙', exact: true }).check()
    await page.getByLabel('批量操作类型').selectOption('complete')
    await page.getByRole('button', { name: '应用', exact: true }).click()
    await expect(page.getByRole('alert')).toContainText('本次批量操作未保存')
    await page.getByRole('button', { name: '刷新列表', exact: true }).click()

    await expect(page.locator('.task-list li').filter({ hasText: '批量任务乙' })).toBeVisible()
    await expect(page.locator('.task-list li').filter({ hasText: '批量任务甲' })).toHaveCount(0)
    await showFilters(page)
    await page.getByLabel('状态').selectOption('completed')
    await expect(page.locator('.task-list li').filter({ hasText: '批量任务甲' })).toBeVisible()
    await expect(page.locator('.task-list li').filter({ hasText: '批量任务乙' })).toHaveCount(0)

    await page.getByLabel('状态').selectOption('all')
    await expect(page.locator('.task-list li')).toHaveCount(2)
    await page.getByRole('checkbox', { name: '选择任务 批量任务甲', exact: true }).check()
    await page.getByRole('checkbox', { name: '选择任务 批量任务乙', exact: true }).check()
    await page.getByLabel('批量操作类型').selectOption('set_priority')
    await page.getByLabel('批量优先级').selectOption('3')
    await page.getByRole('button', { name: '应用', exact: true }).click()
    await expect(page.getByText('已完成 2 项批量操作。')).toBeVisible()
    await expect(page.locator('.task-list li').filter({ hasText: '批量任务甲' })).toContainText('高')
    await expect(page.locator('.task-list li').filter({ hasText: '批量任务乙' })).toContainText('高')
    expect(runtimeErrors.get(second)).toEqual([])
  } finally {
    await second.close()
  }
})

test('编辑冲突保留输入，确认后基于服务器最新版重试', async ({
  page,
  request,
  context,
}) => {
  await account(page, request)
  await createSimpleTask(page, '并发编辑任务')
  await openTask(page, '并发编辑任务')
  await page.getByLabel('正文', { exact: false }).fill('主页面保留的修改')

  const second = await context.newPage()
  watchRuntime(second)
  try {
    await second.goto('/tasks')
    await openTask(second, '并发编辑任务')
    await second.getByLabel('正文', { exact: false }).fill('第二页面先保存的内容')
    await second.getByRole('button', { name: '保存修改', exact: true }).first().click()
    await expect(second.getByText('修改已保存。')).toBeVisible()

    await page.getByRole('button', { name: '保存修改', exact: true }).first().click()
    await expect(page.getByRole('alert')).toContainText('已在其他页面或设备更新')
    await expect(page.getByText('第二页面先保存的内容')).toBeVisible()
    await expect(page.getByLabel('正文', { exact: false })).toHaveValue('主页面保留的修改')
    await page.getByRole('button', { name: '保留填写并基于最新版重试', exact: true }).click()
    await page.getByRole('button', { name: '保存修改', exact: true }).first().click()
    await expect(page.getByText('修改已保存。')).toBeVisible()
    await page.reload()
    await openTask(page, '并发编辑任务')
    await expect(page.getByLabel('正文', { exact: false })).toHaveValue('主页面保留的修改')
    expect(runtimeErrors.get(second)).toEqual([])
  } finally {
    await second.close()
  }
})

test('列表失败可恢复，离线创建保留输入并在恢复后只创建一次', async ({
  page,
  request,
  context,
}, info) => {
  await account(page, request)
  await page.route('**/api/v1/tasks?**', route => route.abort('failed'))
  await page.reload()
  await expect(page.getByRole('alert')).toContainText('无法连接服务')
  await page.unroute('**/api/v1/tasks?**')
  await page.getByRole('button', { name: '刷新列表', exact: true }).click()
  await expect(page.getByText('这里还没有任务')).toBeVisible()

  await page.getByRole('button', { name: '新建任务', exact: true }).click()
  await page.getByLabel('任务名称', { exact: true }).fill('离线后重试的任务')
  await page.getByLabel('正文', { exact: false }).fill('此输入在失败后必须保留。')
  await context.setOffline(true)
  await page.getByRole('button', { name: '创建任务', exact: true }).first().click()
  await expect(page.getByRole('alert')).toContainText('无法连接服务')
  await expect(page.getByRole('alert')).toContainText('保存状态尚未确认')
  await expect(page.getByLabel('任务名称', { exact: true })).toHaveValue('离线后重试的任务')
  await context.setOffline(false)
  await page.getByRole('button', { name: '创建任务', exact: true }).first().click()
  await expect(page.getByText('任务已创建并保存到服务端。')).toBeVisible()
  await returnToList(page)
  await page.getByLabel('搜索任务').fill('离线后重试的任务')
  await page.getByRole('button', { name: '搜索', exact: true }).click()
  await expect(page.locator('.task-list li')).toHaveCount(1)

  await page.setViewportSize({ width: 320, height: 700 })
  await page.goto('/account/appearance')
  await page.getByLabel('字号', { exact: true }).selectOption('larger')
  await page.goto('/tasks')
  await expect(page.locator('.task-list li').filter({ hasText: '离线后重试的任务' })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  await screenshot(page, info, '320-large-font-list')
})
