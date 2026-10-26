import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, test, vi } from 'vitest';
import { api, newKey, post, streamAnswer, upload } from '../services/api';
import type { ProductInstance } from '../types/api';
import { ChatPanel } from './ChatPanel';

vi.mock('../services/api', () => ({
  api: vi.fn(async () => ({ speechToText: false })),
  post: vi.fn(),
  streamAnswer: vi.fn(),
  upload: vi.fn(),
  newKey: vi.fn(() => '00000000-0000-4000-8000-000000000001'),
}));

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(newKey).mockReturnValue('00000000-0000-4000-8000-000000000001');
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

test('无官方资料的产品仍可提问，历史中的通用建议保持明确标识', () => {
  render(
    <ChatPanel
      product={{ ...product, knowledgeStatus: 'partial' }}
      initialConversation={{
        id: 'general-history',
        nextCursor: null,
        items: [
          {
            id: 'generic-answer',
            role: 'assistant',
            content: '先检查电脑的蓝牙设置。',
            citations: [],
            answerBasis: 'general',
          },
        ],
      }}
    />,
  );
  expect(screen.getByText('通用建议 · 未经该型号官方资料核验')).toBeVisible();
  expect(screen.queryByText('已核验来源')).not.toBeInTheDocument();
  expect(screen.getByLabelText('提问内容')).toBeEnabled();
});

test('恢复联网来源并强制重搜原问题，不重复追加用户输入', async () => {
  vi.mocked(streamAnswer).mockResolvedValue({
    id: 'new-run',
    status: 'completed',
    message: {
      id: 'new-answer',
      role: 'assistant',
      content: '重新搜索回答',
      answerBasis: 'web',
      citations: [],
    },
  } as never);
  render(
    <ChatPanel
      product={product}
      initialConversation={{
        id: 'web-chat',
        nextCursor: null,
        items: [
          {
            id: 'original-question',
            role: 'user',
            content: '蓝牙怎么连接？',
            citations: [],
          },
          {
            id: 'answer',
            role: 'assistant',
            content: '选择设备。[1]',
            answerBasis: 'web',
            citations: [],
            webSources: [
              {
                id: 'web',
                rank: 1,
                title: '蓝牙教程',
                url: 'https://guide.example/help',
                sourceKind: 'third_party',
                fetchedAt: '2026-10-25T10:00:00Z',
                pageStart: null,
                pageEnd: null,
              },
            ],
          },
        ],
      }}
    />,
  );
  expect(screen.getByText('基于联网资料')).toBeVisible();
  expect(screen.getByText('第三方资料 · 非官方核验')).toBeVisible();
  await userEvent.click(screen.getByRole('button', { name: '重新联网搜索' }));
  await waitFor(() => expect(streamAnswer).toHaveBeenCalled());
  expect(vi.mocked(streamAnswer).mock.calls[0].slice(7)).toEqual([
    true,
    'original-question',
  ]);
  expect(screen.getAllByText('蓝牙怎么连接？', { exact: true })).toHaveLength(
    1,
  );
});

test('建议问题只填入草稿，用户发送前不创建会话或调用模型', async () => {
  render(<ChatPanel product={product} />);
  await userEvent.click(screen.getByRole('button', { name: '如何开始使用？' }));
  expect(screen.getByLabelText('提问内容')).toHaveValue('如何开始使用？');
  expect(post).not.toHaveBeenCalled();
  expect(streamAnswer).not.toHaveBeenCalled();
});

test('旧问题重搜回答之后仍关联旧问题，而非最近一次新问题', async () => {
  vi.mocked(streamAnswer).mockResolvedValue({
    id: 'run',
    status: 'completed',
  } as never);
  render(
    <ChatPanel
      product={product}
      initialConversation={{
        id: 'chat',
        nextCursor: null,
        items: [
          { id: 'q1', role: 'user', content: '旧问题', citations: [] },
          { id: 'a1', role: 'assistant', content: '旧回答', citations: [] },
          { id: 'q2', role: 'user', content: '新问题', citations: [] },
          { id: 'a2', role: 'assistant', content: '新回答', citations: [] },
          {
            id: 'research-a1',
            role: 'assistant',
            content: '旧问题重搜回答',
            citations: [],
            inputMessageId: 'q1',
          },
        ],
      }}
    />,
  );
  await userEvent.click(
    screen.getAllByRole('button', { name: '重新联网搜索' }).at(-1)!,
  );
  await waitFor(() => expect(streamAnswer).toHaveBeenCalled());
  expect(vi.mocked(streamAnswer).mock.calls[0][1]).toBe('旧问题');
});

test.each(['重试回答', '查询结果'])(
  '%s成功后原问题ID可用于重新联网',
  async (action) => {
    const recovered = {
      id: 'saved-run',
      inputMessageId: 'server-question',
      status: 'completed',
      message: {
        id: 'saved-answer',
        inputMessageId: 'server-question',
        role: 'assistant',
        content: '已经恢复',
        citations: [],
        answerBasis: 'web',
      },
    };
    vi.mocked(api).mockImplementation(async (path) =>
      path.includes('/runs/') ? recovered : ({ speechToText: false } as never),
    );
    vi.mocked(streamAnswer)
      .mockImplementationOnce(async (_id, _question, callbacks) => {
        callbacks.status('failed-run', 'running');
        throw new Error('断网');
      })
      .mockImplementationOnce(async (_id, _question, callbacks) => {
        callbacks.status('retry-run', 'running');
        throw new Error('再次断网');
      })
      .mockResolvedValue(recovered as never);
    render(
      <ChatPanel
        product={product}
        initialConversation={{ id: 'chat', nextCursor: null, items: [] }}
      />,
    );
    await userEvent.type(screen.getByLabelText('提问内容'), '原问题');
    await userEvent.click(screen.getByRole('button', { name: '发送问题' }));
    await userEvent.click(
      await screen.findByRole('button', { name: '重试回答' }),
    );
    await screen.findByText('再次断网');
    await userEvent.click(await screen.findByRole('button', { name: action }));
    await screen.findByText('已经恢复');
    await userEvent.click(screen.getByRole('button', { name: '重新联网搜索' }));
    await waitFor(() =>
      expect(vi.mocked(streamAnswer).mock.calls.at(-1)?.[8]).toBe(
        'server-question',
      ),
    );
  },
);

test('查询旧回答期间新建会话，迟到结果不能混入新会话', async () => {
  let resolve!: (value: unknown) => void;
  vi.mocked(api).mockImplementation(async (path: string) => {
    if (path === '/capabilities') return { speechToText: false };
    if (path === '/conversations/saved/runs/old-run')
      return new Promise((yes) => {
        resolve = yes;
      });
    throw new Error(`Unexpected request: ${path}`);
  });
  vi.mocked(streamAnswer).mockImplementationOnce(
    async (_id, _question, callbacks) => {
      callbacks.status('old-run', 'running');
      throw new Error('连接中断');
    },
  );
  render(
    <ChatPanel
      product={product}
      initialConversation={{ id: 'saved', items: [], nextCursor: null }}
    />,
  );
  await userEvent.type(screen.getByLabelText('提问内容'), '原来的问题');
  await userEvent.click(screen.getByRole('button', { name: '发送问题' }));
  await userEvent.click(
    await screen.findByRole('button', { name: '查询结果' }),
  );
  await userEvent.click(screen.getByRole('button', { name: '新建会话' }));
  await act(async () =>
    resolve({
      id: 'old-run',
      status: 'completed',
      message: {
        id: 'old-answer',
        role: 'assistant',
        content: '旧会话的迟到回答',
        citations: [],
      },
    }),
  );
  expect(screen.queryByText('旧会话的迟到回答')).toBeNull();
});

test('历史会话加载完成前离开页面，迟到结果不能再次导航', async () => {
  let resolve!: (value: unknown) => void;
  vi.mocked(api).mockImplementation(async (path: string) => {
    if (path === '/capabilities') return { speechToText: false };
    if (path.startsWith('/conversations?'))
      return {
        items: [
          { id: 'saved', title: '旧会话', userProductInstanceId: product.id },
        ],
        nextCursor: null,
      };
    if (path === '/conversations/saved/messages')
      return new Promise((yes) => {
        resolve = yes;
      });
    throw new Error(`Unexpected request: ${path}`);
  });
  const onConversationChange = vi.fn();
  const view = render(
    <ChatPanel product={product} onConversationChange={onConversationChange} />,
  );
  await userEvent.click(screen.getByRole('button', { name: '历史会话' }));
  await userEvent.click(await screen.findByRole('button', { name: '旧会话' }));
  view.unmount();
  await act(async () => resolve({ items: [], nextCursor: null }));
  expect(onConversationChange).not.toHaveBeenCalled();
});

test('删除会话完成前离开页面，删除成功不能触发旧页面导航', async () => {
  let resolve!: (value: unknown) => void;
  vi.mocked(api).mockImplementation(
    async (path: string, options?: RequestInit) => {
      if (path === '/capabilities') return { speechToText: false };
      if (path === '/conversations/saved' && options?.method === 'DELETE')
        return new Promise((yes) => {
          resolve = yes;
        });
      throw new Error(`Unexpected request: ${path}`);
    },
  );
  const onConversationChange = vi.fn();
  const view = render(
    <ChatPanel
      product={product}
      initialConversation={{ id: 'saved', items: [], nextCursor: null }}
      onConversationChange={onConversationChange}
    />,
  );
  await userEvent.click(screen.getByRole('button', { name: '删除会话' }));
  await userEvent.type(screen.getByLabelText('输入删除以确认'), '删除');
  await userEvent.click(screen.getByRole('button', { name: '确认删除' }));
  view.unmount();
  await act(async () => resolve(undefined));
  expect(onConversationChange).not.toHaveBeenCalled();
});

test('取消接口迟迟未返回时也立即中止本地回答连接', async () => {
  let signal!: AbortSignal;
  vi.mocked(post).mockImplementation(async () => new Promise(() => {}));
  vi.mocked(streamAnswer).mockImplementationOnce(
    async (_id, _question, callbacks, requestSignal) => {
      signal = requestSignal;
      callbacks.status('old-run', 'running');
      return new Promise((_resolve, reject) => {
        requestSignal.addEventListener(
          'abort',
          () => reject(new DOMException('已取消', 'AbortError')),
          { once: true },
        );
      });
    },
  );
  render(
    <ChatPanel
      product={product}
      initialConversation={{ id: 'saved', items: [], nextCursor: null }}
    />,
  );
  await userEvent.type(screen.getByLabelText('提问内容'), '原来的问题');
  await userEvent.click(screen.getByRole('button', { name: '发送问题' }));
  await userEvent.click(screen.getByRole('button', { name: '停止回答' }));
  expect(signal.aborted).toBe(true);
  expect(await screen.findByRole('button', { name: '发送问题' })).toBeVisible();
});

test('收到运行编号前断网，重试沿用原幂等键且不重复提问', async () => {
  vi.mocked(newKey).mockImplementation(() => crypto.randomUUID());
  vi.mocked(streamAnswer)
    .mockRejectedValueOnce(new Error('连接中断'))
    .mockResolvedValueOnce({
      id: 'replayed',
      status: 'completed',
      message: {
        id: 'answer',
        role: 'assistant',
        content: '恢复后的回答',
        citations: [],
      },
    });
  const { container } = render(
    <ChatPanel
      product={product}
      initialConversation={{ id: 'saved', items: [], nextCursor: null }}
    />,
  );
  await userEvent.type(screen.getByLabelText('提问内容'), '同一个问题');
  await userEvent.click(screen.getByRole('button', { name: '发送问题' }));
  await userEvent.click(
    await screen.findByRole('button', { name: '重试回答' }),
  );
  await screen.findByText('恢复后的回答');
  expect(container.querySelectorAll('.bubble.user')).toHaveLength(1);
  expect(vi.mocked(streamAnswer).mock.calls[1][4]).toBe(
    vi.mocked(streamAnswer).mock.calls[0][4],
  );
  expect(vi.mocked(streamAnswer).mock.calls[1][5]).toBeUndefined();
});

test('重试失败运行时再次断网，下一次仍重放同一重试请求', async () => {
  vi.mocked(newKey).mockImplementation(() => crypto.randomUUID());
  vi.mocked(streamAnswer)
    .mockImplementationOnce(async (_id, _question, callbacks) => {
      callbacks.status('first-failed', 'running');
      throw new Error('首次中断');
    })
    .mockRejectedValueOnce(new Error('重试连接中断'))
    .mockResolvedValueOnce({
      id: 'replayed',
      status: 'completed',
      message: {
        id: 'answer',
        role: 'assistant',
        content: '重放成功',
        citations: [],
      },
    });
  const { container } = render(
    <ChatPanel
      product={product}
      initialConversation={{ id: 'saved', items: [], nextCursor: null }}
    />,
  );
  await userEvent.type(screen.getByLabelText('提问内容'), '同一个问题');
  await userEvent.click(screen.getByRole('button', { name: '发送问题' }));
  await userEvent.click(
    await screen.findByRole('button', { name: '重试回答' }),
  );
  await screen.findByText('重试连接中断');
  await userEvent.click(screen.getByRole('button', { name: '重试回答' }));
  await screen.findByText('重放成功');
  const attempts = vi.mocked(streamAnswer).mock.calls;
  expect(attempts[1][4]).not.toBe(attempts[0][4]);
  expect(attempts[2][4]).toBe(attempts[1][4]);
  expect(attempts[1][5]).toBe('first-failed');
  expect(attempts[2][5]).toBe('first-failed');
  expect(container.querySelectorAll('.bubble.user')).toHaveLength(1);
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
    '00000000-0000-4000-8000-000000000001',
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

test('直接恢复历史会话后，分页按服务端时间顺序追加消息', async () => {
  vi.mocked(api).mockImplementation(async (path: string) => {
    if (path === '/capabilities') return { speechToText: false } as never;
    if (path === '/conversations/saved/messages?cursor=first')
      return {
        items: [
          { id: 'second', role: 'user', content: '第二条问题', citations: [] },
        ],
        nextCursor: null,
      } as never;
    throw new Error(`Unexpected request: ${path}`);
  });
  render(
    <ChatPanel
      product={product}
      initialConversation={{
        id: 'saved',
        items: [
          { id: 'first', role: 'user', content: '第一条问题', citations: [] },
        ],
        nextCursor: 'first',
      }}
    />,
  );
  expect(screen.getByText('第一条问题')).toBeVisible();
  await userEvent.click(screen.getByRole('button', { name: '加载更多消息' }));
  await screen.findByText('第二条问题');
  expect(
    screen.getAllByText(/第[一二]条问题/).map((node) => node.textContent),
  ).toEqual(['第一条问题', '第二条问题']);
});

test('未读完历史分页时先补齐旧消息，再显示新问题和回答', async () => {
  vi.mocked(api).mockImplementation(async (path: string) => {
    if (path === '/capabilities') return { speechToText: false } as never;
    if (path === '/conversations/saved/messages?cursor=first')
      return {
        items: [
          {
            id: 'second',
            role: 'user',
            content: '第二条旧问题',
            citations: [],
          },
        ],
        nextCursor: null,
      } as never;
    throw new Error(`Unexpected request: ${path}`);
  });
  vi.mocked(streamAnswer).mockResolvedValueOnce({
    id: 'run',
    status: 'completed',
    message: {
      id: 'answer',
      role: 'assistant',
      content: '新的回答',
      citations: [],
    },
  });
  render(
    <ChatPanel
      product={product}
      initialConversation={{
        id: 'saved',
        items: [
          { id: 'first', role: 'user', content: '第一条旧问题', citations: [] },
        ],
        nextCursor: 'first',
      }}
    />,
  );
  await userEvent.type(screen.getByLabelText('提问内容'), '新问题');
  await userEvent.click(screen.getByRole('button', { name: '发送问题' }));
  await screen.findByText('新的回答');
  expect(
    screen
      .getAllByText(/第[一二]条旧问题|^新问题$|^新的回答$/)
      .map((node) => node.textContent),
  ).toEqual(['第一条旧问题', '第二条旧问题', '新问题', '新的回答']);
  expect(screen.queryByRole('button', { name: '加载更多消息' })).toBeNull();
});

test('加载最后一页历史消息时保留用户新上传的图片草稿', async () => {
  vi.mocked(upload).mockResolvedValue(photo);
  vi.mocked(api).mockImplementation(async (path: string) => {
    if (path === '/capabilities') return { speechToText: false } as never;
    if (path === '/conversations/saved/messages?cursor=first')
      return {
        items: [
          {
            id: 'last',
            role: 'assistant',
            content: '最后一条旧回答',
            citations: [],
          },
        ],
        nextCursor: null,
      } as never;
    throw new Error(`Unexpected request: ${path}`);
  });
  const { container } = render(
    <ChatPanel
      product={product}
      initialConversation={{
        id: 'saved',
        items: [
          { id: 'first', role: 'user', content: '旧问题', citations: [] },
        ],
        nextCursor: 'first',
      }}
    />,
  );
  await userEvent.upload(
    container.querySelector('input[type="file"]')!,
    new File(['photo'], 'new.jpg', { type: 'image/jpeg' }),
  );
  await screen.findByLabelText('待发送图片');
  await userEvent.click(screen.getByRole('button', { name: '加载更多消息' }));
  await screen.findByText('最后一条旧回答');
  expect(screen.getByLabelText('待发送图片')).toBeVisible();
});

test.each(['stop', 'unmount'])(
  '补齐历史期间 %s 后不再发送问题或启动模型',
  async (action) => {
    let resolve!: (value: unknown) => void;
    vi.mocked(api).mockImplementation(async (path: string) => {
      if (path === '/capabilities') return { speechToText: false } as never;
      if (path === '/conversations/saved/messages?cursor=first')
        return new Promise((yes) => {
          resolve = yes;
        });
      throw new Error(`Unexpected request: ${path}`);
    });
    const view = render(
      <ChatPanel
        product={product}
        initialConversation={{
          id: 'saved',
          items: [
            { id: 'first', role: 'user', content: '旧问题', citations: [] },
          ],
          nextCursor: 'first',
        }}
      />,
    );
    await userEvent.type(screen.getByLabelText('提问内容'), '不应发送的新问题');
    await userEvent.click(screen.getByRole('button', { name: '发送问题' }));
    if (action === 'stop')
      await userEvent.click(screen.getByRole('button', { name: '停止回答' }));
    else view.unmount();
    await act(async () => resolve({ items: [], nextCursor: null }));
    expect(streamAnswer).not.toHaveBeenCalled();
    if (action === 'stop')
      expect(screen.getByLabelText('提问内容')).toHaveValue('不应发送的新问题');
  },
);

test('纯图片不自动提问，澄清后保留图片供下一句补充问题使用', async () => {
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
  expect(screen.getByLabelText('待发送图片')).toBeVisible();
  expect(container.querySelector('.bubble.user img')).toHaveAttribute(
    'src',
    '/private-photo',
  );
  await userEvent.type(screen.getByLabelText('提问内容'), '充满需要多久？');
  await userEvent.click(screen.getByRole('button', { name: '发送问题' }));
  await waitFor(() => expect(streamAnswer).toHaveBeenCalledTimes(2));
  expect(vi.mocked(streamAnswer).mock.calls[1][1]).toBe('充满需要多久？');
  expect(vi.mocked(streamAnswer).mock.calls[1][6]).toEqual(['photo-1']);
  expect(upload).toHaveBeenCalledTimes(1);
  expect(post).toHaveBeenCalledTimes(1);
  expect(screen.queryByLabelText('待发送图片')).not.toBeInTheDocument();
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

test('恢复历史纯图片澄清时展示并保留图片，继续问题沿用同一会话', async () => {
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
        {
          id: 'clarification',
          role: 'assistant',
          content: '请补充具体问题',
          citations: [],
        },
      ],
    } as never;
  });
  render(<ChatPanel product={product} />);
  await userEvent.click(screen.getByRole('button', { name: '历史会话' }));
  await userEvent.click(
    await screen.findByRole('button', { name: '图片会话' }),
  );
  expect(await screen.findByLabelText('待发送图片')).toBeVisible();
  vi.mocked(streamAnswer).mockResolvedValue({
    id: 'followup',
    status: 'completed',
  });
  await userEvent.type(screen.getByLabelText('提问内容'), '怎样充电？');
  await userEvent.click(screen.getByRole('button', { name: '发送问题' }));
  await waitFor(() => expect(streamAnswer).toHaveBeenCalled());
  expect(vi.mocked(streamAnswer).mock.calls[0][0]).toBe('saved');
  expect(vi.mocked(streamAnswer).mock.calls[0][6]).toEqual(['photo-1']);
  expect(upload).not.toHaveBeenCalled();
  expect(post).not.toHaveBeenCalled();
});
