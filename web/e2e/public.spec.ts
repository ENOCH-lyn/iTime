import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page, type TestInfo } from '@playwright/test'
import { mkdir } from 'node:fs/promises'

const evidence = '/tmp/itime-ui-evidence'
const runtimeErrors = new WeakMap<Page, string[]>()

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

async function screenshot(page: Page, info: TestInfo, name: string) {
  await mkdir(evidence, { recursive: true })
  await page.screenshot({
    path: `${evidence}/${info.project.name}-public-${name}.png`,
    fullPage: true,
  })
}

test.beforeEach(async ({ page }) => watchRuntime(page))
test.afterEach(async ({ page }) => expect(runtimeErrors.get(page)).toEqual([]))

test('公开首页展示产品、主入口和完整工作流', async ({ page }, info) => {
  await page.goto('/')

  await expect(page).toHaveTitle('iTime · 从容安排每一天')
  await expect(page.getByRole('heading', { level: 1, name: 'iTime' })).toBeVisible()
  await expect(page.getByRole('link', { name: '开始使用' }).first()).toHaveAttribute(
    'href',
    '/register',
  )
  await expect(page.locator('.hero-schedule-scene')).toBeVisible()
  if (info.project.name !== 'mobile') {
    await expect(page.getByText('高等数学', { exact: true })).toBeVisible()
  }
  await expect(page.getByRole('heading', { name: '从看清时间开始' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '从眼前的一件事开始。' }))
    .toBeVisible()

  await page.getByRole('link', { name: '看看如何安排' }).click()
  await expect(page.locator('#workflow')).toBeInViewport()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    .toBe(true)
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  await screenshot(page, info, 'home')

  if (info.project.name === 'desktop') {
    await page.evaluate(() => localStorage.setItem(
      'itime-appearance',
      JSON.stringify({ theme: 'light', fontSize: 'larger' }),
    ))
    await page.setViewportSize({ width: 320, height: 700 })
    await page.reload()
    await expect(page.getByRole('heading', { level: 1, name: 'iTime' })).toBeVisible()
    await expect(page.getByRole('link', { name: '开始使用' }).first()).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
      .toBe(true)
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
    await screenshot(page, info, 'home-320-large-font')
  }
})
