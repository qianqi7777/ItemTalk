import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, test, vi } from 'vitest';
import { api, post, upload } from '../services/api';
import type { Asset, Identification, ProductInstance } from '../types/api';
import { HomePage } from './HomePage';
import { SessionContext } from '../stores/session';

const polling = vi.hoisted(() => ({
  current: undefined as undefined | (() => Promise<void>),
}));
vi.mock('../hooks/usePolling', () => ({
  usePolling: (action: () => Promise<void>, enabled: boolean) => {
    if (enabled) polling.current = action;
  },
}));
vi.mock('../services/api', () => ({
  api: vi.fn(),
  post: vi.fn(),
  upload: vi.fn(),
  newKey: () => 'test-key',
}));
vi.mock('../features/manuals/ManualPanel', () => ({ ManualPanel: () => null }));
vi.mock('../features/memories/MemoryPanel', () => ({
  MemoryPanel: () => null,
}));
vi.mock('../features/guidance/GuidancePanel', () => ({
  GuidancePanel: () => null,
}));
vi.mock('./ChatPanel', () => ({ ChatPanel: () => null }));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
const product: ProductInstance = {
  id: 'device',
  productModelId: 'model',
  nickname: null,
  modelCode: 'Existing Device',
  brandName: 'Acme',
  identityStatus: 'user_confirmed',
  verificationStatus: 'pending',
  knowledgeStatus: 'ready',
  knowledgeJobId: null,
};
const attempt = (
  id: string,
  status: Identification['status'],
): Identification => ({
  id,
  status,
  jobId: 'job-' + id,
  clarification: null,
  candidates:
    status === 'queued'
      ? []
      : [
          {
            candidateId: id + '-candidate',
            brandName: 'Acme',
            modelCode: id + ' Phone',
            confidence: 0.6,
            evidence: '外观候选',
          },
        ],
  photos: [],
});
beforeEach(() => {
  vi.resetAllMocks();
  polling.current = undefined;
  sessionStorage.clear();
});

test('刷新页面后保留待排除的候选，再继续补充', async () => {
  const id = '11111111-1111-4111-8111-111111111111';
  sessionStorage.setItem('itemtalk.recognition:viewer', id);
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === '/me/products') return { items: [] };
    if (path === '/identifications/' + id)
      return attempt(id, 'waiting_for_user');
    throw new Error('Unexpected request');
  });
  const page = () => (
    <SessionContext.Provider
      value={{
        user: { id: 'viewer', email: 'test@example.com' },
        refresh: async () => {},
      }}
    >
      <HomePage />
    </SessionContext.Provider>
  );
  const first = render(page());
  await screen.findByText('Acme · ' + id + ' Phone');
  await userEvent.click(
    screen.getByRole('button', { name: '都不是，排除这些候选' }),
  );
  expect(
    screen.getByText('已排除这些候选，请补充文字或照片，再继续判断。'),
  ).toBeVisible();
  first.unmount();
  render(page());
  await screen.findByText('Acme · ' + id + ' Phone');
  expect(
    screen.getByText('已排除这些候选，请补充文字或照片，再继续判断。'),
  ).toBeVisible();
});

test('排除候选后等待用户补充，再把排除项随下一轮提交', async () => {
  vi.mocked(api).mockResolvedValue({ items: [] });
  vi.mocked(post).mockResolvedValue(attempt('old', 'waiting_for_user'));
  render(<HomePage />);
  await userEvent.type(
    screen.getByRole('textbox', { name: '或者，描述一下你的产品' }),
    '一台手机',
  );
  await userEvent.click(screen.getByRole('button', { name: '开始识别' }));
  await screen.findByText('Acme · old Phone');
  await userEvent.click(
    screen.getByRole('button', { name: '都不是，排除这些候选' }),
  );
  expect(post).toHaveBeenCalledTimes(1);
  expect(
    screen.getByText('已排除这些候选，请补充文字或照片，再继续判断。'),
  ).toBeVisible();
  expect(
    screen.getByRole('button', { name: '综合照片继续判断' }),
  ).toBeDisabled();
  await userEvent.type(
    screen.getByRole('textbox', { name: '或者，描述一下你的产品' }),
    '侧面有一个红色按钮',
  );
  await userEvent.click(
    screen.getByRole('button', { name: '综合照片继续判断' }),
  );
  expect(post).toHaveBeenLastCalledWith(
    '/products/identify',
    expect.objectContaining({
      supersedesAttemptId: 'old',
      rejectedCandidateIds: ['old-candidate'],
      inputText: '侧面有一个红色按钮',
    }),
    'test-key',
  );
});

test.each(['success', 'failure'])(
  '新识别会话丢弃旧轮询的迟到 %s',
  async (outcome) => {
    const old = deferred<Identification>();
    vi.mocked(api).mockImplementation(async (path) => {
      if (path === '/me/products') return { items: [product] };
      if (path === '/identifications/old') return old.promise;
      throw new Error('Unexpected ' + path);
    });
    vi.mocked(post)
      .mockResolvedValueOnce(attempt('old', 'queued'))
      .mockResolvedValueOnce(attempt('new', 'waiting_for_user'));
    render(<HomePage />);
    await userEvent.click(
      await screen.findByRole('button', { name: '重新识别' }),
    );
    await userEvent.type(
      screen.getByRole('textbox', { name: '或者，描述一下你的产品' }),
      '旧物品',
    );
    await userEvent.click(screen.getByRole('button', { name: '开始识别' }));
    await waitFor(() => expect(polling.current).toBeDefined());
    let pending!: Promise<void>;
    act(() => {
      pending = polling.current!();
    });
    await userEvent.click(
      screen.getByRole('button', { name: /Acme.*Existing Device/ }),
    );
    await userEvent.click(screen.getByRole('button', { name: '重新识别' }));
    await userEvent.type(
      screen.getByRole('textbox', { name: '或者，描述一下你的产品' }),
      '新物品',
    );
    await userEvent.click(screen.getByRole('button', { name: '开始识别' }));
    await screen.findByText('Acme · new Phone');
    await act(async () => {
      if (outcome === 'success')
        old.resolve(attempt('old', 'waiting_for_user'));
      else old.reject(new Error('旧会话网络错误'));
      await pending;
    });
    expect(screen.getByText('Acme · new Phone')).toBeVisible();
    expect(screen.queryByText('Acme · old Phone')).toBeNull();
    expect(screen.queryByText('旧会话网络错误')).toBeNull();
  },
);

test.each(['success', 'failure'])(
  '离开并重进识别后忽略旧照片上传的迟到 %s',
  async (outcome) => {
    const old = deferred<Asset>();
    vi.mocked(api).mockResolvedValue({ items: [] });
    vi.mocked(post).mockResolvedValue(attempt('old', 'waiting_for_user'));
    vi.mocked(upload).mockReturnValue(old.promise);
    const { container, unmount } = render(<HomePage />);
    await userEvent.type(
      screen.getByRole('textbox', { name: '或者，描述一下你的产品' }),
      '先判断一台手机',
    );
    await userEvent.click(screen.getByRole('button', { name: '开始识别' }));
    await screen.findByText('Acme · old Phone');
    await userEvent.upload(
      container.querySelector(
        '#recognition-photos input[type=file]',
      ) as HTMLInputElement,
      new File(['image'], 'old.jpg', { type: 'image/jpeg' }),
    );
    expect(upload).toHaveBeenCalledTimes(1);
    unmount();
    render(<HomePage />);
    await act(async () => {
      if (outcome === 'success')
        old.resolve({
          id: 'old-photo',
          mimeType: 'image/jpeg',
          sizeBytes: 5,
          kind: 'product_photo',
          status: 'available',
          readUrl: '/old.jpg',
        });
      else old.reject(new Error('旧照片上传失败'));
    });
    expect(screen.queryByRole('img', { name: '产品照片 1' })).toBeNull();
    expect(screen.queryByText('旧照片上传失败')).toBeNull();
    expect(screen.getByRole('button', { name: '开始识别' })).toBeDisabled();
  },
);

test.each(['success', 'failure'])(
  '已开始新识别时旧草稿恢复的迟到 %s 不覆盖新会话',
  async (outcome) => {
    const old = deferred<Identification>();
    const id = '11111111-1111-4111-8111-111111111111';
    sessionStorage.setItem('itemtalk.recognition:viewer', id);
    vi.mocked(api).mockImplementation(async (path) => {
      if (path === '/me/products') return { items: [product] };
      if (path === '/identifications/' + id) return old.promise;
      throw new Error('Unexpected ' + path);
    });
    vi.mocked(post).mockResolvedValue(attempt('new', 'waiting_for_user'));
    render(
      <SessionContext.Provider
        value={{
          user: { id: 'viewer', email: 'test@example.com' },
          refresh: async () => {},
        }}
      >
        <HomePage />
      </SessionContext.Provider>,
    );
    await waitFor(() =>
      expect(api).toHaveBeenCalledWith('/identifications/' + id),
    );
    await userEvent.type(
      screen.getByRole('textbox', { name: '或者，描述一下你的产品' }),
      '新物品',
    );
    await userEvent.click(screen.getByRole('button', { name: '开始识别' }));
    await screen.findByText('Acme · new Phone');
    await act(async () => {
      if (outcome === 'success')
        old.resolve(attempt('old', 'waiting_for_user'));
      else old.reject(new Error('旧草稿已经过期'));
    });
    expect(screen.getByText('Acme · new Phone')).toBeVisible();
    expect(sessionStorage.getItem('itemtalk.recognition:viewer')).toBe('new');
    expect(
      screen.queryByRole('heading', { name: 'Existing Device' }),
    ).toBeNull();
    expect(screen.queryByText('旧草稿已经过期')).toBeNull();
  },
);
