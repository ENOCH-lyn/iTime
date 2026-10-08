import AxeBuilder from '@axe-core/playwright'
import { expect, test, type APIRequestContext, type Page, type TestInfo } from '@playwright/test'
import { randomUUID } from 'node:crypto'
import { mkdir, readFile, writeFile } from 'node:fs/promises'

const password = 'Valid-ai-timetable-ui-password-123'
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

async function screenshot(page: Page, info: TestInfo, name: string) {
  await mkdir(evidence, { recursive: true })
  await page.screenshot({
    path: `${evidence}/${info.project.name}-ai-timetable-${name}.png`,
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
  const address = `ai-timetable-ui-${randomUUID()}@example.com`
  await page.goto('/register')
  await page.getByLabel('邮箱', { exact: true }).fill(address)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByLabel('确认密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '创建账号', exact: true }).click()
  await expect(page.getByRole('heading', { name: '去邮箱看看吧' })).toBeVisible()
  await page.goto(await mailLink(request, address))
  await expect(page).toHaveURL('/ai')
  await page.getByRole('button', { name: '新建会话', exact: true }).click()
  await expect(page.getByRole('heading', { name: '新对话', exact: true })).toBeVisible()
}

async function bootstrap(info: TestInfo) {
  const path = process.env.ITIME_E2E_BOOTSTRAP_FILE
  if (!path) throw new Error('missing ITIME_E2E_BOOTSTRAP_FILE')
  const values = JSON.parse(await readFile(path, 'utf8')) as {
    project: string
    calendar_origin: string
  }[]
  const value = values.find(item => item.project === info.project.name)
  if (!value) throw new Error(`missing bootstrap for ${info.project.name}`)
  return value
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

test('会话图片附件生成候选并在统一编辑器确认', async ({
  page,
  request,
}, info) => {
  const imagePath = `/tmp/itime-conversation-timetable-${randomUUID()}.png`
  await writeFile(imagePath, screenshotPng)
  await account(page, request)

  await page.locator('.composer-secondary-actions input[type=file]').setInputFiles(imagePath)
  await expect(page.getByLabel('待发送附件')).toContainText('itime-conversation-timetable')
  await page.getByLabel('消息', { exact: true }).fill('按图片内容导入这份课表')
  await page.getByRole('button', { name: '发送消息', exact: true }).click()
  await expect(page.getByLabel('待发送附件')).toHaveCount(0)

  const card = page.getByRole('region', { name: '课表导入' }).first()
  await expect(card.getByText('课表图片', { exact: true })).toBeVisible()
  await expect(card.getByRole('button', { name: '调整', exact: true })).toBeVisible({
    timeout: 15_000,
  })
  await screenshot(page, info, 'image-ready')

  await card.getByRole('button', { name: '调整', exact: true }).click()
  await expect(page).toHaveURL(/\/calendar\?import=/)
  const dialog = page.getByRole('dialog', { name: '导入日历' })
  await expect(dialog.getByText('高等数学', { exact: true })).toBeVisible()
  await dialog.getByRole('button', { name: '校对 高等数学' }).click()
  const editor = dialog.locator('.import-candidate-editor')
  await editor.getByLabel('名称').fill('会话高等数学')
  await editor.getByRole('button', { name: '保存校对', exact: true }).click()
  await expect(dialog.getByText('会话高等数学', { exact: true })).toBeVisible()
  await dialog.getByRole('button', { name: '确认选中 1 条', exact: true }).click()
  await expect(dialog.getByText('导入 1 条')).toBeVisible()
  await dialog.getByRole('button', { name: '完成', exact: true }).click()
  await expect(page.getByRole('button', { name: /会话高等数学/ }).first()).toBeVisible()

  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    .toBe(true)
  await screenshot(page, info, 'image-confirmed')
})

test('Agent 从自然语言创建课表卡并可直接确认', async ({
  page,
  request,
}, info) => {
  await account(page, request)
  const prompt = '会话课表导入测试：请安排每周一、三上午的高等数学。'
  await page.getByLabel('消息', { exact: true }).fill(prompt)
  await page.getByRole('button', { name: '发送消息', exact: true }).click()
  await expect(page.getByText('已整理为课表候选，请确认后导入。')).toBeVisible({
    timeout: 15_000,
  })

  const card = page.getByRole('region', { name: '课表导入' }).last()
  await expect(card.getByText('文字描述', { exact: true })).toBeVisible()
  const confirm = card.getByRole('button', { name: /确认导入 1 条/ })
  await expect(confirm).toBeVisible({ timeout: 15_000 })
  await confirm.click()
  await expect(card.getByText('已导入 1 条')).toBeVisible()
  await expect(card.getByText('已完成', { exact: true })).toBeVisible()
  await screenshot(page, info, 'text-confirmed')

  await card.getByRole('button', { name: '在日历中查看', exact: true }).click()
  await expect(page).toHaveURL(/\/calendar\?date=/)
  await expect(page.getByRole('button', { name: /高等数学/ }).first()).toBeVisible()
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    .toBe(true)
})

test('Agent 从公开网址创建课表卡并进入统一编辑器', async ({
  page,
  request,
}, info) => {
  const runtime = await bootstrap(info)
  await account(page, request)
  const url = `${runtime.calendar_origin}/calendar/${randomUUID()}/conversation.ics`
  await page.getByLabel('消息', { exact: true }).fill(`会话课表网址测试：${url}`)
  await page.getByRole('button', { name: '发送消息', exact: true }).click()
  await expect(page.getByText('已整理为课表候选，请确认后导入。')).toBeVisible({
    timeout: 15_000,
  })

  const card = page.getByRole('region', { name: '课表导入' }).last()
  await expect(card.getByText('公开网页', { exact: true })).toBeVisible()
  await expect(card.getByRole('button', { name: '调整', exact: true })).toBeVisible({
    timeout: 15_000,
  })
  await card.getByRole('button', { name: '调整', exact: true }).click()
  await expect(page).toHaveURL(/\/calendar\?import=/)
  const dialog = page.getByRole('dialog', { name: '导入日历' })
  await expect(dialog.getByText('订阅课程', { exact: true })).toBeVisible()
  expect((await new AxeBuilder({ page }).include('.calendar-import-dialog').analyze()).violations)
    .toEqual([])
  await screenshot(page, info, 'public-url-ready')
})
