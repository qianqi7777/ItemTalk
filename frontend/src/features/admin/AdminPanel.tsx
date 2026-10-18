import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  InputNumber,
  Modal,
  Select,
  Space,
  Spin,
  Switch,
  Table,
} from 'antd';
import { api, ApiError, newKey } from '../../services/api';

type Runtime = {
  version: number;
  globalDailyLimit: number;
  subjectDailyLimit: number;
  backgroundPaused: boolean;
  chatProfile: string;
};
type Metric = {
  kind: string;
  operation: string;
  provider: string;
  model: string;
  outcome: string;
  currency: string;
  count: number;
  p95_ms: number;
  first_token_ms?: number | null;
  input_known: number;
  input_tokens: number | null;
  output_known: number;
  output_tokens: number | null;
  priced_calls: number;
  estimated_cost: number | string | null;
};
type Audit = {
  id: number;
  actor_id?: string | null;
  version: number;
  created_at: string;
  after_value: unknown;
};
type Overview = {
  groups: Metric[];
  runtime: Runtime;
  modelProfiles: { id: string; model: string; accepted: boolean }[];
  hardLimits: { global: number; subject: number };
  queue: {
    job_type: string;
    status: string;
    count: number;
    oldest_wait_seconds: number;
  }[];
  trend: { hour: string; kind: string; count: number; p95_ms: number }[];
  costNotice: string;
};

function usageLabel(total: number | null, known: number, count: number) {
  return known === 0
    ? '未知'
    : `${total ?? 0}${known < count ? `（${count - known}次未知）` : ''}`;
}

function Controls({
  data,
  saved,
}: {
  data: Overview;
  saved: () => Promise<void>;
}) {
  const [draft, setDraft] = useState(data.runtime);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const pending = useRef({ body: '', key: '' });
  const submit = async () => {
    const body = JSON.stringify({
      expectedVersion: draft.version,
      globalDailyLimit: draft.globalDailyLimit,
      subjectDailyLimit: draft.subjectDailyLimit,
      backgroundPaused: draft.backgroundPaused,
      chatProfile: draft.chatProfile,
    });
    if (pending.current.body !== body)
      pending.current = { body, key: newKey() };
    setBusy(true);
    setError('');
    try {
      await api('/admin/runtime-config', {
        method: 'PATCH',
        body,
        headers: { 'Idempotency-Key': pending.current.key },
      });
      await saved();
    } catch (failure) {
      setError(
        failure instanceof ApiError && failure.status === 409
          ? '配置已被其他管理员修改，请刷新后重新确认。'
          : failure instanceof Error
            ? failure.message
            : '保存失败，请重试',
      );
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card title={`运行控制 · 版本 ${data.runtime.version}`}>
      {error && <Alert type="error" title={error} />}
      <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
        <label>
          全局日调用额度{' '}
          <InputNumber
            aria-label="全局日调用额度"
            min={1}
            max={data.hardLimits.global}
            value={draft.globalDailyLimit}
            disabled={busy}
            onChange={(value) =>
              value !== null && setDraft({ ...draft, globalDailyLimit: value })
            }
          />
        </label>
        <label>
          默认主体日调用额度{' '}
          <InputNumber
            aria-label="默认主体日调用额度"
            min={1}
            max={data.hardLimits.subject}
            value={draft.subjectDailyLimit}
            disabled={busy}
            onChange={(value) =>
              value !== null && setDraft({ ...draft, subjectDailyLimit: value })
            }
          />
        </label>
        <label>
          暂停领取新的后台业务任务{' '}
          <Switch
            aria-label="暂停新后台任务"
            checked={draft.backgroundPaused}
            disabled={busy}
            onChange={(value) =>
              setDraft({ ...draft, backgroundPaused: value })
            }
          />
        </label>
        <p>
          已运行任务及其子任务继续处理；账号和资产清理继续运行。调整额度不会清零计数，额度按
          UTC 日结算。
        </p>
        <label>
          新任务使用的文本模型{' '}
          <Select
            aria-label="文本模型方案"
            style={{ minWidth: 220 }}
            value={draft.chatProfile}
            disabled={busy}
            onChange={(value) => setDraft({ ...draft, chatProfile: value })}
            options={data.modelProfiles.map((profile) => ({
              value: profile.id,
              disabled: !profile.accepted,
              label: `${profile.model}${profile.accepted ? '' : '（未验收）'}`,
            }))}
          />
        </label>
        <p>模型切换只影响新运行；密钥、服务地址及向量模型通过受限配置管理。</p>
        <Button
          type="primary"
          loading={busy}
          onClick={() =>
            Modal.confirm({
              title: '确认修改运行配置',
              content: '修改将记录审计，并影响后续任务及调用额度。',
              okText: '确认保存',
              cancelText: '取消',
              onOk: submit,
            })
          }
        >
          保存运行配置
        </Button>
      </Space>
    </Card>
  );
}

export function AdminPanel() {
  const [days, setDays] = useState(1);
  const [data, setData] = useState<Overview | null>(null);
  const [audit, setAudit] = useState<Audit[]>([]);
  const [next, setNext] = useState<number | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const sequence = useRef(0);
  const refresh = useCallback(async () => {
    const current = ++sequence.current;
    setLoading(true);
    try {
      const [overview, history] = await Promise.all([
        api<Overview>(`/admin/overview?days=${days}`),
        api<{ items: Audit[]; nextBefore: number | null }>('/admin/audit'),
      ]);
      if (current !== sequence.current) return;
      setData(overview);
      setAudit(history.items);
      setNext(history.nextBefore);
      setError('');
    } catch (failure) {
      if (current !== sequence.current) return;
      setData(null);
      setError(
        failure instanceof ApiError && failure.status === 403
          ? '此页面仅供已授权管理员使用。'
          : failure instanceof Error
            ? failure.message
            : '读取运行数据失败',
      );
    } finally {
      if (current === sequence.current) setLoading(false);
    }
  }, [days]);
  useEffect(() => {
    const timer = setTimeout(() => void refresh(), 0);
    return () => {
      clearTimeout(timer);
      // 请求序号不是DOM引用；卸载时使尚未返回的请求失效。
      // eslint-disable-next-line react-hooks/exhaustive-deps
      sequence.current++;
    };
  }, [refresh]);
  return (
    <main
      className="admin-panel"
      style={{ padding: 24, maxWidth: 1400, margin: 'auto' }}
    >
      <Space style={{ marginBottom: 20 }} wrap>
        <h1>运行管理</h1>
        <Select
          aria-label="统计时间范围"
          value={days}
          onChange={setDays}
          options={[
            { value: 1, label: '最近一天' },
            { value: 7, label: '最近七天' },
            { value: 30, label: '最近三十天' },
          ]}
        />
        <Button onClick={() => void refresh()} loading={loading}>
          刷新数据
        </Button>
      </Space>
      {error && <Alert type="error" title={error} />}
      {loading && !data && <Spin />}
      {data && (
        <Space orientation="vertical" size="large" style={{ width: '100%' }}>
          <Alert
            type="info"
            title={data.costNotice}
            description="统计不包含私人问题或模型正文。明细保留30天，日汇总保留一年。"
          />
          <section className="admin-summary" aria-label="运行概况">
            {[
              [
                '业务请求',
                data.groups
                  .filter((row) => row.kind === 'http')
                  .reduce((n, row) => n + row.count, 0),
              ],
              [
                '外部调用',
                data.groups
                  .filter((row) => row.kind === 'upstream')
                  .reduce((n, row) => n + row.count, 0),
              ],
              [
                '向量缓存命中',
                data.groups
                  .filter(
                    (row) => row.kind === 'cache' && row.outcome === 'hit',
                  )
                  .reduce((n, row) => n + row.count, 0),
              ],
              [
                '排队任务',
                data.queue
                  .filter((row) => row.status === 'queued')
                  .reduce((n, row) => n + row.count, 0),
              ],
            ].map(([label, value]) => (
              <Card key={label}>
                <small>{label}</small>
                <h2>{value}</h2>
              </Card>
            ))}
          </section>
          <Card title="请求耗时趋势">
            {data.trend.filter((row) => row.kind === 'http').length ? (
              <>
                <svg
                  viewBox="0 0 600 100"
                  role="img"
                  aria-label="请求P95耗时趋势"
                  style={{ width: '100%', maxHeight: 150 }}
                >
                  <polyline
                    fill="none"
                    stroke="#185B4A"
                    strokeWidth="2"
                    points={(() => {
                      const rows = data.trend.filter(
                        (row) => row.kind === 'http',
                      );
                      const maximum = Math.max(
                        1,
                        ...rows.map((row) => row.p95_ms),
                      );
                      return rows
                        .map(
                          (row, index) =>
                            `${10 + (index * 580) / Math.max(1, rows.length - 1)},${90 - (row.p95_ms * 80) / maximum}`,
                        )
                        .join(' ');
                    })()}
                  />
                </svg>
                <p>
                  最新小时请求 P95：
                  {Math.round(
                    data.trend.filter((row) => row.kind === 'http').at(-1)
                      ?.p95_ms ?? 0,
                  )}{' '}
                  ms
                </p>
                <p>
                  按小时展示请求
                  P95，时间使用浏览器本地时区。样本不足时不推断容量。
                </p>
              </>
            ) : (
              <p>暂无请求样本</p>
            )}
          </Card>
          <Card title="请求、检索与模型用量">
            <Table
              size="small"
              scroll={{ x: 1000 }}
              dataSource={data.groups}
              rowKey={(row) =>
                JSON.stringify([
                  row.kind,
                  row.operation,
                  row.provider,
                  row.model,
                  row.outcome,
                  row.currency,
                ])
              }
              columns={[
                {
                  title: '类型／操作',
                  render: (_, row) => `${row.kind} / ${row.operation}`,
                },
                { title: '模型', render: (_, row) => row.model || '—' },
                { title: '结果', dataIndex: 'outcome' },
                { title: '次数', dataIndex: 'count' },
                {
                  title: 'P95(ms)',
                  render: (_, row) => Math.round(row.p95_ms),
                },
                {
                  title: '首个正文(ms)',
                  render: (_, row) =>
                    row.first_token_ms == null
                      ? '—'
                      : Math.round(row.first_token_ms),
                },
                {
                  title: '输入token',
                  render: (_, row) =>
                    usageLabel(row.input_tokens, row.input_known, row.count),
                },
                {
                  title: '输出token',
                  render: (_, row) =>
                    usageLabel(row.output_tokens, row.output_known, row.count),
                },
                {
                  title: '费用估算',
                  render: (_, row) =>
                    row.priced_calls === 0
                      ? '未计价'
                      : `${row.currency} ${Number(row.estimated_cost).toFixed(6)}${row.priced_calls < row.count ? '（部分未计价）' : ''}`,
                },
              ]}
            />
          </Card>
          <Card title="后台队列">
            <Table
              size="small"
              rowKey={(row) => `${row.job_type}:${row.status}`}
              dataSource={data.queue}
              scroll={{ x: 450 }}
              columns={[
                { title: '任务类型', dataIndex: 'job_type' },
                { title: '状态', dataIndex: 'status' },
                { title: '数量', dataIndex: 'count' },
                {
                  title: '最长等待(秒)',
                  render: (_, row) => Math.round(row.oldest_wait_seconds),
                },
              ]}
            />
          </Card>
          <Controls key={data.runtime.version} data={data} saved={refresh} />
          <Card title="配置修改审计">
            <Table
              size="small"
              rowKey="id"
              dataSource={audit}
              scroll={{ x: 650 }}
              columns={[
                { title: '版本', dataIndex: 'version' },
                {
                  title: '操作者',
                  render: (_, row) => row.actor_id?.slice(0, 8) ?? '未知',
                },
                {
                  title: '修改时间',
                  render: (_, row) => new Date(row.created_at).toLocaleString(),
                },
                {
                  title: '修改后配置',
                  render: (_, row) => (
                    <code
                      style={{ whiteSpace: 'normal', overflowWrap: 'anywhere' }}
                    >
                      {JSON.stringify(row.after_value)}
                    </code>
                  ),
                },
              ]}
            />
            {next !== null && (
              <Button
                onClick={async () => {
                  try {
                    const result = await api<{
                      items: Audit[];
                      nextBefore: number | null;
                    }>(`/admin/audit?before=${next}`);
                    setAudit((previous) => [...previous, ...result.items]);
                    setNext(result.nextBefore);
                  } catch {
                    setError('读取更多审计记录失败，请重试');
                  }
                }}
              >
                加载更早记录
              </Button>
            )}
          </Card>
        </Space>
      )}
    </main>
  );
}
