import { defineConfig } from '@playwright/test'
import base from './playwright.config'

const baseURL = process.env.ITIME_PERF_WEB_ORIGIN
if (!baseURL?.startsWith('http://localhost:')) {
  throw new Error('ITIME_PERF_WEB_ORIGIN 必须是 localhost 性能预览地址')
}

export default defineConfig({
  ...base,
  testMatch: 'performance-under-load.spec.ts',
  timeout: 60_000,
  outputDir: '/tmp/itime-ui-performance-results',
  use: {
    ...base.use,
    baseURL,
  },
  webServer: undefined,
})
