import { mkdir } from 'node:fs/promises'
import { randomUUID } from 'node:crypto'
import AxeBuilder from '@axe-core/playwright'
import { expect, test, type APIRequestContext, type Page, type TestInfo } from '@playwright/test'

const password = 'Valid-review-password-123'

async function screenshot(page: Page, info: TestInfo, name: string) {
  const path = `/tmp/itime-ui-evidence/${info.project.name}-review-${name}.png`
  await mkdir('/tmp/itime-ui-evidence', { recursive: true })
  await page.screenshot({ path, fullPage: true, animations: 'disabled' })
}

async function mailLink(request: APIRequestContext, address: string) {
  let id = ''
  await expect.poll(async () => {
    const response = await request.get(
      `http://127.0.0.1:58025/api/v1/search?query=${encodeURIComponent(`to:${address}`)}`,
    )
    const result = await response.json()
    id = result.messages?.[0]?.ID ?? ''
    return id
  }).not.toBe('')
  const message = await (
    await request.get(`http://127.0.0.1:58025/api/v1/message/${id}`)
  ).json()
  return String(message.Text).match(
    /http:\/\/localhost:4173\/verify-email#token=[A-Za-z0-9_-]{43}/,
  )![0]
}

async function account(page: Page, request: APIRequestContext) {
  const address = `review-ui-${randomUUID()}@example.com`
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

async function openMemories(page: Page) {
  await page.goto('/reviews?view=memories')
  await expect(page.getByRole('heading', { name: '长期记忆' })).toBeVisible()
}

async function expectConversationMemory(page: Page, expected: string) {
  await page.goto('/ai')
  await page.getByRole('button', { name: '新建会话', exact: true }).click()
  await page.getByLabel('消息', { exact: true }).fill('检查记忆上下文')
  await page.getByRole('button', { name: '发送消息', exact: true }).click()
  await expect(
    page.getByRole('list', { name: '会话消息' }).getByText(expected, {
      exact: true,
    }),
  ).toBeVisible({ timeout: 15_000 })
}

async function pageHealth(page: Page) {
  expect(await page.locator('vite-error-overlay').count()).toBe(0)
  expect(await page.evaluate(
    () => document.documentElement.scrollWidth <= window.innerWidth,
  )).toBe(true)
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
}

test('daily review resumes jobs and manages report memories', async ({
  page,
  request,
}, info) => {
  const consoleErrors: string[] = []
  page.on('console', message => {
    if (
      message.type() === 'error'
      && !message.text().includes('Failed to load resource')
    ) consoleErrors.push(message.text())
  })
  await account(page, request)
  await page.getByRole('link', { name: '复盘', exact: true }).click()
  await expect(page).toHaveURL(/\/reviews/)
  await expect(page).toHaveTitle('复盘 · iTime')
  await expect(page.getByRole('heading', { name: '事实概览' })).toBeVisible()
  await expect(page.getByText('计划块').locator('..').getByRole('definition'))
    .toHaveText('0')

  await page.getByLabel('补充事实').fill('上午更适合持续思考。')
  await page.getByRole('button', { name: '保存事实' }).click()
  await expect(page.getByText('补充事实已保存。')).toBeVisible()

  await page.getByRole('button', { name: '开始追问' }).click()
  await expect(page.getByText('今天最顺利的是哪一段？')).toBeVisible({
    timeout: 15_000,
  })
  await page.getByLabel('回答当前问题').fill('整理资料时思路最连贯。')
  await page.getByRole('button', { name: '回答并继续' }).click()
  await expect(page.getByText('今天最顺利的是哪一段？')).toHaveCount(2, {
    timeout: 15_000,
  })

  await page.getByRole('button', { name: '生成报告', exact: true }).click()
  await expect(page.getByText('今天完成了核心事项，也留下了可继续优化的线索。'))
    .toBeVisible({ timeout: 15_000 })
  await expect(page.getByText('AI 推测', { exact: true })).toBeVisible()
  await expect(page.getByText('深度工作时段', { exact: true })).toBeVisible()

  await page.getByLabel('新批注').fill('这里补充记录一次临时讨论。')
  await page.getByRole('button', { name: '添加批注' }).click()
  await expect(page.getByText('这里补充记录一次临时讨论。')).toBeVisible()

  await page.getByLabel('重新生成要求').fill('离页恢复')
  await page.getByRole('button', { name: '生成新版本' }).click()
  await page.getByRole('link', { name: '任务', exact: true }).click()
  await expect(page).toHaveURL('/tasks')
  await page.getByRole('link', { name: '复盘', exact: true }).click()
  await expect(page.getByText('第 2 版报告已生成。')).toBeVisible({
    timeout: 15_000,
  })
  await expect(page.getByLabel('报告版本')).toHaveValue('2')
  await page.getByLabel('报告版本').selectOption('1')
  await expect(
    page.locator('.review-report-lead').getByText('第 1 版', { exact: true }),
  ).toBeVisible()
  await page.getByLabel('报告版本').selectOption('2')

  const candidates = page.locator('.review-memory-proposals li')
  await expect(candidates).toHaveCount(2)
  await candidates.nth(0).getByRole('button', { name: '确认保存记忆' }).click()
  await expect(candidates.nth(0).getByText('已保存', { exact: true })).toBeVisible()
  await candidates.nth(1).getByRole('button', { name: '拒绝记忆建议' }).click()
  await expect(candidates.nth(1).getByText('已拒绝', { exact: true })).toBeVisible()

  await expectConversationMemory(page, '已读取长期记忆。')
  await openMemories(page)
  await expect(page.getByText('深度工作时段', { exact: true })).toBeVisible()
  const memoryToggle = page.getByRole('checkbox')
  await expect(memoryToggle).toBeChecked()
  await memoryToggle.uncheck()
  await expect(page.getByText('长期记忆已暂停，后续 AI 请求不会读取已有记忆。'))
    .toBeVisible()
  await expectConversationMemory(page, '未读取长期记忆。')
  await openMemories(page)
  await memoryToggle.check()
  await expect(page.getByText('长期记忆已恢复用于后续 AI 请求。')).toBeVisible()

  page.once('dialog', dialog => dialog.accept())
  await page.getByRole('button', { name: '删除记忆' }).click()
  await expect(page.getByText('还没有已确认的长期记忆。')).toBeVisible()
  await expectConversationMemory(page, '未读取长期记忆。')
  await openMemories(page)
  await page.getByRole('tab', { name: '回收站' }).click()
  await expect(page.getByText('深度工作时段', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '恢复', exact: true }).click()
  await expect(page.getByText('记忆已恢复。')).toBeVisible()
  await page.getByRole('tab', { name: '使用中' }).click()
  await page.getByRole('button', { name: '编辑', exact: true }).click()
  await page.getByLabel('标题', { exact: true }).fill('深度工作时段（已修订）')
  await page.getByRole('button', { name: '保存修改', exact: true }).click()
  await expect(page.getByText('深度工作时段（已修订）', { exact: true })).toBeVisible()

  await page.getByRole('tab', { name: '每日复盘' }).click()
  await pageHealth(page)
  await screenshot(page, info, 'desktop-report')

  if (info.project.name.includes('mobile')) {
    await page.evaluate(() => {
      localStorage.setItem('itime.appearance', JSON.stringify({
        theme: 'dark',
        fontSize: 'larger',
      }))
    })
    await page.setViewportSize({ width: 320, height: 700 })
    await page.reload()
    await expect(page.getByRole('heading', { name: '事实概览' })).toBeVisible()
    await pageHealth(page)
    await screenshot(page, info, '320-large-dark')
  }
  expect(consoleErrors).toEqual([])
})

test('cancelled review report preserves inputs and releases the active state', async ({
  page,
  request,
}) => {
  await account(page, request)
  await page.goto('/reviews')
  await page.getByLabel('补充事实').fill('离页恢复')
  await page.getByRole('button', { name: '保存事实' }).click()
  await page.getByRole('button', { name: '跳过，生成报告' }).click()
  await expect(page.getByRole('button', { name: '取消', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '取消', exact: true }).click()
  await expect(page.getByText('生成已取消，本次额度未扣除。')).toBeVisible()
  await expect(page.getByRole('button', { name: '跳过，生成报告' })).toBeEnabled()
})
