import { expect, test } from '@playwright/test';

// Browser regression for restored provenance; backend API/SSE has separate real-PG tests.
test('缺资料仍可输入问题，通用建议在移动端和刷新后明确标注', async ({
  page,
}) => {
  const product = {
    id: 'missing-manual',
    productModelId: 'unknown',
    nickname: '我的鼠标',
    brandName: '未登记品牌',
    modelCode: 'G3-test',
    identityStatus: 'candidate',
    verificationStatus: 'pending',
    knowledgeStatus: 'partial',
    knowledgeJobId: null,
  };
  const conversation = {
    id: 'general-chat',
    title: '蓝牙连接问题',
    userProductInstanceId: product.id,
    status: 'active',
    createdAt: '2026-10-22T00:00:00Z',
  };
  const message = {
    id: 'general-answer',
    role: 'assistant',
    answerBasis: 'general',
    citations: [],
    content:
      '通用建议 · 未经该型号官方资料核验\n\n如果设备支持蓝牙，可以打开电脑的蓝牙设置，检查设备列表。',
  };
  await page.route('**/api/v1/**', async (route) => {
    const path = new URL(route.request().url()).pathname.replace(
      /^.*\/api\/v1/,
      '',
    );
    const reply = (json: unknown) => route.fulfill({ json });
    if (path === '/me')
      return reply({ id: 'generic-user', email: 'generic@example.invalid' });
    if (path === '/capabilities')
      return reply({ speechToText: false, adminConsole: false });
    if (path === '/me/products') return reply({ items: [product] });
    if (path === '/me/products/missing-manual') return reply(product);
    if (path === '/conversations/general-chat') return reply(conversation);
    if (path === '/conversations/general-chat/messages')
      return reply({ items: [message], nextCursor: null });
    return route.fulfill({
      status: 500,
      json: { message: `Unexpected request: ${path}` },
    });
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/conversations/general-chat');
  await expect(page.getByRole('note')).toHaveText(
    '通用建议 · 未经该型号官方资料核验',
  );
  await expect(page.getByText('已核验来源')).toHaveCount(0);
  await expect(page.getByLabel('提问内容')).toBeEnabled();
  await page.reload();
  await expect(page.getByRole('note')).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.evaluate(async () => {
    await document.fonts.ready;
    await Promise.all(
      document
        .getAnimations()
        .filter(
          (animation) => animation.effect?.getTiming().iterations !== Infinity,
        )
        .map((animation) => animation.finished.catch(() => undefined)),
    );
  });
  await page.screenshot({
    path: 'test-results/general-help-mobile.png',
    fullPage: true,
  });
});
