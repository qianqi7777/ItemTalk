import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { Alert, Button, Input, Spin } from 'antd';
import { ArrowUpRight, MessageCircle, Search } from 'lucide-react';
import { api } from '../services/api';
import type { Conversation, ProductInstance } from '../types/api';

type HistoryPageResult = { items: Conversation[]; nextCursor: string | null };

export function HistoryPage() {
  const [query, setQuery] = useState('');
  const [instanceId, setInstanceId] = useState('');
  const [filters, setFilters] = useState({ query: '', instanceId: '' });
  const [products, setProducts] = useState<ProductInstance[]>([]);
  const [items, setItems] = useState<Conversation[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState('');
  const revision = useRef(0);

  const historyPath = useCallback(
    (nextCursor?: string) => {
      const params = new URLSearchParams({ limit: '30' });
      if (filters.query) params.set('q', filters.query);
      if (filters.instanceId) params.set('instanceId', filters.instanceId);
      if (nextCursor) params.set('cursor', nextCursor);
      return `/conversations?${params}`;
    },
    [filters],
  );

  useEffect(() => {
    const current = ++revision.current;
    void Promise.all([
      api<{ items: ProductInstance[] }>('/me/products'),
      api<HistoryPageResult>(historyPath()),
    ])
      .then(([devices, page]) => {
        if (revision.current !== current) return;
        setProducts(devices.items);
        setItems(page.items);
        setCursor(page.nextCursor);
      })
      .catch((error: unknown) => {
        if (revision.current === current)
          setError(error instanceof Error ? error.message : '历史对话加载失败');
      })
      .finally(() => {
        if (revision.current === current) setLoading(false);
      });
    return () => {
      revision.current += 1;
    };
  }, [historyPath]);

  const loadMore = async () => {
    if (!cursor || loading || loadingMore) return;
    const current = ++revision.current;
    setLoadingMore(true);
    setError('');
    try {
      const page = await api<HistoryPageResult>(historyPath(cursor));
      if (current !== revision.current) return;
      setItems((old) => [
        ...old,
        ...page.items.filter(
          (item) => !old.some((existing) => existing.id === item.id),
        ),
      ]);
      setCursor(page.nextCursor);
    } catch (error) {
      if (current === revision.current)
        setError(error instanceof Error ? error.message : '更多对话加载失败');
    } finally {
      if (current === revision.current) setLoadingMore(false);
    }
  };

  const applyFilters = (next: typeof filters) => {
    revision.current += 1;
    setLoading(true);
    setLoadingMore(false);
    setError('');
    setItems([]);
    setCursor(null);
    setFilters(next);
  };

  const filtered = Boolean(filters.query || filters.instanceId);
  return (
    <main className="workspace history-page">
      <header className="page-heading">
        <div>
          <span className="eyebrow">对话记录 / CONVERSATION HISTORY</span>
          <h1>接着上次，继续聊。</h1>
          <p>所有设备的对话都在这里，按最近活跃排序。</p>
        </div>
      </header>
      <form
        className="history-filters"
        onSubmit={(event) => {
          event.preventDefault();
          applyFilters({ query: query.trim(), instanceId });
        }}
      >
        <Input
          aria-label="搜索历史对话"
          placeholder="搜索标题或消息内容"
          maxLength={200}
          prefix={<Search size={17} aria-hidden="true" />}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <select
          aria-label="筛选设备"
          value={instanceId}
          onChange={(event) => setInstanceId(event.target.value)}
        >
          <option value="">所有设备</option>
          {products.map((product) => (
            <option key={product.id} value={product.id}>
              {product.nickname || `${product.brandName} ${product.modelCode}`}
            </option>
          ))}
        </select>
        <Button type="primary" htmlType="submit" aria-label="搜索">
          搜索
        </Button>
      </form>
      {error && (
        <div className="history-error">
          <Alert title={error} type="error" showIcon role="alert" />
          <Button
            aria-label="重试"
            onClick={() => applyFilters({ ...filters })}
          >
            重试
          </Button>
        </div>
      )}
      {loading && (
        <div className="history-empty" role="status">
          <Spin />
          <p>正在加载历史对话…</p>
        </div>
      )}
      {!loading && !error && !items.length && (
        <div className="history-empty">
          <MessageCircle size={38} aria-hidden="true" />
          <h2>{filtered ? '没有找到匹配的对话' : '还没有历史对话'}</h2>
          <p>
            {filtered
              ? '换一个关键词或设备试试。'
              : '在设备工作区开始提问，对话会自动保存在这里。'}
          </p>
          <Link to="/">
            前往设备工作区 <ArrowUpRight size={15} aria-hidden="true" />
          </Link>
        </div>
      )}
      {!loading && items.length > 0 && (
        <div className="history-list" aria-label="历史对话列表">
          {items.map((item) => {
            const device = products.find(
              (product) => product.id === item.userProductInstanceId,
            );
            return (
              <Link
                className="history-card"
                key={item.id}
                to={`/conversations/${encodeURIComponent(item.id)}`}
              >
                <span className="section-icon">
                  <MessageCircle size={21} aria-hidden="true" />
                </span>
                <div className="history-card-content">
                  <h2>{item.title || '未命名会话'}</h2>
                  <span>
                    {device
                      ? device.nickname ||
                        `${device.brandName} ${device.modelCode}`
                      : '设备信息暂不可用'}
                  </span>
                  <time dateTime={item.createdAt}>
                    创建于{' '}
                    {new Date(item.createdAt).toLocaleString('zh-CN', {
                      dateStyle: 'medium',
                      timeStyle: 'short',
                    })}
                  </time>
                </div>
                <ArrowUpRight size={20} aria-hidden="true" />
              </Link>
            );
          })}
        </div>
      )}
      {!loading && cursor && (
        <Button
          className="history-more"
          loading={loadingMore}
          onClick={() => void loadMore()}
        >
          加载更多对话
        </Button>
      )}
    </main>
  );
}
