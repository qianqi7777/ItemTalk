import { expect, test, type Page } from '@playwright/test';

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
  } = {},
) {
  let authenticated = options.authenticated ?? true;
  const admin = options.admin ?? true;
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace(/^.*\/api\/v1/, '');
    const method = request.method();
    const reply = (body: unknown, status = 200) =>
      route.fulfill({ status, json: body });
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
      return reply({ items: options.product ? [product] : [] });
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

async function fits(page: Page) {
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
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
      const mobile = page.getByRole('navigation', { name: '移动页面导航' });
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

test('手机设备工具、输入区与账号抽屉', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await fixture(page, { product: true, speech: true });
  await page.goto('/');
  await expect(
    page.getByRole('heading', { name: 'WH-1000XM5', exact: true }),
  ).toBeVisible();
  await fits(page);
  await expect(page.getByRole('button', { name: '录音提问' })).toBeVisible();
  await expect(page.getByLabel('上传录音')).toBeAttached();
  await page.screenshot({
    path: 'test-results/ui-refresh/device-mobile.png',
    fullPage: true,
    animations: 'disabled',
  });
  await page.getByRole('button', { name: '使用说明书', exact: true }).click();
  await expect(page.getByRole('heading', { name: '充电与电池' })).toBeVisible();
  await expect(page.getByText('已核验来源', { exact: true })).toBeVisible();
  await fits(page);
  await page.screenshot({
    path: 'test-results/ui-refresh/manual-mobile.png',
    fullPage: true,
    animations: 'disabled',
  });
  await page.getByRole('button', { name: '使用说明书', exact: true }).click();
  await page.getByRole('button', { name: '我的记忆', exact: true }).click();
  await expect(page.getByText('偏好简短的中文操作说明。')).toBeVisible();
  await page.getByRole('button', { name: '操作引导', exact: true }).click();
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
  await page.getByLabel('提问内容').fill('设备应该怎样充电？');
  await page.setViewportSize({ width: 390, height: 500 });
  await page.getByLabel('提问内容').scrollIntoViewIfNeeded();
  const composer = await page.locator('.chat-composer').boundingBox();
  const navigation = await page
    .getByRole('navigation', { name: '移动页面导航' })
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
