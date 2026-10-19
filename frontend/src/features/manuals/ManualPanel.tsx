import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { Alert, Button, Spin } from 'antd';
import { BookOpen } from 'lucide-react';
import { ApiError, api, newKey, post } from '../../services/api';
import { usePolling } from '../../hooks/usePolling';
import { MessageContent } from '../../components/MessageContent';
import { CitationCard } from '../../components/CitationCard';
import type { Manual, ManualSection, ProductInstance } from '../../types/api';

export function ManualPanel({ product }: { product: ProductInstance }) {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<'mine' | 'public'>('mine');
  const modeRef = useRef<'mine' | 'public'>('mine');
  const loadSequence = useRef(0);
  const [manual, setManual] = useState<Manual>();
  const [selected, setSelected] = useState('');
  const [loading, setLoading] = useState(false);
  const [missing, setMissing] = useState(false);
  const [error, setError] = useState('');
  const load = useCallback(
    async (requestedMode: 'mine' | 'public' = modeRef.current) => {
      const sequence = ++loadSequence.current;
      try {
        const value = await api<Manual>(
          `/products/${product.productModelId}/manual${requestedMode === 'mine' ? `?instanceId=${product.id}` : ''}`,
        );
        if (sequence !== loadSequence.current) return;
        setManual(value);
        setMissing(false);
        setSelected((id) =>
          value.sections.some((section) => section.id === id)
            ? id
            : value.sections[0]?.id || '',
        );
        setError('');
      } catch (error) {
        if (sequence !== loadSequence.current) return;
        if (error instanceof ApiError && [401, 404].includes(error.status)) {
          setManual(undefined);
          setSelected('');
          setMissing(true);
        } else
          setError(error instanceof Error ? error.message : '说明书加载失败');
      }
    },
    [product.productModelId, product.id],
  );
  useEffect(() => {
    const changed = () => {
      if (open) void load();
    };
    window.addEventListener('itemtalk-memory-change', changed);
    return () => window.removeEventListener('itemtalk-memory-change', changed);
  }, [open, load]);
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
  const generate = async (
    visibility: 'public' | 'private' = manual?.visibility || 'public',
  ) => {
    setLoading(true);
    setError('');
    try {
      await post(
        `/products/${product.productModelId}/manual-jobs`,
        visibility === 'private' ? { userProductInstanceId: product.id } : {},
        newKey(),
        !manual?.latestBuild?.inputsStale &&
          manual?.visibility === visibility &&
          manual?.latestBuild &&
          ['failed', 'partial'].includes(manual.latestBuild.status)
          ? manual.latestBuild.jobId || undefined
          : !manual?.inputsStale &&
              manual?.visibility === visibility &&
              (manual?.status === 'failed' || manual?.status === 'partial')
            ? manual.jobId || undefined
            : undefined,
      );
      setMode(visibility === 'private' ? 'mine' : 'public');
      modeRef.current = visibility === 'private' ? 'mine' : 'public';
      await load(visibility === 'private' ? 'mine' : 'public');
    } catch (error) {
      setError(error instanceof Error ? error.message : '生成请求失败');
    } finally {
      setLoading(false);
    }
  };
  const fillChapter = async () => {
    if (!manual || !selected) return;
    setLoading(true);
    try {
      await post(
        `/manuals/${manual.id}/section-jobs`,
        { sectionIds: [selected] },
        newKey(),
      );
      await load();
      setError('');
    } catch (error) {
      setError(error instanceof Error ? error.message : '章节补全失败');
    } finally {
      setLoading(false);
    }
  };
  const generating =
    ['pending', 'generating'].includes(manual?.status || '') ||
    ['pending', 'generating'].includes(manual?.latestBuild?.status || '');
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
      {!open && <p className="tool-description">查阅章节与核验来源</p>}
      {open && (
        <div className="manual-content">
          <div className="inline-actions">
            <Button
              disabled={loading}
              aria-pressed={mode === 'mine'}
              onClick={() => {
                setMode('mine');
                modeRef.current = 'mine';
                void load('mine');
              }}
            >
              我的说明书
            </Button>
            <Button
              disabled={loading}
              aria-pressed={mode === 'public'}
              onClick={() => {
                setMode('public');
                modeRef.current = 'public';
                void load('public');
              }}
            >
              通用说明书
            </Button>
            {manual?.visibility !== 'private' && (
              <Button
                aria-label="生成我的说明书"
                loading={loading}
                disabled={loading}
                onClick={() => void generate('private')}
              >
                生成我的说明书
              </Button>
            )}
          </div>
          {loading && <Spin aria-label="加载说明书" />}
          {error && <Alert title={error} type="error" showIcon />}
          {missing && (
            <div className="inline-actions">
              <p>说明书尚未生成，将只使用已核验的资料。</p>
              <Button
                loading={loading}
                onClick={() =>
                  void generate(mode === 'mine' ? 'private' : 'public')
                }
              >
                {mode === 'mine' ? '生成个人版本' : '生成通用版本'}
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
                {manual.visibility === 'private'
                  ? '实例专属 · 仅本人可见'
                  : '通用核验资料'}
              </p>
              {manual.inputsStale && (
                <div className="inline-actions">
                  <Alert
                    title={
                      manual.contextStale
                        ? '个人设置已变化，旧章节仅作历史参考。'
                        : '核验资料有更新，可生成新版本。'
                    }
                    type="warning"
                    showIcon
                  />
                  <Button
                    loading={loading}
                    disabled={generating}
                    onClick={() => void generate()}
                  >
                    更新说明书
                  </Button>
                </div>
              )}
              {!!manual.personalContext?.length && (
                <aside aria-label="已确认的个人设置">
                  <strong>你已确认的设置与偏好（不是官方证据）</strong>
                  <ul>
                    {manual.personalContext.map((value, index) => (
                      <li key={index}>{value.content}</li>
                    ))}
                  </ul>
                </aside>
              )}
              {manual.coverage && (
                <p>
                  核验片段引用 {manual.coverage.citedChunks}/
                  {manual.coverage.totalChunks}
                </p>
              )}
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
                    {current.reused && <p>沿用已核验章节。</p>}
                    {current.generationStatus !== 'ready' &&
                      !generating &&
                      !manual.inputsStale && (
                        <Button
                          loading={loading}
                          onClick={() => void fillChapter()}
                        >
                          补全这一章
                        </Button>
                      )}
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
                    {!!current.personalNotes?.length && (
                      <aside aria-label="本章个人补充">
                        <strong>结合个人设置的补充（非官方证据）</strong>
                        <ul>
                          {current.personalNotes.map((note, index) => (
                            <li key={index}>{note.text}</li>
                          ))}
                        </ul>
                      </aside>
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
