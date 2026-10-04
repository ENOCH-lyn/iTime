import AxeBuilder from '@axe-core/playwright'
import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from '@playwright/test'
import { randomUUID } from 'node:crypto'

const password = 'Valid-transcription-ui-password-123'
const runtimeErrors = new WeakMap<Page, string[]>()

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
  const address = `transcription-ui-${randomUUID()}@example.com`
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
}

function taskVoiceControl(page: Page) {
  return page.getByRole('region', { name: '任务名称语音输入' })
}

async function recordTaskVoice(page: Page) {
  const control = taskVoiceControl(page)
  await control.getByRole('button', {
    name: /^(开始语音输入|重新录制语音)$/,
  }).click()
  await expect(control.getByRole('status')).toContainText('正在录音')
  await page.waitForTimeout(500)
  await control.getByRole('button', { name: '停止录音' }).click()
  await expect(control.getByLabel('转写草稿')).toHaveValue(
    '周五前完成论文提纲',
    { timeout: 15_000 },
  )
  return control
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

test.beforeEach(async ({ page }) => {
  watchRuntime(page)
})

test.afterEach(async ({ page }) => {
  expect(runtimeErrors.get(page)).toEqual([])
})

test('任务语音控件录制、转写并展示独立草稿', async ({ page, request }) => {
  await account(page, request)
  await page.goto('/tasks')
  await page.getByRole('button', { name: '新建任务', exact: true }).click()

  const control = await recordTaskVoice(page)
  await control.getByRole('button', { name: '应用到任务名称' }).click()
  await expect(page.getByLabel('任务名称', { exact: true })).toHaveValue(
    '周五前完成论文提纲',
  )
  await expect(control).toContainText('尚未保存')
  await page.getByRole('button', { name: '创建任务', exact: true }).first().click()
  await expect(page.getByText('任务已创建并保存到服务端。')).toBeVisible()
  expect(await new AxeBuilder({ page }).analyze()).toMatchObject({ violations: [] })
  expect(await page.evaluate(
    () => document.documentElement.scrollWidth <= innerWidth,
  )).toBe(true)
})

test('任务转写不覆盖后续手写且阻止超限替换', async ({ page, request }) => {
  await account(page, request)
  await page.goto('/tasks')
  await page.getByRole('button', { name: '新建任务', exact: true }).click()
  await page.getByLabel('任务名称', { exact: true }).fill('原始手写标题')

  const control = taskVoiceControl(page)
  await control.getByRole('button', { name: '开始语音输入' }).click()
  await expect(control.getByRole('status')).toContainText('正在录音')
  await page.waitForTimeout(500)
  await control.getByRole('button', { name: '停止录音' }).click()
  await page.getByLabel('任务名称', { exact: true }).fill('转写期间继续手写')
  await expect(control.getByLabel('转写草稿')).toHaveValue(
    '周五前完成论文提纲',
    { timeout: 15_000 },
  )
  await expect(page.getByLabel('任务名称', { exact: true })).toHaveValue(
    '转写期间继续手写',
  )
  await expect(control.getByRole('button', { name: '替换当前内容' })).toBeVisible()
  await expect(control.getByRole('button', { name: '追加到当前内容' })).toBeVisible()

  await control.getByLabel('转写草稿').fill('长'.repeat(201))
  await expect(control.getByText('任务名称不能超过 200 个字符')).toBeVisible()
  await expect(control.getByRole('button', { name: '替换当前内容' })).toBeDisabled()
  await control.getByLabel('转写草稿').fill('改为校对后的标题')
  await control.getByRole('button', { name: '替换当前内容' }).click()
  await expect(page.getByLabel('任务名称', { exact: true })).toHaveValue(
    '改为校对后的标题',
  )
  await expect(control).toContainText('尚未保存')

  const secondControl = await recordTaskVoice(page)
  await secondControl.getByLabel('转写草稿').fill('补充检查引用')
  await secondControl.getByRole('button', { name: '追加到当前内容' }).click()
  await expect(page.getByLabel('任务名称', { exact: true })).toHaveValue(
    '改为校对后的标题\n补充检查引用',
  )

  await page.route('**/api/v1/tasks', route => {
    if (route.request().method() === 'POST') void route.abort('failed')
    else void route.continue()
  })
  await page.getByRole('button', { name: '创建任务', exact: true }).first().click()
  await expect(page.getByText('无法连接服务')).toBeVisible()
  await expect(page.getByLabel('任务名称', { exact: true })).toHaveValue(
    '改为校对后的标题\n补充检查引用',
  )
  await page.unroute('**/api/v1/tasks')
})

test('已归档和回收站任务不提供语音入口', async ({ page, request }) => {
  await account(page, request)
  await page.goto('/tasks')
  await page.getByRole('button', { name: '新建任务', exact: true }).click()
  await page.getByLabel('任务名称', { exact: true }).fill('语音入口生命周期')
  await page.getByRole('button', { name: '创建任务', exact: true }).first().click()
  await expect(page.getByText('任务已创建并保存到服务端。')).toBeVisible()

  await page.getByRole('button', { name: '归档', exact: true }).click()
  await expect(page.getByText('任务已归档。')).toBeVisible()
  await page.getByRole('group', { name: '任务范围' })
    .getByRole('button', { name: '已归档', exact: true }).click()
  await page.locator('.task-list li')
    .filter({ hasText: '语音入口生命周期' }).getByRole('button').click()
  await expect(page.getByText('此任务已归档')).toBeVisible()
  await expect(taskVoiceControl(page)).toHaveCount(0)

  page.once('dialog', dialog => dialog.accept())
  await page.getByRole('button', { name: '移到回收站', exact: true }).click()
  await expect(page.getByText('任务已移到回收站，将保留 30 天。')).toBeVisible()
  await page.getByRole('group', { name: '任务范围' })
    .getByRole('button', { name: '回收站', exact: true }).click()
  await page.locator('.task-list li')
    .filter({ hasText: '语音入口生命周期' }).getByRole('button').click()
  await expect(page.getByText('此任务在回收站中')).toBeVisible()
  await expect(taskVoiceControl(page)).toHaveCount(0)
})
