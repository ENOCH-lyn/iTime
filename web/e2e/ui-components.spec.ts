import { expect, test, type Page } from '@playwright/test'

function watchRuntime(page: Page) {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('console', message => {
    if (
      ['error', 'warning'].includes(message.type())
      && !message.text().includes('Failed to load resource')
    ) errors.push(message.text())
  })
  return errors
}

test('Element Plus 根配置在所有视口生效', async ({ page }) => {
  const errors = watchRuntime(page)
  await page.goto('/login')

  await expect(page.locator('[data-ui-library="element-plus"]')).toBeVisible()
  await expect(page.getByRole('heading', { name: '欢迎回来' })).toBeVisible()
  await expect(page.locator('.el-input')).toHaveCount(2)
  await expect(page.locator('.appearance-compact .el-select')).toBeVisible()
  const theme = await page.evaluate(() => {
    const styles = getComputedStyle(document.documentElement)
    return {
      primary: styles.getPropertyValue('--el-color-primary').trim(),
      font: styles.getPropertyValue('--el-font-family').trim(),
      fits: document.documentElement.scrollWidth <= innerWidth,
    }
  })
  expect(theme.primary).not.toBe('')
  expect(theme.font).not.toBe('')
  expect(theme.fits).toBe(true)
  expect(errors).toEqual([])
})
