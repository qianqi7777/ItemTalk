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
  // 聊天附件走正常上传/确认与SSE，不复用识别照片的内存状态。
  const questionImage = {
    name: 'question.png',
    mimeType: 'image/png',
    buffer: Buffer.from(
      'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=',
      'base64',
    ),
  };
  await page
    .locator('.chat-photo-draft input[type=file]')
    .setInputFiles(questionImage);
  await expect(page.getByLabel('待发送图片')).toBeVisible();
  await page.getByLabel('提问内容').fill('如何开机？');
  await page.getByRole('button', { name: '发送问题' }).click();
  await expect(page.getByText('已核验来源')).toBeVisible();
  await expect(page.getByText(/长按电源键 3 秒开机/)).toBeVisible();
  await expect(page.getByText(/第 1 页/)).toBeVisible();
  await expect(page.locator('.bubble.user img')).toHaveCount(1);
  await page.screenshot({
    path: 'test-results/v1-chat-image-desktop.png',
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: 'test-results/v1-chat-image-mobile.png',
    fullPage: true,
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.getByRole('button', { name: '有帮助' }).click();
  await expect(page.getByText('反馈已记录')).toBeVisible();
  // 纯图片澄清后刷新/恢复历史，补充的问题仍显式携带同一附件。
  await page
    .locator('.chat-photo-draft input[type=file]')
    .setInputFiles(questionImage);
  await expect(page.getByLabel('待发送图片')).toBeVisible();
  const imageRequest = page.waitForRequest(
    (request) =>
      request.method() === 'POST' && request.url().endsWith('/stream'),
  );
  await page.getByRole('button', { name: '发送问题' }).click();
  const attachedId = (await imageRequest).postDataJSON().assetIds[0];
  await expect(
    page.getByText(
      '图片已收到。请补充您想了解的问题，例如充电方法或指示灯异常。',
    ),
  ).toBeVisible();
  await page.reload();
  await page.getByRole('button', { name: '历史会话' }).click();
  await page.getByRole('button', { name: '如何开机？', exact: true }).click();
  await expect(page.getByLabel('待发送图片')).toBeVisible();
  await page.getByLabel('提问内容').fill('如何带电拆机并短接保护？');
  const followupRequest = page.waitForRequest(
    (request) =>
      request.method() === 'POST' && request.url().endsWith('/stream'),
  );
  await page.getByRole('button', { name: '发送问题' }).click();
  expect((await followupRequest).postDataJSON().assetIds).toEqual([attachedId]);
  await expect(
    page.getByText('此操作存在安全风险，请停止操作并联系官方售后或专业人员。'),
  ).toBeVisible();
  await expect(page.getByLabel('待发送图片')).toHaveCount(0);
  await page.getByRole('button', { name: '使用说明书' }).click();
  await expect(page.getByRole('heading', { name: '产品概览' })).toBeVisible({
    timeout: 30000,
  });
  await expect(
    page.locator('.manual-content').getByText('已核验来源').first(),
  ).toBeVisible();
  await page.getByRole('button', { name: '新建会话' }).click();
  await page.getByRole('button', { name: '历史会话' }).click();
  await page.getByRole('button', { name: '如何开机？', exact: true }).click();
  await expect(
    page.locator('.messages').getByText(/长按电源键 3 秒开机/),
  ).toBeVisible();
  await expect(page.locator('.messages').getByText('反馈已记录')).toBeVisible();
  await expect(page.locator('.bubble.user img')).toHaveCount(3);
  await page.getByLabel('提问内容').fill('请记住我喜欢简短中文，如何开机？');
  await page.getByRole('button', { name: '发送问题' }).click();
  await expect(page.getByRole('button', { name: '发送问题' })).toBeVisible();
  await page.getByRole('button', { name: '我的记忆' }).click();
  await expect(async () => {
    if (!(await page.getByRole('button', { name: '确认记住' }).count()))
      await page.getByRole('button', { name: '刷新记忆' }).click();
    await expect(page.getByRole('button', { name: '确认记住' })).toBeVisible();
  }).toPass({ timeout: 15000 });
  await page.getByRole('button', { name: '确认记住' }).click();
  await expect(page.getByText('全局偏好 · 已确认')).toBeVisible();
  await page
    .getByLabel('提问内容')
    .fill('请记住当前产品开启节能模式，如何开机？');
  await page.getByRole('button', { name: '发送问题' }).click();
  await expect(async () => {
    await page.getByRole('button', { name: '刷新记忆' }).click();
    await expect(page.getByRole('button', { name: '确认记住' })).toBeVisible();
  }).toPass({ timeout: 15000 });
  await page.getByRole('button', { name: '确认记住' }).click();
  await expect(page.getByText('当前产品 · 已确认')).toBeVisible();
  await page
    .getByRole('button', { name: '生成我的说明书', exact: true })
    .click();
  await expect(page.getByText('实例专属 · 仅本人可见')).toBeVisible();
  await expect(page.getByLabel('本章个人补充')).toContainText(
    '当前产品开启节能模式',
    { timeout: 30000 },
  );
  await page.screenshot({
    path: 'test-results/v1-personal-manual-desktop.png',
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: 'test-results/v1-personal-manual-mobile.png',
    fullPage: true,
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.getByRole('button', { name: '通用说明书', exact: true }).click();
  await expect(page.getByText('通用核验资料')).toBeVisible();
  await expect(page.getByLabel('已确认的个人设置')).toHaveCount(0);
  await page.getByRole('button', { name: '我的说明书', exact: true }).click();
  await expect(page.getByLabel('本章个人补充')).toBeVisible();
  while (
    await page.getByRole('button', { name: '删除记忆', exact: true }).count()
  ) {
    const count = await page
      .getByRole('button', { name: '删除记忆', exact: true })
      .count();
    await page
      .getByRole('button', { name: '删除记忆', exact: true })
      .first()
      .click();
    await expect(
      page.getByRole('button', { name: '删除记忆', exact: true }),
    ).toHaveCount(count - 1);
  }
  await expect(page.getByLabel('本章个人补充')).toHaveCount(0);
  await expect(page.getByText(/暂无记忆/)).toBeVisible();
  await page.getByRole('button', { name: '操作引导' }).click();
  await page.getByLabel('你想完成什么操作？').fill('如何开机');
  await page.getByRole('button', { name: '开始引导', exact: true }).click();
  await expect(page.getByText('当前步骤 1')).toBeVisible();
  await page.getByRole('button', { name: '暂停引导', exact: true }).click();
  await expect(
    page.getByText('引导已暂停，恢复后仍从当前步骤开始。'),
  ).toBeVisible();
  // 重载页面后从持久化列表恢复同一引导，不能依赖React内存状态。
  await page.reload();
  await page.getByRole('button', { name: '操作引导', exact: true }).click();
  await page
    .getByRole('button', { name: '如何开机 · 已暂停', exact: true })
    .click();
  await page.getByRole('button', { name: '恢复引导', exact: true }).click();
  await expect(page.getByText('当前步骤 1')).toBeVisible();
  await page.getByRole('button', { name: '已完成', exact: true }).click();
  await expect(page.getByText('当前步骤 2')).toBeVisible();
  await page.screenshot({
    path: 'test-results/v1-guidance-desktop.png',
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: 'test-results/v1-guidance-mobile.png',
    fullPage: true,
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.getByRole('button', { name: '已完成', exact: true }).click();
  await expect(page.getByText('操作引导已完成', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '开始新的引导', exact: true }).click();
  await page
    .getByLabel('你想完成什么操作？')
    .fill('如何带电拆开电源并短接保护');
  await page.getByRole('button', { name: '开始引导', exact: true }).click();
  await expect(page.getByText(/该操作风险较高/)).toBeVisible();
  await expect(
    page.getByRole('button', { name: '已完成', exact: true }),
  ).toHaveCount(0);
  await expect(page.locator('.guidance-step')).toHaveCount(0);
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
  // 从真实历史恢复再删除，验证软删除立即屏蔽，UI不能继续沿用旧会话。
  await page.getByRole('button', { name: '历史会话', exact: true }).click();
  await page.getByRole('button', { name: '如何开机？', exact: true }).click();
  await page.getByRole('button', { name: '删除会话', exact: true }).click();
  await expect(page.getByRole('button', { name: '确认删除' })).toBeDisabled();
  await page.getByLabel('输入删除以确认').fill('删除');
  await page.getByRole('button', { name: '确认删除' }).click();
  await expect(
    page.getByRole('button', { name: '删除会话', exact: true }),
  ).toHaveCount(0);
  await page.getByRole('button', { name: '历史会话', exact: true }).click();
  await expect(page.getByText('还没有历史会话。')).toBeVisible();
  const productsResponse = await page.request.get('/api/v1/me/products');
  const currentProduct = (await productsResponse.json()).items[0];
  const guidesResponse = await page.request.get(
    `/api/v1/guidance-sessions?instanceId=${currentProduct.id}`,
  );
  const ownedGuides = (await guidesResponse.json()).items;
  await page.getByRole('button', { name: '删除产品', exact: true }).click();
  await page.getByLabel('输入删除以确认').fill('删除');
  await page.getByRole('button', { name: '确认删除' }).click();
  await expect(
    page.getByRole('heading', { name: '认识你的产品' }),
  ).toBeVisible();
  expect(
    (
      await page.request.get(`/api/v1/me/products/${currentProduct.id}`)
    ).status(),
  ).toBe(404);
  for (const guide of ownedGuides) {
    expect(
      (
        await page.request.get(`/api/v1/guidance-sessions/${guide.id}`)
      ).status(),
    ).toBe(404);
  }
  await page.getByRole('button', { name: '打开账号菜单', exact: true }).click();
  await page.getByRole('button', { name: '删除账号', exact: true }).click();
  await expect(
    page.getByRole('dialog', { name: '删除账号', exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole('dialog', { name: '删除账号', exact: true }),
  ).toHaveCSS('opacity', '1');
  await page.getByRole('dialog', { name: '删除账号', exact: true }).screenshot({
    path: 'test-results/v1-delete-mobile.png',
  });
  await page.getByLabel('输入删除以确认').fill('删除');
  await page.getByRole('button', { name: '确认删除' }).click();
  await expect(page.getByLabel('邮箱')).toBeVisible();
  expect((await page.request.get('/api/v1/me')).status()).toBe(401);
});
