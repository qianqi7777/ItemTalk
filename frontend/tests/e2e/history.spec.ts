import { expect, test, type Page } from '@playwright/test';

// 内存 API 响应仅用于浏览器交互验证，不调用模型或写入业务数据库。
const headphones = {
  id: 'headphones',
  productModelId: 'sony',
  nickname: '通勤耳机',
  brandName: 'Sony',
  modelCode: 'WH-1000XM5',
  identityStatus: 'verified',
  verificationStatus: 'verified',
  knowledgeStatus: 'ready',
  knowledgeJobId: null,
};
const camera = {
  ...headphones,
  id: 'camera',
  productModelId: 'canon',
  nickname: null,
  brandName: 'Canon',
  modelCode: 'R6',
};
const conversations = [
  {
    id: 'saved-camera',
    title: '相机对焦设置',
    userProductInstanceId: 'camera',
    status: 'active',
    createdAt: '2026-10-20T08:00:00Z',
  },
  {
    id: 'saved-headphones',
    title: '怎样调节降噪',
    userProductInstanceId: 'headphones',
    status: 'active',
    createdAt: '2026-10-19T08:00:00Z',
  },
];
const citation = {
  knowledgeChunkId: 'chunk',
  documentVersionId: 'document',
  title: 'R6 使用说明书',
  url: 'https://example.com/manual',
  pageStart: 12,
  pageEnd: 12,
  rank: 1,
  verificationStatus: 'verified',
  verifiedAt: '2026-10-18T00:00:00Z',
};
const initialMessages = [
  {
    id: 'question',
    role: 'user',
    content: '怎样调整对焦模式？',
    citations: [],
    assets: [],
  },
  {
    id: 'answer',
    role: 'assistant',
    content: '请从对焦菜单选择合适的模式。[1]',
    citations: [citation],
  },
];

async function fixture(page: Page) {
  const unexpected: string[] = [];
  const sent: { path: string; body: unknown }[] = [];
  const messages = [...initialMessages];
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/^.*\/api\/v1/, '');
    const reply = (json: unknown, status = 200) =>
      route.fulfill({ status, json });
    if (path === '/me')
      return reply({ id: 'history-reader', email: 'reader@example.com' });
    if (path === '/capabilities')
      return reply({ speechToText: false, adminConsole: false });
    if (path === '/me/products') return reply({ items: [headphones, camera] });
    if (path === '/me/products/camera') return reply(camera);
    if (path === '/conversations' && request.method() === 'GET') {
      const instanceId = url.searchParams.get('instanceId');
      const query = url.searchParams.get('q') || '';
      return reply({
        items: conversations.filter(
          (item) =>
            (!instanceId || item.userProductInstanceId === instanceId) &&
            item.title.includes(query),
        ),
        nextCursor: null,
      });
    }
    if (path === '/conversations/saved-camera') return reply(conversations[0]);
    if (path === '/conversations/saved-camera/messages')
      return reply({ items: messages, nextCursor: null });
    if (path === '/conversations/saved-camera/stream') {
      sent.push({ path, body: request.postDataJSON() });
      const body = request.postDataJSON() as { content: string };
      messages.push({
        id: 'followup',
        role: 'user',
        content: body.content,
        citations: [],
        assets: [],
      });
      messages.push({
        id: 'followup-answer',
        role: 'assistant',
        content: '已沿用原会话继续回答。',
        citations: [],
      });
      return route.fulfill({
        contentType: 'text/event-stream',
        body: 'event: status\ndata: {"runId":"run","status":"running"}\n\nevent: done\ndata: {"runId":"run"}\n\n',
      });
    }
    if (path === '/conversations/saved-camera/runs/run')
      return reply({
        id: 'run',
        status: 'completed',
        message: messages.at(-1),
      });
    if (path === '/conversations/missing')
      return reply({ code: 'NOT_FOUND', message: '会话不存在或已删除' }, 404);
    unexpected.push(`${request.method()} ${path}`);
    return reply(
      { code: 'NOT_FOUND', message: 'Unexpected fixture request' },
      404,
    );
  });
  return { unexpected, sent };
}

for (const width of [320, 390, 1440]) {
  test(`${width}px 统一历史搜索筛选、跨设备继续对话与刷新`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: width < 800 ? 844 : 1000 });
    const state = await fixture(page);
    await page.goto('/history');
    const navigation = page.getByRole('navigation', {
      name: width < 800 ? '移动页面导航' : '主导航',
    });
    await expect(
      navigation.getByRole('link', { name: '历史对话' }),
    ).toHaveAttribute('aria-current', 'page');
    await expect(
      page.getByRole('link', { name: /相机对焦设置/ }),
    ).toBeVisible();
    await expect(
      page.getByRole('link', { name: /怎样调节降噪/ }),
    ).toBeVisible();
    await expect
      .poll(() =>
        page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
      )
      .toBe(true);
    await page.screenshot({
      path: `test-results/history/list-${width}.png`,
      fullPage: true,
      animations: 'disabled',
    });
    await page.getByLabel('搜索历史对话').fill('降噪');
    await page.getByRole('button', { name: '搜索', exact: true }).click();
    await expect(page.getByRole('link', { name: /相机对焦设置/ })).toHaveCount(
      0,
    );
    await expect(
      page.getByRole('link', { name: /怎样调节降噪/ }),
    ).toBeVisible();
    await page.getByLabel('搜索历史对话').clear();
    await page.getByLabel('筛选设备').selectOption('camera');
    await page.getByRole('button', { name: '搜索', exact: true }).click();
    await expect(page.getByRole('link', { name: /怎样调节降噪/ })).toHaveCount(
      0,
    );
    await page.getByRole('link', { name: /相机对焦设置/ }).click();
    await expect(page).toHaveURL(/\/conversations\/saved-camera$/);
    await expect(
      page.getByRole('heading', { name: 'Canon R6', level: 1 }),
    ).toBeVisible();
    await expect(
      page.getByText('怎样调整对焦模式？', { exact: true }),
    ).toBeVisible();
    await expect(page.getByText('已核验来源', { exact: true })).toBeVisible();
    await expect(
      navigation.getByRole('link', { name: '历史对话' }),
    ).toHaveAttribute('aria-current', 'page');
    await page.reload();
    await expect(
      page.getByRole('heading', { name: 'Canon R6', level: 1 }),
    ).toBeVisible();
    await expect(
      page.getByText('怎样调整对焦模式？', { exact: true }),
    ).toBeVisible();
    await page.getByLabel('提问内容').fill('继续问对焦');
    await page.getByRole('button', { name: '发送问题' }).click();
    await expect(page.getByText('已沿用原会话继续回答。')).toBeVisible();
    expect(state.sent).toEqual([
      {
        path: '/conversations/saved-camera/stream',
        body: { content: '继续问对焦' },
      },
    ]);
    await expect
      .poll(() =>
        page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
      )
      .toBe(true);
    await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
    await page.screenshot({
      path: `test-results/history/conversation-${width}.png`,
      fullPage: false,
      animations: 'disabled',
    });
    await page.reload();
    await expect(page.getByText('继续问对焦', { exact: true })).toBeVisible();
    await expect(page.getByText('已沿用原会话继续回答。')).toBeVisible();
    await page.getByRole('link', { name: '全部历史对话' }).click();
    await expect(
      page.getByRole('link', { name: /相机对焦设置/ }),
    ).toBeVisible();
    expect(state.unexpected).toEqual([]);
  });
}

test('无权访问或已删除的对话不展示聊天输入区', async ({ page }) => {
  const state = await fixture(page);
  await page.goto('/conversations/missing');
  await expect(page.getByRole('alert')).toHaveText('会话不存在或已删除');
  await expect(page.getByLabel('提问内容')).toHaveCount(0);
  await expect(
    page.getByRole('button', { name: '重试', exact: true }),
  ).toBeVisible();
  await page.getByRole('link', { name: '全部历史对话' }).click();
  await expect(page.getByRole('link', { name: /相机对焦设置/ })).toBeVisible();
  expect(state.unexpected).toEqual([]);
});

test('删除尚未完成时后退，迟到成功不能把用户带回聊天页', async ({ page }) => {
  await fixture(page);
  let release!: () => void;
  let waiting = false;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route('**/api/v1/conversations/saved-camera', async (route) => {
    if (route.request().method() !== 'DELETE') return route.fallback();
    waiting = true;
    await gate;
    await route.fulfill({ status: 204 });
  });
  try {
    await page.goto('/history');
    await page.getByRole('link', { name: /相机对焦设置/ }).click();
    await page.getByRole('button', { name: '删除会话' }).click();
    await page.getByLabel('输入删除以确认').fill('删除');
    await page.getByRole('button', { name: '确认删除' }).click();
    await expect.poll(() => waiting).toBe(true);
    await page.goBack();
    await expect(page).toHaveURL(/\/history$/);
    const response = page.waitForResponse(
      (value) => value.request().method() === 'DELETE',
    );
    release();
    await response;
    await page.evaluate(
      () =>
        new Promise<void>((resolve) =>
          requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
        ),
    );
    await expect(page).toHaveURL(/\/history$/);
    await expect(
      page.getByRole('link', { name: /相机对焦设置/ }),
    ).toBeVisible();
  } finally {
    release();
  }
});

test('历史加载尚未完成时离开，迟到结果不能强制跳转', async ({ page }) => {
  await fixture(page);
  let delay = false;
  let waiting = false;
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(
    '**/api/v1/conversations/saved-camera/messages',
    async (route) => {
      if (!delay) return route.fallback();
      waiting = true;
      await gate;
      await route.fulfill({
        json: { items: initialMessages, nextCursor: null },
      });
    },
  );
  try {
    await page.goto('/conversations/saved-camera');
    await expect(
      page.getByText('怎样调整对焦模式？', { exact: true }),
    ).toBeVisible();
    delay = true;
    await page.getByRole('button', { name: '历史会话', exact: true }).click();
    await page
      .getByRole('button', { name: '相机对焦设置', exact: true })
      .click();
    await expect.poll(() => waiting).toBe(true);
    await page.getByRole('link', { name: '全部历史对话' }).click();
    await expect(page).toHaveURL(/\/history$/);
    const response = page.waitForResponse((value) =>
      value.url().endsWith('/saved-camera/messages'),
    );
    release();
    await response;
    await page.evaluate(
      () =>
        new Promise<void>((resolve) =>
          requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
        ),
    );
    await expect(page).toHaveURL(/\/history$/);
    await expect(
      page.getByRole('link', { name: /相机对焦设置/ }),
    ).toBeVisible();
  } finally {
    release();
  }
});

test('查询旧运行期间新建对话，迟到回答不会进入新对话', async ({ page }) => {
  await fixture(page);
  let waiting = false;
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route('**/api/v1/conversations/saved-camera/stream', (route) =>
    route.fulfill({
      contentType: 'text/event-stream',
      body: 'event: status\ndata: {"runId":"old-run","status":"running"}\n\nevent: error\ndata: {"code":"INTERRUPTED","message":"连接中断"}\n\n',
    }),
  );
  await page.route(
    '**/api/v1/conversations/saved-camera/runs/old-run',
    async (route) => {
      waiting = true;
      await gate;
      await route.fulfill({
        json: {
          id: 'old-run',
          status: 'completed',
          message: {
            id: 'late',
            role: 'assistant',
            content: '旧会话的迟到回答',
            citations: [],
          },
        },
      });
    },
  );
  try {
    await page.goto('/conversations/saved-camera');
    await page.getByLabel('提问内容').fill('查询前的问题');
    await page.getByRole('button', { name: '发送问题' }).click();
    await page.getByRole('button', { name: '查询结果' }).click();
    await expect.poll(() => waiting).toBe(true);
    await page.getByRole('button', { name: '新建会话' }).click();
    const response = page.waitForResponse((value) =>
      value.url().endsWith('/runs/old-run'),
    );
    release();
    await response;
    await page.evaluate(
      () =>
        new Promise<void>((resolve) =>
          requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
        ),
    );
    await expect(page).toHaveURL(/\/conversations\?instanceId=camera$/);
    await expect(
      page.getByText('旧会话的迟到回答', { exact: true }),
    ).toHaveCount(0);
    await expect(
      page.getByRole('heading', { name: '关于 R6，想了解什么？' }),
    ).toBeVisible();
  } finally {
    release();
  }
});

test('未收到运行编号便断网时重试重放原请求，不重复显示问题', async ({
  page,
}) => {
  await fixture(page);
  const attempts: {
    key: string | undefined;
    retryOf: string | undefined;
    body: unknown;
  }[] = [];
  await page.route(
    '**/api/v1/conversations/saved-camera/stream',
    async (route) => {
      const headers = route.request().headers();
      attempts.push({
        key: headers['idempotency-key'],
        retryOf: headers['retry-of'],
        body: route.request().postDataJSON(),
      });
      if (attempts.length === 1) return route.abort('connectionreset');
      await route.fulfill({
        contentType: 'text/event-stream',
        body: 'event: status\ndata: {"runId":"replayed","status":"completed"}\n\nevent: done\ndata: {"runId":"replayed"}\n\n',
      });
    },
  );
  await page.route(
    '**/api/v1/conversations/saved-camera/runs/replayed',
    (route) =>
      route.fulfill({
        json: {
          id: 'replayed',
          status: 'completed',
          message: {
            id: 'answer-replayed',
            role: 'assistant',
            content: '原请求重放成功',
            citations: [],
          },
        },
      }),
  );
  await page.goto('/conversations/saved-camera');
  await page.getByLabel('提问内容').fill('断网前的问题');
  await page.getByRole('button', { name: '发送问题' }).click();
  await page.getByRole('button', { name: '重试回答' }).click();
  await expect(page.getByText('原请求重放成功', { exact: true })).toBeVisible();
  await expect(page.getByText('断网前的问题', { exact: true })).toHaveCount(1);
  expect(attempts).toHaveLength(2);
  expect(attempts[0].key).toBeTruthy();
  expect(attempts[1]).toEqual(attempts[0]);
});
