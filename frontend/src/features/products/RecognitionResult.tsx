import { Alert, Button, Space, Spin, Tag } from 'antd';
import type { Candidate, Identification } from '../../types/api';
import { CandidateList } from './CandidateList';

const stages: Record<string, string> = {
  observing: '正在观察物品外观…',
  finding_candidates: '正在查找相近产品资料…',
  comparing: '正在综合照片和参考资料比较…',
};

export function RecognitionResult({
  attempt,
  busy,
  onConfirm,
  onRejectAll,
  onRestart,
  onAddPhotos,
}: {
  attempt: Identification;
  busy: boolean;
  onConfirm: (value: Candidate) => void;
  onRejectAll: () => void;
  onRestart: () => void;
  onAddPhotos: () => void;
}) {
  const processing = ['queued', 'processing'].includes(attempt.status);
  const finalRound = (attempt.roundNo ?? 1) >= (attempt.maxRounds ?? 4);
  return (
    <div className="identification-result" aria-live="polite">
      <p>
        <Tag>
          第 {attempt.roundNo ?? 1} / {attempt.maxRounds ?? 4} 轮
        </Tag>{' '}
        多个角度一起判断，最终由你选择。
      </p>
      {processing && (
        <div className="recognition-loading">
          <Spin size="small" />{' '}
          {stages[attempt.progress?.stage ?? ''] ?? '正在识别产品，请稍候…'}
        </div>
      )}
      {attempt.observation && (
        <section className="recognition-observation" aria-label="外观初步判断">
          <h2>先看看它是什么</h2>
          <p>
            {attempt.observation.category}
            {attempt.observation.brandHint
              ? ` · 品牌线索：${attempt.observation.brandHint}`
              : ''}
          </p>
          <ul>
            {attempt.observation.features.map((feature, index) => (
              <li key={index}>{feature}</li>
            ))}
          </ul>
        </section>
      )}
      {!processing && (
        <>
          {attempt.clarification && (
            <Alert
              type={attempt.status === 'failed' ? 'error' : 'info'}
              title={attempt.clarification}
              showIcon
            />
          )}
          {attempt.status === 'waiting_for_user' && (
            <>
              <h2>
                {attempt.observation ? '看看哪一款更接近' : '请确认准确型号'}
              </h2>
              <p>用户选择后仍需官方来源核验，才会进入公共资料库。</p>
              <CandidateList
                candidates={attempt.candidates}
                busy={busy}
                onConfirm={onConfirm}
              />
            </>
          )}
          {!!attempt.photoRequests?.length && (
            <section className="recognition-next" aria-label="建议补拍">
              <h3>下一张拍什么</h3>
              <ul>
                {attempt.photoRequests.map((request, index) => (
                  <li key={index}>
                    <strong>{request.instruction}</strong>
                    <p>{request.reason}</p>
                  </li>
                ))}
              </ul>
            </section>
          )}
          {finalRound && (
            <Alert
              type="info"
              title="已完成 4 轮判断。可以选择候选或手动填写；仍无法确定时可重新开始，不会强行指定型号。"
            />
          )}
          <Space wrap>
            <Button disabled={busy || finalRound} onClick={onAddPhotos}>
              继续补拍
            </Button>
            {attempt.candidates.some(
              (candidate) => candidate.candidateId || candidate.productModelId,
            ) && (
              <Button disabled={busy || finalRound} onClick={onRejectAll}>
                都不是，排除这些候选
              </Button>
            )}
            <Button disabled={busy} onClick={onRestart}>
              重新开始识别
            </Button>
          </Space>
        </>
      )}
    </div>
  );
}
