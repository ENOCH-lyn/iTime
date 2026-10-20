import { chromium } from '@playwright/test'

const base = 'http://localhost:5173'
const outDir = 'C:/Users/ENOCH/Desktop/dsh/tasks/itime/persistent/preview'
const browser = await chromium.launch({
  executablePath: 'C:/Users/ENOCH/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe',
})
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, locale: 'zh-CN' })
const errors = []
page.on('pageerror', error => errors.push(String(error)))
await page.goto(`${base}/login`, { waitUntil: 'networkidle' })
await page.getByLabel('邮箱', { exact: true }).fill('ui-check-1792456578232@example.com')
await page.getByLabel('密码', { exact: true }).fill('Valid-password-123')
await page.getByRole('button', { name: '登录', exact: true }).click()
await page.waitForURL('**/timetable', { timeout: 15000 })
await page.locator('.board-event', { hasText: '组会' }).first().waitFor({ timeout: 10000 })

await page.locator('.tt-ai-form textarea').fill('把周三晚上 9 点的「组会」计划块挪到 8 点半，时长一小时。')
await page.locator('.tt-ai-form button[type="submit"]').click()
await page.getByText(/调整计划块「组会」/).waitFor({ timeout: 120000 })
await page.screenshot({ path: `${outDir}/timetable-ai-plan-block-2026-10-20.png` })
await page.getByRole('button', { name: '确认调整' }).click()
await page.getByText('AI 安排已写入').waitFor({ timeout: 30000 })
await page.waitForTimeout(1200)
const labels = []
const events = page.locator('.board-event', { hasText: '组会' })
for (let i = 0; i < await events.count(); i += 1) {
  labels.push((await events.nth(i).innerText()).replace(/\n/g, ' | '))
}
console.log('group meeting events:', labels)
await page.screenshot({ path: `${outDir}/timetable-ai-plan-block-applied-2026-10-20.png` })
console.log('errors=', errors.length)
for (const error of errors) console.log(' -', error)
await browser.close()
