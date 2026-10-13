import { StrictMode, useCallback, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { Button, ConfigProvider, Spin } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { LogOut, PackageSearch, ShieldCheck } from 'lucide-react';
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
              <PackageSearch aria-hidden="true" />
              <strong>
                识物说 <small>ItemTalk</small>
              </strong>
            </a>
            <div className="nav-label">工作空间</div>
            <div className="nav-item">
              <PackageSearch size={18} aria-hidden="true" />
              我的产品
            </div>
            <div className="sidebar-bottom">
              <ShieldCheck size={18} aria-hidden="true" />
              <p>
                从可信来源
                <br />
                找到可靠答案
              </p>
            </div>
          </aside>
          <div className="main-area">
            <header className="topbar">
              <span>产品助手</span>
              <div>
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
          colorPrimary: '#2563EB',
          colorSuccess: '#15803D',
          colorWarning: '#B45309',
          colorError: '#DC2626',
          colorTextBase: '#172033',
          colorBgLayout: '#F6F8FC',
          borderRadius: 8,
          fontSize: 14,
        },
        components: {
          Button: { controlHeight: 40 },
          Card: { borderRadiusLG: 12 },
        },
      }}
    >
      <BrowserRouter basename={import.meta.env.BASE_URL}>
        <App />
      </BrowserRouter>
    </ConfigProvider>
  </StrictMode>,
);
