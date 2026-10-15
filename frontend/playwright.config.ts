import { defineConfig } from '@playwright/test';

/** 主链路连接 API/Worker（外部模型为 mock）；竞态用例拦截 API 控制响应顺序。 */
export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: false,
  workers: 1,
  timeout: 60000,
  use: {
    baseURL: process.env.E2E_BASE_URL || 'http://localhost:5173',
    trace: 'retain-on-failure',
  },
  webServer: process.env.E2E_EXTERNAL_SERVER
    ? undefined
    : {
        command: 'npm run dev',
        url: 'http://localhost:5173',
        reuseExistingServer: !process.env.CI,
      },
});
