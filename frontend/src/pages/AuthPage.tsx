import { useState } from 'react';
import { Alert, Button, Form, Input, Segmented } from 'antd';
import {
  ArrowUpRight,
  BookOpen,
  Camera,
  LockKeyhole,
  Mail,
  MessageCircle,
  ShieldCheck,
} from 'lucide-react';
import { Brand } from '../components/Brand';
import { DeviceScene } from '../components/DeviceScene';
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
      <header className="auth-header">
        <Brand />
        <span>让每一件设备，都更好用。</span>
      </header>
      <div className="auth-content">
        <section className="auth-story">
          <span className="story-kicker">
            <span /> 从认识它，到用好它
          </span>
          <h1>
            设备的使用说明，
            <br />
            <em>可以是一场对话。</em>
          </h1>
          <p>
            拍下产品，确认型号。
            <br />
            让复杂的说明书，变成简单的对话。
          </p>
          <DeviceScene />
          <div className="story-steps" aria-label="使用步骤">
            <span>
              <Camera size={17} aria-hidden="true" />
              拍照识别
            </span>
            <span>
              <BookOpen size={17} aria-hidden="true" />
              核验资料
            </span>
            <span>
              <MessageCircle size={17} aria-hidden="true" />
              有据问答
            </span>
          </div>
          <div className="trust-note">
            <ShieldCheck size={20} aria-hidden="true" />
            每一个可靠答案，都有来源。
          </div>
        </section>
        <section className="auth-form">
          <span className="form-kicker">YOUR DEVICE, UNDERSTOOD.</span>
          <h2>{mode === '登录' ? '欢迎回来。' : '你好，新朋友。'}</h2>
          <p>
            {mode === '登录'
              ? '你的设备助手，已经准备好了。'
              : '为手里的设备，建立专属资料入口。'}
          </p>
          <Segmented
            value={mode}
            options={['登录', '注册']}
            onChange={setMode}
            block
            disabled={busy}
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
              <Input
                prefix={<Mail size={17} aria-hidden="true" />}
                autoComplete="email"
                placeholder="you@example.com"
              />
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
                prefix={<LockKeyhole size={17} aria-hidden="true" />}
                autoComplete={
                  mode === '登录' ? 'current-password' : 'new-password'
                }
                placeholder="至少 8 位字符"
              />
            </Form.Item>
            <Button type="primary" htmlType="submit" loading={busy} block>
              {mode === '登录' ? '登录并开始' : '注册并开始'}
              <ArrowUpRight size={18} aria-hidden="true" />
            </Button>
          </Form>
          <div className="auth-assurance">
            <ShieldCheck size={15} aria-hidden="true" />
            <span>产品照片保持私有，答案依据核验资料。</span>
          </div>
        </section>
      </div>
      <footer className="auth-footer">
        <span>© {new Date().getFullYear()} ItemTalk 识物说</span>
        <span>从认识它，到用好它。</span>
      </footer>
    </main>
  );
}
