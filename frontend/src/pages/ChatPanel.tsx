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
import type { Citation, Message, ProductInstance, Run } from '../types/api';
import { SpeechInput } from '../features/speech/SpeechInput';

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
  useEffect(() => {
    onBusyChange?.(busy);
  }, [busy, onBusyChange]);
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

  const send = async (question = input, retry = false) => {
    if (!question.trim()) return;
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
      <div className="messages" aria-live="polite">
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
            <p>{message.content}</p>
            <div className="citations">
              {message.citations.map((citation) => (
                <CitationCard
                  citation={citation}
                  key={citation.knowledgeChunkId}
                />
              ))}
            </div>
          </article>
        ))}
        {busy && (
          <article className="bubble assistant">
            <span className="speaker">
              <Sparkles size={13} aria-hidden="true" />
              识物说
            </span>
            <div className="answer-draft">
              {draft || (
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
          disabled={busy}
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
            disabled={!input.trim()}
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
