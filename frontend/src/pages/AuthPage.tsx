import { useState } from 'react';
import { Alert, Button, Form, Input, Segmented } from 'antd';
import { PackageSearch, ShieldCheck } from 'lucide-react';
import { post } from '../services/api';
import { useSession } from '../stores/session';

export function AuthPage() {
  const [mode, setMode] = useState('登录');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const { refresh } = useSession();
  const submit = async (values: { email: string; password: string }) => {
    setBusy(true);
    setError('');
    try {
      if (mode === '注册') await post('/auth/register', values);
      await post('/auth/login', values);
      await refresh();
    } catch (error) {
      setError(error instanceof Error ? error.message : '登录失败');
    } finally {
      setBusy(false);
    }
  };
  return (
    <main className="auth-layout">
      <section className="auth-story">
        <div className="brand">
          <PackageSearch aria-hidden="true" />
          <strong>
            识物说 <small>ItemTalk</small>
          </strong>
        </div>
        <span className="eyebrow">让设备更容易使用</span>
        <h1>
          从认识它，
          <br />
          到用好它。
        </h1>
        <p>
          拍下产品或铭牌，确认型号，
          <br />
          通过已核验的资料，找到有据可查的答案。
        </p>
        <div className="trust-note">
          <ShieldCheck size={20} aria-hidden="true" />
          每一个可靠答案，都有来源
        </div>
      </section>
      <section className="auth-form">
        <h2>欢迎来到识物说</h2>
        <p>为手里的设备，建立专属资料入口。</p>
        <Segmented
          value={mode}
          options={['登录', '注册']}
          onChange={setMode}
          block
        />
        {error && <Alert title={error} type="error" role="alert" showIcon />}
        <Form layout="vertical" onFinish={submit} requiredMark={false}>
          <Form.Item
            label="邮箱"
            name="email"
            rules={[
              { required: true, type: 'email', message: '请输入有效邮箱' },
            ]}
          >
            <Input autoComplete="email" placeholder="you@example.com" />
          </Form.Item>
          <Form.Item
            label="密码"
            name="password"
            rules={[
              {
                required: true,
                min: 8,
                max: 128,
                message: '密码需为 8–128 位',
              },
            ]}
          >
            <Input.Password
              autoComplete={
                mode === '登录' ? 'current-password' : 'new-password'
              }
              placeholder="至少 8 位字符"
            />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={busy} block>
            {mode === '登录' ? '登录并开始' : '注册并开始'}
          </Button>
        </Form>
      </section>
    </main>
  );
}
