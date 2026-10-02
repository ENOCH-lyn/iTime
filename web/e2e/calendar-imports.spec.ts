import AxeBuilder from '@axe-core/playwright'
import { expect, test, type APIRequestContext, type Page, type TestInfo } from '@playwright/test'
import { randomUUID } from 'node:crypto'
import { mkdir, writeFile } from 'node:fs/promises'

const password = 'Valid-calendar-import-ui-password-123'
const evidence = '/tmp/itime-ui-evidence'
const runtimeErrors = new WeakMap<Page, string[]>()
const screenshotPng = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAASsAAABACAIAAADNgsbOAAABR0lEQVR42u3cwQ2DMAxA'
  + '0czADlzZfztYgUiO41jvq9emisnjUCHGK2lfwwgkAiUCJREoESiJQIlASQRKBEoiUCJQ'
  + 'EoESgZIyBF738/8TuFTaD00tNTXftOGsOyI1L0qbLRBIIIEEEkgggQQSSCCBBBJIYOip'
  + 'arDy1JR3UU8TGHiLWbfUEVsgkEACCSSQQAIJJJBAAgkkkEACo4dO4NLj22ALBBJIIIEE'
  + 'EkgggQQSSCCBBBJIYA2Bux5D67dBAgkkkEACCSSQQAIJJJBAAgkksIvAwO+2/y9016wI'
  + 'JJBAAgkkkEACCSSQQAIJJJBAAgmscfUJJJBAAgkkkEACCSSQQAIJJLCYwLSHidYt5Z3Z'
  + 'BSfpndkEEkgggQQSSCCBBBJIIIEEEigpMQIlAiUCJREoESiJQIlASQRKBEoiUCJQEoES'
  + 'gZIIlE7uA2qfPFsoP349AAAAAElFTkSuQmCC',
  'base64',
)

function isoDate(date: Date) {
  return date.toISOString().slice(0, 10)
}

function futureDate(days: number) {
  const value = new Date()
  value.setUTCDate(value.getUTCDate() + days)
  return isoDate(value)
}

async function screenshot(page: Page, info: TestInfo, name: string) {
  await mkdir(evidence, { recursive: true })
  await page.screenshot({
    path: `${evidence}/${info.project.name}-calendar-import-${name}.png`,
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
  const address = `calendar-import-ui-${randomUUID()}@example.com`
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
  await page.getByRole('link', { name: '日历', exact: true }).click()
  await expect(page).toHaveURL('/calendar')
}

async function csrf(page: Page) {
  const response = await page.request.get('/api/v1/auth/me')
  expect(response.ok()).toBeTruthy()
  return (await response.json()).csrf_token as string
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

test.beforeEach(async ({ page }) => watchRuntime(page))
test.afterEach(async ({ page }) => expect(runtimeErrors.get(page)).toEqual([]))

test('下载 Excel 模板、上传候选、确认后进入日历并识别重复', async ({
  page,
  request,
}, info) => {
  await account(page, request)
  const templateResponse = await page.request.get(
    '/api/v1/calendar/imports/template.xlsx',
  )
  expect(templateResponse.ok()).toBeTruthy()
  const templatePath = `/tmp/itime-template-${randomUUID()}.xlsx`
  await writeFile(templatePath, await templateResponse.body())

  await page.getByRole('button', { name: '导入日历', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '导入日历' })
  await expect(dialog).toBeVisible()
  await dialog.locator('input[type=file]').setInputFiles(templatePath)
  await dialog.getByRole('button', { name: '读取候选', exact: true }).click()
  await expect(dialog.getByText('已读取 1 条候选，尚未写入日历。')).toBeVisible()
  await expect(dialog.getByText('高等数学', { exact: true })).toBeVisible()
  await expect(dialog.getByText('可确认', { exact: true })).toBeVisible()
  await dialog.getByRole('button', { name: '确认选中 1 条', exact: true }).click()
  await expect(dialog.getByText('导入 1 条')).toBeVisible()
  await expect(dialog.getByText('已完成', { exact: true }).first()).toBeVisible()
  expect((await new AxeBuilder({ page }).include('.calendar-import-dialog').analyze()).violations)
    .toEqual([])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    .toBe(true)
  await screenshot(page, info, 'xlsx-confirmed')

  await dialog.getByRole('button', { name: '完成', exact: true }).click()
  await expect(dialog).toBeHidden()
  await page.getByLabel('跳转日期').fill('2026-09-07')
  await expect(page.getByText('正在读取日历…')).toHaveCount(0)
  await expect(page.getByRole('button', { name: /高等数学/ }).first()).toBeVisible()

  await page.getByRole('button', { name: '导入日历', exact: true }).click()
  await expect(dialog).toBeVisible()
  await dialog.locator('input[type=file]').setInputFiles(templatePath)
  await dialog.getByRole('button', { name: '读取候选', exact: true }).click()
  await expect(dialog.getByText('此安排已经导入，不会重复创建。')).toBeVisible()
  await expect(dialog.getByText('已存在', { exact: true })).toBeVisible()
  const duplicateRow = dialog.locator('.import-candidate-list > li').filter({
    hasText: '高等数学',
  })
  await expect(duplicateRow.getByRole('button', { name: /校对|跳过/ })).toHaveCount(0)
})

test('ICS 部分错误可校对后确认，并展示候选间时间冲突', async ({
  page,
  request,
}, info) => {
  const eventDate = futureDate(30)
  const compactDate = eventDate.replaceAll('-', '')
  const icsPath = `/tmp/itime-import-${randomUUID()}.ics`
  await writeFile(
    icsPath,
    [
      'BEGIN:VCALENDAR',
      'VERSION:2.0',
      'PRODID:-//iTime UI Test//EN',
      'BEGIN:VEVENT',
      'UID:valid-ui@example.com',
      `DTSTART;TZID=Asia/Shanghai:${compactDate}T090000`,
      `DTEND;TZID=Asia/Shanghai:${compactDate}T100000`,
      'SUMMARY:ICS 研讨课',
      'END:VEVENT',
      'BEGIN:VEVENT',
      'UID:invalid-ui@example.com',
      `DTSTART;TZID=Asia/Shanghai:${compactDate}T093000`,
      `DTEND;TZID=Asia/Shanghai:${compactDate}T103000`,
      'RRULE:FREQ=MONTHLY;BYDAY=1MO;COUNT=2',
      'SUMMARY:待校对 ICS',
      'END:VEVENT',
      'END:VCALENDAR',
      '',
    ].join('\r\n'),
  )
  await account(page, request)
  await page.getByRole('button', { name: '导入日历', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '导入日历' })
  await dialog.locator('input[type=file]').setInputFiles(icsPath)
  await dialog.getByRole('button', { name: '读取候选', exact: true }).click()
  await expect(dialog.getByText('1 条可确认 · 1 条需校对')).toBeVisible()
  await expect(dialog.getByText('暂不支持带序号的 BYDAY 规则。'))
    .toBeVisible()

  await dialog.getByRole('button', { name: '校对 待校对 ICS' }).click()
  const editor = dialog.locator('.import-candidate-editor')
  await expect(editor.getByLabel('名称')).toBeFocused()
  await editor.getByLabel('开始日期').fill(eventDate)
  await editor.getByLabel('开始时刻').fill('09:30')
  await editor.getByLabel('结束时刻').fill('10:30')

  const imports = await (
    await page.request.get('/api/v1/calendar/imports?limit=5')
  ).json()
  const batch = imports.items[0]
  const latestDetail = await (
    await page.request.get(`/api/v1/calendar/imports/${batch.id}`)
  ).json()
  const serverCandidate = latestDetail.candidates.find(
    (candidate: { external_uid: string }) =>
      candidate.external_uid === 'invalid-ui@example.com',
  )
  const concurrent = await page.request.put(
    `/api/v1/calendar/imports/${batch.id}/candidates/${serverCandidate.id}`,
    {
      headers: { 'X-CSRF-Token': await csrf(page) },
      data: {
        operation_id: randomUUID(),
        expected_batch_version: batch.version,
        expected_version: serverCandidate.version,
        kind: 'fixed',
        term_id: null,
        name: '服务器候选',
        description: '',
        location: '',
        busy: true,
        time_zone: 'Asia/Shanghai',
        start_date: eventDate,
        starts_at: '11:00:00',
        ends_at: '12:00:00',
        end_day_offset: 0,
        start_period_number: null,
        end_period_number: null,
        ambiguous_utc_offset_minutes: null,
        recurrence: {
          frequency: 'none',
          interval: 1,
          weekdays: [],
          month: null,
          day_of_month: null,
          until: null,
          count: null,
          week_numbers: [],
        },
      },
    },
  )
  expect(concurrent.ok()).toBeTruthy()

  await editor.getByRole('button', { name: '保存校对', exact: true }).click()
  await expect(editor.getByText('服务器最新版 · 候选版本 2')).toBeVisible()
  await expect(editor.getByLabel('名称')).toHaveValue('待校对 ICS')
  await screenshot(page, info, 'candidate-version-conflict')
  await editor.getByRole('button', {
    name: '保留当前填写并基于最新版本重试',
  }).click()
  await editor.getByRole('button', { name: '保存校对', exact: true }).click()
  await expect(dialog.getByText('候选已校对，可加入本次确认。')).toBeVisible()
  await expect(dialog.getByRole('button', { name: '校对 待校对 ICS' })).toBeFocused()
  await dialog.getByRole('button', { name: '确认选中 2 条', exact: true }).click()
  await expect(dialog.getByText(/发现 2 处时间冲突/)).toBeVisible()
  await expect(dialog.getByText('已完成', { exact: true }).first()).toBeVisible()
  expect((await new AxeBuilder({ page }).include('.calendar-import-dialog').analyze()).violations)
    .toEqual([])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    .toBe(true)
  await screenshot(page, info, 'ics-conflicts')

  await dialog.getByRole('button', { name: '完成', exact: true }).click()
  await page.getByLabel('跳转日期').fill(eventDate)
  await expect(page.getByText('正在读取日历…')).toHaveCount(0)
  await expect(page.getByRole('button', { name: /ICS 研讨课/ }).first()).toBeVisible()
  await expect(page.getByRole('button', { name: /待校对 ICS/ }).first()).toBeVisible()
  expect(await page.locator('vite-error-overlay').count()).toBe(0)

  if (info.project.name === 'desktop') {
    await page.evaluate(() => localStorage.setItem(
      'itime-appearance',
      JSON.stringify({ theme: 'light', fontSize: 'larger' }),
    ))
    await page.setViewportSize({ width: 320, height: 700 })
    await page.reload()
    await page.getByRole('button', { name: '导入日历', exact: true }).click()
    const narrowDialog = page.getByRole('dialog', { name: '导入日历' })
    await expect(narrowDialog).toBeVisible()
    await expect(
      narrowDialog.locator('.import-history-list .state-label').getByText(
        '已完成',
        { exact: true },
      ),
    ).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
      .toBe(true)
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
    await screenshot(page, info, '320-large-font')
  }
})

test('截图后台识别可离开后恢复并校对确认', async ({
  page,
  request,
}, info) => {
  const imagePath = `/tmp/itime-screenshot-${randomUUID()}.png`
  await writeFile(imagePath, screenshotPng)
  await account(page, request)
  await page.getByRole('button', { name: '导入日历', exact: true }).click()
  let dialog = page.getByRole('dialog', { name: '导入日历' })
  await dialog.locator('.import-mode-switch label').filter({ hasText: '截图识别' }).click()
  await dialog.locator('.import-screenshot input[type=file]').setInputFiles(imagePath)
  await dialog.getByRole('button', { name: '开始识别', exact: true }).click()
  await expect(dialog.getByText('截图已提交，离开此页面也会继续识别。')).toBeVisible()
  await dialog.getByRole('button', { name: '完成', exact: true }).click()
  await expect(dialog).toBeHidden()

  await page.waitForTimeout(1200)
  await page.getByRole('button', { name: '导入日历', exact: true }).click()
  dialog = page.getByRole('dialog', { name: '导入日历' })
  await dialog.locator('.import-mode-switch label').filter({ hasText: '截图识别' }).click()
  await expect(dialog.getByText('高等数学', { exact: true })).toBeVisible()
  await expect(dialog.getByText('可确认', { exact: true })).toBeVisible()
  await expect(dialog.getByText('等待校对', { exact: true })).toBeVisible()
  await dialog.getByRole('button', { name: '确认选中 1 条', exact: true }).click()
  await expect(dialog.getByText('导入 1 条')).toBeVisible()
  await expect(dialog.getByText('已完成', { exact: true }).first()).toBeVisible()
  expect((await new AxeBuilder({ page }).include('.calendar-import-dialog').analyze()).violations)
    .toEqual([])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    .toBe(true)
  await screenshot(page, info, 'screenshot-confirmed')

  await dialog.getByRole('button', { name: '完成', exact: true }).click()
  await page.getByLabel('跳转日期').fill('2026-10-05')
  await expect(page.getByText('正在读取日历…')).toHaveCount(0)
  await expect(page.getByRole('button', { name: /高等数学/ }).first()).toBeVisible()

  if (info.project.name === 'desktop') {
    await page.evaluate(() => localStorage.setItem(
      'itime-appearance',
      JSON.stringify({ theme: 'light', fontSize: 'larger' }),
    ))
    await page.setViewportSize({ width: 320, height: 700 })
    await page.reload()
    await page.getByRole('button', { name: '导入日历', exact: true }).click()
    const narrowDialog = page.getByRole('dialog', { name: '导入日历' })
    await narrowDialog.locator('.import-mode-switch label').filter({ hasText: '截图识别' }).click()
    await expect(narrowDialog.getByText('等待校对', { exact: true })).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
      .toBe(true)
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
    await screenshot(page, info, 'screenshot-320-large-font')
  }
})
