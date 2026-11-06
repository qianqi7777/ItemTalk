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
  compact = false,
}: {
  attempt: Identification;
  busy: boolean;
  onConfirm: (value: Candidate) => void;
  onRejectAll: () => void;
  onRestart: () => void;
  onAddPhotos: () => void;
  compact?: boolean;
}) {
  const processing = ['queued', 'processing'].includes(attempt.status);
  const finalRound = (attempt.roundNo ?? 1) >= (attempt.maxRounds ?? 4);
  if (processing) {
    const order = ['observing', 'finding_candidates', 'comparing'];
    const current = order.indexOf(attempt.progress?.stage ?? '');
    return (
      <section
        className="recognition-processing"
        aria-label="识别进度"
        aria-live="polite"
      >
        <div className="processing-title">
          <Spin size="small" />
          <h2>
            {stages[attempt.progress?.stage ?? ''] ??
              '任务已排队，等待开始识别…'}
          </h2>
        </div>
        <p>结果将在这里出现，无需再次提交照片。</p>
        <ol>
          {['观察物品外观', '查找相近产品资料', '综合照片与参考资料比较'].map(
            (label, index) => (
              <li
                key={label}
                data-state={
                  current === index
                    ? 'active'
                    : current > index
                      ? 'done'
                      : 'pending'
                }
              >
                <span aria-hidden="true">
                  {current > index ? '✓' : index + 1}
                </span>
                {label}
              </li>
            ),
          )}
        </ol>
        <small>
          第 {attempt.roundNo ?? 1} / {attempt.maxRounds ?? 4} 轮 ·
          最终型号由你确认
        </small>
      </section>
    );
  }
  const actions = (
    <Space wrap className="recognition-secondary-actions">
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
      {!compact && (
        <Button disabled={busy} onClick={onRestart}>
          重新开始识别
        </Button>
      )}
    </Space>
  );
  const photoRequests = !!attempt.photoRequests?.length && (
    <details className="recognition-next" open={compact ? undefined : true}>
      <summary>
        {compact
          ? `建议补拍：${attempt.photoRequests[0].instruction}`
          : '下一张拍什么'}
      </summary>
      <ul>
        {attempt.photoRequests.map((request, index) => (
          <li key={index}>
            <strong>{request.instruction}</strong>
            <p>{request.reason}</p>
          </li>
        ))}
      </ul>
    </details>
  );
  return (
    <div
      className={`identification-result ${compact ? 'result-compact' : ''}`}
      aria-live="polite"
    >
      <div className="recognition-overview">
        <p className="recognition-round">
          <Tag>
            第 {attempt.roundNo ?? 1} / {attempt.maxRounds ?? 4} 轮
          </Tag>{' '}
          {compact ? '由你确认' : '多个角度一起判断，最终由你选择。'}
        </p>
        {processing && (
          <div className="recognition-loading">
            <Spin size="small" />{' '}
            {stages[attempt.progress?.stage ?? ''] ?? '正在识别产品，请稍候…'}
          </div>
        )}
        <details
          className="recognition-context"
          open={compact ? undefined : true}
        >
          <summary>外观观察与判断说明</summary>
          {attempt.observation && (
            <section
              className="recognition-observation"
              aria-label="外观初步判断"
            >
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
          {!processing && attempt.clarification && (
            <Alert
              type={attempt.status === 'failed' ? 'error' : 'info'}
              title={attempt.clarification}
              showIcon
            />
          )}
          {compact && finalRound && (
            <p>
              已完成 4
              轮判断。可以选择候选或手动填写；仍无法确定时可重新开始，不会强行指定型号。
            </p>
          )}
          {compact && !processing && (
            <Button disabled={busy} onClick={onRestart}>
              重新开始识别
            </Button>
          )}
        </details>
      </div>
      {compact && attempt.status === 'failed' && (
        <Alert
          type="error"
          title={
            attempt.clarification || '本轮识别失败，请补充信息或重新开始。'
          }
          showIcon
        />
      )}
      {!processing && (
        <>
          {attempt.status === 'waiting_for_user' && (
            <>
              <h2 className="candidate-heading">
                {attempt.observation ? '看看哪一款更接近' : '请确认准确型号'}
              </h2>
              {!compact && (
                <p>用户选择后仍需官方来源核验，才会进入公共资料库。</p>
              )}
              <CandidateList
                candidates={attempt.candidates}
                busy={busy}
                onConfirm={onConfirm}
                compact={compact}
                beforeActions={compact ? photoRequests : undefined}
                extraActions={compact ? actions : undefined}
              />
            </>
          )}
          {!compact && photoRequests}
          {finalRound && !compact && (
            <Alert
              type="info"
              title="已完成 4 轮判断。可以选择候选或手动填写；仍无法确定时可重新开始，不会强行指定型号。"
            />
          )}
          {(!compact || attempt.status !== 'waiting_for_user') && actions}
        </>
      )}
    </div>
  );
}
