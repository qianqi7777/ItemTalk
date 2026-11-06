import { useEffect, useState } from 'react';
import { Alert, Button, Spin } from 'antd';
import { Brain } from 'lucide-react';
import { api, post } from '../../services/api';
import type { Memory } from '../../types/api';

export function MemoryPanel({
  instanceId,
  embedded = false,
}: {
  instanceId: string;
  embedded?: boolean;
}) {
  // 面板展示当前产品设置和本人全局偏好，打开/刷新都不会自动确认候选。
  const [open, setOpen] = useState(embedded);
  const [items, setItems] = useState<Memory[]>([]);
  const [busy, setBusy] = useState(embedded);
  const [error, setError] = useState('');
  useEffect(() => {
    if (!embedded) return;
    let active = true;
    void api<{ items: Memory[] }>(`/me/memories?instanceId=${instanceId}`)
      .then((page) => {
        if (active) setItems(page.items);
      })
      .catch((error: unknown) => {
        if (active)
          setError(error instanceof Error ? error.message : '记忆加载失败');
      })
      .finally(() => {
        if (active) setBusy(false);
      });
    return () => {
      active = false;
    };
  }, [embedded, instanceId]);
  const load = async () => {
    const page = await api<{ items: Memory[] }>(
      `/me/memories?instanceId=${instanceId}`,
    );
    setItems(page.items);
  };
  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError('');
    try {
      await action();
    } catch (error) {
      setError(error instanceof Error ? error.message : '记忆操作失败');
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="memory-panel">
      <Button
        hidden={embedded}
        aria-expanded={open}
        disabled={busy}
        icon={<Brain size={16} aria-hidden="true" />}
        onClick={() => {
          setOpen(!open);
          if (!open) void run(load);
        }}
      >
        我的记忆
      </Button>
      {!open && <p className="tool-description">确认并管理个人使用偏好</p>}
      {open && (
        <div className="memory-content">
          <p>
            这是你的偏好或个人设置，不是产品官方说明。只有你确认后，才会用于后续回答。
          </p>
          {busy && <Spin aria-label="读取记忆" />}
          {error && <Alert title={error} type="error" showIcon />}
          {!items.length && !busy && (
            <p>
              暂无记忆。提问时可以明确说“请记住我的偏好”，之后在这里确认候选。
            </p>
          )}
          <Button size="small" disabled={busy} onClick={() => void run(load)}>
            刷新记忆
          </Button>
          {items.map((memory) => (
            <article key={memory.id} className="memory-card">
              <small>
                {memory.scope === 'global' ? '全局偏好' : '当前产品'} ·{' '}
                {memory.consentStatus === 'pending' ? '待你确认' : '已确认'}
              </small>
              <p>{memory.content}</p>
              {memory.expiresAt && (
                <small>
                  有效期至{' '}
                  {new Date(memory.expiresAt).toLocaleDateString('zh-CN')}
                </small>
              )}
              <div className="inline-actions">
                {memory.consentStatus === 'pending' && (
                  <Button
                    size="small"
                    disabled={busy}
                    onClick={() =>
                      void run(async () => {
                        await post(`/me/memories/${memory.id}/confirm`, {});
                        window.dispatchEvent(
                          new Event('itemtalk-memory-change'),
                        );
                        await load();
                      })
                    }
                  >
                    确认记住
                  </Button>
                )}
                <Button
                  size="small"
                  danger
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      await api(`/me/memories/${memory.id}`, {
                        method: 'DELETE',
                      });
                      window.dispatchEvent(new Event('itemtalk-memory-change'));
                      await load();
                    })
                  }
                >
                  {memory.consentStatus === 'pending' ? '不记住' : '删除记忆'}
                </Button>
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
