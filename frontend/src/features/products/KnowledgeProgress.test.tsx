import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';
import { KnowledgeProgress } from './KnowledgeProgress';
import type { ProductInstance } from '../../types/api';

test('资料不足时说明仍可使用有明确标注的参考回答', () => {
  render(
    <KnowledgeProgress
      product={{ knowledgeStatus: 'partial' } as ProductInstance}
    />,
  );
  expect(screen.getByText(/仍可提问/)).toBeVisible();
  expect(screen.getByText(/模型推测，未经资料核验/)).toBeVisible();
});

test('显示服务端研究阶段和失败信息', () => {
  const product = { knowledgeStatus: 'parsing' } as ProductInstance;
  render(
    <KnowledgeProgress
      product={product}
      job={{
        id: 'job',
        status: 'running',
        progress: { stage: 'parsing', percent: 35 },
        lastError: null,
      }}
    />,
  );
  expect(screen.getByText('解析与核验资料')).toBeVisible();
  expect(screen.getByRole('progressbar')).toHaveAttribute(
    'aria-valuenow',
    '35',
  );
});

test('补充资料就绪不显示官方完成或持续加载，明确保留非官方提示', () => {
  render(
    <KnowledgeProgress
      product={{ knowledgeStatus: 'supplemental_ready' } as ProductInstance}
    />,
  );
  expect(screen.getByText('第三方资料可用 · 非官方核验')).toBeVisible();
  expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
  expect(screen.queryByText('资料已就绪')).not.toBeInTheDocument();
});
