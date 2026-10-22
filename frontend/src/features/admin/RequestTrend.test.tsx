import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { RequestTrend } from './RequestTrend';

describe('request trend', () => {
  it('explains missing samples instead of drawing a zero trend', () => {
    render(<RequestTrend points={[]} />);
    expect(screen.getByText('暂无请求样本')).toBeVisible();
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
  });

  it('renders a visible single sample without inventing a trend', () => {
    const { container } = render(
      <RequestTrend
        points={[{ hour: '2026-10-19T01:00:00Z', kind: 'http', p95_ms: 152 }]}
      />,
    );
    expect(
      screen.getByRole('img', { name: '请求P95耗时趋势' }),
    ).toBeInTheDocument();
    expect(container.querySelector('circle')).toHaveAttribute('cx', '324');
    expect(container.querySelector('polyline')).not.toBeInTheDocument();
    expect(screen.getByText(/仅有一个小时样本/)).toBeVisible();
  });

  it('uses actual elapsed time for gaps and does not mutate the received order', () => {
    const points = [
      { hour: '2026-10-19T04:00:00Z', kind: 'http', p95_ms: 50 },
      { hour: '2026-10-19T00:00:00Z', kind: 'http', p95_ms: 100 },
      { hour: '2026-10-19T01:00:00Z', kind: 'http', p95_ms: 80 },
    ];
    const { container } = render(<RequestTrend points={points} />);
    expect(container.querySelectorAll('circle')[1]).toHaveAttribute(
      'cx',
      '186',
    );
    expect(points[0].hour).toBe('2026-10-19T04:00:00Z');
  });
});
