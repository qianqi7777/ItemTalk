import { Alert, Progress, Tag } from 'antd';
import type { Job, ProductInstance } from '../../types/api';

const knowledgeLabels: Record<string, string> = {
  not_requested: '尚未建立资料',
  queued: '任务已排队',
  researching: '查找官方资料',
  researching_supplemental: '查找第三方说明书',
  parsing: '解析与核验资料',
  embedding: '建立检索索引',
  ready: '资料已就绪',
  supplemental_ready: '第三方资料可用 · 非官方核验',
  partial: '资料仍待补充',
  failed: '资料构建失败',
};
export function KnowledgeProgress({
  product,
  job,
  compact = false,
}: {
  product: ProductInstance;
  job?: Job;
  compact?: boolean;
}) {
  const label = knowledgeLabels[product.knowledgeStatus] || '正在处理';
  return (
    <div className="knowledge-progress" aria-live="polite">
      <Tag color={product.knowledgeStatus === 'ready' ? 'green' : 'gold'}>
        {label}
      </Tag>
      {['partial', 'failed', 'not_requested'].includes(
        product.knowledgeStatus,
      ) && (
        <p className={compact ? 'knowledge-help-compact' : undefined}>
          {compact ? (
            '资料不足，仍可提问；具体操作需适用资料。'
          ) : (
            <>
              仍可提问，资料不足时会提供明确标注的低风险通用建议；具体参数与操作需要适用资料。
            </>
          )}
        </p>
      )}
      {![
        'ready',
        'supplemental_ready',
        'partial',
        'failed',
        'not_requested',
      ].includes(product.knowledgeStatus) && (
        <Progress percent={job?.progress.percent || 0} status="active" />
      )}
      {job?.lastError &&
        (compact ? (
          <details className="knowledge-error">
            <summary>查看失败原因</summary>
            <p>{job.lastError}</p>
          </details>
        ) : (
          <Alert
            title={job.lastError}
            type={product.knowledgeStatus === 'failed' ? 'error' : 'warning'}
            showIcon
          />
        ))}
    </div>
  );
}
