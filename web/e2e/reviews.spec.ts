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
  await expect(page).toHaveURL('/ai')
}

async function pageHealth(page: Page) {
  expect(await page.locator('vite-error-overlay').count()).toBe(0)
  expect(await page.evaluate(
    () => document.documentElement.scrollWidth <= window.innerWidth,
  )).toBe(true)
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
}

test('复盘页展示事实概览并可保存补充事实', async ({
  page,
  request,
}, info) => {
  await account(page, request)
  await page.goto('/reviews')
  await expect(page).toHaveTitle('复盘 · iTime')
  await expect(page.getByRole('heading', { name: '事实概览' })).toBeVisible()
  await expect(page.getByText('计划块').locator('..').getByRole('definition'))
    .toHaveText('0')

  await page.getByLabel('补充事实').fill('上午更适合持续思考。')
  await page.getByRole('button', { name: '保存事实' }).click()
  await expect(page.getByText('补充事实已保存。')).toBeVisible()
  await pageHealth(page)
  await screenshot(page, info, 'facts-saved')
})

test('daily review resumes jobs and manages report memories', async ({
  page,
  request,
}, info) => {
  test.skip(true, 'S2 AI 工具链接管复盘生成后恢复')
  void page; void request; void info
})

test('cancelled review report preserves inputs and releases the active state', async ({
  page,
  request,
}) => {
  test.skip(true, 'S2 AI 工具链接管复盘生成后恢复')
  void page; void request
})
