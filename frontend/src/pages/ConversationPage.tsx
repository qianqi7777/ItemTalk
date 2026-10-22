import { useEffect, useRef, useState } from 'react';
import {
  Link,
  useNavigate,
  useParams,
  useSearchParams,
} from 'react-router-dom';
import { Alert, Button, Spin } from 'antd';
import { ArrowLeft } from 'lucide-react';
import { api } from '../services/api';
import type { Conversation, MessagePage, ProductInstance } from '../types/api';
import { ChatPanel, type RestoredConversation } from './ChatPanel';

type RestoredWorkspace = {
  key: string;
  product: ProductInstance;
  conversation?: RestoredConversation;
};

/** 先验证会话归属和设备，再展示聊天；服务端仍逐次验证资源权限。 */
export function ConversationPage() {
  const { conversationId } = useParams();
  const [search] = useSearchParams();
  const instanceId = search.get('instanceId') || '';
  const navigate = useNavigate();
  const [workspace, setWorkspace] = useState<RestoredWorkspace>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const publishedTarget = useRef<string | null>(null);

  useEffect(() => {
    const target = `${conversationId || ''}:${instanceId}`;
    // 聊天组件自身切换会话时只更新地址，避免重挂载中断正在发送的请求。
    if (publishedTarget.current === target) {
      publishedTarget.current = null;
      return;
    }
    publishedTarget.current = null;
    let active = true;
    setLoading(true);
    setError('');
    const restore = async () => {
      const conversation = conversationId
        ? await api<Conversation>(
            `/conversations/${encodeURIComponent(conversationId)}`,
          )
        : undefined;
      const deviceId = conversation?.userProductInstanceId || instanceId;
      if (!deviceId) throw new Error('请选择要恢复的历史对话');
      const [product, page] = await Promise.all([
        api<ProductInstance>(`/me/products/${encodeURIComponent(deviceId)}`),
        conversationId
          ? api<MessagePage>(
              `/conversations/${encodeURIComponent(conversationId)}/messages`,
            )
          : Promise.resolve(undefined),
      ]);
      if (active)
        setWorkspace({
          key: `${target}:${retry}`,
          product,
          conversation:
            conversationId && page
              ? { id: conversationId, ...page }
              : undefined,
        });
    };
    void restore()
      .catch((error: unknown) => {
        if (active)
          setError(error instanceof Error ? error.message : '历史对话恢复失败');
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [conversationId, instanceId, retry]);

  const updateAddress = (id: string) => {
    if (!workspace) return;
    const nextInstance = id ? '' : workspace.product.id;
    if (id === (conversationId || '') && nextInstance === instanceId) return;
    publishedTarget.current = `${id}:${nextInstance}`;
    navigate(
      id
        ? `/conversations/${encodeURIComponent(id)}`
        : `/conversations?instanceId=${encodeURIComponent(nextInstance)}`,
      { replace: true },
    );
  };

  return (
    <main className="workspace conversation-page">
      <Link className="history-back" to="/history">
        <ArrowLeft size={17} aria-hidden="true" />
        全部历史对话
      </Link>
      {loading ? (
        <div className="history-empty" role="status">
          <Spin />
          <p>正在恢复历史对话…</p>
        </div>
      ) : error ? (
        <div className="history-error">
          <Alert title={error} type="error" showIcon role="alert" />
          <Button
            aria-label="重试"
            onClick={() => setRetry((value) => value + 1)}
          >
            重试
          </Button>
        </div>
      ) : (
        workspace && (
          <>
            <header className="page-heading">
              <div>
                <span className="eyebrow">
                  继续对话 / CONTINUE CONVERSATION
                </span>
                <h1>
                  {workspace.product.nickname ||
                    `${workspace.product.brandName} ${workspace.product.modelCode}`}
                </h1>
                <p>接着之前的问题，继续了解你的设备。</p>
              </div>
            </header>
            <ChatPanel
              key={workspace.key}
              product={workspace.product}
              initialConversation={workspace.conversation}
              onConversationChange={updateAddress}
            />
          </>
        )
      )}
    </main>
  );
}
