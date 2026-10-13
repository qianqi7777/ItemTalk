import { render, screen } from '@testing-library/react';
import { expect, test, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { CandidateList } from './CandidateList';

test('候选必须经过用户明确确认，低置信度展示提醒', async () => {
  const onConfirm = vi.fn();
  const candidate = {
    productModelId: 'model',
    brandName: '品牌',
    modelCode: 'AB-10',
    confidence: 0.6,
    evidence: '铭牌局部匹配',
  };
  render(
    <CandidateList
      candidates={[candidate]}
      busy={false}
      onConfirm={onConfirm}
    />,
  );
  expect(onConfirm).not.toHaveBeenCalled();
  expect(screen.getByText(/待确认置信度/)).toBeVisible();
  await userEvent.click(screen.getByRole('button', { name: '确认此型号' }));
  expect(onConfirm).toHaveBeenCalledWith(candidate);
});
