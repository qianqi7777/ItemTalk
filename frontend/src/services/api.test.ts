import { afterEach, expect, test, vi } from 'vitest';
import { streamAnswer } from './api';

afterEach(() => vi.unstubAllGlobals());
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
