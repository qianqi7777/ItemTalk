import { expect, test } from '@playwright/test';

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
