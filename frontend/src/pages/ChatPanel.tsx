import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Input, Spin, Upload } from 'antd';
import {
  ArrowUp,
  Square,
  MessageCircle,
  Sparkles,
  ShieldCheck,
  ArrowUpRight,
  ImagePlus,
} from 'lucide-react';
import { api, newKey, post, streamAnswer, upload } from '../services/api';
import { CitationCard } from '../components/CitationCard';
import { MessageContent } from '../components/MessageContent';
import type {
  Citation,
  Conversation,
  Message,
  MessagePage,
  ProductInstance,
  Run,
  Asset,
} from '../types/api';
import { MessageFeedback } from '../features/feedback/MessageFeedback';
import { SpeechInput } from '../features/speech/SpeechInput';
import { DeleteAction } from '../components/DeleteAction';

type QuestionDraft = { content: string; assets: Asset[] };

function QuestionImages({ assets }: { assets: Asset[] }) {
  return (
    <div className="chat-attachments">
      {assets
        .filter((asset) => asset.mimeType.startsWith('image/'))
        .map((asset, index) =>
          asset.readUrl ? (
            <a
              key={asset.id}
              href={asset.readUrl}
              target="_blank"
              rel="noreferrer"
            >
              <img
                src={asset.readUrl}
                alt={`提问图片 ${index + 1}`}
                loading="lazy"
                referrerPolicy="no-referrer"
              />
            </a>
          ) : (
            <span key={asset.id}>提问图片 {index + 1}</span>
          ),
        )}
    </div>
  );
}

export function ChatPanel({
  product,
  onBusyChange,
}: {
  product: ProductInstance;
  onBusyChange?: (busy: boolean) => void;
}) {
  const [conversation, setConversation] = useState('');
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [photos, setPhotos] = useState<Asset[]>([]);
  const [uploading, setUploading] = useState(false);
  const uploadPending = useRef(false);
  const mounted = useRef(true);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [history, setHistory] = useState<Conversation[]>([]);
  const [historyQuery, setHistoryQuery] = useState('');
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [historyCursor, setHistoryCursor] = useState<string | null>(null);
  const [messageCursor, setMessageCursor] = useState<string | null>(null);
  useEffect(() => {
    onBusyChange?.(busy || loadingHistory || uploading);
  }, [busy, loadingHistory, uploading, onBusyChange]);
  const [error, setError] = useState('');
  const [draft, setDraft] = useState('');
  const [citations, setCitations] = useState<Citation[]>([]);
  const [retryDraft, setRetryDraft] = useState<QuestionDraft>();
  const [speechEnabled, setSpeechEnabled] = useState(false);
  useEffect(() => {
    void api<{ speechToText: boolean }>('/capabilities')
      .then((value) => setSpeechEnabled(value.speechToText))
      .catch(() => {});
  }, []);
  const controller = useRef<AbortController | null>(null);
  const [runId, setRunId] = useState('');
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (messages.length || draft)
      end.current?.scrollIntoView?.({ behavior: 'smooth' });
  }, [messages, draft]);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      controller.current?.abort();
    };
  }, []);

  const attachPhoto = async (file: File) => {
    // 上传期间禁止产品/会话切换；组件卸载后不接收旧上传的迟到结果。
    if (uploadPending.current || busy || loadingHistory) return;
    if (photos.length >= 10) {
      setError('每条问题最多附带 10 张图片');
      return;
    }
    if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) {
      setError('请选择 JPEG、PNG 或 WebP 图片');
      return;
    }
    if (!file.size || file.size > 10 * 1024 * 1024) {
      setError('图片大小须在 1 字节至 10 MB 之间');
      return;
    }
    uploadPending.current = true;
    setUploading(true);
    setError('');
    try {
      const asset = await upload(file);
      if (mounted.current) setPhotos((old) => [...old, asset]);
    } catch (error) {
      if (mounted.current)
        setError(
          error instanceof Error ? error.message : '图片上传失败，请重试',
        );
    } finally {
      uploadPending.current = false;
      if (mounted.current) setUploading(false);
    }
  };

  const loadHistory = async (append = false) => {
    if (uploadPending.current) return;
    setLoadingHistory(true);
    try {
      const page = await api<{
        items: Conversation[];
        nextCursor: string | null;
      }>(
        `/conversations?instanceId=${product.id}&q=${encodeURIComponent(historyQuery)}${append && historyCursor ? `&cursor=${historyCursor}` : ''}`,
      );
      setHistory((old) =>
        append ? [...old, ...(page.items || [])] : page.items || [],
      );
      setHistoryCursor(page.nextCursor || null);
    } catch (error) {
      setError(error instanceof Error ? error.message : '历史会话加载失败');
    } finally {
      setLoadingHistory(false);
    }
  };
  const chooseConversation = async (id: string) => {
    if (uploadPending.current) return;
    // 恢复服务端会话ID与消息页，继续提问时沿用原会话，避免历史断成两段。
    setLoadingHistory(true);
    setError('');
    try {
      const page = await api<MessagePage>(`/conversations/${id}/messages`);
      setConversation(id);
      setMessages(page.items);
      setMessageCursor(page.nextCursor);
      setRunId('');
      setRetryDraft(undefined);
      setPhotos([]);
      setHistoryOpen(false);
    } catch (error) {
      setError(error instanceof Error ? error.message : '会话恢复失败');
    } finally {
      setLoadingHistory(false);
    }
  };
  const loadOlder = async () => {
    setLoadingHistory(true);
    try {
      const page = await api<MessagePage>(
        `/conversations/${conversation}/messages?cursor=${messageCursor}`,
      );
      setMessages((old) => [...page.items, ...old]);
      setMessageCursor(page.nextCursor);
    } catch (error) {
      setError(error instanceof Error ? error.message : '消息加载失败');
    } finally {
      setLoadingHistory(false);
    }
  };

  const send = async (question = input, retry = false, assets = photos) => {
    if (
      (!question.trim() && !assets.length) ||
      busy ||
      loadingHistory ||
      uploadPending.current
    )
      return;
    const sent: QuestionDraft = { content: question, assets: [...assets] };
    setBusy(true);
    setError('');
    setDraft('');
    setCitations([]);
    if (!retry) {
      setInput('');
      setPhotos([]);
      setRetryDraft(undefined);
    }
    const previous = retry ? runId : undefined;
    if (!retry) setRunId('');
    try {
      let id = conversation;
      if (!id) {
        const response = await post<{ id: string }>('/conversations', {
          userProductInstanceId: product.id,
        });
        id = response.id;
        setConversation(id);
      }
      controller.current = new AbortController();
      if (!retry)
        setMessages((current) => [
          ...current,
          {
            id: newKey(),
            role: 'user',
            content: question,
            citations: [],
            assets,
          },
        ]);
      const run = await streamAnswer(
        id,
        question,
        {
          status: (id) => {
            setRunId(id);
          },
          token: (text) => setDraft((current) => current + text),
          citation: (citation) =>
            setCitations((current) => [...current, citation]),
        },
        controller.current.signal,
        newKey(),
        previous,
        assets.map((asset) => asset.id),
      );
      if (run.message) {
        setMessages((current) => [...current, run.message!]);
        setRetryDraft(undefined);
      } else if (run.status !== 'completed') {
        setRetryDraft(sent);
        setError(
          `回答${run.status === 'cancelled' ? '已停止' : '中断'}，可以重试。`,
        );
      }
    } catch (error) {
      setError(
        error instanceof Error && error.name !== 'AbortError'
          ? error.message
          : '回答已停止或连接中断',
      );
      setRetryDraft(sent);
    } finally {
      setBusy(false);
      setDraft('');
      setCitations([]);
    }
  };
  const stop = async () => {
    if (conversation && runId)
      await post(
        `/conversations/${conversation}/runs/${runId}/cancel`,
        {},
      ).catch(() => {});
    controller.current?.abort();
  };
  const recover = async () => {
    try {
      const run = await api<Run>(
        `/conversations/${conversation}/runs/${runId}`,
      );
      if (run.message) {
        setMessages((current) => [
          ...current.filter((message) => message.id !== run.message!.id),
          run.message!,
        ]);
        setError('');
        setRetryDraft(undefined);
      } else setError(run.lastError || `当前回答状态：${run.status}`);
    } catch (error) {
      setError(error instanceof Error ? error.message : '查询失败');
    }
  };
  return (
    <section className="chat-panel">
      <div className="section-heading">
        <span className="section-icon">
          <MessageCircle size={20} aria-hidden="true" />
        </span>
        <div>
          <h2>和设备聊聊</h2>
          <p>你来提问，我来查阅核验资料。</p>
        </div>
        <span className="chat-trust">
          <ShieldCheck size={13} aria-hidden="true" />
          依据资料回答
        </span>
      </div>
      <div className="conversation-toolbar inline-actions">
        {conversation && (
          <DeleteAction
            path={`/conversations/${conversation}`}
            label="删除会话"
            explanation="当前会话、反馈和由它产生的记忆将删除。仍被其他内容引用的照片会保留。"
            disabled={busy || loadingHistory || uploading}
            onBusyChange={setBusy}
            onDeleted={() => {
              setConversation('');
              setMessages([]);
              setMessageCursor(null);
              setRunId('');
              setRetryDraft(undefined);
              setPhotos([]);
              setHistory((old) =>
                old.filter((item) => item.id !== conversation),
              );
              setDraft('');
              setCitations([]);
              setError('');
            }}
          />
        )}
        <Button
          disabled={busy || loadingHistory || uploading}
          onClick={() => {
            setHistoryOpen(!historyOpen);
            if (!historyOpen) void loadHistory();
          }}
        >
          历史会话
        </Button>
        <Button
          disabled={busy || loadingHistory || uploading}
          onClick={() => {
            setConversation('');
            setMessages([]);
            setMessageCursor(null);
            setRunId('');
            setRetryDraft(undefined);
            setPhotos([]);
            setError('');
            setHistoryOpen(false);
          }}
        >
          新建会话
        </Button>
      </div>
      {historyOpen && (
        <aside className="conversation-history" aria-label="历史会话">
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void loadHistory();
            }}
          >
            <Input
              aria-label="搜索历史会话"
              value={historyQuery}
              onChange={(event) => setHistoryQuery(event.target.value)}
              placeholder="搜索标题或问题"
            />
            <Button
              htmlType="submit"
              disabled={busy || uploading}
              loading={loadingHistory}
            >
              搜索
            </Button>
          </form>
          {!history.length && !loadingHistory && <p>还没有历史会话。</p>}
          {history.map((item) => (
            <Button
              key={item.id}
              disabled={busy || loadingHistory || uploading}
              onClick={() => void chooseConversation(item.id)}
            >
              {item.title || '未命名会话'}
            </Button>
          ))}
          {historyCursor && (
            <Button
              disabled={busy || uploading}
              loading={loadingHistory}
              onClick={() => void loadHistory(true)}
            >
              更多会话
            </Button>
          )}
        </aside>
      )}
      <div className="messages" aria-live="polite">
        {messageCursor && (
          <Button
            disabled={busy || uploading}
            loading={loadingHistory}
            onClick={() => void loadOlder()}
          >
            加载更早消息
          </Button>
        )}
        {!messages.length && (
          <div className="chat-empty">
            <span className="assistant-orb">
              <Sparkles size={30} strokeWidth={1.4} aria-hidden="true" />
            </span>
            <h3>关于 {product.modelCode}，想了解什么？</h3>
            <p>从一个小问题开始，让使用设备变得更简单。</p>
            <div className="starter-questions">
              {['如何开始使用？', '需要注意什么？', '有哪些常用功能？'].map(
                (question) => (
                  <button
                    key={question}
                    type="button"
                    onClick={() => setInput(question)}
                  >
                    {question}
                    <ArrowUpRight size={14} aria-hidden="true" />
                  </button>
                ),
              )}
            </div>
          </div>
        )}
        {messages.map((message) => (
          <article className={`bubble ${message.role}`} key={message.id}>
            <span className="speaker">
              {message.role !== 'user' && (
                <Sparkles size={13} aria-hidden="true" />
              )}
              {message.role === 'user' ? '你' : '识物说'}
            </span>
            {message.role === 'user' ? (
              <>
                {message.content && <p>{message.content}</p>}
                <QuestionImages assets={message.assets || []} />
              </>
            ) : (
              <MessageContent
                content={message.content || ''}
                citations={message.citations}
              />
            )}
            <div className="citations">
              {message.citations.map((citation) => (
                <CitationCard
                  citation={citation}
                  key={citation.knowledgeChunkId}
                />
              ))}
            </div>
            {message.role === 'assistant' && (
              <MessageFeedback message={message} />
            )}
          </article>
        ))}
        {busy && (
          <article className="bubble assistant">
            <span className="speaker">
              <Sparkles size={13} aria-hidden="true" />
              识物说
            </span>
            <div className="answer-draft">
              {draft ? (
                <MessageContent content={draft} citations={citations} />
              ) : (
                <>
                  <Spin size="small" /> 正在检索核验资料…
                </>
              )}
            </div>
            {citations.map((citation) => (
              <CitationCard
                citation={citation}
                key={citation.knowledgeChunkId}
              />
            ))}
          </article>
        )}
        <div ref={end} />
      </div>
      {error && (
        <Alert
          title={error}
          type="warning"
          showIcon
          action={
            <div className="inline-actions">
              {runId && (
                <Button size="small" onClick={() => void recover()}>
                  查询结果
                </Button>
              )}
              {retryDraft && (
                <Button
                  size="small"
                  aria-label="重试回答"
                  disabled={busy || uploading || loadingHistory}
                  onClick={() =>
                    void send(retryDraft.content, !!runId, retryDraft.assets)
                  }
                >
                  重试
                </Button>
              )}
            </div>
          }
        />
      )}
      {speechEnabled && !busy && (
        <SpeechInput
          onConfirm={(text) =>
            setInput((current) => (current ? `${current}\n${text}` : text))
          }
        />
      )}
      <div className="chat-photo-draft">
        <Upload
          accept="image/jpeg,image/png,image/webp"
          showUploadList={false}
          disabled={busy || loadingHistory || uploading || photos.length >= 10}
          beforeUpload={(file) => {
            void attachPhoto(file);
            return false;
          }}
        >
          <Button
            aria-label="添加提问图片"
            icon={<ImagePlus size={16} aria-hidden="true" />}
            loading={uploading}
            disabled={
              busy || loadingHistory || uploading || photos.length >= 10
            }
          >
            添加图片
          </Button>
        </Upload>
        {!!photos.length && (
          <div aria-label="待发送图片" aria-live="polite">
            <QuestionImages assets={photos} />
            {photos.map((photo, index) => (
              <Button
                key={photo.id}
                size="small"
                disabled={busy || uploading}
                aria-label={`移除提问图片 ${index + 1}`}
                onClick={() =>
                  setPhotos((old) => old.filter((item) => item.id !== photo.id))
                }
              >
                移除图片 {index + 1}
              </Button>
            ))}
          </div>
        )}
      </div>
      <form
        className="chat-composer"
        onSubmit={(event) => {
          event.preventDefault();
          void send();
        }}
      >
        <Input.TextArea
          aria-label="提问内容"
          placeholder="输入你的问题，答案会附上资料来源"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          autoSize={{ minRows: 2, maxRows: 5 }}
          maxLength={10000}
          disabled={busy || loadingHistory || uploading}
        />
        {busy ? (
          <Button
            aria-label="停止回答"
            onClick={() => void stop()}
            icon={<Square size={16} />}
          >
            停止
          </Button>
        ) : (
          <Button
            aria-label="发送问题"
            type="primary"
            htmlType="submit"
            disabled={
              (!input.trim() && !photos.length) || loadingHistory || uploading
            }
            icon={<ArrowUp size={18} />}
          >
            发送
          </Button>
        )}
      </form>
      <small className="privacy-note">
        产品图片与问题会发送至模型服务进行处理；证据不足时，助手会请求补充信息。
      </small>
    </section>
  );
}
