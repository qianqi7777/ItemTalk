import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AdminPanel } from './AdminPanel';
import { api, ApiError } from '../../services/api';

vi.mock('../../services/api', () => ({
  api: vi.fn(),
  newKey: () => 'test-idempotency-key',
  ApiError: class ApiError extends Error {
    constructor(
      public status: number,
      public code: string,
      message: string,
    ) {
      super(message);
    }
  },
}));

const overview = {
  runtime: {
    version: 1,
    globalDailyLimit: 80,
    subjectDailyLimit: 40,
    backgroundPaused: false,
    chatProfile: 'current',
  },
  hardLimits: { global: 1000, subject: 100 },
  modelProfiles: [
    { id: 'current', model: 'existing-model', accepted: true },
    { id: 'quality', model: 'candidate-model', accepted: false },
  ],
  queue: [],
  trend: [],
  costNotice: '费用为估算，不等于云账单',
  groups: [
    {
      kind: 'upstream',
      operation: 'chat',
      model: 'existing-model',
      provider: 'dashscope',
      outcome: 'success',
      currency: '',
      count: 2,
      p95_ms: 100,
      input_known: 0,
      input_tokens: null,
      output_known: 0,
      output_tokens: null,
      priced_calls: 0,
      estimated_cost: null,
    },
  ],
};

beforeEach(() => {
  vi.mocked(api).mockReset();
  vi.mocked(api).mockImplementation(async (path) =>
    path.startsWith('/admin/overview')
      ? overview
      : { items: [], nextBefore: null },
  );
});

describe('V2 administrator panel', () => {
  it('shows a permission failure without rendering controls', async () => {
    vi.mocked(api).mockRejectedValue(
      new ApiError(403, 'FORBIDDEN', '禁止访问'),
    );
    render(<AdminPanel />);
    expect(
      await screen.findByText('此页面仅供已授权管理员使用。'),
    ).toBeInTheDocument();
    expect(screen.queryByText('保存运行配置')).not.toBeInTheDocument();
  });

  it('distinguishes unknown usage and unpriced calls from zero', async () => {
    render(<AdminPanel />);
    expect(await screen.findByText('未计价')).toBeInTheDocument();
    expect(screen.getAllByText('未知')).toHaveLength(2);
    expect(screen.getByText('费用为估算，不等于云账单')).toBeInTheDocument();
    expect(screen.getByLabelText('全局日调用额度')).toHaveValue('80');
  });

  it('confirms writes and sends the version and idempotency key', async () => {
    render(<AdminPanel />);
    fireEvent.click(await screen.findByText('保存运行配置'));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(
      vi.mocked(api).mock.calls.filter((call) => call[1]?.method === 'PATCH'),
    ).toHaveLength(0);
    fireEvent.click(screen.getByText('确认保存'));
    await waitFor(() =>
      expect(
        vi.mocked(api).mock.calls.some((call) => call[1]?.method === 'PATCH'),
      ).toBe(true),
    );
    const write = vi
      .mocked(api)
      .mock.calls.find((call) => call[1]?.method === 'PATCH')!;
    expect(write[0]).toBe('/admin/runtime-config');
    expect(write[1]?.headers).toEqual({
      'Idempotency-Key': 'test-idempotency-key',
    });
    expect(JSON.parse(write[1]?.body as string).expectedVersion).toBe(1);
  });
});
