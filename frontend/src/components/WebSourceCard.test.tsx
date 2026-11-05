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

test('已撤销的补充来源显示失效提示，危险URL不能打开', () => {
  render(
    <WebSourceCard
      source={{
        id: 'withdrawn',
        rank: 1,
        title: '旧说明书',
        url: 'javascript:alert(1)',
        sourceKind: 'third_party',
        fetchedAt: '2026-10-25T10:00:00Z',
        pageStart: 2,
        pageEnd: 3,
        availabilityStatus: 'withdrawn',
      }}
    />,
  );
  expect(screen.getByText(/来源已撤销.*不要依此执行操作/)).toBeVisible();
  expect(screen.queryByRole('link')).not.toBeInTheDocument();
});
