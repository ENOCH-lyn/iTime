import { defineConfig } from '@playwright/test'
import base from './playwright.config'

const baseURL = process.env.ITIME_DEEPSEEK_WEB_ORIGIN
if (!baseURL?.startsWith('http://localhost:')) {
  throw new Error('ITIME_DEEPSEEK_WEB_ORIGIN 必须是 localhost 地址')
}
const storageState = process.env.ITIME_DEEPSEEK_STORAGE_STATE
if (!storageState) {
  throw new Error('缺少 ITIME_DEEPSEEK_STORAGE_STATE')
}
const desktop = base.projects?.find(project => project.name === 'desktop')
if (!desktop) {
  throw new Error('基础 Playwright 配置缺少 desktop 项目')
}

export default defineConfig({
  ...base,
  testMatch: 'deepseek-live.spec.ts',
  testIgnore: [],
  timeout: 5 * 60_000,
  expect: { timeout: 180_000 },
  outputDir: '/tmp/itime-deepseek-ui-results',
  projects: [desktop],
  use: {
    ...base.use,
    baseURL,
    storageState,
  },
  webServer: undefined,
})
