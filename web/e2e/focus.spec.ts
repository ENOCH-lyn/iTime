import AxeBuilder from '@axe-core/playwright'
import { expect, test, type APIRequestContext, type Page, type TestInfo } from '@playwright/test'
import { randomUUID } from 'node:crypto'
import { mkdir } from 'node:fs/promises'

const password = 'Valid-focus-ui-password-123'
const evidence = '/tmp/itime-ui-evidence'
const runtimeErrors = new WeakMap<Page, string[]>()

async function screenshot(page: Page, info: TestInfo, name: string) {
  await mkdir(evidence, { recursive: true })
  await page.screenshot({
    path: `${evidence}/${info.project.name}-focus-${name}.png`,
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
  const address = `focus-ui-${randomUUID()}@example.com`
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

  const session = await (await page.request.get('/api/v1/auth/me')).json()
  const settings = (await (
    await page.request.get('/api/v1/time-settings')
  ).json()).settings
  const response = await page.request.put('/api/v1/time-settings', {
    headers: { 'X-CSRF-Token': session.csrf_token },
    data: {
      operation_id: randomUUID(),
      expected_version: settings.version,
      time_zone: 'Asia/Shanghai',
      week_starts_on: 1,
      activity_day_start_minute: 240,
    },
  })
  expect(response.ok()).toBeTruthy()
  return session.csrf_token as string
}

async function createTask(page: Page, csrf: string) {
  const response = await page.request.post('/api/v1/tasks', {
    headers: { 'X-CSRF-Token': csrf },
    data: {
      operation_id: randomUUID(),
      title: '整理课程笔记',
      description: '专注页真实任务',
      project_id: null,
      estimated_minutes: 45,
      priority: 2,
      labels: ['专注'],
      links: [],
      steps: [
        { title: '整理重点' },
        { title: '补充例题' },
      ],
    },
  })
  expect(response.ok()).toBeTruthy()
  return (await response.json()).task
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
  page.on('response', response => {
    if (response.url().includes('/api/v1/') && response.status() >= 500) {
      errors.push(`${response.status()} ${response.url()}`)
    }
  })
}

async function assertQuality(page: Page) {
  expect(runtimeErrors.get(page) ?? []).toEqual([])
  const overflow = await page.evaluate(() => (
    [...document.querySelectorAll<HTMLElement>('body *')]
      .filter(element => {
        const style = getComputedStyle(element)
        if (
          style.display === 'none'
          || style.visibility === 'hidden'
          || style.opacity === '0'
          || style.position === 'fixed'
        ) return false
        const rect = element.getBoundingClientRect()
        return rect.left < -1 || rect.right > window.innerWidth + 1
      })
      .map(element => {
        const rect = element.getBoundingClientRect()
        return {
          element: `${element.tagName.toLowerCase()}.${element.className}`,
          left: Math.round(rect.left),
          right: Math.round(rect.right),
          width: Math.round(rect.width),
        }
      })
  ))
  expect(overflow).toEqual([])
  expect(await new AxeBuilder({ page }).analyze()).toMatchObject({ violations: [] })
}

test.beforeEach(async ({ page }) => {
  watchRuntime(page)
})

test('自由专注可暂停恢复结束并从历史修订', async ({ page, request }, info) => {
  await account(page, request)
  await page.goto('/focus')
  await expect(page.getByRole('heading', { name: '专注' })).toBeVisible()
  await page.locator('.focus-setup').getByLabel('专注内容').fill('阅读设计资料')
  await page.getByRole('button', { name: '开始专注' }).click()
  await expect(page.getByText('进行中', { exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: '阅读设计资料' })).toBeVisible()

  await page.getByRole('button', { name: '暂停' }).click()
  await expect(page.getByText('已暂停', { exact: true })).toBeVisible()
  await page.reload()
  await expect(page.getByText('已暂停', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '继续' }).click()
  await expect(page.getByText('进行中', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '结束' }).click()
  await expect(page.getByText('本次专注已结束。')).toBeVisible()
  await expect(page.getByRole('heading', { name: '阅读设计资料' })).toBeVisible()

  await page.locator('.focus-main').getByRole('button', { name: '修订记录' }).click()
  await page.getByLabel('修订原因').fill('补记短暂离开时间')
  await page.getByLabel('有效时长（分钟）').fill('0')
  await page.getByRole('button', { name: '保存修订' }).click()
  await expect(page.getByText('专注记录已修订。')).toBeVisible()
  await expect(
    page.locator('.focus-main').getByText('已修订', { exact: true }),
  ).toBeVisible()
  await screenshot(page, info, 'history-revised')
  await assertQuality(page)
})

test('任务倒计时的步骤和任务完成保持独立', async ({ page, request }, info) => {
  const csrf = await account(page, request)
  const task = await createTask(page, csrf)
  await page.goto(`/focus?task=${task.id}`)
  await page.locator('.focus-mode label').filter({ hasText: '倒计时' }).click()
  await page.getByLabel('目标分钟').fill('1')
  await page.getByRole('button', { name: '开始专注' }).click()
  await expect(page.getByRole('heading', { name: '整理课程笔记' })).toBeVisible()

  await page.getByLabel('整理重点').check()
  await page.getByLabel('补充例题').check()
  await expect(page.getByText('2 / 2 步')).toBeVisible()
  const taskBeforeEnd = await (
    await page.request.get(`/api/v1/tasks/${task.id}`)
  ).json()
  expect(taskBeforeEnd.task.status).toBe('open')

  await page.getByRole('button', { name: '结束' }).click()
  await expect(page.getByRole('button', { name: '完成任务' })).toBeVisible()
  await page.getByRole('button', { name: '完成任务' }).click()
  await expect(page.getByText('关联任务已完成。')).toBeVisible()
  const taskAfterEnd = await (
    await page.request.get(`/api/v1/tasks/${task.id}`)
  ).json()
  expect(taskAfterEnd.task.status).toBe('completed')

  await screenshot(page, info, 'task-finished')
  await assertQuality(page)

  if (info.project.name === 'desktop') {
    await page.evaluate(() => {
      document.documentElement.dataset.fontSize = 'large'
      document.documentElement.dataset.theme = 'dark'
      window.resizeTo(320, 700)
    })
    await page.setViewportSize({ width: 320, height: 700 })
    await expect(page.locator('.focus-toolbar').getByRole('heading', { name: '专注' })).toBeVisible()
    await screenshot(page, info, '320-large-dark')
    await assertQuality(page)
  }
})
