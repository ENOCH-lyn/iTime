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
  await expect(page.getByRole('link', { name: '查看演示' })).toHaveAttribute(
    'href',
    '/demo',
  )
  await expect(page.locator('.hero-schedule-scene')).toBeVisible()
  if (info.project.name !== 'mobile') {
    await expect(page.getByText('高等数学', { exact: true })).toBeVisible()
  }
  await expect(page.getByRole('heading', { name: '从看清时间开始' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '从眼前的一件事开始。' }))
    .toBeVisible()

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

test('公开演示可切换密度、明确完成并在刷新后重置', async ({ page }, info) => {
  await page.goto('/demo')

  await expect(page).toHaveTitle('产品演示 · iTime')
  await expect(page.getByRole('heading', { level: 1, name: '一天的安排' })).toBeVisible()
  await expect(page.getByText('演示数据只保留在当前页面，刷新后会重置。'))
    .toBeVisible()
  await expect(page.getByRole('heading', { name: '均衡安排' })).toBeVisible()
  await expect(page.locator('.demo-timeline').getByText('10:45', { exact: true }))
    .toBeVisible()

  await page.getByLabel('紧凑', { exact: true }).check()
  await expect(page.getByRole('heading', { name: '紧凑安排' })).toBeVisible()
  await expect(page.locator('.demo-timeline').getByText('10:30', { exact: true }))
    .toBeVisible()

  const completeReport = page.getByRole('button', { name: '标记完成：整理实验报告' })
  await completeReport.click()
  await expect(page.getByText('1/3 完成')).toBeVisible()
  await expect(page.getByRole('button', { name: '重新打开：整理实验报告' }))
    .toHaveAttribute('aria-pressed', 'true')
  await expect(page.locator('.demo-timeline').getByText('已完成')).toBeVisible()

  await page.reload()
  await expect(page.getByRole('heading', { name: '均衡安排' })).toBeVisible()
  await expect(page.getByText('0/3 完成')).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    .toBe(true)
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  await screenshot(page, info, 'demo')

  if (info.project.name === 'desktop') {
    await page.evaluate(() => localStorage.setItem(
      'itime-appearance',
      JSON.stringify({ theme: 'light', fontSize: 'larger' }),
    ))
    await page.setViewportSize({ width: 320, height: 700 })
    await page.reload()
    await expect(page.getByRole('heading', { level: 1, name: '一天的安排' }))
      .toBeVisible()
    await expect(page.getByRole('radio', { name: '均衡' })).toBeChecked()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
      .toBe(true)
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
    await screenshot(page, info, 'demo-320-large-font')
  }
})
