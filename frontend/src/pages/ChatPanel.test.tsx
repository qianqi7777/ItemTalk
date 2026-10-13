import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';
import { api, post, streamAnswer } from '../services/api';
import type { ProductInstance } from '../types/api';
import { ChatPanel } from './ChatPanel';

vi.mock('../services/api', () => ({
  api: vi.fn(async () => ({ speechToText: false })),
  post: vi.fn(),
  streamAnswer: vi.fn(),
  newKey: () => 'draft-key',
}));

const product: ProductInstance = {
  id: 'instance',
  productModelId: 'model',
  nickname: null,
  brandName: 'Sony',
  modelCode: 'WH-1000XM5',
  identityStatus: 'verified',
  verificationStatus: 'verified',
  knowledgeJobId: null,
  knowledgeStatus: 'ready',
};

test('建议问题只填入草稿，用户发送前不创建会话或调用模型', async () => {
  render(<ChatPanel product={product} />);
  await userEvent.click(screen.getByRole('button', { name: '如何开始使用？' }));
  expect(screen.getByLabelText('提问内容')).toHaveValue('如何开始使用？');
  expect(post).not.toHaveBeenCalled();
  expect(streamAnswer).not.toHaveBeenCalled();
});

test('历史会话恢复已有消息，继续提问沿用会话而非重新创建', async () => {
  vi.mocked(api).mockImplementation(async (path: string) => {
    if (path === '/capabilities') return { speechToText: false } as never;
    if (path.startsWith('/conversations?'))
      return {
        items: [
          { id: 'saved', title: '昨日开机', userProductInstanceId: product.id },
        ],
        nextCursor: null,
      } as never;
    if (path === '/conversations/saved/messages')
      return {
        items: [
          {
            id: 'old-question',
            role: 'user',
            content: '原来的问题',
            citations: [],
          },
        ],
        nextCursor: null,
      } as never;
    throw new Error('Unexpected request');
  });
  vi.mocked(streamAnswer).mockResolvedValueOnce({
    id: 'new-run',
    status: 'completed',
    message: {
      id: 'answer',
      role: 'assistant',
      content: '恢复后的回答',
      citations: [],
    },
  });
  render(<ChatPanel product={product} />);
  await userEvent.click(screen.getByRole('button', { name: '历史会话' }));
  await userEvent.click(
    await screen.findByRole('button', { name: '昨日开机' }),
  );
  expect(await screen.findByText('原来的问题')).toBeVisible();
  await userEvent.type(screen.getByLabelText('提问内容'), '继续问');
  await userEvent.click(screen.getByRole('button', { name: '发送问题' }));
  expect(await screen.findByText('恢复后的回答')).toBeVisible();
  expect(post).not.toHaveBeenCalled();
  expect(streamAnswer).toHaveBeenCalledWith(
    'saved',
    '继续问',
    expect.any(Object),
    expect.any(AbortSignal),
    'draft-key',
    undefined,
  );
});
