import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';
import { KnowledgeProgress } from './KnowledgeProgress';
import type { ProductInstance } from '../../types/api';

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
