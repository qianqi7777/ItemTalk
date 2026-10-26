import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Modal } from 'antd';
import { ModelConfigs } from './ModelConfigs';
import { api, ApiError } from '../../services/api';

vi.mock('../../services/api', () => ({
  api: vi.fn(),
  newKey: () => 'model-test-key',
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

const runtime = {
  version: 7,
  globalDailyLimit: 80,
  subjectDailyLimit: 40,
  backgroundPaused: false,
  chatProfile: 'current',
};
const listing = { encryptionReady: true, activeProfile: 'current', items: [] };
const saved = {
  id: 'managed_01234567890123456789012345678901',
  name: '备用模型',
  base_url: 'https://models.example.com/v1',
  protocol: 'openai_chat',
  model: 'model-v2',
  created_at: '2026-10-26T00:00:00Z',
};
const refreshed = vi.fn(async () => undefined);

function fill() {
  fireEvent.change(screen.getByLabelText('配置名称'), {
    target: { value: '备用模型' },
  });
  fireEvent.change(screen.getByLabelText('API 基础地址'), {
    target: { value: 'https://models.example.com/v1' },
  });
  fireEvent.change(screen.getByLabelText('模型名称'), {
    target: { value: 'model-v2' },
  });
  fireEvent.change(screen.getByLabelText('API Key'), {
    target: { value: 'test-only-secret' },
  });
}

beforeEach(() => {
  vi.mocked(api).mockReset();
  refreshed.mockClear();
  vi.mocked(api).mockImplementation(async (path) =>
    path.endsWith('/test')
      ? {
          testToken: 'proof',
          checks: ['text', 'json', 'stream'],
          validForSeconds: 900,
        }
      : path === '/admin/model-configs'
        ? saved
        : { ...runtime, version: 8, chatProfile: saved.id },
  );
});

afterEach(() => Modal.destroyAll());

describe('manual text-model configuration', () => {
  it('keeps the test button accessible by its name while loading', () => {
    vi.mocked(api).mockImplementation(() => new Promise(() => {}));
    render(
      <ModelConfigs listing={listing} runtime={runtime} onSaved={refreshed} />,
    );
    fill();
    fireEvent.click(screen.getByRole('button', { name: '测试配置' }));
    expect(
      screen.getByRole('button', { name: '测试配置' }),
    ).toBeInTheDocument();
  });
  it('requires a successful test and invalidates it when a tested field changes', async () => {
    render(
      <ModelConfigs listing={listing} runtime={runtime} onSaved={refreshed} />,
    );
    fill();
    expect(screen.getByRole('button', { name: '保存并启用' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '测试配置' }));
    await waitFor(() =>
      expect(screen.getByRole('button', { name: '保存并启用' })).toBeEnabled(),
    );
    fireEvent.change(screen.getByLabelText('模型名称'), {
      target: { value: 'untested-model' },
    });
    expect(screen.getByRole('button', { name: '保存并启用' })).toBeDisabled();
  });

  it('saves only after confirmation, clears the key and activates with the current version', async () => {
    render(
      <ModelConfigs listing={listing} runtime={runtime} onSaved={refreshed} />,
    );
    fill();
    fireEvent.click(screen.getByRole('button', { name: '测试配置' }));
    await waitFor(() =>
      expect(screen.getByRole('button', { name: '保存并启用' })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole('button', { name: '保存并启用' }));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(vi.mocked(api).mock.calls).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', { name: '确认启用' }));
    await waitFor(() => expect(refreshed).toHaveBeenCalled());
    const create = vi
      .mocked(api)
      .mock.calls.find(([path]) => path === '/admin/model-configs')!;
    expect(JSON.parse(create[1]!.body as string)).toMatchObject({
      apiKey: 'test-only-secret',
      testToken: 'proof',
    });
    expect(create[1]!.headers).toEqual({ 'Idempotency-Key': 'model-test-key' });
    const activate = vi
      .mocked(api)
      .mock.calls.find(([path]) => path === '/admin/runtime-config')!;
    expect(JSON.parse(activate[1]!.body as string)).toEqual({
      expectedVersion: 7,
      globalDailyLimit: 80,
      subjectDailyLimit: 40,
      backgroundPaused: false,
      chatProfile: saved.id,
    });
    expect(screen.getByLabelText('API Key')).toHaveValue('');
  });

  it('keeps the current selection after a failed test', async () => {
    vi.mocked(api).mockRejectedValue(
      new ApiError(502, 'UPSTREAM_MODEL_ERROR', '上游密钥认证失败'),
    );
    render(
      <ModelConfigs listing={listing} runtime={runtime} onSaved={refreshed} />,
    );
    fill();
    fireEvent.click(screen.getByRole('button', { name: '测试配置' }));
    expect(await screen.findByText('上游密钥认证失败')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '保存并启用' })).toBeDisabled();
    expect(vi.mocked(api).mock.calls).toHaveLength(1);
  });

  it('allows recovery by selecting an existing immutable configuration', async () => {
    render(
      <ModelConfigs
        listing={{ ...listing, items: [saved] }}
        runtime={runtime}
        onSaved={refreshed}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: '启用 备用模型' }));
    fireEvent.click(await screen.findByRole('button', { name: '确认启用' }));
    await waitFor(() => expect(refreshed).toHaveBeenCalled());
    expect(vi.mocked(api).mock.calls[0][0]).toBe('/admin/runtime-config');
  });

  it('explains that a saved configuration remains available when activation conflicts', async () => {
    vi.mocked(api).mockImplementation(async (path) => {
      if (path.endsWith('/test'))
        return { testToken: 'proof', validForSeconds: 900 };
      if (path === '/admin/model-configs') return saved;
      throw new ApiError(409, 'RESOURCE_CONFLICT', '版本冲突');
    });
    render(
      <ModelConfigs listing={listing} runtime={runtime} onSaved={refreshed} />,
    );
    fill();
    fireEvent.click(screen.getByRole('button', { name: '测试配置' }));
    await waitFor(() =>
      expect(screen.getByRole('button', { name: '保存并启用' })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole('button', { name: '保存并启用' }));
    fireEvent.click(await screen.findByRole('button', { name: '确认启用' }));
    expect(await screen.findByText(/配置已保存，但未启用/)).toBeInTheDocument();
    expect(screen.getByLabelText('API Key')).toHaveValue('');
  });

  it('disables credential submission until server encryption is configured', () => {
    render(
      <ModelConfigs
        listing={{ ...listing, encryptionReady: false }}
        runtime={runtime}
        onSaved={refreshed}
      />,
    );
    expect(screen.getByRole('button', { name: '测试配置' })).toBeDisabled();
    expect(screen.getByText(/服务器尚未配置模型密钥加密/)).toBeInTheDocument();
  });
});
