import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';
import { post, streamAnswer } from '../services/api';
import { HomePage } from './HomePage';

vi.mock('../services/api', () => ({
  api: vi.fn(async (path: string) =>
    path === '/me/products'
      ? {
          items: [
            {
              id: 'instance',
              productModelId: 'model',
              brandName: 'Sony',
              modelCode: 'WH-1000XM5',
              identityStatus: 'verified',
              knowledgeStatus: 'ready',
            },
          ],
        }
      : { speechToText: false },
  ),
  post: vi.fn(),
  streamAnswer: vi.fn(),
  upload: vi.fn(),
  newKey: () => 'test-key',
}));

test('重新识别返回上传入口，不删除产品或自动建立新的资料任务', async () => {
  render(<HomePage />);
  await screen.findByRole('heading', { name: 'WH-1000XM5', level: 2 });
  await userEvent.click(screen.getByRole('button', { name: '重新识别' }));
  expect(screen.getByRole('heading', { name: '认识你的产品' })).toBeVisible();
  expect(screen.getByText('把你的设备，放进这里')).toBeVisible();
  expect(
    screen.queryByRole('heading', { name: 'WH-1000XM5', level: 2 }),
  ).not.toBeInTheDocument();
  expect(post).not.toHaveBeenCalled();
});

test('正在生成回答时不能重新识别并丢失当前运行', async () => {
  vi.mocked(post).mockResolvedValueOnce({ id: 'conversation' });
  vi.mocked(streamAnswer).mockImplementationOnce(
    () => new Promise<never>(() => {}),
  );
  render(<HomePage />);
  await screen.findByRole('heading', { name: 'WH-1000XM5', level: 2 });
  await userEvent.click(screen.getByRole('button', { name: '如何开始使用？' }));
  await userEvent.click(screen.getByRole('button', { name: '发送问题' }));
  await waitFor(() =>
    expect(screen.getByRole('button', { name: '重新识别' })).toBeDisabled(),
  );
  expect(screen.getByRole('button', { name: '停止回答' })).toBeVisible();
});
