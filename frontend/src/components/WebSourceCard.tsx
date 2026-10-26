import { ExternalLink, Globe } from 'lucide-react';
import type { WebSource } from '../types/api';

export function WebSourceCard({ source }: { source: WebSource }) {
  const labels = {
    official: '官方来源',
    third_party: '第三方资料 · 非官方核验',
    unverified_official: '官网身份待核验',
  };
  const safeUrl = /^https:\/\//i.test(source.url) ? source.url : undefined;
  return (
    <article className="citation web-source" data-kind={source.sourceKind}>
      <div className="citation-heading">
        <Globe size={16} aria-hidden="true" />
        <span>{labels[source.sourceKind]}</span>
        <span className="citation-rank">来源 {source.rank}</span>
      </div>
      <a href={safeUrl} target="_blank" rel="noopener noreferrer">
        {source.title}
        <ExternalLink size={14} aria-hidden="true" />
      </a>
      <small>
        {source.pageStart !== null
          ? `第 ${source.pageStart}${source.pageEnd !== source.pageStart ? `–${source.pageEnd}` : ''} 页 · `
          : ''}
        检索于 {new Date(source.fetchedAt).toLocaleString('zh-CN')}
      </small>
    </article>
  );
}
