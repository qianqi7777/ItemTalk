import { Button, Tag } from 'antd';
import type { Candidate } from '../../types/api';

export function CandidateList({
  candidates,
  busy,
  onConfirm,
}: {
  candidates: Candidate[];
  busy: boolean;
  onConfirm: (candidate: Candidate) => void;
}) {
  return (
    <div className="candidate-list">
      {candidates.map((candidate) => (
        <article className="candidate" key={candidate.productModelId}>
          <div>
            <h3>
              {candidate.brandName} · {candidate.modelCode}
            </h3>
            <Tag color={candidate.confidence >= 0.85 ? 'green' : 'gold'}>
              {candidate.confidence >= 0.85 ? '较高' : '待确认'}置信度 ·{' '}
              {Math.round(candidate.confidence * 100)}%
            </Tag>
          </div>
          <p>{candidate.evidence}</p>
          <Button
            type="primary"
            loading={busy}
            onClick={() => onConfirm(candidate)}
          >
            确认此型号
          </Button>
        </article>
      ))}
    </div>
  );
}
