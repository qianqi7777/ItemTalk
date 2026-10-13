import { useCallback, useState, type ReactNode } from 'react';
import { Alert, Button, Spin } from 'antd';
import { BookOpen } from 'lucide-react';
import { ApiError, api, newKey, post } from '../../services/api';
import { usePolling } from '../../hooks/usePolling';
import { MessageContent } from '../../components/MessageContent';
import { CitationCard } from '../../components/CitationCard';
import type { Manual, ManualSection, ProductInstance } from '../../types/api';

export function ManualPanel({ product }: { product: ProductInstance }) {
  const [open, setOpen] = useState(false);
  const [manual, setManual] = useState<Manual>();
  const [selected, setSelected] = useState('');
  const [loading, setLoading] = useState(false);
  const [missing, setMissing] = useState(false);
  const [error, setError] = useState('');
  const load = useCallback(async () => {
    try {
      const value = await api<Manual>(
        `/products/${product.productModelId}/manual?instanceId=${product.id}`,
      );
      setManual(value);
      setMissing(false);
      setSelected((id) =>
        value.sections.some((section) => section.id === id)
          ? id
          : value.sections[0]?.id || '',
      );
      setError('');
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) setMissing(true);
      else setError(error instanceof Error ? error.message : '说明书加载失败');
    }
  }, [product.productModelId, product.id]);
  usePolling(
    load,
    open &&
      !!manual &&
      (['pending', 'generating'].includes(manual.status) ||
        (!!manual.latestBuild &&
          ['pending', 'generating'].includes(manual.latestBuild.status))),
  );
  const toggle = async () => {
    setOpen(!open);
    if (!open) {
      setLoading(true);
      await load();
      setLoading(false);
    }
  };
  const generate = async () => {
    setLoading(true);
    setError('');
    try {
      await post(
        `/products/${product.productModelId}/manual-jobs`,
        {},
        newKey(),
        manual?.latestBuild &&
          ['failed', 'partial'].includes(manual.latestBuild.status)
          ? manual.latestBuild.jobId || undefined
          : manual?.status === 'failed' || manual?.status === 'partial'
            ? manual.jobId || undefined
            : undefined,
      );
      await load();
    } catch (error) {
      setError(error instanceof Error ? error.message : '生成请求失败');
    } finally {
      setLoading(false);
    }
  };
  const current = manual?.sections.find((section) => section.id === selected);
  const chapterTree = (parentId: string | null): ReactNode => {
    const rows =
      manual?.sections
        .filter((section) => section.parentId === parentId)
        .sort((a, b) => a.sortOrder - b.sortOrder) || [];
    return rows.length ? (
      <ul>
        {rows.map((section: ManualSection) => (
          <li key={section.id}>
            <button
              type="button"
              aria-pressed={selected === section.id}
              onClick={() => setSelected(section.id)}
            >
              {section.title}
            </button>
            {chapterTree(section.id)}
          </li>
        ))}
      </ul>
    ) : null;
  };
  return (
    <section className="manual-panel">
      <Button
        icon={<BookOpen size={17} aria-hidden="true" />}
        onClick={() => void toggle()}
        aria-expanded={open}
      >
        使用说明书
      </Button>
      {open && (
        <div className="manual-content">
          {loading && <Spin aria-label="加载说明书" />}
          {error && <Alert title={error} type="error" showIcon />}
          {missing && (
            <div className="inline-actions">
              <p>说明书尚未生成，将只使用已核验的资料。</p>
              <Button loading={loading} onClick={() => void generate()}>
                生成说明书
              </Button>
            </div>
          )}
          {manual?.latestBuild && (
            <Alert
              type="info"
              showIcon
              title={`版本 ${manual.latestBuild.version} ${['pending', 'generating'].includes(manual.latestBuild.status) ? '正在生成' : '尚未完整就绪'}，仍可阅读当前版本。`}
            />
          )}
          {manual?.latestBuild &&
            ['failed', 'partial'].includes(manual.latestBuild.status) && (
              <Button loading={loading} onClick={() => void generate()}>
                重试新版本
              </Button>
            )}
          {manual && (
            <>
              <p>
                说明书版本 {manual.version}
                {['pending', 'generating'].includes(manual.status)
                  ? ' · 正在生成章节…'
                  : ''}
              </p>
              {['failed', 'partial'].includes(manual.status) && (
                <Button onClick={() => void generate()} loading={loading}>
                  重试未完成章节
                </Button>
              )}
              <div className="manual-layout">
                <nav aria-label="说明书章节">{chapterTree(null)}</nav>
                {current && (
                  <article>
                    <h3>{current.title}</h3>
                    {current.citations.some(
                      (citation) => citation.verificationStatus !== 'verified',
                    ) && (
                      <Alert
                        title="本章部分来源已失效，请等待新版本，不要依此执行操作。"
                        type="warning"
                        showIcon
                      />
                    )}
                    {current.body ? (
                      <MessageContent
                        content={current.body}
                        citations={current.citations}
                      />
                    ) : (
                      <p>本章正在根据核验资料生成。</p>
                    )}
                    <div className="citations">
                      {current.citations.map((citation) => (
                        <CitationCard
                          key={citation.knowledgeChunkId}
                          citation={citation}
                        />
                      ))}
                    </div>
                  </article>
                )}
              </div>
            </>
          )}
        </div>
      )}
    </section>
  );
}
