import { navigateToProduct } from './helpers/product-navigation'
import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page, type TestInfo } from '@playwright/test'
import { mkdir } from 'node:fs/promises'

const evidenceRoot = '/tmp/itime-ui-evidence'
const runtimeErrors = new WeakMap<Page, string[]>()

function liveDate(offset: number) {
  const minuteBucket = Math.floor(Date.now() / 60_000) % 1500
  const value = new Date(Date.UTC(2028, 0, 1 + minuteBucket + offset))
  return value.toISOString().slice(0, 10)
}

function watchRuntime(page: Page) {
  const errors: string[] = []
  runtimeErrors.set(page, errors)
  page.on('pageerror', error => errors.push(error.message))
  page.on('console', message => {
    if (
      ['error', 'warning'].includes(message.type())
      && !message.text().includes('Failed to load resource')
    ) errors.push(`${message.type()}: ${message.text()}`)
  })
  page.on('response', response => {
    if (response.url().includes('/api/v1/') && response.status() >= 500) {
      errors.push(`${response.status()} ${response.url()}`)
    }
  })
}

async function pageHealth(page: Page) {
  expect(runtimeErrors.get(page) ?? []).toEqual([])
  expect(await page.locator('vite-error-overlay').count()).toBe(0)
  expect(await page.evaluate(
    () => document.documentElement.scrollWidth <= window.innerWidth + 1,
  )).toBe(true)
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
}

async function screenshot(page: Page, info: TestInfo, name: string) {
  await mkdir(evidenceRoot, { recursive: true })
  await page.screenshot({
    path: `${evidenceRoot}/${info.project.name}-deepseek-${name}.png`,
    fullPage: true,
    animations: 'disabled',
  })
}

test.beforeEach(async ({ page }) => watchRuntime(page))
test.afterEach(async ({ page }) => {
  expect(runtimeErrors.get(page) ?? []).toEqual([])
})

test('DeepSeek 完成对话、规划、课表图片与文字导入和复盘闭环', async ({
  page,
  context,
}, info) => {
  const firstImageDate = liveDate(0)
  const secondImageDate = liveDate(1)
  const textDate = liveDate(2)
  await page.goto('/ai')
  await expect(page).toHaveTitle('AI 对话 · iTime')
  await page.getByRole('button', { name: '新建会话', exact: true }).click()
  await page.getByLabel('消息', { exact: true }).fill(
    '请用两句话说明如何把今天的学习任务安排得更可执行。',
  )
  await page.getByRole('button', { name: '发送消息', exact: true }).click()
  const assistant = page.locator(
    '.message-assistant.message-complete .message-content',
  ).last()
  await expect(assistant).toBeVisible()
  await expect(assistant).not.toHaveText('')
  await screenshot(page, info, 'conversation')

  await navigateToProduct(page, '任务')
  await page.getByRole('button', { name: '新建任务', exact: true }).click()
  await page.getByLabel('任务名称', { exact: true }).fill('完成 DeepSeek 接入验收报告')
  await page.getByLabel('正文', { exact: false }).fill(
    '整理真实对话、规划、视觉导入和复盘结果。',
  )
  await page.getByRole('button', { name: '创建任务', exact: true }).first().click()
  await expect(page.getByText('任务已创建并保存到服务端。')).toBeVisible()

  await navigateToProduct(page, '规划')
  await expect(page.getByRole('heading', {
    name: '待规划任务',
    exact: true,
  })).toBeVisible()
  await page.getByLabel('补充要求').fill(
    '优先安排验收报告，并保留休息缓冲。',
  )
  await page.getByRole('button', {
    name: '生成计划提案',
    exact: true,
  }).click()
  await expect(page.getByText('这是预览，确认后才会写入计划')).toBeVisible()
  await page.getByRole('button', {
    name: '确认并写入计划',
    exact: true,
  }).click()
  await expect(page.getByText(
    /^提案已写入计划，当前为第 \d+ 版。$/,
  )).toBeVisible()
  await screenshot(page, info, 'planning')

  const imagePage = await context.newPage()
  await imagePage.setViewportSize({ width: 960, height: 640 })
  await imagePage.setContent(`
    <main style="font-family:Arial,sans-serif;padding:28px;color:#102a22">
      <h1>2026 秋季课表</h1>
      <table style="border-collapse:collapse;width:100%;font-size:24px">
        <tr><th style="border:2px solid #31654f;padding:16px">日期</th>
          <th style="border:2px solid #31654f;padding:16px">时间</th>
          <th style="border:2px solid #31654f;padding:16px">课程</th>
          <th style="border:2px solid #31654f;padding:16px">地点</th></tr>
        <tr><td style="border:2px solid #31654f;padding:16px">${firstImageDate}</td>
          <td style="border:2px solid #31654f;padding:16px">09:00-10:30</td>
          <td style="border:2px solid #31654f;padding:16px">高等数学</td>
          <td style="border:2px solid #31654f;padding:16px">A101</td></tr>
        <tr><td style="border:2px solid #31654f;padding:16px">${secondImageDate}</td>
          <td style="border:2px solid #31654f;padding:16px">14:00-15:30</td>
          <td style="border:2px solid #31654f;padding:16px">大学英语</td>
          <td style="border:2px solid #31654f;padding:16px">B203</td></tr>
      </table>
    </main>
  `)
  const imagePath = '/tmp/itime-deepseek-course.png'
  await imagePage.screenshot({ path: imagePath })
  await imagePage.close()

  await navigateToProduct(page, '日历')
  await page.getByRole('button', { name: '导入日历', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '导入日历' })
  await dialog.locator('.import-mode-switch').getByText('图片', { exact: true }).click()
  await dialog.locator('.import-image-source input[type=file]').setInputFiles(
    imagePath,
  )
  await dialog.getByRole('button', { name: '生成候选', exact: true }).click()
  await expect(dialog.getByText('已提交，处理完成后可校对。')).toBeVisible()
  await expect(dialog.locator('.import-candidate-list li').first()).toBeVisible()
  await expect(dialog.getByText('高等数学', { exact: true }).first()).toBeVisible()
  const firstCandidate = dialog.locator('.import-candidate-list li').first()
  await firstCandidate.getByRole('button', { name: /校对/ }).click()
  const editor = dialog.locator('.import-candidate-editor')
  await editor.getByLabel('名称').fill('DeepSeek 图像课表验收')
  await editor.getByLabel('类型').selectOption('fixed')
  await editor.getByLabel('重复方式').selectOption('none')
  await editor.getByRole('button', { name: '保存校对', exact: true }).click()
  await expect(dialog.getByText('DeepSeek 图像课表验收', { exact: true })).toBeVisible()
  await screenshot(page, info, 'calendar-image')
  await dialog.getByRole('button', { name: /确认选中/ }).click()
  await expect(dialog.getByText(/本次处理/)).toBeVisible()

  await dialog.locator('.import-mode-switch').getByText('文字', { exact: true }).click()
  await dialog.getByLabel('课表文字描述').fill(
    `${textDate} 16:00 到 17:00 上线性代数研讨课，地点 C305。`,
  )
  await dialog.getByRole('button', { name: '生成候选', exact: true }).click()
  await expect(dialog.getByText('已提交，处理完成后可校对。')).toBeVisible()
  await expect(dialog.getByText('线性代数研讨课', { exact: true }).first()).toBeVisible()
  const textCandidate = dialog.locator('.import-candidate-list li').filter({
    hasText: '线性代数研讨课',
  })
  await textCandidate.getByRole('button', { name: /校对/ }).click()
  const textEditor = dialog.locator('.import-candidate-editor')
  await textEditor.getByLabel('类型').selectOption('fixed')
  await textEditor.getByLabel('重复方式').selectOption('none')
  await textEditor.getByRole('button', { name: '保存校对', exact: true }).click()
  await screenshot(page, info, 'calendar-text')
  await dialog.getByRole('button', { name: /确认选中/ }).click()
  await expect(dialog.getByText(/本次处理/)).toBeVisible()
  await dialog.getByRole('button', { name: '完成', exact: true }).click()

  await navigateToProduct(page, '复盘')
  await expect(page.getByRole('heading', { name: '事实概览' })).toBeVisible()
  await page.getByLabel('补充事实').fill(
    `今天完成了 DeepSeek 对话、规划和课表截图识别验收（${firstImageDate}）。`,
  )
  await page.getByRole('button', { name: '保存事实' }).click()
  await page.getByRole('button', { name: '跳过，生成报告' }).click()
  await expect(page.getByRole('heading', { name: '复盘报告' })).toBeVisible()
  await expect(page.locator('.review-report-body')).toBeVisible()
  await screenshot(page, info, 'review')
  await pageHealth(page)
})
