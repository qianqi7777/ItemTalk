import { expect, test } from '@playwright/test';

test('注册、上传、确认、资料构建与带引用问答', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/');
  await page.getByText('注册', { exact: true }).click();
  await page.getByLabel('邮箱').fill(`e2e-${Date.now()}@example.com`);
  await page.getByLabel('密码', { exact: true }).fill('e2e-password-123');
  await page.getByRole('button', { name: '注册并开始' }).click();
  await expect(page.getByText('认识你的产品')).toBeVisible();
  await page.locator('input[type=file]').setInputFiles({
    name: 'device.png',
    mimeType: 'image/png',
    buffer: Buffer.from(
      'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=',
      'base64',
    ),
  });
  await expect(page.getByText('照片已上传')).toBeVisible();
  await page.getByRole('button', { name: '开始识别' }).click();
  await page.getByRole('button', { name: '确认此型号' }).click();
  await expect(page.getByText('资料已就绪')).toBeVisible({ timeout: 30000 });
  await page.getByLabel('提问内容').fill('如何开机？');
  await page.getByRole('button', { name: '发送问题' }).click();
  await expect(page.getByText('已核验来源')).toBeVisible();
  await expect(page.getByText(/长按电源键 3 秒开机/)).toBeVisible();
  await expect(page.getByText(/第 1 页/)).toBeVisible();
  // 等待聊天区平滑滚动结束，避免 fullPage 截图捕获中间滚动位置。
  await page.waitForTimeout(350);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: 'test-results/mvp-desktop.png',
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: 'test-results/mvp-mobile.png',
    fullPage: true,
  });
});
