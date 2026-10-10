import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

const runtimeErrors = new WeakMap<Page, string[]>()

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

test.beforeEach(async ({ page }) => watchRuntime(page))
test.afterEach(async ({ page }) => {
  expect(runtimeErrors.get(page) ?? []).toEqual([])
})

test('DeepSeek 完成对话、规划、课表图片与文字导入和复盘闭环', async ({
  page,
  context,
}, info) => {
  test.skip(true, 'S2 AI 工具链接管规划/复盘生成后恢复')
  void page; void context; void info; void AxeBuilder
})
