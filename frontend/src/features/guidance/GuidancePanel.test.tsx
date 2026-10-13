import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';
import { api, post } from '../../services/api';
import type { Guidance, ProductInstance } from '../../types/api';
import { GuidancePanel } from './GuidancePanel';

vi.mock('../../services/api', () => ({
  api: vi.fn(async () => ({ items: [] })),
  post: vi.fn(),
  upload: vi.fn(),
  newKey: () => 'guidance-test-key',
}));
const product: ProductInstance = {
  id: 'device',
  productModelId: 'model',
  nickname: null,
  brandName: '演示',
  modelCode: 'DEMO-100',
  identityStatus: 'verified',
  verificationStatus: 'verified',
  knowledgeJobId: null,
  knowledgeStatus: 'ready',
};
const session: Guidance = {
  id: 'guide',
  topic: '开机',
  status: 'active',
  riskLevel: 'medium',
  version: 1,
  currentStepNo: 1,
  notice: null,
  currentStep: {
    id: 'step',
    stepNo: 1,
    instruction: '按官方说明连接设备',
    successCriteria: '准备条件满足',
    riskLevel: 'medium',
    preconditions: ['确保设备按手册要求准备'],
    safetyNotes: ['不要带电拆机'],
    failureOfStepId: null,
    status: 'active',
    citations: [
      {
        knowledgeChunkId: 'chunk',
        documentVersionId: 'version',
        title: '手册',
        url: 'https://example.com/manual',
        pageStart: 1,
        pageEnd: 1,
        rank: 1,
        verificationStatus: 'verified',
      },
    ],
  },
};

async function openAndCreate() {
  await userEvent.click(screen.getByRole('button', { name: '操作引导' }));
  await userEvent.type(screen.getByLabelText('你想完成什么操作？'), '开机');
  await userEvent.click(screen.getByRole('button', { name: '开始引导' }));
}

test('中风险只有确认前提后才能提交完成，反馈包含步骤号和版本', async () => {
  vi.mocked(post)
    .mockResolvedValueOnce(session)
    .mockResolvedValueOnce({
      ...session,
      version: 2,
      status: 'completed',
      currentStep: null,
    });
  render(<GuidancePanel product={product} />);
  await openAndCreate();
  expect(await screen.findByRole('button', { name: '已完成' })).toBeDisabled();
  await userEvent.click(
    screen.getByRole('checkbox', {
      name: '我已按官方说明确认本步骤的安全前提',
    }),
  );
  await userEvent.click(screen.getByRole('button', { name: '已完成' }));
  await waitFor(() =>
    expect(post).toHaveBeenLastCalledWith(
      '/guidance-sessions/guide/feedback',
      {
        currentStepNo: 1,
        expectedVersion: 1,
        result: 'completed',
        note: null,
        assetIds: [],
        preconditionsConfirmed: true,
      },
      'guidance-test-key',
    ),
  );
  expect(await screen.findByText('操作引导已完成')).toBeVisible();
});

test('高风险不展示可执行步骤、确认框或恢复按钮', async () => {
  vi.mocked(post).mockResolvedValueOnce({
    ...session,
    status: 'blocked_unsafe',
    riskLevel: 'high',
    currentStep: null,
    currentStepNo: null,
    notice: '请停止操作并联系专业人员',
  });
  render(<GuidancePanel product={product} />);
  await openAndCreate();
  expect(await screen.findByText('请停止操作并联系专业人员')).toBeVisible();
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: '已完成' }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: '恢复引导' }),
  ).not.toBeInTheDocument();
});

test('历史暂停引导恢复时沿用服务端版本，不创建新会话', async () => {
  vi.mocked(api)
    .mockResolvedValueOnce({
      items: [{ ...session, status: 'paused', version: 4 }],
    })
    .mockResolvedValueOnce({ ...session, status: 'paused', version: 4 });
  vi.mocked(post).mockResolvedValueOnce({ ...session, version: 5 });
  render(<GuidancePanel product={product} />);
  await userEvent.click(screen.getByRole('button', { name: '操作引导' }));
  await userEvent.click(
    await screen.findByRole('button', { name: '开机 · 已暂停' }),
  );
  await userEvent.click(
    await screen.findByRole('button', { name: '恢复引导' }),
  );
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith(
      '/guidance-sessions/guide/resume',
      { expectedVersion: 4 },
      'guidance-test-key',
    ),
  );
});
