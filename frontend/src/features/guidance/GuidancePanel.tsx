import { useEffect, useState } from 'react';
import { Alert, Button, Checkbox, Input, Upload } from 'antd';
import { Route } from 'lucide-react';
import { api, newKey, post, upload } from '../../services/api';
import { CitationCard } from '../../components/CitationCard';
import type { Asset, Guidance, ProductInstance } from '../../types/api';

export function GuidancePanel({
  product,
  onBusyChange,
}: {
  product: ProductInstance;
  onBusyChange?: (busy: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const [topic, setTopic] = useState('');
  const [session, setSession] = useState<Guidance>();
  const [history, setHistory] = useState<Guidance[]>([]);
  const [note, setNote] = useState('');
  const [photos, setPhotos] = useState<Asset[]>([]);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    onBusyChange?.(busy);
  }, [busy, onBusyChange]);
  const applySession = (value: Guidance) => {
    // 新步骤/版本必须重新确认前提，不能把上一步的勾选或照片带入下一步。
    if (value.id !== session?.id || value.version !== session?.version) {
      setConfirmed(false);
      setPhotos([]);
      setNote('');
    }
    setSession(value);
  };

  const run = async (action: () => Promise<void>) => {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      await action();
    } catch (error) {
      setError(
        error instanceof Error ? error.message : '引导操作失败，请刷新状态',
      );
    } finally {
      setBusy(false);
    }
  };
  const loadHistory = async () => {
    const page = await api<{ items: Guidance[] }>(
      `/guidance-sessions?instanceId=${product.id}`,
    );
    setHistory(page.items);
  };
  const load = async (id: string) =>
    applySession(await api<Guidance>(`/guidance-sessions/${id}`));
  const create = async () => {
    applySession(
      await post<Guidance>(
        '/guidance-sessions',
        { userProductInstanceId: product.id, topic },
        newKey(),
      ),
    );
    await loadHistory();
  };
  const submit = async (
    result: 'completed' | 'failed' | 'unclear' | 'paused',
  ) => {
    if (!session) return;
    // 每次提交携带服务端版本，后台锁与幂等记录决定推进，不在客户端猜下一步。
    applySession(
      await post<Guidance>(
        `/guidance-sessions/${session.id}/feedback`,
        {
          currentStepNo: session.currentStepNo,
          expectedVersion: session.version,
          result,
          note: note.trim() || null,
          assetIds: photos.map((photo) => photo.id),
          preconditionsConfirmed: confirmed,
        },
        newKey(),
      ),
    );
    await loadHistory();
  };
  const resume = async () => {
    if (!session) return;
    applySession(
      await post<Guidance>(
        `/guidance-sessions/${session.id}/resume`,
        { expectedVersion: session.version },
        newKey(),
      ),
    );
    await loadHistory();
  };
  const step = session?.currentStep;
  const invalidEvidence =
    !!step &&
    step.citations.some(
      (citation) => citation.verificationStatus !== 'verified',
    );
  const needsConfirmation =
    !!step && (step.riskLevel === 'medium' || step.preconditions.length > 0);

  return (
    <section className="guidance-panel">
      <Button
        aria-expanded={open}
        disabled={busy}
        icon={<Route size={16} aria-hidden="true" />}
        onClick={() => {
          setOpen(!open);
          if (!open) void run(loadHistory);
        }}
      >
        操作引导
      </Button>
      {!open && <p className="tool-description">一步一步，完成设备操作</p>}
      {open && (
        <div className="guidance-content">
          {error && <Alert title={error} type="error" showIcon />}
          {!session && (
            <form
              onSubmit={(event) => {
                event.preventDefault();
                void run(create);
              }}
            >
              <label htmlFor="guidance-topic">你想完成什么操作？</label>
              <Input
                id="guidance-topic"
                value={topic}
                maxLength={200}
                disabled={busy}
                onChange={(event) => setTopic(event.target.value)}
                placeholder="例如：开机或蓝牙配对"
              />
              <Button
                htmlType="submit"
                type="primary"
                loading={busy}
                disabled={busy || !topic.trim()}
                aria-label="开始引导"
              >
                开始引导
              </Button>
            </form>
          )}
          {session && (
            <>
              <div className="inline-actions">
                <h3>{session.topic}</h3>
                <Button
                  size="small"
                  disabled={busy}
                  onClick={() => void run(() => load(session.id))}
                >
                  刷新引导
                </Button>
                <Button
                  size="small"
                  disabled={busy}
                  onClick={() => {
                    setSession(undefined);
                    setTopic('');
                  }}
                >
                  开始新的引导
                </Button>
              </div>
              {session.notice && (
                <Alert
                  title={session.notice}
                  type={
                    session.status === 'blocked_unsafe' ? 'error' : 'warning'
                  }
                  showIcon
                />
              )}
              {session.status === 'completed' && (
                <Alert title="操作引导已完成" type="success" showIcon />
              )}
              {session.status === 'paused' && (
                <Alert
                  title="引导已暂停，恢复后仍从当前步骤开始。"
                  type="info"
                  showIcon
                />
              )}
              {step && (
                <article className="guidance-step">
                  <span className="eyebrow">当前步骤 {step.stepNo}</span>
                  <h4>{step.instruction}</h4>
                  {step.preconditions.length > 0 && (
                    <div>
                      <strong>开始前确认</strong>
                      <ul>
                        {step.preconditions.map((value, index) => (
                          <li key={index}>{value}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {step.safetyNotes.length > 0 && (
                    <Alert
                      type="warning"
                      title="安全提示"
                      description={
                        <ul>
                          {step.safetyNotes.map((value, index) => (
                            <li key={index}>{value}</li>
                          ))}
                        </ul>
                      }
                      showIcon
                    />
                  )}
                  <p>完成条件：{step.successCriteria}</p>
                  {step.failureOfStepId && (
                    <p>这是针对上一步问题的排查步骤。</p>
                  )}
                  {invalidEvidence && (
                    <Alert
                      title="本步骤来源已失效，请暂停操作并更新资料。"
                      type="error"
                      showIcon
                    />
                  )}
                  <div className="citations">
                    {step.citations.map((citation) => (
                      <CitationCard
                        citation={citation}
                        key={citation.knowledgeChunkId}
                      />
                    ))}
                  </div>
                  {session.status === 'active' && (
                    <>
                      {needsConfirmation && (
                        <Checkbox
                          checked={confirmed}
                          disabled={busy || invalidEvidence}
                          onChange={(event) =>
                            setConfirmed(event.target.checked)
                          }
                        >
                          我已按官方说明确认本步骤的安全前提
                        </Checkbox>
                      )}
                      <Input.TextArea
                        aria-label="步骤反馈说明"
                        value={note}
                        maxLength={2000}
                        disabled={busy}
                        onChange={(event) => setNote(event.target.value)}
                        placeholder="可选：描述遇到的问题或不清楚的地方"
                        autoSize={{ minRows: 2 }}
                      />
                      <Upload
                        accept="image/jpeg,image/png,image/webp"
                        showUploadList={false}
                        disabled={busy || photos.length >= 3}
                        beforeUpload={(file) => {
                          void run(async () => {
                            if (file.size > 10 * 1024 * 1024)
                              throw new Error('反馈图片需小于10MB');
                            const asset = await upload(file, 'feedback');
                            setPhotos((old) => [...old, asset]);
                          });
                          return false;
                        }}
                      >
                        <Button disabled={busy || photos.length >= 3}>
                          补充反馈照片
                        </Button>
                      </Upload>
                      {photos.length > 0 && (
                        <div className="inline-actions">
                          已上传 {photos.length} 张反馈照片
                          <Button
                            size="small"
                            disabled={busy}
                            onClick={() => setPhotos([])}
                          >
                            移除照片
                          </Button>
                        </div>
                      )}
                      <div className="inline-actions">
                        <Button
                          type="primary"
                          disabled={
                            busy ||
                            invalidEvidence ||
                            (needsConfirmation && !confirmed)
                          }
                          onClick={() => void run(() => submit('completed'))}
                        >
                          已完成
                        </Button>
                        <Button
                          disabled={busy || invalidEvidence}
                          onClick={() => void run(() => submit('failed'))}
                        >
                          遇到问题
                        </Button>
                        <Button
                          disabled={busy || invalidEvidence}
                          onClick={() => void run(() => submit('unclear'))}
                        >
                          没看明白
                        </Button>
                        <Button
                          disabled={busy}
                          onClick={() => void run(() => submit('paused'))}
                        >
                          暂停引导
                        </Button>
                      </div>
                    </>
                  )}
                  {session.status === 'paused' && (
                    <Button
                      type="primary"
                      loading={busy}
                      disabled={invalidEvidence}
                      onClick={() => void run(resume)}
                    >
                      恢复引导
                    </Button>
                  )}
                </article>
              )}
            </>
          )}
          {history.length > 0 && (
            <div className="guidance-history">
              <strong>此前的引导</strong>
              {history.map((item) => (
                <Button
                  key={item.id}
                  disabled={busy}
                  onClick={() => void run(() => load(item.id))}
                >
                  {item.topic}
                  {item.status === 'paused' ? ' · 已暂停' : ''}
                </Button>
              ))}
            </div>
          )}
          <small className="privacy-note">
            引导依据核验资料，问题描述与反馈照片会发送至配置的模型服务处理；不要上传密码或联系方式。
          </small>
        </div>
      )}
    </section>
  );
}
