import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { beforeEach, expect, test, vi } from 'vitest';
import { api, post, streamAnswer } from '../services/api';
import { ConversationPage } from './ConversationPage';

vi.mock('../services/api', () => ({
  api: vi.fn(),
  post: vi.fn(),
  streamAnswer: vi.fn(),
  upload: vi.fn(),
  newKey: () => 'key',
}));

const product = {
  id: 'camera',
  productModelId: 'model',
  nickname: null,
  brandName: 'Canon',
  modelCode: 'R6',
  identityStatus: 'verified',
  verificationStatus: 'verified',
  knowledgeStatus: 'ready',
  knowledgeJobId: null,
};
const question = {
  id: 'question',
  role: 'user',
  content: '对焦问题',
  citations: [],
  assets: [
    {
      id: 'photo',
      mimeType: 'image/jpeg',
      sizeBytes: 123,
      status: 'available',
      kind: 'product_photo',
      readUrl: '/photo',
    },
  ],
};

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === '/conversations/saved')
      return {
        id: 'saved',
        title: '相机设置',
        userProductInstanceId: 'camera',
        status: 'active',
        createdAt: '2026-10-20T08:00:00Z',
      };
    if (path === '/me/products/camera') return product;
    if (path === '/conversations/saved/messages')
      return { items: [question], nextCursor: null };
    if (path === '/capabilities') return { speechToText: false };
    throw new Error(`Unexpected request: ${path}`);
  });
});

function Location() {
  const location = useLocation();
  return (
    <output aria-label="当前地址">
      {location.pathname}
      {location.search}
    </output>
  );
}

function show() {
  render(
    <MemoryRouter initialEntries={['/conversations/saved']}>
      <Location />
      <Routes>
        <Route
          path="/conversations/:conversationId?"
          element={<ConversationPage />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

test('从持久链接恢复对应设备和图片，继续提问不新建会话', async () => {
  vi.mocked(streamAnswer).mockResolvedValueOnce({
    id: 'run',
    status: 'completed',
    message: {
      id: 'answer',
      role: 'assistant',
      content: '继续回答',
      citations: [],
    },
  });
  show();
  expect(
    await screen.findByRole('heading', { name: 'Canon R6', level: 1 }),
  ).toBeVisible();
  expect(screen.getByText('对焦问题')).toBeVisible();
  expect(screen.getByRole('img', { name: '提问图片 1' })).toHaveAttribute(
    'src',
    '/photo',
  );
  await userEvent.type(screen.getByLabelText('提问内容'), '继续问');
  await userEvent.click(screen.getByRole('button', { name: '发送问题' }));
  expect(await screen.findByText('继续回答')).toBeVisible();
  expect(post).not.toHaveBeenCalled();
  expect(streamAnswer).toHaveBeenCalledWith(
    'saved',
    '继续问',
    expect.any(Object),
    expect.any(AbortSignal),
    'key',
    undefined,
    [],
  );
});

test('恢复失败时保留重试入口，不打开错误设备的新会话', async () => {
  vi.mocked(api).mockRejectedValueOnce(new Error('会话不存在或已删除'));
  show();
  expect(await screen.findByRole('alert')).toHaveTextContent(
    '会话不存在或已删除',
  );
  expect(screen.queryByLabelText('提问内容')).toBeNull();
  await userEvent.click(screen.getByRole('button', { name: '重试' }));
  expect(await screen.findByText('对焦问题')).toBeVisible();
});

test('新建会话后更新持久链接，刷新目标不再指向旧会话', async () => {
  vi.mocked(post).mockResolvedValueOnce({ id: 'new-conversation' });
  vi.mocked(streamAnswer).mockResolvedValueOnce({
    id: 'new-run',
    status: 'completed',
    message: {
      id: 'new-answer',
      role: 'assistant',
      content: '新会话回答',
      citations: [],
    },
  });
  show();
  await screen.findByText('对焦问题');
  await userEvent.click(screen.getByRole('button', { name: '新建会话' }));
  expect(screen.queryByText('对焦问题')).toBeNull();
  await waitFor(() =>
    expect(screen.getByLabelText('当前地址')).toHaveTextContent(
      '/conversations?instanceId=camera',
    ),
  );
  await userEvent.type(screen.getByLabelText('提问内容'), '新问题');
  await userEvent.click(screen.getByRole('button', { name: '发送问题' }));
  expect(await screen.findByText('新会话回答')).toBeVisible();
  await waitFor(() =>
    expect(screen.getByLabelText('当前地址')).toHaveTextContent(
      '/conversations/new-conversation',
    ),
  );
  await waitFor(() =>
    expect(streamAnswer).toHaveBeenCalledWith(
      'new-conversation',
      '新问题',
      expect.any(Object),
      expect.any(AbortSignal),
      'key',
      undefined,
      [],
    ),
  );
  expect(screen.queryByRole('alert')).toBeNull();
});
