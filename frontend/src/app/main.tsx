import {
  lazy,
  StrictMode,
  Suspense,
  useCallback,
  useEffect,
  useState,
} from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Route, Routes } from 'react-router-dom';
import { ConfigProvider, Spin } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { AppShell } from '../components/AppShell';
import { api, post } from '../services/api';
import { SessionContext } from '../stores/session';
import type { User } from '../types/api';
import { AuthPage } from '../pages/AuthPage';
import { HomePage } from '../pages/HomePage';
import './styles.css';

const AdminPanel = lazy(() =>
  import('../features/admin/AdminPanel').then((module) => ({
    default: module.AdminPanel,
  })),
);

export function App() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [adminUser, setAdminUser] = useState<string | null>(null);
  const admin = Boolean(user && adminUser === user.id);
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
  useEffect(() => {
    let active = true;
    if (user)
      void api<{ adminConsole?: boolean }>('/capabilities')
        .then((value) => {
          if (active) setAdminUser(value.adminConsole ? user.id : null);
        })
        .catch(() => {
          if (active) setAdminUser(null);
        });
    return () => {
      active = false;
    };
  }, [user]);
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
        <AppShell
          user={user}
          admin={admin}
          onRefresh={refresh}
          onLogout={() => post('/auth/logout', {}).then(refresh)}
        >
          <Routes>
            <Route
              path="/admin"
              element={
                <Suspense fallback={<Spin />}>
                  <AdminPanel />
                </Suspense>
              }
            />
            <Route path="*" element={<HomePage />} />
          </Routes>
        </AppShell>
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
          colorPrimary: '#1D6252',
          colorSuccess: '#247451',
          colorWarning: '#B45309',
          colorError: '#DC2626',
          colorTextBase: '#203D34',
          colorBgLayout: '#F4F5F0',
          colorBorder: '#DCE4DA',
          borderRadius: 12,
          fontSize: 14,
          fontFamily:
            '-apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif',
        },
        components: {
          Button: { controlHeight: 44, primaryShadow: '0 3px 8px #173e3314' },
          Input: {
            controlHeight: 46,
            activeShadow: '0 0 0 3px rgba(24, 91, 74, 0.1)',
          },
          Card: { borderRadiusLG: 20 },
          Table: { headerBg: '#F3F6F1', cellPaddingBlockSM: 14 },
        },
      }}
    >
      <BrowserRouter basename={import.meta.env.BASE_URL}>
        <App />
      </BrowserRouter>
    </ConfigProvider>
  </StrictMode>,
);
