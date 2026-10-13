import { Alert, Progress, Tag } from 'antd';
import type { Job, ProductInstance } from '../../types/api';

const knowledgeLabels: Record<string, string> = {
  not_requested: '尚未建立资料',
  queued: '任务已排队',
  researching: '查找官方资料',
  parsing: '解析与核验资料',
  embedding: '建立检索索引',
  ready: '资料已就绪',
  partial: '资料仍待补充',
  failed: '资料构建失败',
};
export function KnowledgeProgress({
  product,
  job,
}: {
  product: ProductInstance;
  job?: Job;
}) {
  const label = knowledgeLabels[product.knowledgeStatus] || '正在处理';
  return (
    <div aria-live="polite">
      <Tag color={product.knowledgeStatus === 'ready' ? 'green' : 'gold'}>
        {label}
      </Tag>
      {!['ready', 'partial', 'failed', 'not_requested'].includes(
        product.knowledgeStatus,
      ) && <Progress percent={job?.progress.percent || 0} status="active" />}
      {job?.lastError && (
        <Alert
          title={job.lastError}
          type={product.knowledgeStatus === 'failed' ? 'error' : 'warning'}
          showIcon
        />
      )}
    </div>
  );
}
