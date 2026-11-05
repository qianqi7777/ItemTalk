import { expect, test } from '@playwright/test';

for (const initialStatus of ['failed', 'partial']) {
  test(`手机用户确认型号 ${initialStatus} 重试保留草稿，第三方查找阶段持续轮询`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: 320, height: 640 });
    const product = {
      id: 'retry-device',
      productModelId: 'retry-model',
      nickname: null,
      brandName: '示例品牌',
      modelCode: 'DEMO-100',
      identityStatus: 'user_confirmed',
      verificationStatus: 'pending',
      knowledgeJobId: null,
      knowledgeStatus: initialStatus,
    };
    let retried = false;
    let reads = 0;
    await page.route('**/api/v1/**', async (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname.replace(/^.*\/api\/v1/, '');
      const reply = (json: unknown, status = 200) =>
        route.fulfill({ json, status });
      if (path === '/me')
        return reply({ id: 'retry-user', email: 'test@example.invalid' });
      if (path === '/capabilities')
        return reply({ speechToText: false, adminConsole: false });
      if (path === '/me/products') return reply({ items: [product] });
      if (path === '/me/products/retry-device')
        return reply({
          ...product,
          knowledgeStatus: retried
            ? ++reads === 1
              ? 'researching_supplemental'
              : 'supplemental_ready'
            : initialStatus,
        });
      if (path === '/products/retry-model/knowledge-jobs') {
        retried = true;
        return reply({ id: 'retry' }, 202);
      }
      return reply({ message: `Unexpected: ${path}` }, 500);
    });
    await page.goto('/');
    await page.getByLabel('提问内容').fill('重试后继续保留这个问题');
    await page
      .getByRole('button', { name: '重试资料构建', exact: true })
      .click();
    await expect(
      page.getByText('查找第三方说明书', { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText('第三方资料可用 · 非官方核验', { exact: true }),
    ).toBeVisible();
    await expect(page.getByLabel('提问内容')).toHaveValue(
      '重试后继续保留这个问题',
    );
    await expect(
      page.getByRole('heading', { name: 'DEMO-100', exact: true }),
    ).toBeVisible();
    expect(reads).toBeGreaterThanOrEqual(2);
    const input = await page.locator('.chat-composer').boundingBox();
    const nav = await page
      .getByRole('navigation', { name: '移动页面导航' })
      .boundingBox();
    expect(input!.y + input!.height).toBeLessThanOrEqual(nav!.y);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  });
}

test('联网来源刷新保持，重搜显示真实阶段且不复制问题', async ({ page }) => {
  const product = {
    id: 'web-device',
    productModelId: 'model',
    brandName: '演示设备',
    modelCode: 'Pro100',
    knowledgeStatus: 'partial',
  };
  const source = {
    id: 'source',
    rank: 1,
    title: 'Pro100 蓝牙教程',
    url: 'https://guide.example/pro100',
    sourceKind: 'third_party',
    fetchedAt: '2026-10-25T10:00:00Z',
    pageStart: null,
    pageEnd: null,
  };
  const answer = {
    id: 'answer',
    role: 'assistant',
    inputMessageId: 'question',
    content: '选择对应设备。[1]',
    answerBasis: 'web',
    citations: [],
    webSources: [source],
  };
  const messages = [
    { id: 'question', role: 'user', content: '蓝牙怎么连接？', citations: [] },
    answer,
  ];
  let release!: () => void;
  let sent: Record<string, unknown> | undefined;
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace(/^.*\/api\/v1/, '');
    const reply = (json: unknown) => route.fulfill({ json });
    if (path === '/me')
      return reply({ id: 'web-user', email: 'web@example.invalid' });
    if (path === '/capabilities')
      return reply({ speechToText: false, adminConsole: false });
    if (path === '/me/products') return reply({ items: [product] });
    if (path === '/me/products/web-device') return reply(product);
    if (path === '/conversations/web-chat')
      return reply({ id: 'web-chat', userProductInstanceId: product.id });
    if (path === '/conversations/web-chat/messages')
      return reply({ items: messages, nextCursor: null });
    if (path === '/conversations/web-chat/stream') {
      sent = request.postDataJSON();
      return route.fulfill({
        contentType: 'text/event-stream',
        body: 'event: status\ndata: {"runId":"new","status":"web_reading"}\n\nevent: done\ndata: {"runId":"new"}\n\n',
      });
    }
    if (path === '/conversations/web-chat/runs/new') {
      await new Promise<void>((resolve) => {
        release = resolve;
      });
      const fresh = {
        ...answer,
        id: 'new-answer',
        content: '重新读取后的回答。[1]',
      };
      messages.push(fresh);
      return reply({
        id: 'new',
        inputMessageId: 'question',
        status: 'completed',
        message: fresh,
      });
    }
    return route.fulfill({
      status: 500,
      json: { message: `Unexpected: ${path}` },
    });
  });
  await page.goto('/conversations/web-chat');
  await expect(page.getByText('第三方资料 · 非官方核验')).toBeVisible();
  await page.getByRole('button', { name: '重新联网搜索', exact: true }).click();
  await expect(page.getByText('正在读取资料…')).toBeVisible();
  expect(sent?.forceWebSearch).toBe(true);
  expect(sent?.sourceMessageId).toBe('question');
  await expect.poll(() => Boolean(release)).toBe(true);
  release();
  await expect(
    page.locator('.bubble.assistant').last().locator('.message-content'),
  ).toHaveText('重新读取后的回答。1');
  await expect(page.locator('.bubble.user')).toHaveCount(1);
  await page.reload();
  await expect(page.getByText('基于联网资料')).toHaveCount(2);
  for (const width of [1440, 390, 320]) {
    await page.setViewportSize({ width, height: 900 });
    await page.evaluate(() => window.scrollTo(0, 0));
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    if (width === 390) {
      await page.evaluate(async () => {
        await document.fonts.ready;
        await Promise.all(
          document
            .getAnimations()
            .filter((a) => a.effect?.getTiming().iterations !== Infinity)
            .map((a) => a.finished.catch(() => undefined)),
        );
      });
      await page.screenshot({
        path: 'test-results/web-help-mobile.png',
        fullPage: true,
      });
    }
  }
});

test('手机补充资料独立于官方说明书，切换工具保留草稿并恢复索引与撤销标识', async ({
  page,
}) => {
  await page.setViewportSize({ width: 320, height: 640 });
  const product = {
    id: 'supplement-device',
    productModelId: 'model',
    nickname: null,
    brandName: '示例品牌',
    modelCode: 'DEMO-100',
    identityStatus: 'user_confirmed',
    verificationStatus: 'pending',
    knowledgeJobId: null,
    knowledgeStatus: 'supplemental_ready',
    supplementalDocumentCount: 1,
  };
  let retryCount = 0;
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/^.*\/api\/v1/, '');
    const reply = (json: unknown, status = 200) =>
      route.fulfill({ json, status });
    if (path === '/me')
      return reply({ id: 'supplement-user', email: 'test@example.invalid' });
    if (path === '/capabilities')
      return reply({ speechToText: false, adminConsole: false });
    if (path === '/me/products') return reply({ items: [product] });
    if (path === '/me/products/supplement-device') return reply(product);
    if (path === '/products/model/knowledge-jobs') {
      retryCount++;
      return reply({ id: 'retry' }, 202);
    }
    if (path === '/products/model/manual')
      return reply({ code: 'NOT_FOUND', message: '尚无官方说明书' }, 404);
    if (path === '/products/model/supplemental-documents') {
      expect(url.searchParams.get('instanceId')).toBe('supplement-device');
      return reply({
        items: [
          {
            id: 'supplement',
            title: 'DEMO-100 第三方说明书',
            url: 'https://manual.example/demo-100.pdf',
            sourceKind: 'third_party',
            fetchedAt: '2026-11-05T00:00:00Z',
            pageCount: 12,
            chunkCount: 8,
            matchStatus: 'exact',
            availabilityStatus: 'available',
          },
        ],
      });
    }
    if (path === '/conversations/indexed-chat')
      return reply({ id: 'indexed-chat', userProductInstanceId: product.id });
    if (path === '/conversations/indexed-chat/messages')
      return reply({
        items: [
          {
            id: 'indexed-answer',
            role: 'assistant',
            content: '历史第三方回答。[1]',
            citations: [],
            answerBasis: 'web',
            webLookup: {
              status: 'succeeded',
              reason: null,
              searchedAt: '2026-11-05T00:00:00Z',
              queryKey: 'battery',
              cached: false,
              lookupMode: 'supplemental_index',
            },
            webSources: [
              {
                id: 'withdrawn',
                rank: 1,
                title: '历史补充原文',
                url: 'https://manual.example/old.pdf',
                sourceKind: 'third_party',
                fetchedAt: '2026-11-05T00:00:00Z',
                pageStart: 2,
                pageEnd: 2,
                availabilityStatus: 'withdrawn',
              },
            ],
          },
        ],
        nextCursor: null,
      });
    return reply({ message: `Unexpected: ${path}` }, 500);
  });
  await page.goto('/');
  await expect(
    page.getByText('第三方资料可用 · 非官方核验', { exact: true }),
  ).toBeVisible();
  await expect(page.getByText('资料已就绪', { exact: true })).toHaveCount(0);
  await page.getByLabel('提问内容').fill('保留补充资料问题草稿');
  await page.getByRole('button', { name: '重试资料构建', exact: true }).click();
  await expect.poll(() => retryCount).toBe(1);
  await expect(
    page.getByRole('heading', { name: 'DEMO-100', exact: true }),
  ).toBeVisible();
  await page.getByRole('button', { name: '说明书 · 记忆 · 操作引导' }).click();
  await page.getByRole('button', { name: '使用说明书', exact: true }).click();
  await expect(
    page.getByRole('link', { name: 'DEMO-100 第三方说明书' }),
  ).toHaveAttribute('href', 'https://manual.example/demo-100.pdf');
  await expect(page.getByText(/12 页/)).toBeVisible();
  await expect(
    page.getByRole('navigation', { name: '说明书章节' }),
  ).toHaveCount(0);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: 'test-results/supplemental-manual-mobile.png',
    animations: 'disabled',
  });
  await page.getByRole('button', { name: '返回设备聊天' }).click();
  await expect(page.getByLabel('提问内容')).toHaveValue('保留补充资料问题草稿');
  const input = await page.locator('.chat-composer').boundingBox();
  const nav = await page
    .getByRole('navigation', { name: '移动页面导航' })
    .boundingBox();
  expect(input!.y + input!.height).toBeLessThanOrEqual(nav!.y);
  await page.goto('/conversations/indexed-chat');
  await page.reload();
  await expect(
    page.getByText('来自第三方补充资料库 · 非官方核验'),
  ).toBeVisible();
  await expect(page.getByText(/来源已撤销.*不要依此执行操作/)).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.locator('.bubble.assistant').screenshot({
    path: 'test-results/supplemental-answer-mobile.png',
    animations: 'disabled',
  });
});
