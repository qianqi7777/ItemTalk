import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';
import { api, post } from '../../services/api';
import type { Memory } from '../../types/api';
import { MemoryPanel } from './MemoryPanel';

vi.mock('../../services/api', () => ({ api: vi.fn(), post: vi.fn() }));

const candidate: Memory = {
  id: 'memory',
  memoryType: 'preference',
  scope: 'global',
  userProductInstanceId: null,
  content: '偏好简短中文',
  consentStatus: 'pending',
  confirmedAt: null,
  expiresAt: null,
};

test('待确认记忆不自动保存，确认后刷新显示已确认', async () => {
  vi.mocked(api).mockResolvedValueOnce({ items: [candidate] });
  vi.mocked(post).mockResolvedValueOnce({
    ...candidate,
    consentStatus: 'confirmed',
  });
  vi.mocked(api).mockResolvedValueOnce({
    items: [{ ...candidate, consentStatus: 'confirmed' }],
  });
  render(<MemoryPanel instanceId="device" />);
  expect(api).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole('button', { name: '我的记忆' }));
  expect(await screen.findByText('偏好简短中文')).toBeVisible();
  expect(post).not.toHaveBeenCalled();
  expect(api).toHaveBeenCalledWith('/me/memories?instanceId=device');
  await userEvent.click(screen.getByRole('button', { name: '确认记住' }));
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith('/me/memories/memory/confirm', {}),
  );
  expect(await screen.findByText('全局偏好 · 已确认')).toBeVisible();
});

test('拒绝记忆发送删除请求，不发送确认请求', async () => {
  vi.mocked(api)
    .mockResolvedValueOnce({ items: [candidate] })
    .mockResolvedValueOnce(undefined)
    .mockResolvedValueOnce({ items: [] });
  render(<MemoryPanel instanceId="device" />);
  await userEvent.click(screen.getByRole('button', { name: '我的记忆' }));
  await userEvent.click(await screen.findByRole('button', { name: '不记住' }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith('/me/memories/memory', {
      method: 'DELETE',
    }),
  );
  expect(post).not.toHaveBeenCalled();
  expect(await screen.findByText(/暂无记忆/)).toBeVisible();
});
