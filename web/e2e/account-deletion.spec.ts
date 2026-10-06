import AxeBuilder from '@axe-core/playwright'
import { randomUUID } from 'node:crypto'
import { mkdir } from 'node:fs/promises'
import {
  expect,
  test,
  type APIRequestContext,
  type Page,
  type TestInfo,
} from '@playwright/test'

const password = 'Valid-account-deletion-password-123'
const runtimeErrors = new WeakMap<Page, string[]>()
const evidence = '/tmp/itime-ui-evidence'

async function screenshot(page: Page, info: TestInfo, name: string) {
  await mkdir(evidence, { recursive: true })
  await page.screenshot({
    path: `${evidence}/${info.project.name}-account-deletion-${name}.png`,
    fullPage: name !== 'dialog',
    animations: 'disabled',
  })
}

async function mailMessageIds(
  request: APIRequestContext,
  address: string,
) {
  const response = await request.get(
    `http://127.0.0.1:58025/api/v1/search?query=${encodeURIComponent(`to:${address}`)}`,
  )
  const result = await response.json()
  return result.messages.map((message: { ID: string }) => message.ID) as string[]
}

async function mailLink(
  request: APIRequestContext,
  address: string,
  excludedIds: string[] = [],
) {
  let id = ''
  await expect.poll(async () => {
    const response = await request.get(
      `http://127.0.0.1:58025/api/v1/search?query=${encodeURIComponent(`to:${address}`)}`,
    )
    const result = await response.json()
    id = result.messages.find((message: { ID: string; Subject: string }) =>
      message.Subject.includes('验证邮箱')
      && !excludedIds.includes(message.ID))?.ID ?? ''
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
  requestedEmail?: string,
  excludedMailIds: string[] = [],
) {
  const email = requestedEmail
    ?? `account-deletion-${randomUUID()}@example.com`
  await page.goto('/register')
  await page.getByLabel('邮箱', { exact: true }).fill(email)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByLabel('确认密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '创建账号', exact: true }).click()
  await page.goto(await mailLink(request, email, excludedMailIds))
  await expect(page).toHaveURL('/ai')
  const session = await (await page.request.get('/api/v1/auth/me')).json()
  return {
    email,
    userId: session.user.id as string,
    csrf: session.csrf_token as string,
  }
}

async function createTask(page: Page, csrf: string, marker: string) {
  const response = await page.request.post('/api/v1/tasks', {
    headers: { 'X-CSRF-Token': csrf },
    data: {
      operation_id: randomUUID(),
      title: marker,
      description: '用于验证注销撤销与最终清理的持久任务。',
      project_id: null,
      estimated_minutes: 45,
      priority: 2,
      labels: ['注销验证'],
      links: [],
      steps: [{ title: '核对恢复和清理结果' }],
    },
  })
  expect(response.status()).toBe(201)
  return (await response.json()).task.id as string
}

async function createReadyExport(page: Page, csrf: string) {
  const created = await page.request.post('/api/v1/data-exports', {
    headers: { 'X-CSRF-Token': csrf },
    data: { operation_id: randomUUID() },
  })
  expect(created.status()).toBe(202)
  const jobId = (await created.json()).job.id as string
  await expect.poll(async () => {
    const response = await page.request.get(
      `/api/v1/data-exports/${jobId}`,
    )
    return response.ok() ? (await response.json()).job.status : response.status()
  }, { timeout: 20_000 }).toBe('succeeded')
  const download = await page.request.get(
    `/api/v1/data-exports/${jobId}/download`,
    { headers: { 'X-CSRF-Token': csrf } },
  )
  expect(download.status()).toBe(200)
  return jobId
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

test('restore once, then let the real worker permanently purge the account', async ({
  page,
  context,
  browser,
  request,
}, info) => {
  test.setTimeout(90_000)
  const account = await registerAndLogin(page, request)
  const marker = `注销保留与清理任务 ${randomUUID()}`
  const taskId = await createTask(page, account.csrf, marker)
  const exportId = await createReadyExport(page, account.csrf)
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
    await screenshot(page, info, 'pending')
    await page.getByRole(
      'button',
      { name: '撤销注销并登录', exact: true },
    ).click()
    await expect(page.getByRole('heading', { name: '账号已恢复' }))
      .toBeVisible()
    await page.getByRole('button', { name: '进入 iTime', exact: true }).click()
    await expect(page).toHaveURL('/ai')

    const restoredSession = await (
      await page.request.get('/api/v1/auth/me')
    ).json()
    const restoredCsrf = restoredSession.csrf_token as string
    const restoredTask = await page.request.get(`/api/v1/tasks/${taskId}`)
    expect(restoredTask.status()).toBe(200)
    expect((await restoredTask.json()).task.title).toBe(marker)
    const expiredExport = await page.request.get(
      `/api/v1/data-exports/${exportId}/download`,
      { headers: { 'X-CSRF-Token': restoredCsrf } },
    )
    expect(expiredExport.status()).toBe(410)
    expect((await expiredExport.json()).error.code)
      .toBe('data_export_expired')

    const controlResponse = await page.request.post(
      '/api/__test/account-deletion-expiry-ticket',
      { headers: { 'X-CSRF-Token': restoredCsrf } },
    )
    expect(controlResponse.status()).toBe(200)
    const ticket = (await controlResponse.json()).ticket as string

    const secondDeletion = await page.request.post(
      '/api/v1/account-deletion',
      {
        headers: { 'X-CSRF-Token': restoredCsrf },
        data: {
          operation_id: randomUUID(),
          email_confirmation: account.email,
          current_password: password,
        },
      },
    )
    expect(secondDeletion.status()).toBe(202)
    const arbitraryOwner = await page.request.post(
      '/api/__test/account-deletion-expire',
      {
        headers: { 'X-CSRF-Token': restoredCsrf },
        data: { ticket, user_id: randomUUID() },
      },
    )
    expect(arbitraryOwner.status()).toBe(422)
    const wrongCsrf = await page.request.post(
      '/api/__test/account-deletion-expire',
      {
        headers: { 'X-CSRF-Token': 'wrong-csrf-token' },
        data: { ticket },
      },
    )
    expect(wrongCsrf.status()).toBe(403)
    const expired = await page.request.post(
      '/api/__test/account-deletion-expire',
      {
        headers: { 'X-CSRF-Token': restoredCsrf },
        data: { ticket },
      },
    )
    expect(expired.status()).toBe(200)

    await expect.poll(async () => {
      const response = await page.request.post('/api/v1/auth/login', {
        data: {
          email: account.email,
          password,
          device_name: '清理后凭据验证',
        },
      })
      const body = await response.json()
      return [response.status(), body.error?.code]
    }, { timeout: 20_000 }).toEqual([401, 'invalid_credentials'])

    await expect.poll(async () => {
      const response = await page.request.post(
        '/api/__test/account-deletion-proof',
        { data: { ticket } },
      )
      return response.ok() ? response.json() : { status: response.status() }
    }, { timeout: 20_000 }).toEqual({
      user_count: 0,
      email_count: 0,
      owner_row_count: 0,
      owner_tables: {},
      completed_receipt_count: 1,
    })

    await page.goto('/login')
    await page.getByLabel('邮箱', { exact: true }).fill(account.email)
    await page.getByLabel('密码', { exact: true }).fill(password)
    await page.getByRole('button', { name: '登录', exact: true }).click()
    await expect(page.getByRole('alert')).toContainText('邮箱或密码不正确')
    await expect(page.getByText('账号正在注销')).toHaveCount(0)

    const oldMailIds = await mailMessageIds(request, account.email)
    const replacement = await registerAndLogin(
      page,
      request,
      account.email,
      oldMailIds,
    )
    expect(replacement.userId).not.toBe(account.userId)
    expect((await page.request.get(`/api/v1/tasks/${taskId}`)).status())
      .toBe(404)
    const replacementTasks = await page.request.get('/api/v1/tasks?limit=100')
    expect(replacementTasks.status()).toBe(200)
    expect(JSON.stringify(await replacementTasks.json())).not.toContain(marker)
  } finally {
    await secondContext.close()
  }
})

test('request deletion from data settings with guarded confirmation', async ({
  page,
  browser,
  request,
}, info) => {
  const account = await registerAndLogin(page, request)
  const secondContext = await browser.newContext({
    baseURL: 'http://localhost:4173',
  })
  try {
    const secondDevice = await secondContext.newPage()
    await login(secondDevice, account.email)
    await page.goto('/account/data')
    await page.emulateMedia({ reducedMotion: 'reduce' })
    if (info.project.name === 'mobile') {
      await page.evaluate(() => localStorage.setItem(
        'itime-appearance',
        JSON.stringify({ theme: 'dark', fontSize: 'larger' }),
      ))
      await page.setViewportSize({ width: 320, height: 700 })
      await page.reload()
    }
    await expect(page).toHaveTitle('数据管理 · iTime')
    await expect(page.getByRole('heading', { name: '最近的数据包' }))
      .toHaveCount(0)
    await expect(page.getByText('还没有生成过数据包。')).toBeVisible()
    await expect(page.getByRole('heading', { name: '注销账号' })).toBeVisible()
    const order = await page.locator(
      '.data-export-settings, .account-deletion-settings',
    ).evaluateAll(elements => elements.map(element =>
      element.classList.contains('data-export-settings')
        ? 'export'
        : 'deletion'))
    expect(order).toEqual(['export', 'deletion'])

    await page.getByRole('button', { name: '注销账号', exact: true }).click()
    let dialog = page.getByRole('dialog', { name: '注销账号？' })
    await expect(dialog).toBeVisible()
    await expect(dialog.getByLabel('完整注册邮箱')).toBeFocused()
    expect(await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    )).toBe(true)
    expect(
      (await new AxeBuilder({ page })
        .include('.account-deletion-dialog')
        .analyze()).violations,
    ).toEqual([])
    await screenshot(page, info, 'dialog')
    await page.keyboard.press('Escape')
    await expect(dialog).toBeHidden()

    await page.getByRole('button', { name: '注销账号', exact: true }).click()
    dialog = page.getByRole('dialog', { name: '注销账号？' })
    await dialog.getByLabel('完整注册邮箱').fill('wrong@example.com')
    await dialog.getByRole('textbox', { name: '当前密码', exact: true })
      .fill(password)
    await dialog.getByRole('button', { name: '确认申请注销' }).click()
    await expect(dialog.getByText('邮箱与当前账号不一致。')).toBeVisible()

    await dialog.getByLabel('完整注册邮箱').fill(account.email)
    await dialog.getByRole('textbox', { name: '当前密码', exact: true })
      .fill('Wrong-password-456')
    await dialog.getByRole('button', { name: '确认申请注销' }).click()
    await expect(dialog.getByRole('alert')).toContainText('邮箱或密码不正确')

    await seedLocalData(page, account.userId, 'pending-before-deletion')
    let confirmationCount = 0
    page.once('dialog', async confirmation => {
      confirmationCount += 1
      expect(confirmation.message()).toContain('2 项操作未同步')
      expect(confirmation.message()).toContain('不会保存')
      await confirmation.dismiss()
    })
    await dialog.getByRole('textbox', { name: '当前密码', exact: true })
      .fill(password)
    await dialog.getByRole('button', { name: '确认申请注销' }).click()
    await expect(dialog).toBeVisible()
    expect(confirmationCount).toBe(1)
    expect((await secondDevice.request.get('/api/v1/auth/me')).status())
      .toBe(200)

    let releaseRequest!: () => void
    let requestStarted!: () => void
    const heldRequest = new Promise<void>(resolve => {
      releaseRequest = resolve
    })
    const requestSeen = new Promise<void>(resolve => {
      requestStarted = resolve
    })
    await page.route('**/api/v1/account-deletion', async route => {
      if (route.request().method() !== 'POST') {
        await route.continue()
        return
      }
      requestStarted()
      await heldRequest
      await route.continue()
    })
    page.once('dialog', confirmation => confirmation.accept())
    const submitButton = dialog.getByRole(
      'button',
      { name: '确认申请注销' },
    )
    const submission = submitButton.click()
    await requestSeen
    await expect(dialog.getByRole('button', { name: '正在提交…' }))
      .toBeDisabled()
    await expect(dialog.getByRole('button', { name: '取消' })).toBeDisabled()
    releaseRequest()
    await submission
    await page.unroute('**/api/v1/account-deletion')
    await expect(page).toHaveURL('/login')
    await expect(page.getByText('注销申请已提交')).toBeVisible()
    await expect(page.getByText(/7 天内/)).toBeVisible()
    expect((await secondDevice.request.get('/api/v1/auth/me')).status())
      .toBe(401)
    await expect.poll(
      async () => localDataState(page, account.userId),
    ).toEqual({ localKeys: [], cursor: null, cacheRows: 0 })
    expect(await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    )).toBe(true)
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
    await screenshot(page, info, 'requested')
    if (info.project.name === 'mobile') {
      await screenshot(page, info, '320-large-dark')
    }

    await page.getByLabel('邮箱', { exact: true }).fill(account.email)
    await page.getByLabel('密码', { exact: true }).fill(password)
    await page.getByRole('button', { name: '登录', exact: true }).click()
    await page.getByRole(
      'button',
      { name: '撤销注销并登录', exact: true },
    ).click()
    await expect(page.getByRole('heading', { name: '账号已恢复' }))
      .toBeVisible()
  } finally {
    await secondContext.close()
  }
})
