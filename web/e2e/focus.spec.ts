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

async function sessionUserId(page: Page) {
  const response = await page.request.get('/api/v1/auth/me')
  expect(response.ok()).toBeTruthy()
  return ((await response.json()).user.id) as string
}

async function startFreeFocus(page: Page, title: string) {
  await page.goto('/focus')
  await expect(page.getByRole('heading', { name: '专注' })).toBeVisible()
  await page.locator('.focus-setup').getByLabel('专注内容').fill(title)
  await page.getByRole('button', { name: '开始专注' }).click()
  await expect(page.getByRole('heading', { name: title })).toBeVisible()
  return (await (
    await page.request.get('/api/v1/focus-sessions/current')
  ).json()).focus
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

test('离线暂停恢复结束按原顺序同步', async ({ page, request, context }, info) => {
  const csrf = await account(page, request)
  const task = await createTask(page, csrf)
  await page.goto(`/focus?task=${task.id}`)
  await page.getByRole('button', { name: '开始专注' }).click()
  await expect(page.getByRole('heading', { name: '整理课程笔记' })).toBeVisible()
  const focus = (await (
    await page.request.get('/api/v1/focus-sessions/current')
  ).json()).focus

  await context.setOffline(true)
  await page.getByRole('button', { name: '暂停' }).click()
  await expect(page.getByText('1 项待同步')).toBeVisible()
  await expect(page.getByText('已暂停', { exact: true })).toBeVisible()
  await page.getByLabel('整理重点').check()
  await expect(page.getByText('2 项待同步')).toBeVisible()
  await page.getByRole('button', { name: '继续' }).click()
  await expect(page.getByText('3 项待同步')).toBeVisible()
  await expect(page.getByText('进行中', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '结束' }).click()
  await expect(page.getByText('4 项待同步')).toBeVisible()
  await expect(page.getByText('已结束', { exact: true })).toBeVisible()

  await context.setOffline(false)
  await expect(page.getByText('已与服务端同步')).toBeVisible()
  await expect.poll(async () => {
    const detail = await (
      await page.request.get(`/api/v1/focus-sessions/${focus.id}`)
    ).json()
    return detail.events.map((event: { kind: string }) => event.kind)
  }).toEqual(['start', 'pause', 'progress', 'resume', 'finish'])
  const savedTask = await (await page.request.get(`/api/v1/tasks/${task.id}`)).json()
  expect(savedTask.task.status).toBe('open')
  expect(savedTask.task.step_progress).toEqual({ total: 2, completed: 1 })
  await screenshot(page, info, 'offline-synced')
  await assertQuality(page)
})

test('未知响应刷新后使用原操作号恢复且不重复保存', async ({
  page,
  request,
  context,
}, info) => {
  await account(page, request)
  const focus = await startFreeFocus(page, '响应恢复验证')
  let dropped = false
  await page.route('**/api/v1/focus-sessions/*/transitions', async route => {
    if (dropped) {
      await route.continue()
      return
    }
    dropped = true
    const intercepted = route.request()
    const cookie = (await context.cookies())
      .map(item => `${item.name}=${item.value}`)
      .join('; ')
    await route.fulfill({
      status: 503,
      contentType: 'application/json',
      body: JSON.stringify({
        error: {
          code: 'service_unavailable',
          message: '测试中断响应',
          request_id: randomUUID(),
          saved: 'unknown',
          retryable: true,
        },
      }),
    })
    const saved = await request.post(intercepted.url(), {
      headers: {
        'X-CSRF-Token': intercepted.headers()['x-csrf-token'],
        Cookie: cookie,
      },
      data: intercepted.postDataJSON(),
    })
    expect(saved.ok()).toBeTruthy()
  })

  await page.getByRole('button', { name: '暂停' }).click()
  await expect(page.getByText('1 项待同步')).toBeVisible()
  await page.unroute('**/api/v1/focus-sessions/*/transitions')
  await page.reload()
  await expect(page.getByText('已与服务端同步')).toBeVisible()
  await expect(page.getByText('已暂停', { exact: true })).toBeVisible()

  const detail = await (
    await page.request.get(`/api/v1/focus-sessions/${focus.id}`)
  ).json()
  expect(detail.events.map((event: { kind: string }) => event.kind))
    .toEqual(['start', 'pause'])
  await screenshot(page, info, 'unknown-response-replayed')
  await assertQuality(page)
})

test('开放状态冲突保留队列并可基于服务器版本重新应用', async ({
  page,
  request,
  context,
}) => {
  await account(page, request)
  await startFreeFocus(page, '冲突重放验证')
  const second = await context.newPage()
  watchRuntime(second)
  try {
    await second.goto('/focus')
    await expect(second.getByText('进行中', { exact: true })).toBeVisible()
    await page.route('**/api/v1/focus-sessions/*/transitions', route =>
      route.abort('connectionreset'))
    await second.getByRole('button', { name: '暂停' }).click()
    await expect(second.getByText('已暂停', { exact: true })).toBeVisible()

    await page.getByRole('button', { name: '结束' }).click()
    await expect(page.getByText('1 项待同步')).toBeVisible()
    await page.unroute('**/api/v1/focus-sessions/*/transitions')
    await page.evaluate(() => window.dispatchEvent(new Event('focus')))
    await expect(page.getByRole('heading', { name: '同步冲突' })).toBeVisible()
    await expect(page.getByText('1 项待同步')).toBeVisible()
    await page.getByRole('button', { name: '重新应用下一项' }).click()
    await expect(page.getByText('已与服务端同步')).toBeVisible()
    await expect(page.getByText('已结束', { exact: true })).toBeVisible()
  } finally {
    expect(runtimeErrors.get(second)).toEqual([])
    await second.close()
  }
})

test('终态冲突可转为修订草稿且仅在明确采用后丢弃队列', async ({
  page,
  request,
  context,
}, info) => {
  await account(page, request)
  await startFreeFocus(page, '终态冲突验证')
  const second = await context.newPage()
  watchRuntime(second)
  try {
    await second.goto('/focus')
    await expect(second.getByText('进行中', { exact: true })).toBeVisible()
    await page.route('**/api/v1/focus-sessions/*/transitions', route =>
      route.abort('connectionreset'))
    await second.getByRole('button', { name: '结束' }).click()
    await expect(second.getByText('已结束', { exact: true })).toBeVisible()

    await page.getByRole('button', { name: '结束' }).click()
    await expect(page.getByText('1 项待同步')).toBeVisible()
    await page.unroute('**/api/v1/focus-sessions/*/transitions')
    await page.evaluate(() => window.dispatchEvent(new Event('focus')))
    await expect(page.getByRole('heading', { name: '同步冲突' })).toBeVisible()
    await assertQuality(page)
    await screenshot(page, info, 'terminal-conflict')
    await page.getByRole('button', { name: '转为修订草稿' }).click()
    await expect(page.getByRole('heading', { name: '修订专注记录' })).toBeVisible()
    await expect(page.getByText('1 项待同步')).toBeVisible()
    await page.getByRole('button', { name: '保存修订' }).click()
    await expect(page.getByText('专注记录已修订。')).toBeVisible()
    await expect(page.getByText('已与服务端同步')).toBeVisible()

    await page.locator('.focus-setup').getByLabel('专注内容')
      .fill('采用服务器状态验证')
    await page.getByRole('button', { name: '开始专注' }).click()
    await expect(page.getByRole('heading', { name: '采用服务器状态验证' })).toBeVisible()
    await second.reload()
    await expect(second.getByText('进行中', { exact: true })).toBeVisible()
    await second.getByRole('button', { name: '结束', exact: true }).click()
    await page.bringToFront()
    await page.waitForTimeout(100)
    await page.route('**/api/v1/focus-sessions/*/transitions', route =>
      route.abort('connectionreset'))
    await page.getByRole('button', { name: '结束', exact: true }).click()
    await expect(page.getByText('1 项待同步')).toBeVisible()
    await page.unroute('**/api/v1/focus-sessions/*/transitions')
    await page.evaluate(() => window.dispatchEvent(new Event('focus')))
    await expect(page.getByRole('heading', { name: '同步冲突' })).toBeVisible()
    await page.getByRole('button', { name: '采用服务端状态' }).click()
    await expect(page.getByText('已与服务端同步')).toBeVisible()
    await expect(page.getByText('已结束', { exact: true })).toBeVisible()
    await screenshot(page, info, 'terminal-conflict-adopted')
    await assertQuality(page)
  } finally {
    expect(runtimeErrors.get(second)).toEqual([])
    await second.close()
  }
})

test('队列按账号隔离并执行 100 项与 7 天边界', async ({
  page,
  request,
}) => {
  await account(page, request)
  const userId = await sessionUserId(page)
  const focus = await startFreeFocus(page, '队列边界验证')
  const now = Date.now()
  const queue = Array.from({ length: 100 }, (_, index) => ({
    operation_id: randomUUID(),
    session_id: focus.id,
    expected_version: focus.version + index,
    action: index % 2 === 0 ? 'pause' : 'resume',
    client_recorded_at: new Date(now + index).toISOString(),
    payload: {},
    created_at: new Date(now).toISOString(),
  }))
  queue.push({
    ...queue[0],
    operation_id: randomUUID(),
    created_at: new Date(now - 8 * 24 * 60 * 60_000).toISOString(),
  })
  queue.push({
    ...queue[0],
    operation_id: randomUUID(),
    action: 'delete' as 'pause',
  })
  await page.evaluate(({ key, items, foreignKey }) => {
    localStorage.setItem(key, JSON.stringify(items))
    localStorage.setItem(foreignKey, JSON.stringify([items[0]]))
  }, {
    key: `itime-focus-queue:${userId}:${focus.id}`,
    items: queue,
    foreignKey: `itime-focus-queue:${randomUUID()}:${focus.id}`,
  })
  await page.route('**/api/v1/focus-sessions/*/transitions', route =>
    route.abort('connectionreset'))
  await page.reload()
  await expect(page.getByText('100 项待同步')).toBeVisible()
  await page.getByRole('button', { name: '暂停' }).click()
  await expect(page.getByRole('alert')).toContainText('最多保留 100 项')
  await expect(page.getByText('100 项待同步')).toBeVisible()
})

test('困难可独立或关联专注记录并完成编辑删除恢复', async ({
  page,
  request,
}, info) => {
  const csrf = await account(page, request)
  const task = await createTask(page, csrf)
  await page.goto('/focus')
  await page.getByLabel('遇到了什么困难？').fill('还没有确定从哪一章开始')
  await page.getByRole('button', { name: '记录困难', exact: true }).click()
  await expect(page.getByText('困难已记录。')).toBeVisible()
  await expect(page.getByText('还没有确定从哪一章开始', { exact: true })).toBeVisible()

  await page.getByLabel('关联任务 可选').selectOption(task.id)
  await page.getByRole('button', { name: '开始专注', exact: true }).click()
  await expect(page.getByRole('heading', { name: task.title, exact: true })).toBeVisible()
  await page.getByRole('tab', { name: '困难', exact: true }).click()
  await page.getByLabel('遇到了什么困难？').fill('例题的条件很多，需要重新拆分推导步骤。')
  await page.getByRole('button', { name: '记录困难', exact: true }).click()
  await expect(page.getByText('困难已记录。')).toBeVisible()

  await page.getByRole('button', {
    name: '编辑困难 例题的条件很多，需要重新拆分推导步骤。',
  }).click()
  await page.getByRole('textbox', { name: '编辑困难', exact: true })
    .fill('例题条件之间的关系还不清楚。')
  await page.getByLabel('分类 可选').fill('理解')
  await page.getByLabel('感受 可选').fill('困惑')
  await page.getByRole('button', { name: '保存困难', exact: true }).click()
  await expect(page.getByText('困难记录已保存。')).toBeVisible()
  await expect(page.getByText('例题条件之间的关系还不清楚。', { exact: true })).toBeVisible()

  page.once('dialog', dialog => dialog.accept())
  await page.getByRole('button', {
    name: '删除困难 例题条件之间的关系还不清楚。',
  }).click()
  await expect(page.getByText('困难已移到回收站，将保留 30 天。')).toBeVisible()
  await page.getByRole('button', { name: '回收站', exact: true }).click()
  await expect(page.getByText('例题条件之间的关系还不清楚。', { exact: true })).toBeVisible()
  await expect(page.getByText(/将于 .* 清理/)).toBeVisible()
  await page.getByRole('button', {
    name: '恢复困难 例题条件之间的关系还不清楚。',
  }).click()
  await expect(page.getByText('困难记录已恢复。')).toBeVisible()
  await page.getByRole('button', { name: '记录', exact: true }).click()
  const aiLink = page.getByRole('link', {
    name: '带到 AI 对话 例题条件之间的关系还不清楚。',
  })
  await expect(aiLink).toHaveAttribute('href', /^\/ai\?difficulty=/)
  await screenshot(page, info, 'difficulties')
  await assertQuality(page)
})
