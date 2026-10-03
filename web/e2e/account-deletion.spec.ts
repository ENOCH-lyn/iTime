import { randomUUID } from 'node:crypto'
import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from '@playwright/test'

const password = 'Valid-account-deletion-password-123'
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

async function registerAndLogin(
  page: Page,
  request: APIRequestContext,
) {
  const email = `account-deletion-${randomUUID()}@example.com`
  await page.goto('/register')
  await page.getByLabel('邮箱', { exact: true }).fill(email)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByLabel('确认密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '创建账号', exact: true }).click()
  await page.goto(await mailLink(request, email))
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '验证邮箱', exact: true }).click()
  await page.goto('/login')
  await page.getByLabel('邮箱', { exact: true }).fill(email)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL('/ai')
  const session = await (await page.request.get('/api/v1/auth/me')).json()
  return {
    email,
    userId: session.user.id as string,
    csrf: session.csrf_token as string,
  }
}

async function login(page: Page, email: string) {
  await page.goto('/login')
  await page.getByLabel('邮箱', { exact: true }).fill(email)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL('/ai')
}

async function seedLocalData(
  page: Page,
  userId: string,
  marker: string,
) {
  await page.evaluate(async ({ userId, marker }) => {
    const operationId = crypto.randomUUID()
    const taskId = crypto.randomUUID()
    const stepId = crypto.randomUUID()
    const sessionId = crypto.randomUUID()
    const timestamp = new Date().toISOString()
    localStorage.setItem(`itime-sync-queue:${userId}`, JSON.stringify([{
      schema_version: 1,
      kind: 'task_step_completion',
      operation_id: operationId,
      task_id: taskId,
      step_id: stepId,
      expected_task_version: 1,
      base_completed: false,
      completed: true,
      created_at: timestamp,
    }]))
    localStorage.setItem(
      `itime-focus-queue:${userId}:${sessionId}`,
      JSON.stringify([{
        operation_id: crypto.randomUUID(),
        session_id: sessionId,
        expected_version: 1,
        action: 'pause',
        client_recorded_at: timestamp,
        payload: {},
        created_at: timestamp,
      }]),
    )
    sessionStorage.setItem(`itime-sync-cursor:${userId}`, marker)
    await new Promise<void>((resolve, reject) => {
      const request = indexedDB.open('itime-sync-cache', 1)
      request.onupgradeneeded = () => {
        const database = request.result
        if (!database.objectStoreNames.contains('responses')) {
          const store = database.createObjectStore(
            'responses',
            { keyPath: 'id' },
          )
          store.createIndex('account_id', 'account_id')
          store.createIndex('accessed_at', 'accessed_at')
        }
      }
      request.onerror = () => reject(request.error)
      request.onsuccess = () => {
        const database = request.result
        const transaction = database.transaction('responses', 'readwrite')
        transaction.objectStore('responses').put({
          id: `${userId}|/account-deletion-test`,
          account_id: userId,
          key: '/account-deletion-test',
          iv: new Uint8Array(12).buffer,
          ciphertext: new TextEncoder().encode(marker).buffer,
          byte_size: marker.length,
          cached_at: timestamp,
          accessed_at: timestamp,
        })
        transaction.oncomplete = () => {
          database.close()
          resolve()
        }
        transaction.onerror = () => reject(transaction.error)
      }
    })
  }, { userId, marker })
}

async function localDataState(page: Page, userId: string) {
  return page.evaluate(async userId => {
    const localKeys = Object.keys(localStorage)
      .filter(key => key.includes(userId))
    const cursor = sessionStorage.getItem(`itime-sync-cursor:${userId}`)
    const cacheRows = await new Promise<number>((resolve, reject) => {
      const request = indexedDB.open('itime-sync-cache', 1)
      request.onerror = () => reject(request.error)
      request.onsuccess = () => {
        const database = request.result
        const transaction = database.transaction('responses', 'readonly')
        const index = transaction.objectStore('responses').index('account_id')
        const countRequest = index.count(IDBKeyRange.only(userId))
        countRequest.onsuccess = () => resolve(countRequest.result)
        countRequest.onerror = () => reject(countRequest.error)
        transaction.oncomplete = () => database.close()
      }
    })
    return { localKeys, cursor, cacheRows }
  }, userId)
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
test.afterEach(async ({ page }) => {
  expect(runtimeErrors.get(page)).toEqual([])
})

test('restore pending deletion and clear local data across tabs', async ({
  page,
  context,
  browser,
  request,
}) => {
  const account = await registerAndLogin(page, request)
  const tab = await context.newPage()
  await tab.goto('/account/sync')
  await expect(tab.getByRole('heading', { name: '同步与离线' }))
    .toBeVisible()

  const secondContext = await browser.newContext({
    baseURL: 'http://localhost:4173',
  })
  try {
    const secondDevice = await secondContext.newPage()
    await login(secondDevice, account.email)

    await seedLocalData(page, account.userId, 'first-tab')
    await seedLocalData(tab, account.userId, 'second-tab')
    expect((await localDataState(page, account.userId)).cacheRows).toBe(1)

    const deletion = await page.request.post(
      '/api/v1/account-deletion',
      {
        headers: { 'X-CSRF-Token': account.csrf },
        data: {
          operation_id: randomUUID(),
          email_confirmation: account.email,
          current_password: password,
        },
      },
    )
    expect(deletion.status()).toBe(202)

    await page.evaluate(userId => {
      const channel = new BroadcastChannel('itime-auth')
      channel.postMessage({ type: 'discard', user_id: userId })
      channel.close()
    }, account.userId)
    await expect.poll(
      async () => localDataState(page, account.userId),
    ).toEqual({ localKeys: [], cursor: null, cacheRows: 0 })
    await expect.poll(
      async () => localDataState(tab, account.userId),
    ).toEqual({ localKeys: [], cursor: null, cacheRows: 0 })
    await expect(tab.getByRole('link', { name: '返回登录', exact: true }))
      .toBeVisible()
    expect((await secondDevice.request.get('/api/v1/auth/me')).status())
      .toBe(401)

    await page.goto('/login')
    await page.getByLabel('邮箱', { exact: true }).fill(account.email)
    await page.getByLabel('密码', { exact: true }).fill('Wrong-password-456')
    await page.getByRole('button', { name: '登录', exact: true }).click()
    await expect(page.getByRole('alert')).toContainText('邮箱或密码不正确')
    await expect(page.getByText('账号正在注销')).toHaveCount(0)

    await page.getByLabel('密码', { exact: true }).fill(password)
    await page.getByRole('button', { name: '登录', exact: true }).click()
    await expect(page.getByRole('heading', { name: '账号正在注销' }))
      .toBeVisible()
    await expect(page.getByText('7 天')).toBeVisible()
    await page.getByRole(
      'button',
      { name: '撤销注销并登录', exact: true },
    ).click()
    await expect(page.getByRole('heading', { name: '账号已恢复' }))
      .toBeVisible()
    await page.getByRole('button', { name: '进入 iTime', exact: true }).click()
    await expect(page).toHaveURL('/ai')
  } finally {
    await secondContext.close()
  }
})
