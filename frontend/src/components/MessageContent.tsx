import { Fragment, type ReactNode } from 'react';
import type { Citation } from '../types/api';

type Block = {
  kind: 'paragraph' | 'ordered' | 'unordered' | 'heading' | 'code';
  lines: string[];
  start?: number;
};

/** 只渲染基础文本排版；原始 HTML 和模型生成的链接保留为普通文字。 */
export function MessageContent({
  content,
  citations = [],
}: {
  content: string;
  citations?: Pick<Citation, 'rank'>[];
}) {
  const ranks = new Set(citations.map((citation) => citation.rank));
  const inline = (text: string): ReactNode =>
    text
      .split(/(\*\*[^*\n]+\*\*|`[^`\n]+`|\[\d{1,3}\])/g)
      .map((part, index) => {
        if (part.startsWith('**') && part.endsWith('**'))
          return <strong key={index}>{part.slice(2, -2)}</strong>;
        if (part.startsWith('`') && part.endsWith('`'))
          return <code key={index}>{part.slice(1, -1)}</code>;
        const rank = /^\[(\d+)\]$/.exec(part);
        if (rank && ranks.has(Number(rank[1])))
          return (
            <sup className="inline-citation" key={index}>
              {rank[1]}
            </sup>
          );
        return <Fragment key={index}>{part}</Fragment>;
      });
  const blocks: Block[] = [];
  let active: Block | undefined;
  let fenced: Block | undefined;
  for (const line of content.replace(/\r\n/g, '\n').split('\n')) {
    if (/^\s*```/.test(line)) {
      if (fenced) fenced = undefined;
      else {
        fenced = { kind: 'code', lines: [] };
        blocks.push(fenced);
      }
      active = undefined;
      continue;
    }
    if (fenced) {
      fenced.lines.push(line);
      continue;
    }
    if (!line.trim()) {
      active = undefined;
      continue;
    }
    const list = /^\s*(?:(\d{1,4})[.)]|[-*])\s+(.+)$/.exec(line);
    const heading = /^\s*#{1,4}\s+(.+)$/.exec(line);
    const kind = heading
      ? 'heading'
      : list
        ? list[1]
          ? 'ordered'
          : 'unordered'
        : 'paragraph';
    const text = heading?.[1] || list?.[2] || line;
    if (!active || active.kind !== kind || kind === 'heading') {
      active = {
        kind,
        lines: [],
        start: list?.[1] ? Number(list[1]) : undefined,
      };
      blocks.push(active);
    }
    active.lines.push(text);
  }
  return (
    <div className="message-content">
      {blocks.map((block, index) => {
        if (block.kind === 'code')
          return (
            <pre key={index}>
              <code>{block.lines.join('\n')}</code>
            </pre>
          );
        if (block.kind === 'heading')
          return <h4 key={index}>{inline(block.lines.join('\n'))}</h4>;
        if (block.kind === 'ordered')
          return (
            <ol key={index} start={block.start}>
              {block.lines.map((line, row) => (
                <li key={row}>{inline(line)}</li>
              ))}
            </ol>
          );
        if (block.kind === 'unordered')
          return (
            <ul key={index}>
              {block.lines.map((line, row) => (
                <li key={row}>{inline(line)}</li>
              ))}
            </ul>
          );
        return <p key={index}>{inline(block.lines.join('\n'))}</p>;
      })}
    </div>
  );
}
