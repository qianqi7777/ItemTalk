import { expect, test } from '@playwright/test';

for (const width of [320, 390, 1440]) {
  test(`模型配置测试、启用与刷新恢复 ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 960 });
    const runtime = {
      version: 3,
      globalDailyLimit: 80,
      subjectDailyLimit: 40,
      backgroundPaused: false,
      chatProfile: 'current',
    };
    const rows: {
      id: string;
      name: string;
      base_url: string;
      protocol: string;
      model: string;
      created_at: string;
    }[] = [];
    let tests = 0;
    await page.route('**/api/v1/**', async (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname.replace(/^.*\/api\/v1/, '');
      const reply = (body: unknown, status = 200) =>
        route.fulfill({ status, json: body });
      if (path === '/me')
        return reply({
          id: 'admin-preview',
          email: 'preview@example.com',
          displayName: '界面验收',
        });
      if (path === '/capabilities')
        return reply({ adminConsole: true, speechToText: false });
      if (path === '/admin/overview')
        return reply({
          runtime,
          hardLimits: { global: 1000, subject: 100 },
          modelProfiles: [
            { id: 'current', model: 'original-model', accepted: true },
            ...rows.map((row) => ({ ...row, accepted: true })),
          ],
          groups: [],
          queue: [],
          trend: [],
          costNotice: '费用为估算，不等于云账单',
        });
      if (path === '/admin/audit')
        return reply({ items: [], nextBefore: null });
      if (path === '/admin/model-configs/test') {
        tests++;
        return reply({
          testToken: 'synthetic-proof',
          checks: ['text', 'json', 'stream'],
          validForSeconds: 900,
        });
      }
      if (path === '/admin/model-configs' && request.method() === 'GET')
        return reply({
          items: rows,
          activeProfile: runtime.chatProfile,
          encryptionReady: true,
        });
      if (path === '/admin/model-configs' && request.method() === 'POST') {
        const input = request.postDataJSON();
        expect(input.apiKey).toBe('test-only-preview-key');
        rows.push({
          id: 'managed_01234567890123456789012345678901',
          name: input.name,
          base_url: input.baseUrl,
          protocol: input.protocol,
          model: input.model,
          created_at: '2026-10-26T00:00:00Z',
        });
        return reply(rows[0], 201);
      }
      if (path === '/admin/runtime-config' && request.method() === 'PATCH') {
        const input = request.postDataJSON();
        expect(input.expectedVersion).toBe(runtime.version);
        expect(input.globalDailyLimit).toBe(80);
        runtime.chatProfile = input.chatProfile;
        runtime.version++;
        return reply(runtime);
      }
      return reply({ code: 'NOT_FOUND', message: '未模拟的接口' }, 404);
    });
    await page.goto('/admin');
    const card = page.locator('#model-configs');
    await expect(card).toBeVisible();
    await page.getByLabel('配置名称', { exact: true }).fill('备用文本模型');
    await page
      .getByLabel('API 基础地址', { exact: true })
      .fill('https://models.example.com/v1');
    await page.getByLabel('模型名称', { exact: true }).fill('vendor/model-v2');
    await page
      .getByLabel('API Key', { exact: true })
      .fill('test-only-preview-key');
    const save = page.getByRole('button', { name: '保存并启用', exact: true });
    await expect(save).toBeDisabled();
    await page.getByRole('button', { name: '测试配置', exact: true }).click();
    await expect(save).toBeEnabled();
    await page.getByLabel('模型名称', { exact: true }).fill('vendor/model-v3');
    await expect(save).toBeDisabled();
    await page.getByRole('button', { name: '测试配置', exact: true }).click();
    await expect(save).toBeEnabled();
    await save.click();
    await page.getByRole('button', { name: '确认启用', exact: true }).click();
    await expect(card.getByText('当前使用', { exact: true })).toBeVisible();
    await expect(page.getByLabel('API Key', { exact: true })).toHaveValue('');
    expect(tests).toBe(2);
    await page.reload();
    await expect(card.getByText('当前使用', { exact: true })).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
    await card.scrollIntoViewIfNeeded();
    await card.screenshot({
      path: `test-results/model-configs/model-configs-${width}.png`,
      animations: 'disabled',
      style: '.topbar, .mobile-nav { visibility: hidden; }',
    });
  });
}
