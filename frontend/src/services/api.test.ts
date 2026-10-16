import { afterEach, expect, test, vi } from 'vitest';
import { streamAnswer } from './api';

afterEach(() => vi.unstubAllGlobals());

test('纯图片SSE请求省略空文字并携带原附件及重试运行', async () => {
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(
      new Response('event: done\ndata: {"runId":"retried"}\n\n', {
        headers: { 'Content-Type': 'text/event-stream' },
      }),
    )
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ id: 'retried', status: 'completed' })),
    );
  vi.stubGlobal('fetch', fetch);
  await streamAnswer(
    'conversation',
    '',
    { token: vi.fn(), status: vi.fn(), citation: vi.fn() },
    new AbortController().signal,
    'key',
    'old-run',
    ['confirmed-image'],
  );
  const options = fetch.mock.calls[0][1];
  expect(JSON.parse(options.body)).toEqual({ assetIds: ['confirmed-image'] });
  expect(options.headers['Retry-Of']).toBe('old-run');
});
test('SSE 跨 UTF-8 和 CRLF 切片后仍正确解析，并查询持久化运行', async () => {
  const source =
    'event: status\r\ndata: {"runId":"run","status":"running"}\r\n\r\n' +
    'event: token\r\ndata: {"text":"中文回答"}\r\n\r\n' +
    'event: done\r\ndata: {"runId":"run","messageId":"message"}\r\n\r\n';
  const bytes = new TextEncoder().encode(source);
  const body = new ReadableStream({
    start(controller) {
      for (const byte of bytes) controller.enqueue(new Uint8Array([byte]));
      controller.close();
    },
  });
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(
      new Response(body, { headers: { 'Content-Type': 'text/event-stream' } }),
    )
    .mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          id: 'run',
          status: 'completed',
          message: { id: 'message' },
        }),
      ),
    );
  vi.stubGlobal('fetch', fetch);
  const token = vi.fn(),
    status = vi.fn();
  const run = await streamAnswer(
    'conversation',
    '问题',
    { token, status, citation: vi.fn() },
    new AbortController().signal,
    'key',
  );
  expect(token).toHaveBeenCalledWith('中文回答');
  expect(status).toHaveBeenCalledWith('run', 'running');
  expect(run.status).toBe('completed');
  expect(fetch).toHaveBeenCalledTimes(2);
});

test('流式草稿含多余引用时使用服务端最终核验消息', async () => {
  const message = {
    id: 'message',
    role: 'assistant',
    content: '关闭设备的说明[2]。',
    citations: [{ rank: 2, pageStart: 25 }],
  };
  const source =
    'event: status\ndata: {"runId":"run","status":"running"}\n\n' +
    'event: token\ndata: {"text":"关闭设备的说明[1][2]。"}\n\n' +
    'event: citation\ndata: {"rank":2,"pageStart":25}\n\n' +
    'event: done\ndata: {"runId":"run","messageId":"message"}\n\n';
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(
      new Response(source, {
        headers: { 'Content-Type': 'text/event-stream' },
      }),
    )
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ id: 'run', status: 'completed', message })),
    );
  vi.stubGlobal('fetch', fetch);
  const token = vi.fn();
  const run = await streamAnswer(
    'conversation',
    '如何关闭？',
    { token, status: vi.fn(), citation: vi.fn() },
    new AbortController().signal,
    'key',
  );
  expect(token).toHaveBeenCalledWith('关闭设备的说明[1][2]。');
  expect(run.message).toEqual(message);
  expect(run.message?.content).not.toContain('[1]');
  expect(fetch).toHaveBeenLastCalledWith(
    '/api/v1/conversations/conversation/runs/run',
    expect.objectContaining({ credentials: 'include' }),
  );
});
