import { ShieldCheck, ExternalLink } from 'lucide-react';
import type { Citation } from '../types/api';

/** 引用只使用服务端生成的证据元数据，不根据模型正文拼接 URL。 */
export function CitationCard({ citation }: { citation: Citation }) {
  const safeUrl = /^https:\/\//i.test(citation.url) ? citation.url : undefined;
  return (
    <article className="citation" data-status={citation.verificationStatus}>
      <div className="citation-heading">
        <ShieldCheck size={16} aria-hidden="true" />
        <span>
          {citation.verificationStatus === 'verified'
            ? '已核验来源'
            : '来源核验已变更'}
        </span>
        <span className="citation-rank">
          来源 {String(citation.rank).padStart(2, '0')}
        </span>
      </div>
      <a href={safeUrl} target="_blank" rel="noopener noreferrer">
        {citation.title}
        <ExternalLink size={14} aria-hidden="true" />
      </a>
      <small>
        第 {citation.pageStart}
        {citation.pageEnd !== citation.pageStart
          ? `–${citation.pageEnd}`
          : ''}{' '}
        页
        {citation.verifiedAt
          ? ` · 核验于 ${new Date(citation.verifiedAt).toLocaleDateString('zh-CN')}`
          : ''}
      </small>
    </article>
  );
}
