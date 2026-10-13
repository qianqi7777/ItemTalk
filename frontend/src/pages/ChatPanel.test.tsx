import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';
import { post, streamAnswer } from '../services/api';
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
