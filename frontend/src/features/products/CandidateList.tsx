import { useState } from 'react';
import { Button, Radio, Tag } from 'antd';
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
  const [selected, setSelected] = useState<string>();
  const keyOf = (candidate: Candidate) =>
    candidate.candidateId ?? candidate.productModelId;
  const chosen =
    candidates.find((candidate) => keyOf(candidate) === selected) ??
    (candidates.length === 1 ? candidates[0] : undefined);
  return (
    <div className="candidate-list">
      {candidates.map((candidate) => (
        <article
          className="candidate"
          key={candidate.candidateId ?? candidate.productModelId}
        >
          <Radio
            name="recognition-model"
            aria-label={`选择 ${candidate.brandName} ${candidate.modelCode}`}
            checked={chosen === candidate}
            disabled={busy}
            onChange={() => setSelected(keyOf(candidate))}
          >
            选择这个型号
          </Radio>
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
              <Tag color="gold">
                {candidate.evidenceLevel === 'visual_guess'
                  ? '外观猜测 · 资料尚未核实'
                  : '外观候选 · 由你选择'}
              </Tag>
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
                {candidate.evidenceLevel === 'visual_guess'
                  ? '这是根据照片提出的猜测，你可以选择、排除或继续补充；不代表已核实型号。'
                  : '资料说明该型号的特征，当前照片是否属于它仍由你确认。'}
              </small>
            </>
          ) : (
            <p>{candidate.evidence}</p>
          )}
        </article>
      ))}
      {!!candidates.length && (
        <Button
          type="primary"
          loading={busy}
          disabled={!chosen || busy}
          onClick={() => chosen && onConfirm(chosen)}
        >
          确认此型号
        </Button>
      )}
    </div>
  );
}
