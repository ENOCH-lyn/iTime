import { randomUUID } from 'node:crypto'
import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

const password = 'Valid-sync-ui-password-123'

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
  const address = `sync-ui-${randomUUID()}@example.com`
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
  return await (await page.request.get('/api/v1/auth/me')).json()
}

async function createTask(page: Page, csrf: string, title: string) {
  const response = await page.request.post('/api/v1/tasks', {
    headers: { 'X-CSRF-Token': csrf },
    data: {
      operation_id: randomUUID(),
      title,
      description: '验证离线读取缓存。',
      project_id: null,
      estimated_minutes: 30,
      priority: 1,
      labels: [],
      links: [],
      steps: [{ title: '离线可见步骤' }],
    },
  })
  expect(response.ok()).toBeTruthy()
  return (await response.json()).task
}

test('offline cache is encrypted and never hides an HTTP failure', async ({
  page,
  request,
}) => {
  const session = await account(page, request)
  const marker = `离线缓存任务 ${randomUUID()}`
  await createTask(page, session.csrf_token, marker)
  await page.goto('/tasks')
  await expect(page.getByText(marker, { exact: true })).toBeVisible()

  await expect.poll(async () => page.evaluate(async () => {
    const open = indexedDB.open('itime-sync-cache')
    const database = await new Promise<IDBDatabase>((resolve, reject) => {
      open.onsuccess = () => resolve(open.result)
      open.onerror = () => reject(open.error)
    })
    const transaction = database.transaction('responses', 'readonly')
    const request = transaction.objectStore('responses').getAll()
    const rows = await new Promise<unknown[]>((resolve, reject) => {
      request.onsuccess = () => resolve(request.result)
      request.onerror = () => reject(request.error)
    })
    database.close()
    return rows.length
  })).toBeGreaterThan(0)
  expect(await page.evaluate(async markerText => {
    const open = indexedDB.open('itime-sync-cache')
    const database = await new Promise<IDBDatabase>((resolve, reject) => {
      open.onsuccess = () => resolve(open.result)
      open.onerror = () => reject(open.error)
    })
    const transaction = database.transaction('responses', 'readonly')
    const request = transaction.objectStore('responses').getAll()
    const rows = await new Promise<unknown[]>((resolve, reject) => {
      request.onsuccess = () => resolve(request.result)
      request.onerror = () => reject(request.error)
    })
    database.close()
    return JSON.stringify(rows).includes(markerText)
  }, marker)).toBe(false)

  await page.getByRole('button', { name: '已归档', exact: true }).click()
  await expect(page.getByRole('heading', { name: '已归档' })).toBeVisible()
  await expect(page.locator('.task-list li')).toHaveCount(0)
  await page.route('**/api/v1/tasks?**', route => route.abort('failed'))
  await page.getByRole('button', { name: '活动', exact: true }).click()
  await expect(page.getByText(marker, { exact: true })).toBeVisible()

  await page.unroute('**/api/v1/tasks?**')
  await page.route('**/api/v1/tasks?**', route => route.fulfill({
    status: 503,
    contentType: 'application/json',
    body: JSON.stringify({
      error: {
        code: 'service_unavailable',
        message: '同步测试服务错误。',
        request_id: 'sync-cache-http-error',
        saved: 'not_saved',
        retryable: true,
        details: {},
      },
    }),
  }))
  await page.getByRole('button', { name: '已归档', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('同步测试服务错误')
})

test('sync coordinator reports events offline state and cursor recovery', async ({
  page,
  request,
  context,
}) => {
  const session = await account(page, request)
  await page.goto('/account/sync')
  await expect(page.getByRole('heading', { name: '同步与离线' })).toBeVisible()
  await expect(page.getByText('已同步', { exact: true })).toBeVisible()

  const marker = `协调器任务 ${randomUUID()}`
  await createTask(page, session.csrf_token, marker)
  await expect.poll(async () => (
    page.getByTestId('sync-latest-change').textContent()
  ), { timeout: 5_000 }).not.toBe('暂无变更')

  await page.goto('/tasks')
  await expect(page.getByLabel(/同步状态：已同步/)).toBeVisible()
  await context.setOffline(true)
  await expect(page.getByText('当前离线。显示最近同步的数据。')).toBeVisible()
  await expect(page.getByLabel(/同步状态：离线/)).toBeVisible()
  await context.setOffline(false)
  await page.getByLabel(/同步状态：/).click()
  await expect(page).toHaveURL('/account/sync')
  await expect(page.getByText('已同步', { exact: true })).toBeVisible()

  await page.evaluate(userId => {
    sessionStorage.setItem(`itime-sync-cursor:${userId}`, 'invalid.cursor')
  }, session.user.id)
  await page.reload()
  await expect(page.getByText('同步基线已重新建立。')).toBeVisible()
  await expect(page.getByText('已同步', { exact: true })).toBeVisible()
})
