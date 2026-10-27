import { useEffect, useRef, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Input,
  Modal,
  Radio,
  Select,
  Space,
  Table,
  Tag,
} from 'antd';
import { api, ApiError, newKey } from '../../services/api';

export type RuntimeConfig = {
  version: number;
  globalDailyLimit: number;
  subjectDailyLimit: number;
  backgroundPaused: boolean;
  chatProfile: string;
  visionProfile?: string;
  sttProfile?: string;
  embeddingProfile?: string;
};
type Role = 'chat' | 'vision' | 'stt' | 'embedding';
const roleLabels: Record<Role, string> = {
  chat: '文本模型',
  vision: '图像模型',
  stt: '录音转写',
  embedding: '向量模型',
};
type Preset = {
  model: string;
  baseUrl: string;
  protocol: string;
  revision?: string | null;
  dimensions?: number | null;
};
type SavedModelConfig = {
  id: string;
  name: string;
  base_url: string;
  protocol: string;
  model: string;
  created_at: string;
  role?: Role;
};
export type ModelConfigListing = {
  encryptionReady: boolean;
  activeProfile: string;
  items: SavedModelConfig[];
  activeProfiles?: Partial<Record<Role, string>>;
  presets?: Record<Role, Preset>;
};
type Draft = {
  name: string;
  baseUrl: string;
  protocol: string;
  model: string;
  apiKey: string;
};
const emptyDraft: Draft = {
  name: '',
  baseUrl: '',
  protocol: 'openai_chat',
  model: '',
  apiKey: '',
};
const chatProtocols = [
  { value: 'openai_chat', label: 'Chat Completions 兼容' },
  { value: 'dashscope_chat', label: '百炼 Chat Completions（关闭思考）' },
];

export function ModelConfigs({
  listing,
  runtime,
  onSaved,
}: {
  listing: ModelConfigListing;
  runtime: RuntimeConfig;
  onSaved: () => Promise<void>;
}) {
  const [role, setRole] = useState<Role>('chat');
  const [draft, setDraft] = useState(emptyDraft);
  const [tested, setTested] = useState<{
    token: string;
    expiresAt: number;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const pending = useRef({ body: '', key: '' });
  const preset = listing.presets?.[role];
  const currentProfile =
    listing.activeProfiles?.[role] ?? runtime[`${role}Profile`] ?? 'current';
  const current = listing.items.find((item) => item.id === currentProfile);
  const protocols =
    role === 'stt'
      ? [
          { value: 'dashscope_audio', label: '百炼音频消息转写' },
          {
            value: 'openai_transcription',
            label: 'Audio Transcriptions 兼容（文件转写）',
          },
        ]
      : role === 'embedding'
        ? [
            {
              value: preset?.protocol ?? 'dashscope_embeddings',
              label: 'Embeddings（保持服务器预设）',
            },
          ]
        : chatProtocols;
  const switchRole = (next: Role) => {
    if (busy) return;
    setRole(next);
    const defaults = listing.presets?.[next];
    setDraft({
      ...emptyDraft,
      baseUrl: defaults?.baseUrl ?? '',
      model: defaults?.model ?? '',
      protocol:
        defaults?.protocol ??
        (next === 'stt'
          ? 'dashscope_audio'
          : next === 'embedding'
            ? 'dashscope_embeddings'
            : 'openai_chat'),
    });
    setTested(null);
    setError('');
    setNotice('');
    pending.current = { body: '', key: '' };
  };
  useEffect(() => {
    if (!tested) return;
    const timer = setTimeout(
      () => {
        setTested(null);
        setNotice('测试结果已过期，请重新测试配置。');
      },
      Math.max(0, tested.expiresAt - Date.now()),
    );
    return () => clearTimeout(timer);
  }, [tested]);

  const edit = (field: keyof Draft, value: string) => {
    setDraft({ ...draft, [field]: value });
    setTested(null);
    setError('');
    setNotice('');
  };
  const test = async () => {
    setBusy(true);
    setError('');
    setNotice('');
    setTested(null);
    try {
      const result = await api<{ testToken: string; validForSeconds: number }>(
        '/admin/model-configs/test',
        {
          method: 'POST',
          body: JSON.stringify({
            ...draft,
            role,
            ...(role === 'embedding'
              ? { revision: preset?.revision, dimensions: preset?.dimensions }
              : {}),
          }),
        },
      );
      setTested({
        token: result.testToken,
        expiresAt: Date.now() + result.validForSeconds * 1000,
      });
      setNotice(
        `测试通过：${role === 'chat' ? '普通回复、JSON 和流式输出可用' : role === 'vision' ? '图片识别和 JSON 输出可用' : role === 'stt' ? '固定录音转写可用' : '批量向量及 1024 维输出有效'}。可以保存并启用。`,
      );
    } catch (failure) {
      setError(
        failure instanceof Error ? failure.message : '测试失败，请检查配置',
      );
    } finally {
      setBusy(false);
    }
  };
  const activate = async (profile: string) => {
    await api('/admin/runtime-config', {
      method: 'PATCH',
      headers: { 'Idempotency-Key': newKey() },
      body: JSON.stringify({
        expectedVersion: runtime.version,
        globalDailyLimit: runtime.globalDailyLimit,
        subjectDailyLimit: runtime.subjectDailyLimit,
        backgroundPaused: runtime.backgroundPaused,
        chatProfile: role === 'chat' ? profile : runtime.chatProfile,
        ...(role === 'chat' ? {} : { [`${role}Profile`]: profile }),
      }),
    });
  };
  const saveAndActivate = async () => {
    if (!tested || tested.expiresAt <= Date.now()) {
      setTested(null);
      setError('测试结果已过期，请重新测试配置。');
      return;
    }
    setBusy(true);
    setError('');
    let created = false;
    try {
      const body = JSON.stringify({
        ...draft,
        role,
        ...(role === 'embedding'
          ? { revision: preset?.revision, dimensions: preset?.dimensions }
          : {}),
        testToken: tested.token,
      });
      if (pending.current.body !== body)
        pending.current = { body, key: newKey() };
      const result = await api<SavedModelConfig>('/admin/model-configs', {
        method: 'POST',
        body,
        headers: { 'Idempotency-Key': pending.current.key },
      });
      created = true;
      setDraft(
        role === 'chat'
          ? emptyDraft
          : {
              ...emptyDraft,
              model: preset?.model ?? '',
              baseUrl: preset?.baseUrl ?? '',
              protocol: preset?.protocol ?? draft.protocol,
            },
      );
      setTested(null);
      pending.current = { body: '', key: '' };
      await activate(result.id);
      setNotice(`已启用「${result.name}」，新任务将使用该配置。`);
    } catch (failure) {
      const detail =
        failure instanceof ApiError && failure.status === 409
          ? '配置版本已更新，请刷新后重试。'
          : failure instanceof Error
            ? failure.message
            : '操作失败，请重试';
      setError(
        created
          ? `配置已保存，但未启用。${detail}可在已保存配置中重新启用。`
          : detail,
      );
    } finally {
      try {
        await onSaved();
      } catch {
        setError('操作结果刷新失败，请刷新页面确认当前配置。');
      }
      setBusy(false);
    }
  };
  const enableExisting = async (row: SavedModelConfig) => {
    setBusy(true);
    setError('');
    try {
      await activate(row.id);
      setNotice(`已启用「${row.name}」，新任务将使用该配置。`);
    } catch (failure) {
      setError(
        failure instanceof Error ? failure.message : '启用失败，请刷新后重试',
      );
    } finally {
      try {
        await onSaved();
      } catch {
        setError('操作结果刷新失败，请刷新页面确认当前配置。');
      }
      setBusy(false);
    }
  };
  const confirm = (name: string, action: () => Promise<void>) =>
    Modal.confirm({
      title: `确认启用${roleLabels[role]}`,
      content: `将启用「${name}」。只影响新任务，进行中的任务继续使用原配置。`,
      okText: '确认启用',
      okButtonProps: { 'aria-label': '确认启用' },
      cancelText: '取消',
      onOk: action,
    });
  const complete = Object.values(draft).every((value) => value.trim());
  return (
    <Card id="model-configs" title="模型配置">
      <p>
        填写服务地址、协议、模型名称和密钥，测试后保存并启用。原配置保留，可随时切回。
      </p>
      <Radio.Group
        aria-label="模型用途"
        value={role}
        disabled={busy}
        onChange={(event) => switchRole(event.target.value)}
      >
        {(Object.keys(roleLabels) as Role[]).map((item) => (
          <Radio.Button key={item} value={item}>
            {roleLabels[item]}
          </Radio.Button>
        ))}
      </Radio.Group>
      <p>
        当前{roleLabels[role]}：
        {current
          ? `${current.name} · ${current.model}`
          : `服务器预设${preset ? ` · ${preset.model}` : ''}`}
        。
      </p>
      {role === 'embedding' && (
        <Alert
          type="info"
          title={`仅更换地址和 API Key；模型 ${preset?.model ?? ''}、修订 ${preset?.revision ?? ''}、1024 维保持不变，不重建资料向量。新地址必须提供同一模型与修订，能力测试不能证明两个服务的向量空间完全相同。`}
        />
      )}
      {!listing.encryptionReady && (
        <Alert
          type="warning"
          title="服务器尚未配置模型密钥加密，请联系运维完成一次性配置。"
        />
      )}
      {error && <Alert role="alert" type="error" title={error} />}
      {notice && <Alert role="status" type="success" title={notice} />}
      <fieldset
        className="model-config-fields"
        aria-label={`新增${roleLabels[role]}配置`}
        disabled={busy || !listing.encryptionReady}
      >
        <div className="control-grid">
          <label className="control-field" htmlFor="model-config-name">
            配置名称
            <Input
              id="model-config-name"
              aria-label="配置名称"
              maxLength={80}
              value={draft.name}
              disabled={busy || !listing.encryptionReady}
              placeholder="例如：备用文本模型"
              onChange={(event) => edit('name', event.target.value)}
            />
          </label>
          <label className="control-field" htmlFor="model-config-base">
            API 基础地址
            <Input
              id="model-config-base"
              aria-label="API 基础地址"
              maxLength={500}
              value={draft.baseUrl}
              disabled={busy || !listing.encryptionReady}
              placeholder="https://供应商域名/v1"
              autoComplete="off"
              onChange={(event) => edit('baseUrl', event.target.value)}
            />
            <small>填写 HTTPS 基础地址，不含接口末尾路径或密钥。</small>
          </label>
          <label className="control-field" htmlFor="model-config-protocol">
            请求协议
            <Select
              id="model-config-protocol"
              aria-label="请求协议"
              value={draft.protocol}
              options={protocols}
              disabled={
                busy || !listing.encryptionReady || role === 'embedding'
              }
              onChange={(value) => edit('protocol', value)}
            />
          </label>
          <label className="control-field" htmlFor="model-config-model">
            模型名称
            <Input
              id="model-config-model"
              aria-label="模型名称"
              maxLength={100}
              value={draft.model}
              disabled={
                busy || !listing.encryptionReady || role === 'embedding'
              }
              placeholder="服务商提供的模型 ID"
              autoComplete="off"
              onChange={(event) => edit('model', event.target.value)}
            />
          </label>
          <label className="control-field" htmlFor="model-config-key">
            API Key
            <Input.Password
              id="model-config-key"
              aria-label="API Key"
              maxLength={4096}
              value={draft.apiKey}
              autoComplete="new-password"
              visibilityToggle={false}
              disabled={busy || !listing.encryptionReady}
              placeholder="仅用于本次新增配置"
              onChange={(event) => edit('apiKey', event.target.value)}
            />
            <small>密钥加密保存，保存后不回显。更换密钥请新增配置。</small>
          </label>
          <div className="control-footer">
            <p>
              测试发送{role === 'chat' ? ' 3 ' : ' 1 '}
              次固定样例请求，会消耗少量供应商额度并计入网站调用额度。只切换当前用途。
            </p>
            <Space wrap>
              <Button
                aria-label="测试配置"
                loading={busy}
                disabled={!complete || !listing.encryptionReady}
                onClick={() => void test()}
              >
                测试配置
              </Button>
              <Button
                type="primary"
                disabled={busy || !tested || !listing.encryptionReady}
                onClick={() => confirm(draft.name, saveAndActivate)}
              >
                保存并启用
              </Button>
            </Space>
          </div>
        </div>
      </fieldset>
      <Table<SavedModelConfig>
        rowKey="id"
        size="small"
        dataSource={listing.items.filter(
          (item) => (item.role ?? 'chat') === role,
        )}
        scroll={{ x: 700 }}
        pagination={false}
        locale={{ emptyText: '尚无新增配置，当前仍使用服务器预设。' }}
        columns={[
          { title: '已保存配置', dataIndex: 'name' },
          { title: '模型', dataIndex: 'model' },
          { title: '服务地址', dataIndex: 'base_url' },
          {
            title: '状态与操作',
            render: (_, row) =>
              currentProfile === row.id ? (
                <Tag color="green">当前使用</Tag>
              ) : (
                <Button
                  disabled={busy || !listing.encryptionReady}
                  aria-label={`启用 ${row.name}`}
                  onClick={() => confirm(row.name, () => enableExisting(row))}
                >
                  启用
                </Button>
              ),
          },
        ]}
      />
      <Button
        disabled={busy || currentProfile === 'current'}
        onClick={() =>
          confirm('服务器预设', async () => {
            setBusy(true);
            setError('');
            try {
              await activate('current');
              setNotice(`已切回${roleLabels[role]}服务器预设。`);
            } catch (failure) {
              setError(
                failure instanceof Error
                  ? failure.message
                  : '切回失败，请刷新重试',
              );
            } finally {
              try {
                await onSaved();
              } catch {
                setError('刷新失败，请刷新页面确认当前配置。');
              }
              setBusy(false);
            }
          })
        }
      >
        切回服务器预设
      </Button>
      <p>各用途独立启用。网站日调用计数不会因切换而清零。</p>
    </Card>
  );
}
