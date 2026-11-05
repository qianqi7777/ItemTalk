import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, test, vi } from 'vitest';
import { ApiError, api, post } from '../../services/api';
import type { Manual, ProductInstance } from '../../types/api';
import { ManualPanel } from './ManualPanel';

vi.mock('../../services/api', async (load) => ({
  ...(await load<object>()),
  api: vi.fn(),
  post: vi.fn(),
}));
beforeEach(() => vi.clearAllMocks());

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

test('个人版本使用实例范围请求，个人设置不渲染成官方引用或HTML', async () => {
  const personal = structuredClone(manual);
  personal.visibility = 'private';
  personal.personalContext = [
    { memoryType: 'product_fact', content: '<script>个人设置[1]</script>' },
  ];
  personal.sections[0].personalNotes = [
    { text: '<script>结合设置的说明[1]</script>' },
  ];
  vi.mocked(api).mockResolvedValueOnce(manual).mockResolvedValue(personal);
  vi.mocked(post).mockResolvedValue({ id: 'private-job' });
  render(<ManualPanel product={product} />);
  await userEvent.click(screen.getByRole('button', { name: '使用说明书' }));
  await waitFor(() =>
    expect(
      screen.getByRole('button', { name: '生成我的说明书' }),
    ).toBeEnabled(),
  );
  await userEvent.click(screen.getByRole('button', { name: '生成我的说明书' }));
  expect(post).toHaveBeenCalledWith(
    '/products/model/manual-jobs',
    { userProductInstanceId: 'instance' },
    expect.any(String),
    undefined,
  );
  expect(await screen.findByText('实例专属 · 仅本人可见')).toBeVisible();
  expect(screen.getByLabelText('已确认的个人设置')).toHaveTextContent(
    '<script>个人设置[1]</script>',
  );
  expect(document.querySelector('aside script')).toBeNull();
  expect(screen.getByLabelText('本章个人补充')).toHaveTextContent(
    '结合设置的说明[1]',
  );
  await userEvent.click(screen.getByRole('button', { name: '通用说明书' }));
  expect(api).toHaveBeenLastCalledWith('/products/model/manual');
});

test('补全只发起选中未完成章节，设置变化时创建新版', async () => {
  const partial = structuredClone(manual);
  partial.visibility = 'private';
  partial.status = 'partial';
  partial.sections[0].generationStatus = 'partial';
  vi.mocked(api).mockResolvedValue(partial);
  vi.mocked(post).mockResolvedValue({ id: 'chapter-job' });
  render(<ManualPanel product={product} />);
  await userEvent.click(screen.getByRole('button', { name: '使用说明书' }));
  await userEvent.click(screen.getByRole('button', { name: '补全这一章' }));
  expect(post).toHaveBeenCalledWith(
    '/manuals/manual/section-jobs',
    { sectionIds: ['intro'] },
    expect.any(String),
  );
});

test('旧当前版本有变化时仍可重试输入有效的新版本', async () => {
  const older = structuredClone(manual);
  older.visibility = 'private';
  older.inputsStale = true;
  older.latestBuild = {
    id: 'newer',
    version: 3,
    status: 'partial',
    jobId: 'newer-job',
    inputsStale: false,
  };
  vi.mocked(api).mockResolvedValue(older);
  vi.mocked(post).mockResolvedValue({ id: 'retry-job' });
  render(<ManualPanel product={product} />);
  await userEvent.click(screen.getByRole('button', { name: '使用说明书' }));
  await userEvent.click(screen.getByRole('button', { name: '重试新版本' }));
  expect(post).toHaveBeenCalledWith(
    '/products/model/manual-jobs',
    { userProductInstanceId: 'instance' },
    expect.any(String),
    'newer-job',
  );
});

test('撤销记忆后立即重读个人版本，不自动花费生成额度', async () => {
  const personal = structuredClone(manual);
  personal.visibility = 'private';
  personal.sections[0].personalNotes = [{ text: '你已开启节能模式' }];
  const revoked = structuredClone(personal);
  revoked.contextStale = true;
  revoked.inputsStale = true;
  revoked.sections[0].personalNotes = [];
  vi.mocked(api).mockResolvedValueOnce(personal).mockResolvedValue(revoked);
  render(<ManualPanel product={product} />);
  await userEvent.click(screen.getByRole('button', { name: '使用说明书' }));
  expect(await screen.findByLabelText('本章个人补充')).toBeVisible();
  fireEvent(window, new Event('itemtalk-memory-change'));
  expect(
    await screen.findByText('个人设置已变化，旧章节仅作历史参考。'),
  ).toBeVisible();
  expect(screen.queryByLabelText('本章个人补充')).toBeNull();
  expect(post).not.toHaveBeenCalled();
});

test('官方说明书404时仍展示独立补充资料及原文，不生成官方章节', async () => {
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === '/products/model/supplemental-documents?instanceId=instance')
      return {
        items: [
          {
            id: 'supplement',
            title: 'DEMO-100 第三方说明书',
            url: 'https://manual.example/demo-100.pdf',
            sourceKind: 'third_party',
            fetchedAt: '2026-11-05T00:00:00Z',
            pageCount: 12,
            chunkCount: 8,
            matchStatus: 'exact',
            availabilityStatus: 'available',
          },
          {
            id: 'unsafe',
            title: '不可打开的来源',
            url: 'javascript:alert(1)',
            sourceKind: 'third_party',
            fetchedAt: '2026-11-05T00:00:00Z',
            pageCount: 1,
            chunkCount: 1,
            matchStatus: 'exact',
            availabilityStatus: 'available',
          },
        ],
      };
    throw new ApiError(404, 'NOT_FOUND', '说明书尚未生成');
  });
  render(
    <ManualPanel
      product={{ ...product, knowledgeStatus: 'supplemental_ready' }}
    />,
  );
  await userEvent.click(screen.getByRole('button', { name: '使用说明书' }));
  const link = await screen.findByRole('link', {
    name: 'DEMO-100 第三方说明书',
  });
  expect(link).toHaveAttribute('href', 'https://manual.example/demo-100.pdf');
  expect(link).toHaveAttribute('rel', 'noopener noreferrer');
  expect(screen.getByText(/12 页/)).toBeVisible();
  expect(screen.getByText('第三方资料 · 非官方核验')).toBeVisible();
  expect(screen.getByText('不可打开的来源')).toBeVisible();
  expect(screen.queryByRole('link', { name: '不可打开的来源' })).toBeNull();
  expect(screen.queryByRole('navigation', { name: '说明书章节' })).toBeNull();
  expect(post).not.toHaveBeenCalled();
});

test('官方资料已就绪但有补充资料时，列表独立加载且失败不影响官方章节', async () => {
  vi.mocked(api).mockImplementation(async (path) => {
    if (path.includes('/supplemental-documents'))
      throw new Error('补充来源暂不可读');
    return manual;
  });
  render(
    <ManualPanel product={{ ...product, supplementalDocumentCount: 1 }} />,
  );
  await userEvent.click(screen.getByRole('button', { name: '使用说明书' }));
  expect(await screen.findByText('补充来源暂不可读')).toBeVisible();
  expect(screen.getByRole('heading', { name: '开始使用' })).toBeVisible();
  expect(screen.getByText(/长按电源键开机/)).toBeVisible();
});

test('切换产品后忽略旧补充列表的迟到响应', async () => {
  let resolve!: (value: unknown) => void;
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === '/products/model/supplemental-documents?instanceId=instance')
      return new Promise((done) => {
        resolve = done;
      });
    if (path.includes('/supplemental-documents')) return { items: [] };
    return manual;
  });
  const { rerender } = render(
    <ManualPanel product={{ ...product, supplementalDocumentCount: 1 }} />,
  );
  await userEvent.click(screen.getByRole('button', { name: '使用说明书' }));
  rerender(
    <ManualPanel
      product={{ ...product, id: 'second', supplementalDocumentCount: 1 }}
    />,
  );
  expect(
    await screen.findByText('当前没有可用的第三方补充资料。'),
  ).toBeVisible();
  await act(async () =>
    resolve({
      items: [
        {
          id: 'old',
          title: '旧产品的资料',
          url: 'https://old.example/manual',
          sourceKind: 'third_party',
          fetchedAt: '2026-11-05T00:00:00Z',
          pageCount: 2,
          chunkCount: 1,
          matchStatus: 'exact',
          availabilityStatus: 'available',
        },
      ],
    }),
  );
  expect(screen.queryByText('旧产品的资料')).toBeNull();
});
