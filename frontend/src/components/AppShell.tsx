import { useEffect, useState, type ReactNode } from 'react';
import { Link, NavLink, useLocation } from 'react-router-dom';
import { Alert, Button, Drawer } from 'antd';
import {
  ArrowUpRight,
  ChevronDown,
  CircleCheck,
  LayoutDashboard,
  LogOut,
  MessageCircle,
  PackageSearch,
  ShieldCheck,
  UserRound,
} from 'lucide-react';
import type { User } from '../types/api';
import { Brand } from './Brand';
import { DeleteAction } from './DeleteAction';

/** 导航只改变展示入口；管理员权限仍由capabilities和每次服务端请求共同校验。 */
export function AppShell({
  user,
  admin,
  onRefresh,
  onLogout,
  children,
}: {
  user: User;
  admin: boolean;
  onRefresh: () => Promise<void>;
  onLogout: () => Promise<void>;
  children: ReactNode;
}) {
  const [accountOpen, setAccountOpen] = useState(false);
  const [accountBusy, setAccountBusy] = useState(false);
  const [accountError, setAccountError] = useState('');
  const { pathname } = useLocation();
  const management = pathname === '/admin';
  const history =
    pathname === '/history' || pathname.startsWith('/conversations');
  // 登录前表单可能已滚动到屏幕下方；进入新空间时重置位置并给键盘一个稳定起点。
  useEffect(() => {
    window.scrollTo(0, 0);
    document.getElementById('main-content')?.focus({ preventScroll: true });
  }, [pathname]);
  const name = user.displayName || user.email;
  const logout = async () => {
    setAccountBusy(true);
    setAccountError('');
    try {
      await onLogout();
      setAccountOpen(false);
    } catch {
      setAccountError('退出失败，请重试。');
    } finally {
      setAccountBusy(false);
    }
  };
  return (
    <div className={`app-shell${management ? ' management-shell' : ''}`}>
      <a className="skip-link" href="#main-content">
        跳到主要内容
      </a>
      <aside className="sidebar">
        <NavLink className="brand-home" to="/" aria-label="识物说首页">
          <Brand inverted />
        </NavLink>
        <span className="nav-label">工作空间</span>
        <nav className="desktop-nav" aria-label="主导航">
          <NavLink className="nav-item" to="/" end>
            <PackageSearch size={19} aria-hidden="true" />
            设备工作区
            <ArrowUpRight size={15} aria-hidden="true" />
          </NavLink>
          <Link
            className={`nav-item${history ? ' active' : ''}`}
            to="/history"
            aria-current={history ? 'page' : undefined}
          >
            <MessageCircle size={19} aria-hidden="true" />
            历史对话
            <ArrowUpRight size={15} aria-hidden="true" />
          </Link>
          {admin && (
            <NavLink className="nav-item" to="/admin">
              <LayoutDashboard size={19} aria-hidden="true" />
              运行管理
              <ArrowUpRight size={15} aria-hidden="true" />
            </NavLink>
          )}
        </nav>
        <div className="sidebar-journey">
          <span className="nav-label">
            {management ? '管理范围' : '从照片，到答案'}
          </span>
          <ol>
            {(management
              ? ['掌握运行情况', '调整后续任务', '追踪配置变更']
              : ['认识你的设备', '核验官方资料', '开始有据问答']
            ).map((label, index) => (
              <li key={label}>
                <span>{String(index + 1).padStart(2, '0')}</span>
                {label}
              </li>
            ))}
          </ol>
        </div>
        <div className="sidebar-bottom">
          <ShieldCheck size={23} aria-hidden="true" />
          <strong>
            {management ? '只看运行，不看隐私。' : '好答案，有据可查。'}
          </strong>
          <p>
            {management
              ? '统计不包含私人问题、照片或模型正文。'
              : '从核验资料中，找到设备的正确打开方式。'}
          </p>
        </div>
        <span className="sidebar-caption">ITEMTALK · 识物，说清楚</span>
      </aside>

      <div className="main-area">
        <header className="topbar">
          <NavLink className="mobile-brand" to="/" aria-label="识物说首页">
            <Brand />
          </NavLink>
          <div className="topbar-title">
            <span className="breadcrumb">工作空间</span>
            <span className="breadcrumb-separator" aria-hidden="true">
              /
            </span>
            <span>
              {management ? '运行管理' : history ? '历史对话' : '设备工作区'}
            </span>
          </div>
          <div className="topbar-actions">
            <span className="topbar-status">
              <CircleCheck size={15} aria-hidden="true" />
              {management ? '仅管理员可见' : '答案附有资料来源'}
            </span>
            <Button
              className="account-button"
              type="text"
              aria-label="打开账号菜单"
              onClick={() => setAccountOpen(true)}
            >
              <span className="user-avatar" aria-hidden="true">
                {name.slice(0, 1).toUpperCase()}
              </span>
              <span className="user-email">{name}</span>
              <ChevronDown size={14} aria-hidden="true" />
            </Button>
          </div>
        </header>
        <div id="main-content" tabIndex={-1}>
          {children}
        </div>
        <footer className="workspace-footer">
          <span>ItemTalk 识物说</span>
          <span>保持好奇，也保持有据。</span>
        </footer>
      </div>

      <nav className="mobile-nav" aria-label="移动页面导航">
        <NavLink to="/" end>
          <PackageSearch size={21} aria-hidden="true" />
          <span>设备工作区</span>
        </NavLink>
        <Link
          className={history ? 'active' : ''}
          to="/history"
          aria-current={history ? 'page' : undefined}
        >
          <MessageCircle size={21} aria-hidden="true" />
          <span>历史对话</span>
        </Link>
        {admin && (
          <NavLink to="/admin">
            <LayoutDashboard size={21} aria-hidden="true" />
            <span>运行管理</span>
          </NavLink>
        )}
        <button type="button" onClick={() => setAccountOpen(true)}>
          <UserRound size={21} aria-hidden="true" />
          <span>我的账号</span>
        </button>
      </nav>
      <Drawer
        title="账号与隐私"
        open={accountOpen}
        onClose={() => setAccountOpen(false)}
        size={360}
        rootClassName="account-drawer"
      >
        {accountError && (
          <Alert title={accountError} type="error" role="alert" />
        )}
        <div className="account-profile">
          <span className="user-avatar">{name.slice(0, 1).toUpperCase()}</span>
          <strong>{user.email}</strong>
          <p>{admin ? '管理员账号' : '个人设备工作空间'}</p>
        </div>
        <Button
          block
          loading={accountBusy}
          icon={<LogOut size={17} aria-hidden="true" />}
          onClick={() => void logout()}
        >
          退出登录
        </Button>
        <div className="account-danger-zone">
          <h2>账号管理</h2>
          <p>删除前请确认，你的个人设备记录将无法继续访问。</p>
          <DeleteAction
            path="/me"
            disabled={accountBusy}
            label="删除账号"
            explanation="账号将立即停用，个人照片、会话、记忆和操作引导会随后清除。公共资料会保留。"
            onDeleted={async () => {
              await onRefresh();
              setAccountOpen(false);
            }}
          />
        </div>
      </Drawer>
    </div>
  );
}
