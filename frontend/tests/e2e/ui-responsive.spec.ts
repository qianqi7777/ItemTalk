import { expect, test, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';

const chatPhoto = readFileSync(
  new URL('../fixtures/recognition-back.png', import.meta.url),
);

// 固定合成响应只验证真实React布局与交互，不代表生产服务或供应商验收。
const user = {
  id: 'ui-account',
  email: 'preview@example.com',
  displayName: '界面验收',
};
const product = {
  id: 'ui-device',
  productModelId: 'ui-model',
  nickname: null,
  modelCode: 'WH-1000XM5',
  brandName: 'Sony',
  identityStatus: 'verified',
  verificationStatus: 'verified',
  knowledgeJobId: null,
  knowledgeStatus: 'ready',
};
const citation = {
  knowledgeChunkId: 'ui-chunk',
  documentVersionId: 'ui-document',
  title: 'WH-1000XM5 帮助指南',
  url: 'https://helpguide.sony.net/mdr/wh1000xm5/v1/zh-cn/',
  pageStart: 21,
  pageEnd: 21,
  rank: 1,
  verificationStatus: 'verified',
  verifiedAt: '2026-10-18T00:00:00Z',
};
const group = {
  kind: 'http',
  operation: 'GET /api/v1/me/products',
  provider: '',
  model: '',
  outcome: '200',
  currency: '',
  count: 128,
  p95_ms: 74,
  first_token_ms: 31,
  input_known: 0,
  input_tokens: null,
  output_known: 0,
  output_tokens: null,
  priced_calls: 0,
  estimated_cost: null,
};

async function fixture(
  page: Page,
  options: {
    authenticated?: boolean;
    admin?: boolean;
    product?: boolean;
    singlePoint?: boolean;
    speech?: boolean;
    failed?: boolean;
  } = {},
) {
  let authenticated = options.authenticated ?? true;
  const admin = options.admin ?? true;
  let photoCount = 0;
  const device = options.failed
    ? { ...product, knowledgeStatus: 'failed', knowledgeJobId: 'failed-job' }
    : product;
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace(/^.*\/api\/v1/, '');
    const method = request.method();
    const reply = (body: unknown, status = 200) =>
      route.fulfill({ status, json: body });
    if (path === '/assets/presign')
      return reply(
        {
          assetId: `chat-photo-${++photoCount}`,
          uploadUrl: `${new URL(request.url()).origin}/api/v1/assets/chat-photo-${photoCount}/content`,
        },
        201,
      );
    if (path.endsWith('/content') && method === 'PUT')
      return route.fulfill({ status: 204 });
    if (path.startsWith('/assets/') && path.endsWith('/confirm'))
      return reply({
        id: path.split('/')[2],
        mimeType: 'image/png',
        sizeBytes: chatPhoto.length,
        status: 'available',
        kind: 'product_photo',
        readUrl: 'data:image/png;base64,' + chatPhoto.toString('base64'),
      });
    if (path === '/auth/login') {
      authenticated = true;
      return route.fulfill({ status: 204 });
    }
    if (path === '/auth/logout') {
      authenticated = false;
      return route.fulfill({ status: 204 });
    }
    if (!authenticated)
      return reply({ code: 'AUTH_REQUIRED', message: '请先登录' }, 401);
    if (path === '/me') return reply(user);
    if (path === '/capabilities')
      return reply({
        speechToText: options.speech ?? false,
        adminConsole: admin,
      });
    if (path === '/me/products')
      return reply({ items: options.product ? [device] : [] });
    if (path === `/me/products/${product.id}`) return reply(device);
    if (path === '/jobs/failed-job')
      return reply({
        id: 'failed-job',
        status: 'failed',
        progress: { percent: 0 },
        lastError: '未找到适用资料，请重新查找或补充准确型号。',
      });
    if (path.startsWith('/admin/') && !admin)
      return reply({ code: 'FORBIDDEN', message: '需要管理员权限' }, 403);
    if (path === '/admin/model-configs')
      return reply({
        items: [],
        activeProfile: 'current',
        encryptionReady: true,
      });
    if (path === '/admin/overview')
      return reply({
        runtime: {
          version: 4,
          globalDailyLimit: 80,
          subjectDailyLimit: 40,
          backgroundPaused: false,
          chatProfile: 'current',
        },
        hardLimits: { global: 1000, subject: 100 },
        modelProfiles: [
          { id: 'current', model: 'qwen3.7-flash', accepted: true },
          { id: 'quality', model: 'qwen3.7-plus', accepted: false },
        ],
        groups: [
          group,
          {
            ...group,
            kind: 'upstream',
            operation: 'chat',
            provider: 'dashscope',
            model: 'qwen3.7-flash',
            outcome: 'success',
            count: 42,
            input_known: 42,
            input_tokens: 12300,
            output_known: 42,
            output_tokens: 5400,
            priced_calls: 42,
            estimated_cost: 0.0285,
            currency: 'CNY',
            p95_ms: 1870,
          },
          {
            ...group,
            kind: 'upstream',
            operation: 'chat',
            provider: 'dashscope',
            model: 'qwen3.7-flash',
            outcome: 'timeout',
            count: 3,
          },
          {
            ...group,
            kind: 'cache',
            operation: 'query_embedding',
            outcome: 'hit',
            count: 18,
          },
        ],
        queue: [
          {
            job_type: 'research',
            status: 'queued',
            count: 2,
            oldest_wait_seconds: 17,
          },
          {
            job_type: 'manual',
            status: 'running',
            count: 1,
            oldest_wait_seconds: 0,
          },
          {
            job_type: 'identify',
            status: 'succeeded',
            count: 32,
            oldest_wait_seconds: 0,
          },
        ],
        trend: (options.singlePoint
          ? [152]
          : [210, 170, 190, 134, 142, 118, 155, 172, 126, 144, 121, 108]
        ).map((p95_ms, index) => ({
          hour: `2026-10-19T${String(index).padStart(2, '0')}:00:00Z`,
          kind: 'http',
          count: 12,
          p95_ms,
        })),
        costNotice:
          '费用为配置单价估算，不等于云账单；未知用量和未计价调用单独显示。',
      });
    if (path === '/admin/audit')
      return reply({
        items: [
          {
            id: 1,
            actor_id: 'preview-admin',
            version: 4,
            created_at: '2026-10-19T01:20:00Z',
            after_value: {
              global_limit: 80,
              subject_limit: 40,
              background_paused: false,
              chat_profile: 'current',
            },
          },
        ],
        nextBefore: null,
      });
    if (path.includes('/manual'))
      return reply({
        id: 'ui-manual',
        version: 1,
        visibility: 'public',
        status: 'ready',
        jobId: null,
        sections: [
          {
            id: 'ui-section',
            parentId: null,
            sectionKey: 'charging',
            title: '充电与电池',
            sortOrder: 0,
            generationStatus: 'ready',
            body: '**开始充电**\n1. 使用随附的 USB Type-C 连接线。\n2. 将耳机连接到电源。\n\n完全充满大约需要 3.5 小时。[1]',
            citations: [citation],
          },
        ],
      });
    if (path.includes('/memories'))
      return reply({
        items: [
          {
            id: 'ui-memory',
            memoryType: 'preference',
            scope: 'instance',
            userProductInstanceId: product.id,
            content: '偏好简短的中文操作说明。',
            consentStatus: 'confirmed',
            confirmedAt: '2026-10-18T00:00:00Z',
            expiresAt: null,
          },
        ],
      });
    if (path === '/guidance-sessions' && method === 'POST')
      return reply(
        {
          id: 'ui-guidance',
          topic: '开始充电',
          status: 'active',
          riskLevel: 'low',
          version: 1,
          currentStepNo: 1,
          notice: null,
          currentStep: {
            id: 'ui-step',
            stepNo: 1,
            instruction: '将 USB Type-C 连接线接入耳机充电接口。',
            successCriteria: '确认充电指示灯亮起。',
            riskLevel: 'low',
            preconditions: [],
            safetyNotes: [],
            failureOfStepId: null,
            status: 'active',
            citations: [citation],
          },
        },
        201,
      );
    if (path === '/guidance-sessions' || path === '/conversations')
      return reply({ items: [], nextCursor: null });
    return reply({ items: [], nextCursor: null });
  });
}

test('键盘仅缩小visualViewport时仍可发送，收起后恢复', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 667 });
  await page.addInitScript(() => {
    const viewport = new EventTarget();
    Object.defineProperties(viewport, {
      height: { value: 667, writable: true },
      offsetTop: { value: 0, writable: true },
    });
    Object.defineProperty(window, 'visualViewport', {
      value: viewport,
      configurable: true,
    });
  });
  await fixture(page, { product: true, admin: false });
  await page.goto('/');
  await page.getByLabel('提问内容').fill('键盘遮挡回归');
  await page.evaluate(() => {
    Object.defineProperty(window.visualViewport!, 'height', {
      value: 360,
      writable: true,
    });
    window.visualViewport!.dispatchEvent(new Event('resize'));
  });
  await withinTaskViewport(page, '.chat-composer');
  await withinTaskViewport(page, '.messages');
  await page.getByRole('button', { name: '说明书 · 记忆 · 操作引导' }).click();
  await expect(async () => {
    const sheet = await page
      .getByRole('dialog', { name: '设备工具' })
      .boundingBox();
    expect(sheet!.y).toBeGreaterThanOrEqual(0);
    expect(sheet!.y + sheet!.height).toBeLessThanOrEqual(360);
  }).toPass();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog', { name: '设备工具' })).toHaveCount(0);
  await expect(
    page.getByRole('button', { name: '说明书 · 记忆 · 操作引导' }),
  ).toBeFocused();
  await page.evaluate(() => {
    Object.defineProperty(window.visualViewport!, 'offsetTop', {
      value: 250,
      writable: true,
    });
    window.visualViewport!.dispatchEvent(new Event('scroll'));
  });
  await withinTaskViewport(page, '.chat-composer');
  await withinTaskViewport(page, '.messages');
  expect(
    (await page.locator('.messages').boundingBox())!.y,
  ).toBeGreaterThanOrEqual(250);
  await page.screenshot({
    path: 'test-results/mobile-task/keyboard-visual-viewport.png',
    animations: 'disabled',
  });
  await page.evaluate(() => {
    Object.defineProperty(window.visualViewport!, 'height', {
      value: 667,
      writable: true,
    });
    Object.defineProperty(window.visualViewport!, 'offsetTop', {
      value: 0,
      writable: true,
    });
    window.visualViewport!.dispatchEvent(new Event('resize'));
  });
  await withinTaskViewport(page, '.chat-composer');
  await expect(page.getByLabel('提问内容')).toHaveValue('键盘遮挡回归');
});

for (const count of [1, 10]) {
  test(`${count}张聊天草稿图片与键盘不遮挡移除和发送`, async ({ page }) => {
    await page.setViewportSize({ width: 360, height: 640 });
    await page.addInitScript(() => {
      const viewport = new EventTarget();
      Object.defineProperties(viewport, {
        height: { value: 640, writable: true },
        offsetTop: { value: 0, writable: true },
      });
      Object.defineProperty(window, 'visualViewport', {
        value: viewport,
        configurable: true,
      });
    });
    await fixture(page, { product: true, admin: false });
    await page.goto('/');
    for (let index = 0; index < count; index++) {
      await page.locator('.chat-photo-draft input[type=file]').setInputFiles({
        name: `question-${index}.png`,
        mimeType: 'image/png',
        buffer: chatPhoto,
      });
      await expect(
        page.getByRole('button', {
          name: `移除提问图片 ${index + 1}`,
          exact: true,
        }),
      ).toBeEnabled();
    }
    for (const index of [1, count]) {
      const selector = `[aria-label="移除提问图片 ${index}"]`;
      await page.locator(selector).scrollIntoViewIfNeeded();
      await withinTaskViewport(page, selector);
    }
    await page.getByLabel('提问内容').fill('图片仍随问题发送');
    await page.evaluate(() => {
      Object.defineProperty(window.visualViewport!, 'height', {
        value: 360,
        writable: true,
      });
      window.visualViewport!.dispatchEvent(new Event('resize'));
    });
    for (const index of [1, count]) {
      const selector = `[aria-label="移除提问图片 ${index}"]`;
      await page.locator(selector).scrollIntoViewIfNeeded();
      await withinTaskViewport(page, selector);
    }
    await withinTaskViewport(page, '.chat-composer');
    await withinTaskViewport(page, '.messages');
    await page.evaluate(() => {
      Object.defineProperty(window.visualViewport!, 'offsetTop', {
        value: 250,
        writable: true,
      });
      window.visualViewport!.dispatchEvent(new Event('scroll'));
    });
    await withinTaskViewport(page, '.chat-composer');
    await withinTaskViewport(page, '.messages');
    await withinTaskViewport(page, `[aria-label="移除提问图片 ${count}"]`);
    await page.screenshot({
      path: `test-results/mobile-task/chat-attachments-${count}-keyboard.png`,
      animations: 'disabled',
    });
    await page
      .getByRole('button', { name: `移除提问图片 ${count}`, exact: true })
      .click();
    await expect(page.getByRole('button', { name: '发送问题' })).toBeEnabled();
    await expect(page.getByLabel('提问内容')).toHaveValue('图片仍随问题发送');
    await page.evaluate(() => {
      Object.defineProperty(window.visualViewport!, 'offsetTop', {
        value: 0,
        writable: true,
      });
      Object.defineProperty(window.visualViewport!, 'height', {
        value: 640,
        writable: true,
      });
      window.visualViewport!.dispatchEvent(new Event('resize'));
    });
    await withinTaskViewport(page, '.chat-composer');
  });
}

test('失败资料保留原因和重试，聊天与工具切换保留草稿', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 640 });
  await fixture(page, { product: true, failed: true, admin: false });
  await page.goto('/');
  await expect(page.getByText('资料构建失败', { exact: true })).toBeVisible();
  await page.getByText('查看失败原因', { exact: true }).click();
  await expect(
    page.getByText('未找到适用资料，请重新查找或补充准确型号。', {
      exact: true,
    }),
  ).toBeVisible();
  await page.getByLabel('提问内容').fill('仍然保留的问题');
  const retry = page.waitForRequest(
    (request) =>
      request.url().includes('/knowledge-jobs') && request.method() === 'POST',
  );
  await page.getByRole('button', { name: '重试资料构建', exact: true }).click();
  await retry;
  await page.getByText('查看失败原因', { exact: true }).click();
  await withinTaskViewport(page, '.chat-composer');
  await page.getByRole('button', { name: '说明书 · 记忆 · 操作引导' }).click();
  await page.getByRole('button', { name: '打开资料', exact: true }).click();
  await expect(page.getByRole('heading', { name: '充电与电池' })).toBeVisible();
  await page.getByRole('button', { name: '返回设备聊天' }).click();
  await expect(page.getByLabel('提问内容')).toHaveValue('仍然保留的问题');
  await page.screenshot({
    path: 'test-results/mobile-task/failed-knowledge.png',
    animations: 'disabled',
  });
});

async function fits(page: Page) {
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
}

async function withinTaskViewport(page: Page, selector: string) {
  // visualViewport resize listeners and textarea layout settle after the browser resize.
  await expect(async () => {
    const target = await page.locator(selector).boundingBox();
    const nav = await page
      .getByRole('navigation', { name: '移动页面导航', includeHidden: true })
      .boundingBox();
    const topbar = await page.locator('.topbar').boundingBox();
    expect(target).not.toBeNull();
    expect(target!.y).toBeGreaterThanOrEqual(topbar!.y + topbar!.height);
    const viewportBottom = await page.evaluate(
      () =>
        (window.visualViewport?.offsetTop ?? 0) +
        (window.visualViewport?.height ?? innerHeight),
    );
    expect(target!.y + target!.height).toBeLessThanOrEqual(
      nav?.y ?? viewportBottom,
    );
    expect(target!.x).toBeGreaterThanOrEqual(0);
    expect(target!.x + target!.width).toBeLessThanOrEqual(
      page.viewportSize()!.width,
    );
    expect(target!.height).toBeGreaterThanOrEqual(
      selector === '.messages' ? 24 : 36,
    );
    expect(
      await page.locator(selector).evaluate((element) => {
        const rect = element.getBoundingClientRect();
        const hit = document.elementFromPoint(
          rect.x + rect.width / 2,
          rect.bottom - 2,
        );
        return hit && element.contains(hit)
          ? true
          : {
              hit: hit?.outerHTML.slice(0, 200),
              rect: rect.toJSON(),
              parent: element.parentElement?.getBoundingClientRect().toJSON(),
            };
      }),
    ).toBe(true);
  }).toPass({ timeout: 5000 });
}

for (const viewport of [
  { width: 360, height: 640 },
  { width: 390, height: 667 },
  { width: 430, height: 844 },
  { width: 430, height: 932 },
  { width: 844, height: 390 },
]) {
  test(`任务首屏主操作 ${viewport.width}x${viewport.height}`, async ({
    page,
  }) => {
    await page.setViewportSize(viewport);
    await fixture(page, { admin: false });
    await page.goto('/');
    await expect(
      page.getByRole('heading', { name: '认识你的产品' }),
    ).toBeVisible();
    await withinTaskViewport(page, '.identify-footer .ant-btn');
    await page.screenshot({
      path: `test-results/mobile-task/capture-${viewport.width}x${viewport.height}.png`,
      animations: 'disabled',
    });
    await fixture(page, { product: true, admin: false });
    await page.reload();
    await expect(
      page.getByRole('heading', { name: 'WH-1000XM5', exact: true }),
    ).toBeVisible();
    await withinTaskViewport(page, '.chat-composer');
    await withinTaskViewport(page, '.messages');
    await fits(page);
    await page.screenshot({
      path: `test-results/mobile-task/chat-${viewport.width}x${viewport.height}.png`,
      animations: 'disabled',
    });
    await page.getByLabel('提问内容').fill('键盘出现后仍然可以发送的问题');
    await page.setViewportSize({
      width: viewport.width,
      height: Math.min(viewport.height, 500),
    });
    await withinTaskViewport(page, '.chat-composer');
    await expect(page.getByRole('button', { name: '发送问题' })).toBeEnabled();
  });
}

for (const width of [320, 390, 1440]) {
  test(`${width}px 登录与空设备工作区`, async ({ page }) => {
    await page.setViewportSize({ width, height: width < 800 ? 844 : 1000 });
    await fixture(page, { authenticated: false, admin: false });
    await page.goto('/');
    await expect(
      page.getByRole('button', { name: '登录并开始' }),
    ).toBeVisible();
    await fits(page);
    await page.screenshot({
      path: `test-results/ui-refresh/auth-${width}.png`,
      fullPage: true,
      animations: 'disabled',
    });
    await page.getByLabel('邮箱').fill('preview@example.com');
    await page.getByLabel('密码', { exact: true }).fill('ui-test-password');
    await page.getByRole('button', { name: '登录并开始' }).click();
    await expect(
      page.getByRole('heading', { name: '认识你的产品' }),
    ).toBeVisible();
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);
    await fits(page);
    const card = await page.locator('.identify-card').boundingBox();
    const description = await page
      .getByLabel('或者，描述一下你的产品')
      .boundingBox();
    const identify = await page
      .getByRole('button', { name: '开始识别', exact: true })
      .boundingBox();
    expect(description!.y + description!.height).toBeLessThan(
      card!.y + card!.height,
    );
    expect(identify!.y + identify!.height).toBeLessThan(card!.y + card!.height);
    if (width < 800) {
      const mobile = page.getByRole('navigation', {
        name: '移动页面导航',
        includeHidden: true,
      });
      await expect(mobile).toBeVisible();
      await expect(
        mobile.getByRole('link', { name: '设备工作区' }),
      ).toHaveAttribute('aria-current', 'page');
      await expect(mobile.getByRole('link', { name: '运行管理' })).toHaveCount(
        0,
      );
    }
    await page.screenshot({
      path: `test-results/ui-refresh/workspace-${width}.png`,
      fullPage: true,
      animations: 'disabled',
    });
    await page.screenshot({
      path: `test-results/ui-refresh/workspace-viewport-${width}.png`,
      animations: 'disabled',
    });
  });

  test(`${width}px 管理空间与单点趋势`, async ({ page }) => {
    await page.setViewportSize({ width, height: width < 800 ? 844 : 1000 });
    await fixture(page, { singlePoint: width === 320 });
    await page.goto('/admin');
    await expect(
      page.getByRole('heading', { name: '运行管理', exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole('button', { name: '保存运行配置' }),
    ).toBeVisible();
    await expect(
      page.getByRole('img', { name: '请求P95耗时趋势' }),
    ).toBeVisible();
    if (width === 320)
      await expect(page.getByText(/仅有一个小时样本/)).toBeVisible();
    await fits(page);
    const navigation = page.getByRole('navigation', {
      name: width < 800 ? '移动页面导航' : '主导航',
    });
    await expect(
      navigation.getByRole('link', { name: '运行管理' }),
    ).toHaveAttribute('aria-current', 'page');
    await page.screenshot({
      path: `test-results/ui-refresh/admin-${width}.png`,
      fullPage: true,
      animations: 'disabled',
    });
    await page.screenshot({
      path: `test-results/ui-refresh/admin-viewport-${width}.png`,
      animations: 'disabled',
    });
  });
}

test('桌面大量历史会话在面板内滚动且不挤走输入区', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await fixture(page, { product: true, admin: false });
  await page.route('**/api/v1/conversations?*', (route) =>
    route.fulfill({
      json: {
        items: Array.from({ length: 30 }, (_, i) => ({
          id: `history-${i}`,
          title: `设备会话 ${i + 1}`,
          userProductInstanceId: product.id,
          createdAt: '2026-11-06T00:00:00Z',
          updatedAt: '2026-11-06T00:00:00Z',
        })),
        nextCursor: null,
      },
    }),
  );
  await page.goto('/');
  await page.getByRole('button', { name: '历史会话', exact: true }).click();
  await expect(
    page.getByRole('button', { name: /设备会话 30/ }),
  ).toBeAttached();
  const box = await page.locator('.chat-composer').boundingBox();
  expect(box!.y + box!.height).toBeLessThanOrEqual(900);
  await page
    .getByRole('button', { name: /设备会话 30/ })
    .scrollIntoViewIfNeeded();
  await expect(
    page.getByRole('button', { name: /设备会话 30/ }),
  ).toBeInViewport();
});

test('桌面模块保留草稿并按需展开资料右栏', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await fixture(page, { product: true, admin: false });
  await page.goto('/');
  await page.getByLabel('提问内容').fill('切换模块后仍保留');
  await page.getByRole('button', { name: '资料', exact: true }).click();
  await expect(page.getByRole('heading', { name: '充电与电池' })).toBeVisible();
  await page.getByRole('button', { name: '记忆', exact: true }).click();
  await expect(page.getByText('偏好简短的中文操作说明。')).toBeVisible();
  await page.getByRole('button', { name: '聊天', exact: true }).click();
  await expect(page.getByLabel('提问内容')).toHaveValue('切换模块后仍保留');
  await expect(
    page.getByRole('complementary', { name: '当前回答资料来源' }),
  ).toBeVisible();
  await page.getByRole('button', { name: '收起资料来源' }).click();
  await expect(
    page.getByRole('complementary', { name: '当前回答资料来源' }),
  ).toHaveCount(0);
  await page.getByRole('button', { name: '展开资料来源' }).click();
  await fits(page);
  await page.screenshot({
    path: 'test-results/ui-refresh/glass-desktop.png',
    animations: 'disabled',
  });
});

test('手机设备工具、输入区与账号抽屉', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await fixture(page, { product: true, speech: true });
  await page.goto('/');
  await expect(
    page.getByRole('heading', { name: 'WH-1000XM5', exact: true }),
  ).toBeVisible();
  await fits(page);
  await page.locator('.speech-menu > summary').click();
  await expect(page.getByRole('button', { name: '录音提问' })).toBeVisible();
  await expect(page.getByLabel('上传录音')).toBeAttached();
  await page.locator('.speech-menu > summary').click();
  await page.screenshot({
    path: 'test-results/ui-refresh/device-mobile.png',
    fullPage: true,
    animations: 'disabled',
  });
  await page.getByRole('button', { name: '说明书 · 记忆 · 操作引导' }).click();
  await page.getByRole('button', { name: '打开资料', exact: true }).click();
  await expect(page.getByRole('heading', { name: '充电与电池' })).toBeVisible();
  await expect(page.getByText('已核验来源', { exact: true })).toBeVisible();
  await fits(page);
  await page.screenshot({
    path: 'test-results/ui-refresh/manual-mobile.png',
    fullPage: true,
    animations: 'disabled',
  });
  await page.getByRole('button', { name: '说明书 · 记忆 · 操作引导' }).click();
  await page.getByRole('button', { name: '打开记忆', exact: true }).click();
  await expect(page.getByText('偏好简短的中文操作说明。')).toBeVisible();
  await page.getByRole('button', { name: '说明书 · 记忆 · 操作引导' }).click();
  await page.getByRole('button', { name: '打开操作引导', exact: true }).click();
  await page.getByLabel('你想完成什么操作？').fill('开始充电');
  await page.getByRole('button', { name: '开始引导', exact: true }).click();
  await expect(
    page.getByText('将 USB Type-C 连接线接入耳机充电接口。'),
  ).toBeVisible();
  await page.screenshot({
    path: 'test-results/ui-refresh/guidance-mobile.png',
    fullPage: true,
    animations: 'disabled',
  });
  await page.getByRole('button', { name: '返回设备聊天' }).click();
  await page.getByLabel('提问内容').fill('设备应该怎样充电？');
  await page.setViewportSize({ width: 390, height: 500 });
  await page.getByLabel('提问内容').scrollIntoViewIfNeeded();
  const composer = await page.locator('.chat-composer').boundingBox();
  const navigation = await page
    .getByRole('navigation', { name: '移动页面导航', includeHidden: true })
    .boundingBox();
  expect(composer!.y + composer!.height).toBeLessThanOrEqual(navigation!.y);
  await fits(page);
  await page.getByRole('button', { name: '打开账号菜单' }).click();
  await expect(page.getByRole('dialog', { name: '账号与隐私' })).toBeVisible();
  await expect(
    page.getByRole('button', { name: '删除账号', exact: true }),
  ).toBeVisible();
});

test('普通账号直接访问管理页与减少动态效果', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await fixture(page, { admin: false });
  await page.goto('/admin');
  await expect(page.getByText('此页面仅供已授权管理员使用。')).toBeVisible();
  await expect(page.getByRole('button', { name: '保存运行配置' })).toHaveCount(
    0,
  );
  await page.goto('/');
  await expect(page.locator('.scene-card').first()).toBeAttached();
  expect(
    await page
      .locator('.scene-card')
      .first()
      .evaluate((element) => getComputedStyle(element).animationName),
  ).toBe('none');
});
