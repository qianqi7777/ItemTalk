import { StrictMode, useCallback, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { Button, ConfigProvider, Spin } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import {
  ArrowUpRight,
  CircleDot,
  LogOut,
  PackageSearch,
  ShieldCheck,
} from 'lucide-react';
import { Brand } from '../components/Brand';
import { api, post } from '../services/api';
import { SessionContext } from '../stores/session';
import type { User } from '../types/api';
import { AuthPage } from '../pages/AuthPage';
import { HomePage } from '../pages/HomePage';
import './styles.css';

export function App() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const refresh = useCallback(async () => {
    try {
      setUser(await api<User>('/me'));
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    const timer = setTimeout(() => void refresh(), 0);
    return () => clearTimeout(timer);
  }, [refresh]);
  return (
    <SessionContext.Provider value={{ user, refresh }}>
      {loading ? (
        <div className="loading-screen">
          <Spin />
          正在加载…
        </div>
      ) : !user ? (
        <AuthPage />
      ) : (
        <div className="app-shell">
          <aside className="sidebar">
            <a className="brand" href={import.meta.env.BASE_URL}>
              <Brand inverted />
            </a>
            <div className="nav-label">WORKSPACE / 工作空间</div>
            <a className="nav-item" href="#workspace" aria-current="page">
              <PackageSearch size={18} aria-hidden="true" />
              设备工作区
              <ArrowUpRight size={16} aria-hidden="true" />
            </a>
            <div className="sidebar-journey">
              <span className="nav-label">从照片，到答案</span>
              <ol>
                <li>
                  <span>01</span>认识你的设备
                </li>
                <li>
                  <span>02</span>核验官方资料
                </li>
                <li>
                  <span>03</span>开始有据问答
                </li>
              </ol>
            </div>
            <div className="sidebar-bottom">
              <span className="sidebar-shield">
                <ShieldCheck size={21} aria-hidden="true" />
              </span>
              <strong>好答案，有据可查。</strong>
              <p>
                从可信的官方资料中，
                <br />
                找到设备的正确打开方式。
              </p>
            </div>
            <span className="sidebar-caption">
              BUILT FOR EVERYDAY CURIOSITY
            </span>
          </aside>
          <div className="main-area">
            <header className="topbar">
              <span className="topbar-title">
                <span className="mobile-brand">
                  <Brand />
                </span>
                <span className="breadcrumb">
                  工作空间<span>/</span>
                </span>
                <span className="topbar-page-name">产品助手</span>
              </span>
              <div>
                <span className="topbar-status">
                  <CircleDot size={13} aria-hidden="true" />
                  官方资料驱动
                </span>
                <span className="user-avatar" aria-hidden="true">
                  {(user.displayName || user.email).slice(0, 1).toUpperCase()}
                </span>
                <span className="user-email">{user.email}</span>
                <Button
                  type="text"
                  aria-label="退出登录"
                  icon={<LogOut size={16} />}
                  onClick={() => void post('/auth/logout', {}).then(refresh)}
                >
                  退出
                </Button>
              </div>
            </header>
            <HomePage />
          </div>
        </div>
      )}
    </SessionContext.Provider>
  );
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ConfigProvider
      locale={zhCN}
      theme={{
        token: {
          colorPrimary: '#185B4A',
          colorSuccess: '#247451',
          colorWarning: '#B45309',
          colorError: '#DC2626',
          colorTextBase: '#1C3028',
          colorBgLayout: '#F7F8F2',
          colorBorder: '#DCE3D9',
          borderRadius: 12,
          fontSize: 14,
        },
        components: {
          Button: { controlHeight: 44, primaryShadow: 'none' },
          Input: {
            controlHeight: 46,
            activeShadow: '0 0 0 3px rgba(24, 91, 74, 0.1)',
          },
          Card: { borderRadiusLG: 22 },
        },
      }}
    >
      <BrowserRouter basename={import.meta.env.BASE_URL}>
        <App />
      </BrowserRouter>
    </ConfigProvider>
  </StrictMode>,
);
