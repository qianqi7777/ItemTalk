import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';
import { api, post } from '../../services/api';
import type { Manual, ProductInstance } from '../../types/api';
import { ManualPanel } from './ManualPanel';

vi.mock('../../services/api', async (load) => ({
  ...(await load<object>()),
  api: vi.fn(),
  post: vi.fn(),
}));

const product: ProductInstance = {
  id: 'instance',
  productModelId: 'model',
  nickname: null,
  brandName: '演示',
  modelCode: 'DEMO-100',
  identityStatus: 'verified',
  verificationStatus: 'verified',
  knowledgeJobId: null,
  knowledgeStatus: 'ready',
};
const manual: Manual = {
  id: 'manual',
  status: 'ready',
  version: 2,
  jobId: 'job',
  sections: [
    {
      id: 'intro',
      parentId: null,
      sectionKey: 'intro',
      title: '开始使用',
      sortOrder: 0,
      body: '长按电源键开机。[1]',
      generationStatus: 'ready',
      citations: [
        {
          knowledgeChunkId: 'chunk',
          documentVersionId: 'version',
          title: '说明书来源',
          url: 'https://example.com/manual',
          pageStart: 1,
          pageEnd: 1,
          rank: 1,
          verificationStatus: 'verified',
        },
      ],
    },
    {
      id: 'detail',
      parentId: 'intro',
      sectionKey: 'detail',
      title: '连接步骤',
      sortOrder: 0,
      body: '请先阅读连接说明。',
      generationStatus: 'ready',
      citations: [],
    },
  ],
};

test('打开说明书才加载当前实例的章节树，切换章节不会调用生成', async () => {
  vi.mocked(api).mockResolvedValue(manual);
  render(<ManualPanel product={product} />);
  expect(api).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole('button', { name: '使用说明书' }));
  expect(await screen.findByText('说明书版本 2')).toBeVisible();
  expect(api).toHaveBeenCalledWith(
    '/products/model/manual?instanceId=instance',
  );
  expect(screen.getByRole('navigation', { name: '说明书章节' })).toBeVisible();
  await userEvent.click(screen.getByRole('button', { name: '连接步骤' }));
  expect(screen.getByRole('heading', { name: '连接步骤' })).toBeVisible();
  expect(post).not.toHaveBeenCalled();
});

test('撤销的历史引用显示警告，仍可查看原章节', async () => {
  const revoked = structuredClone(manual);
  revoked.sections[0].citations[0].verificationStatus = 'revoked';
  vi.mocked(api).mockResolvedValue(revoked);
  render(<ManualPanel product={product} />);
  await userEvent.click(screen.getByRole('button', { name: '使用说明书' }));
  expect(
    await screen.findByText(
      '本章部分来源已失效，请等待新版本，不要依此执行操作。',
    ),
  ).toBeVisible();
  expect(screen.getByText(/长按电源键开机/)).toBeVisible();
});
