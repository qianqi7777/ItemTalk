/** API 传输适配；Cookie 认证、写请求校验和幂等键在这里集中维护。 */
import type { Asset, Citation, Run } from '../types/api';

export const apiBase = import.meta.env.VITE_API_BASE_URL || '/api/v1';
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}
export const newKey = () => crypto.randomUUID();

export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch(`${apiBase}${path}`, {
    credentials: 'include',
    ...options,
    headers: {
      'Content-Type': 'application/json',
      'X-Requested-With': 'XMLHttpRequest',
      ...options.headers,
    },
  });
  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ code: 'INTERNAL_ERROR', message: '请求失败' }));
    throw new ApiError(response.status, error.code, error.message);
  }
  return response.status === 204 ? (undefined as T) : response.json();
}

export const post = <T>(
  path: string,
  body: unknown,
  key?: string,
  retry?: string,
) =>
  api<T>(path, {
    method: 'POST',
    body: JSON.stringify(body),
    headers: {
      ...(key ? { 'Idempotency-Key': key } : {}),
      ...(retry ? { 'Retry-Of': retry } : {}),
    },
  });

export async function upload(
  file: File,
  kind?: 'product_photo' | 'nameplate' | 'voice' | 'feedback',
): Promise<Asset> {
  const signed = await post<{ assetId: string; uploadUrl: string }>(
    '/assets/presign',
    {
      filename: file.name,
      mimeType: file.type,
      sizeBytes: file.size,
      kind:
        kind || (file.type.startsWith('audio/') ? 'voice' : 'product_photo'),
    },
    newKey(),
  );
  const response = await fetch(signed.uploadUrl, {
    method: 'PUT',
    body: file,
    headers: { 'Content-Type': file.type },
  });
  if (!response.ok) throw new Error('图片上传失败，请重试');
  return post<Asset>(`/assets/${signed.assetId}/confirm`, {});
}

export interface StreamCallbacks {
  status: (runId: string, status: string) => void;
  token: (text: string) => void;
  citation: (citation: Citation) => void;
}

/** POST SSE 按事件空行分帧，处理任意网络切片和 UTF-8 多字节边界。 */
export async function streamAnswer(
  conversationId: string,
  content: string,
  callbacks: StreamCallbacks,
  signal: AbortSignal,
  key: string,
  retry?: string,
  assetIds: string[] = [],
): Promise<Run> {
  const response = await fetch(
    `${apiBase}/conversations/${conversationId}/stream`,
    {
      method: 'POST',
      credentials: 'include',
      signal,
      headers: {
        'Content-Type': 'application/json',
        'X-Requested-With': 'XMLHttpRequest',
        'Idempotency-Key': key,
        ...(retry ? { 'Retry-Of': retry } : {}),
      },
      // 纯附件请求必须省略空文字，不能触发服务端的空白输入校验。
      body: JSON.stringify({
        ...(content.trim() ? { content } : {}),
        ...(assetIds.length ? { assetIds } : {}),
      }),
    },
  );
  if (!response.ok) {
    const error = await response.json();
    throw new ApiError(response.status, error.code, error.message);
  }
  if (!response.body) throw new Error('浏览器不支持流式响应');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '',
    runId = '',
    ended = false;
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      // 先拼接网络切片，再规范换行，避免 CR/LF 恰好分处两个切片。
      buffer = buffer.replace(/\r\n/g, '\n');
      let boundary: number;
      while ((boundary = buffer.indexOf('\n\n')) >= 0) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const kind = frame
          .split('\n')
          .find((line) => line.startsWith('event:'))
          ?.slice(6)
          .trim();
        const data = frame
          .split('\n')
          .filter((line) => line.startsWith('data:'))
          .map((line) => line.slice(5).trim())
          .join('\n');
        if (!kind || !data) continue;
        const payload = JSON.parse(data);
        if (kind === 'status') {
          runId = payload.runId;
          callbacks.status(runId, payload.status);
        }
        if (kind === 'token') callbacks.token(payload.text);
        if (kind === 'citation') callbacks.citation(payload);
        if (kind === 'error')
          throw new ApiError(502, payload.code, payload.message);
        if (kind === 'done') {
          runId = payload.runId;
          ended = true;
        }
      }
      if (done) break;
    }
  } finally {
    reader.releaseLock();
  }
  if (!runId) throw new Error('回答连接中断，请重试');
  const run = await api<Run>(`/conversations/${conversationId}/runs/${runId}`);
  if (!ended && run.status === 'running')
    throw new Error('回答仍在生成，请查询运行状态');
  return run;
}
