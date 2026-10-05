import { defineConfig, devices } from '@playwright/test'

process.env.ITIME_E2E_BOOTSTRAP_FILE ??=
  `/tmp/itime-ui-admin-bootstrap-${process.pid}.json`

const browserChannel = process.env.ITIME_E2E_BROWSER_CHANNEL
const browserUse = browserChannel ? { channel: browserChannel } : {}
const browserExecutablePath = process.env.ITIME_E2E_BROWSER_EXECUTABLE_PATH

export default defineConfig({
  testDir: './e2e',
  testIgnore: 'performance-under-load.spec.ts',
  timeout: 45_000,
  expect: { timeout: 8_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  outputDir: '/tmp/itime-ui-results',
  use: {
    baseURL: 'http://localhost:4173',
    trace: 'off',
    screenshot: 'only-on-failure',
    launchOptions: {
      ...(browserExecutablePath
        ? { executablePath: browserExecutablePath }
        : {}),
      args: [
        '--use-fake-device-for-media-stream',
        '--use-fake-ui-for-media-stream',
      ],
    },
  },
  projects: [
    {
      name: 'desktop',
      use: {
        ...devices['Desktop Chrome'],
        ...browserUse,
        viewport: { width: 1440, height: 1000 },
      },
    },
    {
      name: 'mobile',
      use: {
        ...devices['Desktop Chrome'],
        ...browserUse,
        viewport: { width: 390, height: 844 },
        isMobile: true,
        hasTouch: true,
      },
    },
    {
      name: 'tablet',
      use: {
        ...devices['Desktop Chrome'],
        ...browserUse,
        viewport: { width: 768, height: 1024 },
        hasTouch: true,
      },
    },
  ],
  webServer: {
    command: 'cd ../server && uv run python -m tests.web_environment',
    url: 'http://localhost:4173/api/ready',
    timeout: 45_000,
    reuseExistingServer: false,
    gracefulShutdown: { signal: 'SIGTERM', timeout: 10_000 },
  },
})
