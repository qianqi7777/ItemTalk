import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  InputNumber,
  Modal,
  Select,
  Spin,
  Switch,
  Table,
} from 'antd';
import {
  Activity,
  ArrowDownToLine,
  Clock3,
  Layers3,
  RefreshCw,
  ShieldCheck,
  SlidersHorizontal,
  Zap,
} from 'lucide-react';
import { api, ApiError, newKey } from '../../services/api';
import { RequestTrend } from './RequestTrend';

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
    <Card
      id="runtime-controls"
      className="admin-controls"
      title={`运行控制 · 版本 ${data.runtime.version}`}
      extra={<SlidersHorizontal size={18} aria-hidden="true" />}
    >
      {error && <Alert type="error" title={error} />}
      <div className="control-grid">
        <label className="control-field">
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
          <small>全站共用的每日调用额度</small>
        </label>
        <label className="control-field">
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
          <small>同一主体的默认每日调用额度</small>
        </label>
        <div className="control-pause">
          <div>
            <strong>暂停领取新的后台业务任务</strong>
            <p>已运行任务及其子任务继续处理，账号和资产清理不受影响。</p>
          </div>
          <Switch
            aria-label="暂停新后台任务"
            checked={draft.backgroundPaused}
            disabled={busy}
            onChange={(value) =>
              setDraft({ ...draft, backgroundPaused: value })
            }
          />
        </div>
        <label className="control-field control-model">
          新任务使用的文本模型{' '}
          <Select
            aria-label="文本模型方案"
            value={draft.chatProfile}
            disabled={busy}
            onChange={(value) => setDraft({ ...draft, chatProfile: value })}
            options={data.modelProfiles.map((profile) => ({
              value: profile.id,
              disabled: !profile.accepted,
              label: `${profile.model}${profile.accepted ? '' : '（未验收）'}`,
            }))}
          />
          <small>只影响新运行；进行中的任务继续使用原配置。</small>
        </label>
        <div className="control-footer">
          <p>
            调整额度不会清零计数，按 UTC 日结算。密钥与服务地址由受限配置管理。
          </p>
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
        </div>
      </div>
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
  const summaries = data
    ? [
        {
          label: '业务请求',
          value: data.groups
            .filter((r) => r.kind === 'http')
            .reduce((n, r) => n + r.count, 0),
          icon: Activity,
          note: '所选时间范围内',
        },
        {
          label: '外部调用',
          value: data.groups
            .filter((r) => r.kind === 'upstream')
            .reduce((n, r) => n + r.count, 0),
          icon: Zap,
          note: '包含失败与重试',
        },
        {
          label: '向量缓存命中',
          value: data.groups
            .filter((r) => r.kind === 'cache' && r.outcome === 'hit')
            .reduce((n, r) => n + r.count, 0),
          icon: Layers3,
          note: '减少重复向量请求',
        },
        {
          label: '排队任务',
          value: data.queue
            .filter((r) => r.status === 'queued')
            .reduce((n, r) => n + r.count, 0),
          icon: Clock3,
          note: '当前等待领取',
        },
      ]
    : [];
  const errors =
    data?.groups
      .filter(
        (row) =>
          (row.kind === 'upstream' && row.outcome !== 'success') ||
          (row.kind === 'http' &&
            (/^[45]\d\d$/.test(row.outcome) ||
              [
                'INTERNAL_ERROR',
                'UPSTREAM_MODEL_ERROR',
                'AUTH_REQUIRED',
                'FORBIDDEN',
                'RATE_LIMITED',
                'interrupted',
              ].includes(row.outcome))),
      )
      .reduce<Record<string, number>>(
        (result, row) => ({
          ...result,
          [row.outcome]: (result[row.outcome] || 0) + row.count,
        }),
        {},
      ) ?? {};
  const errorTotal = Object.values(errors).reduce((n, count) => n + count, 0);
  return (
    <main className="admin-panel">
      <header className="admin-header">
        <div>
          <span className="eyebrow">运行概览 / OPERATIONS</span>
          <h1>运行管理</h1>
          <p>看清用量与任务，让每一次调整都有记录。</p>
        </div>
        <div className="admin-header-actions">
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
          <Button
            icon={<RefreshCw size={16} aria-hidden="true" />}
            onClick={() => void refresh()}
            loading={loading}
          >
            刷新数据
          </Button>
        </div>
      </header>
      {error && <Alert type="error" title={error} />}
      {loading && !data && (
        <div className="admin-loading" role="status">
          <Spin />
          <p>正在读取运行概况…</p>
        </div>
      )}
      {data && (
        <div className="admin-content" aria-busy={loading}>
          <Alert
            className="admin-notice"
            type="info"
            icon={<ShieldCheck size={19} />}
            showIcon
            title={data.costNotice}
            description="统计不包含私人问题或模型正文。明细保留30天，日汇总保留一年。"
          />
          <section className="admin-summary" aria-label="运行概况">
            {summaries.map(({ label, value, icon: Icon, note }) => (
              <Card key={label} className="stat-card">
                <div className="stat-heading">
                  <span>{label}</span>
                  <Icon size={19} aria-hidden="true" />
                </div>
                <strong className="stat-value">{value.toLocaleString()}</strong>
                <small>{note}</small>
              </Card>
            ))}
          </section>
          <div className="admin-analysis-grid">
            <Card title="请求耗时趋势">
              <RequestTrend points={data.trend} />
            </Card>
            <Card
              title="错误分布"
              extra={<span className="card-meta">{errorTotal} 次</span>}
            >
              {errorTotal ? (
                <ul className="error-distribution">
                  {Object.entries(errors)
                    .sort((a, b) => b[1] - a[1])
                    .map(([label, count]) => (
                      <li key={label}>
                        <div>
                          <span>{label}</span>
                          <strong>{count}</strong>
                        </div>
                        <meter
                          min={0}
                          max={errorTotal}
                          value={count}
                          aria-label={label}
                        />
                      </li>
                    ))}
                </ul>
              ) : (
                <div className="chart-empty">
                  <ShieldCheck size={28} aria-hidden="true" />
                  <strong>暂无错误记录</strong>
                  <p>这里仅统计所选时间内已记录的失败请求。</p>
                </div>
              )}
            </Card>
          </div>
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
                {
                  title: '状态',
                  render: (_, row) => (
                    <span className={`queue-status queue-${row.status}`}>
                      {(
                        {
                          queued: '排队中',
                          running: '处理中',
                          succeeded: '已完成',
                          failed: '失败',
                          cancelled: '已取消',
                          partial_success: '部分完成',
                        } as Record<string, string>
                      )[row.status] || row.status}
                    </span>
                  ),
                },
                { title: '数量', dataIndex: 'count' },
                {
                  title: '最长等待(秒)',
                  render: (_, row) => Math.round(row.oldest_wait_seconds),
                },
              ]}
            />
          </Card>
          <Controls key={data.runtime.version} data={data} saved={refresh} />
          <Card
            id="audit-log"
            title="配置修改审计"
            extra={<ArrowDownToLine size={18} aria-hidden="true" />}
          >
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
                    <details className="audit-config">
                      <summary>查看配置</summary>
                      <pre>{JSON.stringify(row.after_value, null, 2)}</pre>
                    </details>
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
        </div>
      )}
    </main>
  );
}
