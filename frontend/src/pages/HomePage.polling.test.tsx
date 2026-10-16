import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, test, vi } from 'vitest';
import { api } from '../services/api';
import type { ProductInstance } from '../types/api';
import { HomePage } from './HomePage';

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

const first: ProductInstance = {
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
const second: ProductInstance = {
  ...first,
  id: 'second',
  productModelId: 'model-second',
  modelCode: 'Device B',
  knowledgeJobId: null,
  knowledgeStatus: 'ready',
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

beforeEach(() => vi.resetAllMocks());

async function chooseSecond() {
  await userEvent.click(screen.getByRole('combobox', { name: '切换产品' }));
  await userEvent.click(screen.getByText('Device B · Example'));
  await screen.findByRole('heading', { name: 'Device B', level: 2 });
}

test('旧产品查询晚于切换返回时保留新产品且不继续查旧任务', async () => {
  const response = deferred<ProductInstance>();
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === '/me/products') return { items: [first, second] };
    if (path === '/me/products/first') return response.promise;
    throw new Error(`Unexpected request: ${path}`);
  });
  render(<HomePage />);
  await waitFor(() => expect(api).toHaveBeenCalledWith('/me/products/first'), {
    timeout: 3500,
  });
  await chooseSecond();
  await act(async () =>
    response.resolve({ ...first, knowledgeStatus: 'ready' }),
  );
  expect(
    screen.getByRole('heading', { name: 'Device B', level: 2 }),
  ).toBeVisible();
  expect(api).not.toHaveBeenCalledWith('/jobs/job-a');
});

test('重新识别后迟到的产品查询不能恢复旧产品界面', async () => {
  const response = deferred<ProductInstance>();
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === '/me/products') return { items: [first] };
    if (path === '/me/products/first') return response.promise;
    throw new Error(`Unexpected request: ${path}`);
  });
  render(<HomePage />);
  await waitFor(() => expect(api).toHaveBeenCalledWith('/me/products/first'), {
    timeout: 3500,
  });
  await userEvent.click(screen.getByRole('button', { name: '重新识别' }));
  await act(async () =>
    response.resolve({ ...first, knowledgeStatus: 'ready' }),
  );
  expect(screen.getByRole('heading', { name: '认识你的产品' })).toBeVisible();
  expect(
    screen.queryByRole('heading', { name: 'Device A', level: 2 }),
  ).toBeNull();
});

test.each(['success', 'failure'])(
  '切换后丢弃旧任务的迟到 %s',
  async (outcome) => {
    const response = deferred<unknown>();
    vi.mocked(api).mockImplementation(async (path) => {
      if (path === '/me/products') return { items: [first, second] };
      if (path === '/me/products/first') return first;
      if (path === '/jobs/job-a') return response.promise;
      throw new Error(`Unexpected request: ${path}`);
    });
    render(<HomePage />);
    await waitFor(() => expect(api).toHaveBeenCalledWith('/jobs/job-a'), {
      timeout: 3500,
    });
    await chooseSecond();
    await act(async () => {
      if (outcome === 'success')
        response.resolve({
          id: 'job-a',
          status: 'failed',
          progress: {},
          lastError: '旧任务失败',
        });
      else response.reject(new Error('旧任务网络错误'));
    });
    expect(
      screen.getByRole('heading', { name: 'Device B', level: 2 }),
    ).toBeVisible();
    expect(screen.queryByText('旧任务失败')).toBeNull();
    expect(screen.queryByText('旧任务网络错误')).toBeNull();
  },
);

test('切换 A→B→A 后也丢弃第一次 A 的响应', async () => {
  const response = deferred<ProductInstance>();
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === '/me/products') return { items: [first, second] };
    if (path === '/me/products/first') return response.promise;
    throw new Error(`Unexpected request: ${path}`);
  });
  render(<HomePage />);
  await waitFor(() => expect(api).toHaveBeenCalledWith('/me/products/first'), {
    timeout: 3500,
  });
  await chooseSecond();
  await userEvent.click(screen.getByRole('combobox', { name: '切换产品' }));
  await userEvent.click(screen.getByText('Device A · Example'));
  await screen.findByRole('heading', { name: 'Device A', level: 2 });
  await act(async () =>
    response.resolve({ ...first, knowledgeStatus: 'ready' }),
  );
  expect(screen.getByText('查找官方资料')).toBeVisible();
  expect(screen.queryByText('资料已就绪')).toBeNull();
  expect(api).not.toHaveBeenCalledWith('/jobs/job-a');
});

test('未切换时正常应用产品进度及任务错误', async () => {
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === '/me/products') return { items: [first] };
    if (path === '/me/products/first')
      return { ...first, knowledgeStatus: 'failed' };
    if (path === '/jobs/job-a')
      return {
        id: 'job-a',
        status: 'failed',
        progress: {},
        lastError: '当前任务失败',
      };
    throw new Error(`Unexpected request: ${path}`);
  });
  render(<HomePage />);
  await screen.findByText('当前任务失败', {}, { timeout: 3500 });
  expect(screen.getByText('资料构建失败')).toBeVisible();
});
