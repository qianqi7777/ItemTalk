import { expect, test } from '@playwright/test';

// 故意延迟旧页面的响应；所有 API 都由浏览器拦截，不创建账号或调用模型。
for (const delayedStage of ['product', 'job'] as const) {
  test(`切换产品后忽略旧 ${delayedStage} 响应`, async ({ page }, testInfo) => {
    const first = {
      id: 'first',
      productModelId: 'model-first',
      nickname: null,
      modelCode: 'Device A',
      brandName: 'Example',
      identityStatus: 'verified',
      verificationStatus: 'verified',
      knowledgeJobId: 'job-a',
      knowledgeStatus: 'researching',
    };
    const second = {
      ...first,
      id: 'second',
      productModelId: 'model-second',
      modelCode: 'Device B',
      knowledgeJobId: null,
      knowledgeStatus: 'ready',
    };
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    let waiting = false;
    const unexpected: string[] = [];
    const delayedPath =
      delayedStage === 'product' ? '/me/products/first' : '/jobs/job-a';
    await page.route('**/api/v1/**', async (route) => {
      const path = new URL(route.request().url()).pathname.split('/api/v1')[1];
      let value: unknown;
      if (path === '/me')
        value = { id: 'fixture', email: 'switch@example.com' };
      else if (path === '/capabilities') value = { speechToText: false };
      else if (path === '/me/products') value = { items: [first, second] };
      else if (path === '/me/products/first')
        value = {
          ...first,
          knowledgeStatus: delayedStage === 'product' ? 'ready' : 'researching',
        };
      else if (path === '/jobs/job-a')
        value = {
          id: 'job-a',
          status: 'failed',
          progress: {},
          lastError: '旧任务失败',
        };
      else {
        unexpected.push(path);
        await route.fulfill({
          status: 404,
          json: { code: 'NOT_FOUND', message: 'Unexpected request' },
        });
        return;
      }
      if (path === delayedPath) {
        waiting = true;
        await gate;
      }
      await route.fulfill({ json: value });
    });
    try {
      await page.goto('/');
      await expect(
        page.getByRole('heading', { name: 'Device A', exact: true }),
      ).toBeVisible();
      await expect.poll(() => waiting).toBe(true);
      await page.getByRole('combobox', { name: '切换产品' }).click();
      await page.getByText('Device B · Example', { exact: true }).click();
      await expect(
        page.getByRole('heading', { name: 'Device B', exact: true }),
      ).toBeVisible();
      const received = page.waitForResponse((response) =>
        new URL(response.url()).pathname.endsWith(delayedPath),
      );
      release();
      await received;
      // 让 fetch 的 continuation 及 React 提交完成后再检查，避免只测到响应到达前的页面。
      await page.evaluate(
        () =>
          new Promise<void>((resolve) =>
            requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
          ),
      );
      await expect(
        page.getByRole('heading', { name: 'Device B', exact: true }),
      ).toBeVisible();
      await expect(page.getByText('旧任务失败')).toHaveCount(0);
      expect(unexpected).toEqual([]);
      if (delayedStage === 'job') {
        await page.screenshot({
          path: testInfo.outputPath('product-switch-desktop.png'),
          fullPage: true,
        });
        await page.setViewportSize({ width: 390, height: 844 });
        await expect(
          page.getByRole('heading', { name: 'Device B', exact: true }),
        ).toBeVisible();
        await expect
          .poll(() =>
            page.evaluate(
              () => document.documentElement.scrollWidth <= window.innerWidth,
            ),
          )
          .toBe(true);
        await page.screenshot({
          path: testInfo.outputPath('product-switch-mobile.png'),
          fullPage: true,
        });
      }
    } finally {
      release();
    }
  });
}
