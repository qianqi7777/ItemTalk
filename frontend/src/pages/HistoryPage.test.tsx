import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, test, vi } from 'vitest';
import { api } from '../services/api';
import type { ProductInstance } from '../types/api';
import { HistoryPage } from './HistoryPage';

vi.mock('../services/api', () => ({ api: vi.fn() }));

const products: ProductInstance[] = [
  {
    id: 'headphones',
    brandName: 'Sony',
    modelCode: 'WH-1000XM5',
    nickname: '通勤耳机',
    productModelId: 'sony',
    identityStatus: 'verified',
    verificationStatus: 'verified',
    knowledgeStatus: 'ready',
    knowledgeJobId: null,
  },
  {
    id: 'camera',
    brandName: 'Canon',
    modelCode: 'R6',
    nickname: null,
    productModelId: 'canon',
    identityStatus: 'verified',
    verificationStatus: 'verified',
    knowledgeStatus: 'ready',
    knowledgeJobId: null,
  },
];
const first = {
  id: 'saved',
  title: '怎样降噪',
  userProductInstanceId: 'headphones',
  status: 'active',
  createdAt: '2026-10-20T08:00:00Z',
};

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === '/me/products') return { items: products };
    if (path === '/conversations?limit=30')
      return { items: [first], nextCursor: 'saved' };
    if (path === '/conversations?limit=30&cursor=saved')
      return {
        items: [
          {
            ...first,
            id: 'older',
            title: '相机对焦',
            userProductInstanceId: 'camera',
          },
        ],
        nextCursor: null,
      };
    if (path === '/conversations?limit=30&q=%E5%AF%B9%E7%84%A6')
      return {
        items: [
          {
            ...first,
            id: 'found',
            title: '对焦设置',
            userProductInstanceId: 'camera',
          },
        ],
        nextCursor: null,
      };
    if (path === '/conversations?limit=30&instanceId=camera')
      return { items: [], nextCursor: null };
    throw new Error(`Unexpected request: ${path}`);
  });
});

function show() {
  return render(
    <MemoryRouter>
      <HistoryPage />
    </MemoryRouter>,
  );
}

test('集中展示所有设备会话，分页保留既有结果，链接可直接恢复', async () => {
  show();
  const link = await screen.findByRole('link', { name: /怎样降噪/ });
  expect(link).toHaveAttribute('href', '/conversations/saved');
  expect(within(link).getByText('通勤耳机')).toBeVisible();
  await userEvent.click(screen.getByRole('button', { name: '加载更多对话' }));
  expect(await screen.findByRole('link', { name: /相机对焦/ })).toBeVisible();
  expect(link).toBeVisible();
  expect(
    within(screen.getByRole('link', { name: /相机对焦/ })).getByText(
      'Canon R6',
    ),
  ).toBeVisible();
});

test('搜索重置分页与旧列表，设备筛选可得到空结果', async () => {
  show();
  await screen.findByRole('link', { name: /怎样降噪/ });
  await userEvent.type(
    screen.getByRole('textbox', { name: '搜索历史对话' }),
    '对焦',
  );
  await userEvent.click(screen.getByRole('button', { name: '搜索' }));
  expect(await screen.findByRole('link', { name: /对焦设置/ })).toBeVisible();
  expect(screen.queryByRole('link', { name: /怎样降噪/ })).toBeNull();
  await userEvent.clear(screen.getByRole('textbox', { name: '搜索历史对话' }));
  await userEvent.selectOptions(
    screen.getByRole('combobox', { name: '筛选设备' }),
    'camera',
  );
  await userEvent.click(screen.getByRole('button', { name: '搜索' }));
  expect(await screen.findByText('没有找到匹配的对话')).toBeVisible();
  expect(screen.queryByRole('button', { name: '加载更多对话' })).toBeNull();
});

test('失败后可重试，不把失败显示成空历史', async () => {
  vi.mocked(api).mockRejectedValueOnce(new Error('网络断开'));
  show();
  expect(await screen.findByRole('alert')).toHaveTextContent('网络断开');
  expect(screen.queryByText('还没有历史对话')).toBeNull();
  await userEvent.click(screen.getByRole('button', { name: '重试' }));
  expect(await screen.findByRole('link', { name: /怎样降噪/ })).toBeVisible();
});

test('旧分页的迟到响应不能覆盖新搜索结果', async () => {
  let resolve!: (value: unknown) => void;
  const original = vi.mocked(api).getMockImplementation()!;
  vi.mocked(api).mockImplementation(async (path, options) => {
    if (path.includes('cursor='))
      return new Promise((yes) => {
        resolve = yes;
      });
    return original(path, options);
  });
  show();
  await screen.findByRole('link', { name: /怎样降噪/ });
  await userEvent.click(screen.getByRole('button', { name: '加载更多对话' }));
  await userEvent.type(
    screen.getByRole('textbox', { name: '搜索历史对话' }),
    '对焦',
  );
  await userEvent.click(screen.getByRole('button', { name: '搜索' }));
  await screen.findByRole('link', { name: /对焦设置/ });
  await act(async () =>
    resolve({ items: [{ ...first, title: '过时分页' }], nextCursor: null }),
  );
  await waitFor(() => expect(screen.queryByText('过时分页')).toBeNull());
});
