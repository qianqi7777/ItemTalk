import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';
import { CitationCard } from './CitationCard';
import type { Citation } from '../types/api';

const citation: Citation = {
  knowledgeChunkId: 'chunk',
  documentVersionId: 'version',
  title: '官方使用手册',
  url: 'https://example.com/manual',
  pageStart: 3,
  pageEnd: 4,
  rank: 1,
  verificationStatus: 'verified',
  verifiedAt: '2026-10-13T00:00:00Z',
};
test('显示证据页码、核验状态和安全链接', () => {
  render(<CitationCard citation={citation} />);
  expect(screen.getByText(/第 3–4 页/)).toBeVisible();
  expect(screen.getByText('已核验来源')).toBeVisible();
  expect(screen.getByRole('link')).toHaveAttribute(
    'rel',
    'noopener noreferrer',
  );
});
test('阻止危险 URL 并显示来源撤销', () => {
  render(
    <CitationCard
      citation={{
        ...citation,
        url: 'javascript:alert(1)',
        verificationStatus: 'revoked',
      }}
    />,
  );
  expect(screen.getByText('来源核验已变更')).toBeVisible();
  expect(screen.getByText(/官方使用手册/)).not.toHaveAttribute('href');
});
