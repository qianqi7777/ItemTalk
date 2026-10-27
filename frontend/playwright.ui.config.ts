import { defineConfig } from '@playwright/test';

/** 纯界面验收使用内存API响应，无需数据库或真实供应商配置。 */
export default defineConfig({
  testDir: './tests/e2e',
  testMatch: [
    'ui-responsive.spec.ts',
    'history.spec.ts',
    'general-help.spec.ts',
    'web-help.spec.ts',
    'model-configs-ui.spec.ts',
    'model-roles-ui.spec.ts',
  ],
  workers: 1,
  timeout: 60000,
  use: {
    baseURL: process.env.E2E_BASE_URL || 'http://127.0.0.1:63219',
    channel: process.env.PLAYWRIGHT_CHANNEL,
    trace: 'retain-on-failure',
  },
  webServer: process.env.E2E_EXTERNAL_SERVER
    ? undefined
    : {
        command: 'npm run dev -- --port 63219 --strictPort',
        url: 'http://127.0.0.1:63219',
        reuseExistingServer: false,
      },
});
