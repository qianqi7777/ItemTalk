import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, test, vi } from 'vitest';
import { api, post, streamAnswer, upload } from '../services/api';
import type { ProductInstance } from '../types/api';
import { ChatPanel } from './ChatPanel';

vi.mock('../services/api', () => ({
  api: vi.fn(async () => ({ speechToText: false })),
  post: vi.fn(),
  streamAnswer: vi.fn(),
  upload: vi.fn(),
  newKey: () => 'draft-key',
}));

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api).mockResolvedValue({ speechToText: false } as never);
});

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
    [],
  );
});

const photo = {
  id: 'photo-1',
  mimeType: 'image/jpeg',
  sizeBytes: 123,
  status: 'available',
  kind: 'product_photo',
  readUrl: '/private-photo',
};

test('图片上传不自动提问，纯图片发送并在消息中展示附件', async () => {
  vi.mocked(upload).mockResolvedValue(photo);
  vi.mocked(post).mockResolvedValue({ id: 'conversation' });
  vi.mocked(streamAnswer).mockResolvedValue({ id: 'run', status: 'completed' });
  const { container } = render(<ChatPanel product={product} />);
  await userEvent.upload(
    container.querySelector('input[type="file"]')!,
    new File(['photo'], 'device.jpg', { type: 'image/jpeg' }),
  );
  expect(await screen.findByLabelText('待发送图片')).toBeVisible();
  expect(post).not.toHaveBeenCalled();
  expect(streamAnswer).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole('button', { name: '发送问题' }));
  await waitFor(() => expect(streamAnswer).toHaveBeenCalled());
  expect(vi.mocked(streamAnswer).mock.calls[0].slice(0, 2)).toEqual([
    'conversation',
    '',
  ]);
  expect(vi.mocked(streamAnswer).mock.calls[0][6]).toEqual(['photo-1']);
  expect(screen.queryByLabelText('待发送图片')).not.toBeInTheDocument();
  expect(screen.getByRole('img', { name: '提问图片 1' })).toHaveAttribute(
    'src',
    '/private-photo',
  );
});

test('附件失败重试沿用原run和图片，不重新上传或重复用户消息', async () => {
  vi.mocked(upload).mockResolvedValue(photo);
  vi.mocked(post).mockResolvedValue({ id: 'conversation' });
  vi.mocked(streamAnswer)
    .mockImplementationOnce(async (_c, _q, callbacks) => {
      callbacks.status('failed-run', 'running');
      throw new Error('连接中断');
    })
    .mockResolvedValueOnce({ id: 'retry-run', status: 'completed' });
  const { container } = render(<ChatPanel product={product} />);
  await userEvent.upload(
    container.querySelector('input[type="file"]')!,
    new File(['photo'], 'device.jpg', { type: 'image/jpeg' }),
  );
  await screen.findByLabelText('待发送图片');
  await userEvent.click(screen.getByRole('button', { name: '发送问题' }));
  await userEvent.click(
    await screen.findByRole('button', { name: '重试回答' }),
  );
  await waitFor(() => expect(streamAnswer).toHaveBeenCalledTimes(2));
  expect(vi.mocked(streamAnswer).mock.calls[1][5]).toBe('failed-run');
  expect(vi.mocked(streamAnswer).mock.calls[1][6]).toEqual(['photo-1']);
  expect(upload).toHaveBeenCalledTimes(1);
  expect(container.querySelectorAll('.bubble.user')).toHaveLength(1);
});

test('移除待发送图片和新建会话不会把旧附件带入新问题', async () => {
  vi.mocked(upload).mockResolvedValue(photo);
  vi.mocked(post).mockResolvedValue({ id: 'conversation' });
  vi.mocked(streamAnswer).mockResolvedValue({ id: 'run', status: 'completed' });
  const { container } = render(<ChatPanel product={product} />);
  const input =
    container.querySelector<HTMLInputElement>('input[type="file"]')!;
  await userEvent.upload(
    input,
    new File(['photo'], 'device.jpg', { type: 'image/jpeg' }),
  );
  await userEvent.click(
    await screen.findByRole('button', { name: '移除提问图片 1' }),
  );
  expect(screen.getByRole('button', { name: '发送问题' })).toBeDisabled();
  await userEvent.upload(
    container.querySelector('input[type="file"]')!,
    new File(['photo'], 'device2.jpg', { type: 'image/jpeg' }),
  );
  await screen.findByLabelText('待发送图片');
  await userEvent.click(screen.getByRole('button', { name: '新建会话' }));
  expect(screen.queryByLabelText('待发送图片')).not.toBeInTheDocument();
  await userEvent.type(screen.getByLabelText('提问内容'), '新的问题');
  await userEvent.click(screen.getByRole('button', { name: '发送问题' }));
  await waitFor(() => expect(streamAnswer).toHaveBeenCalled());
  expect(vi.mocked(streamAnswer).mock.calls[0][6]).toEqual([]);
});

test('上传失败保持文字草稿，上传期间阻止发送与会话切换', async () => {
  let rejectUpload!: (error: Error) => void;
  vi.mocked(upload).mockImplementation(
    () =>
      new Promise((_resolve, reject) => {
        rejectUpload = reject;
      }),
  );
  const busy = vi.fn();
  const { container } = render(
    <ChatPanel product={product} onBusyChange={busy} />,
  );
  await userEvent.type(screen.getByLabelText('提问内容'), '现有问题');
  await userEvent.upload(
    container.querySelector('input[type="file"]')!,
    new File(['photo'], 'device.jpg', { type: 'image/jpeg' }),
  );
  expect(screen.getByRole('button', { name: '发送问题' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '新建会话' })).toBeDisabled();
  expect(busy).toHaveBeenLastCalledWith(true);
  rejectUpload(new Error('图片上传失败'));
  expect(await screen.findByText('图片上传失败')).toBeVisible();
  expect(screen.getByLabelText('提问内容')).toHaveValue('现有问题');
  expect(streamAnswer).not.toHaveBeenCalled();
});

test('恢复历史消息时展示服务端返回的图片', async () => {
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === '/capabilities') return { speechToText: false } as never;
    if (path.startsWith('/conversations?'))
      return { items: [{ id: 'saved', title: '图片会话' }] } as never;
    return {
      items: [
        {
          id: 'old-image',
          role: 'user',
          content: null,
          citations: [],
          assets: [photo],
        },
      ],
    } as never;
  });
  render(<ChatPanel product={product} />);
  await userEvent.click(screen.getByRole('button', { name: '历史会话' }));
  await userEvent.click(
    await screen.findByRole('button', { name: '图片会话' }),
  );
  expect(await screen.findByRole('img', { name: '提问图片 1' })).toBeVisible();
  expect(screen.queryByLabelText('待发送图片')).not.toBeInTheDocument();
});
