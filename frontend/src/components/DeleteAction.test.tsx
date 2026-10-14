import { beforeEach, expect, test, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { DeleteAction } from './DeleteAction';
import { api } from '../services/api';

vi.mock('../services/api', () => ({ api: vi.fn() }));
beforeEach(() => vi.clearAllMocks());

test('明确输入删除前不调用接口，取消保持内容', async () => {
  const user = userEvent.setup();
  const deleted = vi.fn();
  render(
    <DeleteAction
      path="/me"
      label="删除账号"
      explanation="清理个人资料"
      onDeleted={deleted}
    />,
  );
  await user.click(screen.getByRole('button', { name: '删除账号' }));
  expect(screen.getByRole('button', { name: '确认删除' })).toBeDisabled();
  await user.type(screen.getByLabelText('输入删除以确认'), '删');
  expect(screen.getByRole('button', { name: '确认删除' })).toBeDisabled();
  await user.click(screen.getByRole('button', { name: '取消' }));
  expect(api).not.toHaveBeenCalled();
  expect(deleted).not.toHaveBeenCalled();
});

test('服务冲突时保留确认框与原内容', async () => {
  vi.mocked(api).mockRejectedValue(new Error('文件仍被其他内容引用'));
  const user = userEvent.setup();
  const deleted = vi.fn();
  render(
    <DeleteAction
      path="/assets/a"
      label="删除照片"
      explanation="删除照片"
      onDeleted={deleted}
    />,
  );
  await user.click(screen.getByRole('button', { name: '删除照片' }));
  await user.type(screen.getByLabelText('输入删除以确认'), '删除');
  await user.click(screen.getByRole('button', { name: '确认删除' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(
    '文件仍被其他内容引用',
  );
  expect(deleted).not.toHaveBeenCalled();
});

test('成功接受删除后才更新页面，阻止重复提交', async () => {
  let resolveRequest: (value: unknown) => void;
  vi.mocked(api).mockReturnValue(
    new Promise((resolve) => {
      resolveRequest = resolve;
    }),
  );
  const user = userEvent.setup();
  const deleted = vi.fn();
  const busy = vi.fn();
  render(
    <DeleteAction
      path="/me/products/p"
      label="删除产品"
      explanation="删除私有内容"
      onDeleted={deleted}
      onBusyChange={busy}
    />,
  );
  await user.click(screen.getByRole('button', { name: '删除产品' }));
  await user.type(screen.getByLabelText('输入删除以确认'), '删除');
  await user.click(screen.getByRole('button', { name: '确认删除' }));
  expect(screen.getByRole('button', { name: '确认删除' })).toBeDisabled();
  expect(deleted).not.toHaveBeenCalled();
  expect(api).toHaveBeenCalledExactlyOnceWith('/me/products/p', {
    method: 'DELETE',
  });
  resolveRequest!(undefined);
  await waitFor(() => expect(deleted).toHaveBeenCalledOnce());
  expect(busy.mock.calls).toEqual([[true], [false]]);
});
