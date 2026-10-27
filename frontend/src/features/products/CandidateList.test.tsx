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

test('appearance candidates show reference evidence and uncertainty without invented accuracy', async () => {
  const candidate = {
    candidateId: 'private-candidate',
    basis: 'appearance' as const,
    brandName: 'Acme',
    modelCode: 'Phone 16',
    confidence: 0.6,
    evidence: '镜头布局相似',
    matchReasons: ['镜头排列相似'],
    differences: ['尚未看到侧边按键'],
    missingEvidence: ['需要正面照片'],
    referenceImage: 'data:image/jpeg;base64,ZmFrZQ==',
    sources: [
      {
        id: 'source',
        title: 'Phone 16 技术规格',
        url: 'https://vendor.example/phone',
        sourceKind: 'official' as const,
      },
    ],
  };
  render(
    <CandidateList candidates={[candidate]} busy={false} onConfirm={vi.fn()} />,
  );
  expect(
    screen.getByRole('img', { name: 'Acme Phone 16 参考图' }),
  ).toBeVisible();
  expect(screen.getByText('镜头排列相似')).toBeVisible();
  expect(screen.getByText('需要正面照片')).toBeVisible();
  expect(
    screen.getByRole('link', { name: 'Phone 16 技术规格' }),
  ).toHaveAttribute('href', 'https://vendor.example/phone');
  expect(screen.queryByText(/60%/)).not.toBeInTheDocument();
});

test('multiple visual guesses expose explicit selection before confirmation', async () => {
  const onConfirm = vi.fn();
  const first = {
    candidateId: 'one',
    brandName: 'Acme',
    modelCode: 'Phone 16',
    basis: 'appearance' as const,
    evidenceLevel: 'visual_guess' as const,
    confidence: 0,
    evidence: '镜头布局猜测',
  };
  const second = { ...first, candidateId: 'two', modelCode: 'Phone 17' };
  render(
    <CandidateList
      candidates={[first, second]}
      busy={false}
      onConfirm={onConfirm}
    />,
  );
  expect(screen.getAllByText('外观猜测 · 资料尚未核实')).toHaveLength(2);
  expect(screen.getByRole('button', { name: '确认此型号' })).toBeDisabled();
  await userEvent.click(
    screen.getByRole('radio', { name: '选择 Acme Phone 17' }),
  );
  await userEvent.click(screen.getByRole('button', { name: '确认此型号' }));
  expect(onConfirm).toHaveBeenCalledWith(second);
});
