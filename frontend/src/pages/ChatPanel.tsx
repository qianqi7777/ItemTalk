import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Input, Spin } from 'antd';
import {
  ArrowUp,
  Square,
  MessageCircle,
  Sparkles,
  ShieldCheck,
  ArrowUpRight,
} from 'lucide-react';
import { api, newKey, post, streamAnswer } from '../services/api';
import { CitationCard } from '../components/CitationCard';
import { MessageContent } from '../components/MessageContent';
import type {
  Citation,
  Conversation,
  Message,
  MessagePage,
  ProductInstance,
  Run,
} from '../types/api';
import { MessageFeedback } from '../features/feedback/MessageFeedback';
import { SpeechInput } from '../features/speech/SpeechInput';
import { DeleteAction } from '../components/DeleteAction';

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
  const [historyOpen, setHistoryOpen] = useState(false);
  const [history, setHistory] = useState<Conversation[]>([]);
  const [historyQuery, setHistoryQuery] = useState('');
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [historyCursor, setHistoryCursor] = useState<string | null>(null);
  const [messageCursor, setMessageCursor] = useState<string | null>(null);
  useEffect(() => {
    onBusyChange?.(busy || loadingHistory);
  }, [busy, loadingHistory, onBusyChange]);
  const [error, setError] = useState('');
  const [draft, setDraft] = useState('');
  const [citations, setCitations] = useState<Citation[]>([]);
  const [retryInput, setRetryInput] = useState('');
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
  useEffect(() => () => controller.current?.abort(), []);

  const loadHistory = async (append = false) => {
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
    // 恢复服务端会话ID与消息页，继续提问时沿用原会话，避免历史断成两段。
    setLoadingHistory(true);
    setError('');
    try {
      const page = await api<MessagePage>(`/conversations/${id}/messages`);
      setConversation(id);
      setMessages(page.items);
      setMessageCursor(page.nextCursor);
      setRunId('');
      setRetryInput('');
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

  const send = async (question = input, retry = false) => {
    if (!question.trim() || busy || loadingHistory) return;
    setBusy(true);
    setError('');
    setDraft('');
    setCitations([]);
    setInput('');
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
          { id: newKey(), role: 'user', content: question, citations: [] },
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
      );
      if (run.message) setMessages((current) => [...current, run.message!]);
      else if (run.status !== 'completed') {
        setRetryInput(question);
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
      setRetryInput(question);
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
            disabled={busy || loadingHistory}
            onBusyChange={setBusy}
            onDeleted={() => {
              setConversation('');
              setMessages([]);
              setMessageCursor(null);
              setRunId('');
              setRetryInput('');
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
          disabled={busy || loadingHistory}
          onClick={() => {
            setHistoryOpen(!historyOpen);
            if (!historyOpen) void loadHistory();
          }}
        >
          历史会话
        </Button>
        <Button
          disabled={busy || loadingHistory}
          onClick={() => {
            setConversation('');
            setMessages([]);
            setMessageCursor(null);
            setRunId('');
            setRetryInput('');
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
            <Button htmlType="submit" disabled={busy} loading={loadingHistory}>
              搜索
            </Button>
          </form>
          {!history.length && !loadingHistory && <p>还没有历史会话。</p>}
          {history.map((item) => (
            <Button
              key={item.id}
              disabled={busy || loadingHistory}
              onClick={() => void chooseConversation(item.id)}
            >
              {item.title || '未命名会话'}
            </Button>
          ))}
          {historyCursor && (
            <Button
              disabled={busy}
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
            disabled={busy}
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
              <p>{message.content}</p>
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
              {retryInput && (
                <Button
                  size="small"
                  onClick={() => void send(retryInput, !!runId)}
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
          disabled={busy || loadingHistory}
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
            disabled={!input.trim() || loadingHistory}
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
