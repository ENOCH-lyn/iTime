import { defineConfig } from '@playwright/test'
import base from './playwright.config'

const baseURL = process.env.ITIME_RECOVERY_WEB_ORIGIN
if (!baseURL?.startsWith('http://localhost:')) {
  throw new Error('ITIME_RECOVERY_WEB_ORIGIN 必须是 localhost 恢复预览地址')
}
const storageState = process.env.ITIME_RECOVERY_PRIMARY_STORAGE_STATE
if (!storageState) {
  throw new Error('缺少 ITIME_RECOVERY_PRIMARY_STORAGE_STATE')
}

export default defineConfig({
  ...base,
  testMatch: 'recovery-after-restore.spec.ts',
  testIgnore: [],
  timeout: 60_000,
  outputDir: '/tmp/itime-ui-recovery-results',
  use: {
    ...base.use,
    baseURL,
    storageState,
  },
  webServer: undefined,
})
