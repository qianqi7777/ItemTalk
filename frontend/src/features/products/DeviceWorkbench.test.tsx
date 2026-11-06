import { useState } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';
import { DeviceWorkbench } from './DeviceWorkbench';
import type { ProductInstance } from '../../types/api';

vi.mock('../../pages/ChatPanel', () => ({
  ChatPanel: function Chat() {
    const [draft, setDraft] = useState('');
    return (
      <input
        aria-label="问题草稿"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
      />
    );
  },
}));
vi.mock('../manuals/ManualPanel', () => ({
  ManualPanel: () => <p>说明书正文</p>,
}));
vi.mock('../memories/MemoryPanel', () => ({
  MemoryPanel: () => <p>个人偏好</p>,
}));
vi.mock('../guidance/GuidancePanel', () => ({
  GuidancePanel: function Guide() {
    const [step, setStep] = useState(1);
    return <button onClick={() => setStep(step + 1)}>当前步骤 {step}</button>;
  },
}));
const product = {
  id: 'device',
  productModelId: 'model',
  modelCode: 'Test',
  brandName: 'Brand',
} as ProductInstance;

test('切换设备模块保留问题草稿与已开始的引导步骤，工具首次选择才加载', async () => {
  render(
    <DeviceWorkbench
      product={product}
      compact={false}
      onChatBusyChange={() => {}}
      onGuidanceBusyChange={() => {}}
    />,
  );
  expect(screen.queryByText('说明书正文')).not.toBeInTheDocument();
  await userEvent.type(
    screen.getByRole('textbox', { name: '问题草稿' }),
    '保留我的问题',
  );
  await userEvent.click(screen.getByRole('button', { name: '资料' }));
  expect(screen.getByText('说明书正文')).toBeVisible();
  await userEvent.click(screen.getByRole('button', { name: '操作引导' }));
  await userEvent.click(screen.getByRole('button', { name: '当前步骤 1' }));
  await userEvent.click(screen.getByRole('button', { name: '聊天' }));
  expect(screen.getByRole('textbox', { name: '问题草稿' })).toHaveValue(
    '保留我的问题',
  );
  await userEvent.click(screen.getByRole('button', { name: '操作引导' }));
  expect(screen.getByRole('button', { name: '当前步骤 2' })).toBeVisible();
});

test('手机工具从可关闭的对话框选择模块，聊天草稿继续保留', async () => {
  render(
    <DeviceWorkbench
      product={product}
      compact
      onChatBusyChange={() => {}}
      onGuidanceBusyChange={() => {}}
    />,
  );
  await userEvent.type(
    screen.getByRole('textbox', { name: '问题草稿' }),
    '手机草稿',
  );
  await userEvent.click(
    screen.getByRole('button', { name: '说明书 · 记忆 · 操作引导' }),
  );
  expect(screen.getByRole('dialog')).toBeVisible();
  await userEvent.click(screen.getByRole('button', { name: '打开资料' }));
  expect(screen.getByText('说明书正文')).toBeVisible();
  await userEvent.click(screen.getByRole('button', { name: '返回设备聊天' }));
  expect(screen.getByRole('textbox', { name: '问题草稿' })).toHaveValue(
    '手机草稿',
  );
});
