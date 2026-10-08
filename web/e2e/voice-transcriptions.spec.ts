import AxeBuilder from '@axe-core/playwright'
import {
  expect,
  test,
  type APIRequestContext,
  type Page,
  type TestInfo,
} from '@playwright/test'
import { randomUUID } from 'node:crypto'
import { mkdir } from 'node:fs/promises'

const password = 'Valid-transcription-ui-password-123'
const evidence = '/tmp/itime-ui-evidence'
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
  await expect(page).toHaveURL('/ai')
}

async function configureTimeZone(page: Page) {
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

function difficultyVoiceControl(page: Page) {
  return page.getByRole('region', { name: '困难正文语音输入' })
}

async function recordDifficultyVoice(page: Page) {
  const control = difficultyVoiceControl(page)
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

async function screenshot(page: Page, info: TestInfo, name: string) {
  await mkdir(evidence, { recursive: true })
  await page.screenshot({
    path: `${evidence}/${info.project.name}-voice-transcription-${name}.png`,
    fullPage: true,
  })
}

async function assertPageQuality(page: Page) {
  expect(await page.locator('vite-error-overlay').count()).toBe(0)
  expect(runtimeErrors.get(page)).toEqual([])
  expect(await new AxeBuilder({ page }).analyze()).toMatchObject({ violations: [] })
  expect(await page.evaluate(() => (
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
        return rect.left < -1 || rect.right > innerWidth + 1
      })
      .map(element => {
        const rect = element.getBoundingClientRect()
        return {
          element: `${element.tagName.toLowerCase()}.${element.className}`,
          left: Math.round(rect.left),
          right: Math.round(rect.right),
        }
      })
  ))).toEqual([])
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

test('任务语音控件录制、转写并展示独立草稿', async ({
  page,
  request,
}, info) => {
  await account(page, request)
  await page.goto('/tasks')
  await page.getByRole('button', { name: '新建任务', exact: true }).click()

  const control = await recordTaskVoice(page)
  await screenshot(page, info, 'task-ready')
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

test('困难语音草稿可校对后关联当前专注并保存', async ({ page, request }) => {
  await account(page, request)
  await configureTimeZone(page)
  await page.goto('/focus')
  await page.locator('.focus-setup').getByLabel('专注内容').fill('拆分论文结构')
  await page.getByRole('button', { name: '开始专注', exact: true }).click()
  await expect(page.getByRole('heading', { name: '拆分论文结构' })).toBeVisible()

  const control = await recordDifficultyVoice(page)
  await control.getByLabel('转写草稿').fill('不知道如何拆分这一章')
  await control.getByRole('button', { name: '应用到困难正文' }).click()
  await expect(page.getByLabel('遇到了什么困难？')).toHaveValue(
    '不知道如何拆分这一章',
  )
  await expect(control).toContainText('尚未保存')
  await expect(page.getByLabel('分类 可选')).toHaveValue('')
  await expect(page.getByLabel('感受 可选')).toHaveValue('')
  await expect(page.getByLabel('关联').locator('option:checked')).toContainText(
    '当前专注',
  )

  await page.getByRole('button', { name: '记录困难', exact: true }).click()
  await expect(page.getByText('困难已记录。')).toBeVisible()
  await expect(page.locator('.difficulty-list li').filter({
    hasText: '不知道如何拆分这一章',
  })).toBeVisible()
  await expect(page).toHaveURL('/focus')
  expect(await new AxeBuilder({ page }).analyze()).toMatchObject({ violations: [] })
})

test('困难转写限制、丢弃、编辑态和回收站门禁正确', async ({ page, request }) => {
  await account(page, request)
  await configureTimeZone(page)
  await page.goto('/focus')
  await expect(page.getByRole('heading', { name: '专注', exact: true })).toBeVisible()
  await page.getByLabel('遇到了什么困难？').fill('保留这段手写困难')

  const control = await recordDifficultyVoice(page)
  await control.getByLabel('转写草稿').fill('难'.repeat(2001))
  await expect(control.getByText('困难正文不能超过 2000 个字符')).toBeVisible()
  await expect(control.getByRole('button', { name: '替换当前内容' })).toBeDisabled()
  await control.getByLabel('转写草稿').fill('不采用这段转写')
  await control.getByRole('button', { name: '丢弃', exact: true }).click()
  await expect(page.getByLabel('遇到了什么困难？')).toHaveValue(
    '保留这段手写困难',
  )
  await page.getByRole('button', { name: '记录困难', exact: true }).click()
  await expect(page.getByText('困难已记录。')).toBeVisible()

  const item = page.locator('.difficulty-list li').filter({
    hasText: '保留这段手写困难',
  })
  await item.getByRole('button', { name: /编辑困难/ }).click()
  await expect(page.getByLabel('编辑困难', { exact: true })).toHaveValue(
    '保留这段手写困难',
  )
  await expect(difficultyVoiceControl(page)).toBeVisible()
  await page.getByRole('group', { name: '困难范围' })
    .getByRole('button', { name: '回收站', exact: true }).click()
  await expect(difficultyVoiceControl(page)).toHaveCount(0)
})

test('麦克风权限拒绝保留任务文字并提供恢复说明', async ({
  page,
  request,
}, info) => {
  test.skip(info.project.name !== 'desktop')
  await page.addInitScript(() => {
    Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {
      configurable: true,
      value: () => Promise.reject(
        new DOMException('Permission denied by test', 'NotAllowedError'),
      ),
    })
  })
  await account(page, request)
  await page.goto('/tasks')
  await page.getByRole('button', { name: '新建任务', exact: true }).click()
  await page.getByLabel('任务名称', { exact: true }).fill('权限失败仍保留')

  const control = taskVoiceControl(page)
  await control.getByRole('button', { name: '开始语音输入' }).click()
  await expect(control.getByRole('alert')).toContainText('未允许麦克风权限')
  await expect(page.getByLabel('任务名称', { exact: true })).toHaveValue(
    '权限失败仍保留',
  )
})

test('组件卸载会停止全部麦克风轨道', async ({ page, request }, info) => {
  test.skip(info.project.name !== 'desktop')
  await page.addInitScript(() => {
    const original = navigator.mediaDevices.getUserMedia.bind(
      navigator.mediaDevices,
    )
    Object.defineProperty(window, '__itimeTrackStops', {
      configurable: true,
      writable: true,
      value: 0,
    })
    Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {
      configurable: true,
      value: async (constraints: MediaStreamConstraints) => {
        const stream = await original(constraints)
        for (const track of stream.getTracks()) {
          const stop = track.stop.bind(track)
          Object.defineProperty(track, 'stop', {
            configurable: true,
            value: () => {
              ;(window as typeof window & { __itimeTrackStops: number })
                .__itimeTrackStops += 1
              stop()
            },
          })
        }
        return stream
      },
    })
  })
  await account(page, request)
  await page.goto('/tasks')
  await page.getByRole('button', { name: '新建任务', exact: true }).click()
  const control = taskVoiceControl(page)
  await control.getByRole('button', { name: '开始语音输入' }).click()
  await expect(control.getByRole('status')).toContainText('正在录音')
  await page.getByRole('menuitem', { name: 'AI', exact: true }).click()
  await expect(page).toHaveURL('/ai')
  await expect.poll(() => page.evaluate(
    () => (window as typeof window & { __itimeTrackStops: number })
      .__itimeTrackStops,
  )).toBeGreaterThan(0)
})

test('上传失败以同一操作号重放且只创建一份转写', async ({
  page,
  request,
}, info) => {
  test.skip(info.project.name !== 'desktop')
  const operationIds: string[] = []
  await page.route('**/api/v1/voice-transcriptions?**', async route => {
    if (route.request().method() !== 'POST') {
      await route.continue()
      return
    }
    operationIds.push(route.request().headers()['x-operation-id'] ?? '')
    if (operationIds.length === 1) await route.abort('failed')
    else await route.continue()
  })
  await account(page, request)
  await page.goto('/tasks')
  await page.getByRole('button', { name: '新建任务', exact: true }).click()
  const control = taskVoiceControl(page)
  await control.getByRole('button', { name: '开始语音输入' }).click()
  await expect(control.getByRole('status')).toContainText('正在录音')
  await page.waitForTimeout(500)
  await control.getByRole('button', { name: '停止录音' }).click()
  await expect(control.getByRole('button', { name: '重试原上传' })).toBeVisible()
  await control.getByRole('button', { name: '重试原上传' }).click()
  await expect(control.getByLabel('转写草稿')).toHaveValue(
    '周五前完成论文提纲',
    { timeout: 15_000 },
  )
  expect(operationIds).toHaveLength(2)
  expect(operationIds[0]).not.toBe('')
  expect(operationIds[1]).toBe(operationIds[0])
  const listed = await (
    await page.request.get(
      '/api/v1/voice-transcriptions?purpose=task_title&limit=20',
    )
  ).json()
  expect(listed.items).toHaveLength(1)
})

test('第二页面恢复同一未解决转写草稿', async ({
  context,
  page,
  request,
}, info) => {
  test.skip(info.project.name !== 'desktop')
  await account(page, request)
  await page.goto('/tasks')
  await page.getByRole('button', { name: '新建任务', exact: true }).click()
  const control = taskVoiceControl(page)
  await control.getByRole('button', { name: '开始语音输入' }).click()
  await expect(control.getByRole('status')).toContainText('正在录音')
  await page.waitForTimeout(500)
  const accepted = page.waitForResponse(response => (
    response.request().method() === 'POST'
    && response.url().includes('/api/v1/voice-transcriptions?')
    && response.status() === 202
  ))
  await control.getByRole('button', { name: '停止录音' }).click()
  await accepted
  const second = await context.newPage()
  watchRuntime(second)
  await second.goto('/tasks')
  await second.getByRole('button', { name: '新建任务', exact: true }).click()
  const recovered = taskVoiceControl(second)
  await expect(recovered.getByLabel('转写草稿')).toHaveValue(
    '周五前完成论文提纲',
    { timeout: 15_000 },
  )
  const listed = await (
    await second.request.get(
      '/api/v1/voice-transcriptions?purpose=task_title&limit=20',
    )
  ).json()
  expect(listed.items).toHaveLength(1)
  await recovered.getByRole('button', { name: '丢弃', exact: true }).click()
  expect(runtimeErrors.get(second)).toEqual([])
  await second.close()
})

test('处理中取消清除草稿且晚到结果不会重新出现', async ({
  page,
  request,
}, info) => {
  test.skip(info.project.name !== 'desktop')
  await account(page, request)
  await page.goto('/tasks')
  await page.getByRole('button', { name: '新建任务', exact: true }).click()
  const control = taskVoiceControl(page)
  await control.getByRole('button', { name: '开始语音输入' }).click()
  await expect(control.getByRole('status')).toContainText('正在录音')
  await page.waitForTimeout(500)
  const accepted = page.waitForResponse(response => (
    response.request().method() === 'POST'
    && response.url().includes('/api/v1/voice-transcriptions?')
    && response.status() === 202
  ))
  await control.getByRole('button', { name: '停止录音' }).click()
  await accepted
  await expect(control.getByRole('button', { name: '取消转写' })).toBeVisible()
  await control.getByRole('button', { name: '取消转写' }).click()
  await expect(control).toContainText('已丢弃')
  await expect.poll(async () => {
    const listed = await (
      await page.request.get(
        '/api/v1/voice-transcriptions?purpose=task_title&limit=1',
      )
    ).json()
    return [
      listed.items[0].transcription.resolution,
      listed.items[0].job.status,
    ]
  }).toEqual(['discarded', 'cancelled'])
  await page.reload()
  await page.getByRole('button', { name: '新建任务', exact: true }).click()
  await expect(taskVoiceControl(page).getByLabel('转写草稿')).toHaveCount(0)
})

test('录音达到 60 秒会自动停止并进入转写', async ({
  page,
  request,
}, info) => {
  test.skip(info.project.name !== 'desktop')
  await account(page, request)
  await page.goto('/tasks')
  await page.getByRole('button', { name: '新建任务', exact: true }).click()
  const control = taskVoiceControl(page)
  await page.clock.install()
  await control.getByRole('button', { name: '开始语音输入' }).click()
  await expect(control.getByRole('status')).toContainText('正在录音')
  await page.waitForTimeout(500)
  await page.clock.fastForward(60_100)
  await expect(control.getByLabel('转写草稿')).toHaveValue(
    '周五前完成论文提纲',
    { timeout: 15_000 },
  )
})

test('320px 最大字号深色下转写草稿无溢出', async ({
  page,
  request,
}, info) => {
  test.skip(info.project.name !== 'desktop')
  await account(page, request)
  await page.setViewportSize({ width: 320, height: 780 })
  await page.evaluate(() => {
    localStorage.setItem('itime-appearance', JSON.stringify({
      theme: 'dark',
      fontSize: 'larger',
    }))
  })
  await page.goto('/tasks')
  await page.getByRole('button', { name: '新建任务', exact: true }).click()
  await recordTaskVoice(page)
  await assertPageQuality(page)
  await screenshot(page, info, 'task-ready-320-large-dark')
})
