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
        <article
          className="candidate"
          key={candidate.candidateId ?? candidate.productModelId}
        >
          {candidate.referenceImage && (
            <img
              className="candidate-reference"
              src={candidate.referenceImage}
              alt={`${candidate.brandName} ${candidate.modelCode} 参考图`}
            />
          )}
          <div>
            <h3>
              {candidate.brandName} · {candidate.modelCode}
            </h3>
            {candidate.basis === 'appearance' ? (
              <Tag color="gold">外观候选 · 由你选择</Tag>
            ) : (
              <Tag color={candidate.confidence >= 0.85 ? 'green' : 'gold'}>
                {candidate.confidence >= 0.85 ? '较高' : '待确认'}置信度 ·{' '}
                {Math.round(candidate.confidence * 100)}%
              </Tag>
            )}
          </div>
          {candidate.basis === 'appearance' ? (
            <>
              <p className="candidate-section-label">匹配的外观特征</p>
              <ul>
                {candidate.matchReasons?.map((text, index) => (
                  <li key={index}>{text}</li>
                ))}
              </ul>
              {!!candidate.differences?.length && (
                <>
                  <p className="candidate-section-label">需要比较的差异</p>
                  <ul>
                    {candidate.differences.map((text, index) => (
                      <li key={index}>{text}</li>
                    ))}
                  </ul>
                </>
              )}
              {!!candidate.missingEvidence?.length && (
                <>
                  <p className="candidate-section-label">还没确认的地方</p>
                  <ul>
                    {candidate.missingEvidence.map((text, index) => (
                      <li key={index}>{text}</li>
                    ))}
                  </ul>
                </>
              )}
              <div className="candidate-sources">
                {candidate.sources?.map((source) => (
                  <a
                    key={source.id}
                    href={source.url}
                    target="_blank"
                    rel="noreferrer noopener"
                  >
                    {source.title}
                  </a>
                ))}
              </div>
              <small>
                资料说明该型号的特征，当前照片是否属于它仍由你确认。
              </small>
            </>
          ) : (
            <p>{candidate.evidence}</p>
          )}
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
