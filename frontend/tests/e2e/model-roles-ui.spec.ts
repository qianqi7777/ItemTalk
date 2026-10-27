import { expect, test } from '@playwright/test';

for (const width of [320, 390, 1440]) {
  test(`四类模型独立启用与向量锁定 ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 960 });
    const runtime: Record<string, string | number | boolean> = {
      version: 1,
      globalDailyLimit: 80,
      subjectDailyLimit: 40,
      backgroundPaused: false,
      chatProfile: 'current',
      visionProfile: 'current',
      sttProfile: 'current',
      embeddingProfile: 'current',
    };
    const presets = {
      chat: {
        model: 'text-model',
        protocol: 'dashscope_chat',
        baseUrl: 'https://text.example.com/v1',
      },
      vision: {
        model: 'vision-model',
        protocol: 'dashscope_chat',
        baseUrl: 'https://vision.example.com/v1',
      },
      stt: {
        model: 'asr-model',
        protocol: 'dashscope_audio',
        baseUrl: 'https://asr.example.com/v1',
      },
      embedding: {
        model: 'text-embedding-v4',
        protocol: 'dashscope_embeddings',
        baseUrl: 'https://vector.example.com/v1',
        revision: 'initial',
        dimensions: 1024,
      },
    };
    const rows: Record<string, string>[] = [];
    await page.route('**/api/v1/**', async (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname.replace(/^.*\/api\/v1/, '');
      const reply = (json: unknown, status = 200) =>
        route.fulfill({ status, json });
      if (path === '/me')
        return reply({ id: 'admin-preview', email: 'preview@example.com' });
      if (path === '/capabilities')
        return reply({ adminConsole: true, speechToText: true });
      if (path === '/admin/overview')
        return reply({
          runtime,
          hardLimits: { global: 1000, subject: 100 },
          modelProfiles: [
            { id: 'current', model: 'text-model', accepted: true },
          ],
          groups: [],
          queue: [],
          trend: [],
          costNotice: '估算',
        });
      if (path === '/admin/audit')
        return reply({ items: [], nextBefore: null });
      if (path === '/admin/model-configs/test') {
        const input = request.postDataJSON();
        if (input.role === 'embedding')
          expect(input).toMatchObject({
            model: 'text-embedding-v4',
            dimensions: 1024,
            revision: 'initial',
            protocol: 'dashscope_embeddings',
          });
        return reply({ testToken: 'proof', validForSeconds: 900 });
      }
      if (path === '/admin/model-configs' && request.method() === 'GET')
        return reply({
          items: rows,
          activeProfile: runtime.chatProfile,
          activeProfiles: Object.fromEntries(
            ['chat', 'vision', 'stt', 'embedding'].map((role) => [
              role,
              runtime[`${role}Profile`],
            ]),
          ),
          presets,
          encryptionReady: true,
        });
      if (path === '/admin/model-configs' && request.method() === 'POST') {
        const input = request.postDataJSON();
        const row = {
          id: `managed_${String(rows.length + 1).padStart(32, '0')}`,
          role: input.role,
          name: input.name,
          model: input.model,
          protocol: input.protocol,
          base_url: input.baseUrl,
          created_at: '2026-10-27T00:00:00Z',
        };
        rows.push(row);
        return reply(row, 201);
      }
      if (path === '/admin/runtime-config' && request.method() === 'PATCH') {
        const input = request.postDataJSON();
        expect(input.expectedVersion).toBe(runtime.version);
        expect(input.chatProfile).toBe('current');
        for (const role of ['vision', 'stt', 'embedding'])
          if (`${role}Profile` in input)
            runtime[`${role}Profile`] = input[`${role}Profile`];
        runtime.version = Number(runtime.version) + 1;
        return reply(runtime);
      }
      return reply({}, 404);
    });
    await page.goto('/admin');
    const card = page.locator('#model-configs');
    for (const [role, label] of [
      ['vision', '图像模型'],
      ['stt', '录音转写'],
      ['embedding', '向量模型'],
    ]) {
      await card.getByText(label, { exact: true }).click();
      await card.getByLabel('配置名称', { exact: true }).fill(`${label}备用`);
      await card
        .getByLabel('API 基础地址', { exact: true })
        .fill(`https://${role}.example.com/v1`);
      await card
        .getByLabel('API Key', { exact: true })
        .fill('synthetic-preview-key');
      if (role === 'embedding') {
        await expect(
          card.getByLabel('模型名称', { exact: true }),
        ).toBeDisabled();
        await expect(
          card.getByLabel('请求协议', { exact: true }),
        ).toBeDisabled();
      }
      await card.getByRole('button', { name: '测试配置', exact: true }).click();
      await card
        .getByRole('button', { name: '保存并启用', exact: true })
        .click();
      await page.getByRole('button', { name: '确认启用', exact: true }).click();
      await expect(card.getByText('当前使用', { exact: true })).toBeVisible();
      await expect(page.getByRole('dialog')).toBeHidden();
      await expect(card.getByLabel('API Key', { exact: true })).toHaveValue('');
      expect(runtime[`${role}Profile`]).not.toBe('current');
    }
    expect(runtime.chatProfile).toBe('current');
    await card.locator('.ant-card-head-title').click();
    await card.screenshot({
      path: `test-results/model-roles/model-roles-${width}.png`,
      animations: 'disabled',
      style: '.topbar, .mobile-nav, .skip-link { visibility: hidden; }',
    });
    await card
      .getByRole('button', { name: '切回服务器预设', exact: true })
      .click();
    await page.getByRole('button', { name: '确认启用', exact: true }).click();
    await expect(page.getByRole('dialog')).toBeHidden();
    await expect(
      card.getByRole('button', { name: '切回服务器预设', exact: true }),
    ).toBeDisabled();
    expect(runtime.embeddingProfile).toBe('current');
    expect(runtime.visionProfile).not.toBe('current');
    expect(runtime.sttProfile).not.toBe('current');
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
  });
}
