import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';
import { WebSourceCard } from './WebSourceCard';

test('第三方网页明确标识，链接安全且不显示虚构页码', () => {
  render(
    <WebSourceCard
      source={{
        id: 'web',
        rank: 1,
        title: '鼠标教程',
        url: 'https://guide.example/mouse',
        sourceKind: 'third_party',
        fetchedAt: '2026-10-25T10:00:00Z',
        pageStart: null,
        pageEnd: null,
      }}
    />,
  );
  expect(screen.getByText('第三方资料 · 非官方核验')).toBeVisible();
  expect(screen.getByRole('link', { name: '鼠标教程' })).toHaveAttribute(
    'href',
    'https://guide.example/mouse',
  );
  expect(screen.queryByText(/第.*页|已核验来源/)).not.toBeInTheDocument();
});
