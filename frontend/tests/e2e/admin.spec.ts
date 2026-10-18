import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { expect, test } from '@playwright/test';

test('管理员授权、暂停恢复、审计与普通账号隔离', async ({ page, browser }) => {
  const migrationUrl = process.env.E2E_ADMIN_DATABASE_URL;
  test.skip(
    !migrationUrl,
    '需要独立测试库的迁移身份；不使用应用或生产连接替代',
  );
  const database = new URL(migrationUrl!);
  expect(['localhost', '127.0.0.1']).toContain(database.hostname);
  expect(database.pathname).toMatch(/^\/itemtalk_(e2e|v2_benchmark_preview)$/);
  const suffix = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto('/');
  await page.getByText('注册', { exact: true }).click();
  await page.getByLabel('邮箱').fill(`admin-${suffix}@example.com`);
  await page.getByLabel('密码', { exact: true }).fill('e2e-admin-password-123');
  await page.getByRole('button', { name: '注册并开始' }).click();
  await expect(page.getByText('认识你的产品')).toBeVisible();
  const self = await (await page.request.get('/api/v1/me')).json();
  // CI的专用迁移身份只用于首次授权，密钥通过环境传递，不提供HTTP自助提权入口。
  execFileSync(
    'uv',
    ['run', 'python', '-m', 'app.admin_cli', 'grant', '--user-id', self.id],
    {
      cwd: resolve(process.cwd(), '../backend'),
      env: { ...process.env, DATABASE_URL: migrationUrl! },
      stdio: 'pipe',
      timeout: 30000,
    },
  );
  await page.reload();
  await page.getByRole('link', { name: '运行管理', exact: true }).click();
  await expect(page.getByRole('heading', { name: '运行管理' })).toBeVisible();
  const initial = await (
    await page.request.get('/api/v1/admin/runtime-config')
  ).json();
  const origin = new URL(page.url()).origin;
  try {
    for (const version of [initial.version + 1, initial.version + 2]) {
      await page.getByLabel('暂停新后台任务', { exact: true }).click();
      await page
        .getByRole('button', { name: '保存运行配置', exact: true })
        .click();
      await page.getByRole('button', { name: '确认保存', exact: true }).click();
      await expect(
        page.getByText(`运行控制 · 版本 ${version}`, { exact: true }),
      ).toBeVisible();
    }
    const audit = await (await page.request.get('/api/v1/admin/audit')).json();
    expect(audit.items[0].actor_id).toBe(self.id);
    expect(audit.items[0].after_value.background_paused).toBe(false);
    await page.screenshot({
      path: 'test-results/v2-admin-desktop.png',
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: 'test-results/v2-admin-mobile.png',
      fullPage: true,
    });
    const ordinary = await browser.newContext();
    try {
      const requestHeaders = {
        Origin: origin,
        'X-Requested-With': 'XMLHttpRequest',
      };
      const credentials = {
        email: `ordinary-${suffix}@example.com`,
        password: 'e2e-ordinary-password-123',
      };
      const registered = await ordinary.request.post(
        `${origin}/api/v1/auth/register`,
        { data: credentials, headers: requestHeaders },
      );
      expect(registered.status()).toBe(201);
      expect(
        (
          await ordinary.request.post(`${origin}/api/v1/auth/login`, {
            data: credentials,
            headers: requestHeaders,
          })
        ).status(),
      ).toBe(204);
      expect(
        (
          await ordinary.request.get(`${origin}/api/v1/admin/overview`)
        ).status(),
      ).toBe(403);
      expect(
        await (
          await ordinary.request.get(`${origin}/api/v1/capabilities`)
        ).json(),
      ).toMatchObject({ adminConsole: false });
      await ordinary.request.delete(`${origin}/api/v1/me`, {
        headers: requestHeaders,
      });
    } finally {
      await ordinary.close();
    }
  } finally {
    const latest = await (
      await page.request.get('/api/v1/admin/runtime-config')
    ).json();
    const restored = await page.request.patch('/api/v1/admin/runtime-config', {
      data: { ...initial, expectedVersion: latest.version, version: undefined },
      headers: {
        Origin: origin,
        'X-Requested-With': 'XMLHttpRequest',
        'Idempotency-Key': `reset-${suffix}`,
      },
    });
    expect(restored.status()).toBe(200);
    await page.request.delete('/api/v1/me', {
      headers: { Origin: origin, 'X-Requested-With': 'XMLHttpRequest' },
    });
  }
});
