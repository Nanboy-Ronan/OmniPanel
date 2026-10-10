import { lazy, Suspense, useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { z } from 'zod';
import {
  BarChart3,
  ChevronRight,
  LogOut,
  Menu,
  PanelLeftClose,
  PanelLeftOpen,
  Search,
  X,
} from 'lucide-react';
import { request, session, userSchema, loginStatusSchema, type User } from './lib/api';
import { loginUrl, rememberLoginDestination } from './lib/auth';
import { navigate, useLocationSearch } from './lib/navigation';
import { EmptyState, ErrorState, Loading, PageBoundary, PageSkeleton } from './components/ui';
import { CommandPalette } from './components/CommandPalette';
import { ToastProvider } from './components/Toast';
import { navIcon } from './lib/navIcons';
import KpiPage from './pages/KpiPage';
import TasksPage from './pages/TasksPage';
import { routes, allowed, resolveRoute } from './lib/routes';
const OrdersPage = lazy(() => import('./pages/OrdersPage'));
const UploadPage = lazy(() => import('./pages/UploadPage'));
const AnalysisPage = lazy(() => import('./pages/AnalysisPage'));
const CustomersPage = lazy(() => import('./pages/CustomersPage'));
const IdentityPage = lazy(() =>
  import('./pages/CustomersPage').then((m) => ({ default: m.IdentityPage })),
);
const RetentionPage = lazy(() => import('./pages/RetentionPage'));
const DictionaryPage = lazy(() => import('./pages/DictionaryPage'));
const SqlPage = lazy(() => import('./pages/SqlPage'));
const MediaPage = lazy(() => import('./pages/MediaPage'));
const PgyPage = lazy(() => import('./pages/PgyPage'));
const ImpactPage = lazy(() => import('./pages/ImpactPage'));
const ReportsPage = lazy(() => import('./pages/ReportsPage'));
const UsersPage = lazy(() => import('./pages/AdminPage'));
const LogsPage = lazy(() => import('./pages/AdminPage').then((m) => ({ default: m.LogsPage })));
const DatabasePage = lazy(() =>
  import('./pages/AdminPage').then((m) => ({ default: m.DatabasePage })),
);
const CollectorPage = lazy(() =>
  import('./pages/AdminPage').then((m) => ({ default: m.CollectorPage })),
);
function Page({ page, user }: { page: string; user: User }) {
  const admin = user.role === 'admin';
  switch (page) {
    case 'overview':
      return <KpiPage admin={admin} />;
    case 'tasks':
      return <TasksPage />;
    case 'orders':
      return <OrdersPage />;
    case 'upload':
      return <UploadPage />;
    case 'analysis':
      return <AnalysisPage />;
    case 'customers':
      return <CustomersPage />;
    case 'identity':
      return <IdentityPage />;
    case 'retention':
      return <RetentionPage />;
    case 'dictionary':
      return <DictionaryPage analyst={user.role !== 'viewer'} />;
    case 'sql':
      return <SqlPage />;
    case 'reports':
      return <ReportsPage admin={admin} />;
    case 'wechat':
    case 'traffic':
    case 'xhs':
    case 'zhihu':
    case 'channels':
      return <MediaPage key={page} platform={page} admin={admin} />;
    case 'pgy':
      return <PgyPage admin={admin} />;
    case 'impact':
      return <ImpactPage />;
    case 'users':
      return <UsersPage user={user} />;
    case 'logs':
      return <LogsPage />;
    case 'database':
      return <DatabasePage />;
    case 'collector':
      return <CollectorPage />;
    default:
      return <EmptyState title="页面不存在" />;
  }
}

export default function App({ callback = null }: { callback?: Promise<string> | null }) {
  const client = useQueryClient();
  const [token, setToken] = useState(session.get);
  const [pending, setPending] = useState(!!callback);
  const [authError, setAuthError] = useState<Error | null>(null);
  useEffect(() => {
    let active = true;
    callback
      ?.then((value) => {
        if (active) {
          client.clear();
          setToken(value);
        }
      })
      .catch((error) => {
        if (active) setAuthError(error);
      })
      .finally(() => {
        if (active) setPending(false);
      });
    const expire = () => {
      client.clear();
      setToken(null);
      setAuthError(new Error('登录已过期，请重新登录。'));
    };
    window.addEventListener('session-expired', expire);
    return () => {
      active = false;
      window.removeEventListener('session-expired', expire);
    };
  }, [callback, client]);
  const me = useQuery({
    queryKey: ['me'],
    queryFn: ({ signal }) => request('/auth/me', userSchema, { signal }),
    enabled: !!token && !pending,
  });
  if (pending)
    return (
      <div className="login-layout">
        <Loading label="正在验证企业微信身份" />
      </div>
    );
  if (!token) return <Login error={authError} />;
  if (me.isPending)
    return (
      <div className="login-layout">
        <Loading label="正在加载工作台" />
      </div>
    );
  const logout = () => {
    // Record the sign-out while the token is still valid; never block leaving on it.
    void request('/auth/logout', z.null(), { method: 'POST' }).catch(() => undefined);
    session.clear();
    client.clear();
    setToken(null);
    setAuthError(null);
  };
  if (!me.data)
    return (
      <div className="login-layout">
        <div className="login-card">
          <ErrorState
            error={me.error ?? new Error('无法确认登录身份。')}
            retry={() => void me.refetch()}
          />
          <button onClick={logout}>返回登录</button>
        </div>
      </div>
    );
  return (
    <ToastProvider>
      <Shell user={me.data} onLogout={logout} />
    </ToastProvider>
  );
}
function Login({ error }: { error: Error | null }) {
  const status = useQuery({
    queryKey: ['login-status'],
    queryFn: ({ signal }) =>
      request('/auth/wecom/status', loginStatusSchema, { signal, anonymous: true }),
  });
  return (
    <main className="login-layout">
      <div className="login-intro">
        <div className="brand">
          <span className="brand-icon">
            <BarChart3 size={23} />
          </span>
          OmniPanel
        </div>
        <h1>
          让经营数据
          <br />
          成为决策依据。
        </h1>
        <p>统一指标、明确口径，持续追踪经营表现。</p>
      </div>
      <section className="login-card">
        <div className="eyebrow">WORKSPACE ACCESS</div>
        <h2>登录工作台</h2>
        <p>使用企业微信身份访问授权的数据。</p>
        {error && <ErrorState compact error={error} />}
        {status.isPending ? (
          <Loading label="正在检查登录服务" />
        ) : status.isError ? (
          <ErrorState error={status.error} retry={() => void status.refetch()} />
        ) : status.data.enabled ? (
          <div className="login-actions">
            <a className="button primary" href={loginUrl('qr')} onClick={rememberLoginDestination}>
              企业微信扫码登录
              <ChevronRight size={17} />
            </a>
            <a className="button" href={loginUrl('mobile')} onClick={rememberLoginDestination}>
              在企业微信内授权
            </a>
          </div>
        ) : (
          <EmptyState title="企业微信登录尚未配置">请联系管理员完成登录配置。</EmptyState>
        )}
      </section>
    </main>
  );
}
const COLLAPSE_KEY = 'rpa.console.navCollapsed';
const shortcutLabel = /Mac|iPhone|iPad/.test(navigator.userAgent) ? '⌘K' : 'Ctrl K';
function readCollapsed() {
  try {
    return localStorage.getItem(COLLAPSE_KEY) === '1';
  } catch {
    return false;
  }
}
function Shell({ user, onLogout }: { user: User; onLogout: () => void }) {
  const search = useLocationSearch();
  const route = resolveRoute(
    new URLSearchParams(search).get('page') || (user.role === 'viewer' ? 'orders' : 'overview'),
  );
  const page = route?.key ?? 'unknown';
  const [navOpen, setNavOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const title = route?.title ?? '页面不存在';
  const visible = routes.filter((item) => allowed(user.role, item.role));
  useEffect(() => {
    document.title = `${title} · OmniPanel`;
  }, [title]);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key.toLowerCase() !== 'k' || !(event.metaKey || event.ctrlKey)) return;
      // Ctrl+K is kill-line in macOS text fields; only ⌘K is claimed while editing text.
      const target = event.target instanceof Element ? event.target : null;
      if (
        event.ctrlKey &&
        !event.metaKey &&
        target?.closest('textarea, [contenteditable]:not([contenteditable="false"])')
      )
        return;
      event.preventDefault();
      setPaletteOpen(true);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);
  const toggleCollapsed = () => {
    setCollapsed((value) => {
      try {
        localStorage.setItem(COLLAPSE_KEY, value ? '0' : '1');
      } catch {
        // Preference is a convenience; the rail still toggles for this visit.
      }
      return !value;
    });
  };
  const go = (value: string) => {
    navigate({
      page: value,
      offset: null,
      account: null,
      content: null,
      columns: null,
      q: null,
      start: null,
      end: null,
      platform: null,
      range: null,
      view: null,
      blogger: null,
      campaign: null,
      anchor: null,
      period: null,
      note: null,
      metric: null,
      source: null,
      report: null,
    });
    setNavOpen(false);
    setPaletteOpen(false);
  };
  return (
    <div className={`app-shell ${collapsed ? 'nav-collapsed' : ''}`}>
      <a className="skip-link" href="#content">
        跳到主要内容
      </a>
      {navOpen && (
        <button className="nav-backdrop" aria-label="关闭导航" onClick={() => setNavOpen(false)} />
      )}
      <aside className={`sidebar ${navOpen ? 'open' : ''}`} aria-label="主导航">
        <div className="brand">
          <span className="brand-icon">
            <BarChart3 size={20} />
          </span>
          <div>
            OmniPanel<small>BUSINESS INTELLIGENCE</small>
          </div>
          <button
            className="mobile-close icon-button"
            onClick={() => setNavOpen(false)}
            aria-label="关闭导航"
          >
            <X size={18} />
          </button>
        </div>
        <nav>
          {['商城分析', '数据管理', '内容分析', '系统管理'].map((group) => {
            const items = visible.filter((item) => item.group === group);
            return items.length ? (
              <div key={group}>
                <div className="nav-label">{group}</div>
                {items.map((item) => {
                  const Icon = navIcon(item.key);
                  return (
                    <button
                      key={item.key}
                      className={`nav-item ${page === item.key ? 'active' : ''}`}
                      aria-current={page === item.key ? 'page' : undefined}
                      title={collapsed ? item.title : undefined}
                      onClick={() => go(item.key)}
                    >
                      <Icon size={17} aria-hidden />
                      <span className="nav-text">{item.title}</span>
                    </button>
                  );
                })}
              </div>
            ) : null;
          })}
        </nav>
        <div className="sidebar-footer">
          <div className="avatar" title={user.display_name || user.email}>
            {(user.display_name || user.email).slice(0, 1).toUpperCase()}
          </div>
          <div className="user-info">
            <strong>{user.display_name || user.email}</strong>
            <small>{{ admin: '管理员', analyst: '分析师', viewer: '查看者' }[user.role]}</small>
          </div>
          <button className="logout" onClick={onLogout} aria-label="退出登录" title="退出登录">
            <LogOut size={17} />
          </button>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <div>
            <button
              className="mobile-menu icon-button"
              aria-label="打开导航"
              aria-expanded={navOpen}
              onClick={() => setNavOpen(true)}
            >
              <Menu size={20} />
            </button>
            <button
              className="collapse-toggle icon-button"
              aria-label={collapsed ? '展开侧边栏' : '收起侧边栏'}
              title={collapsed ? '展开侧边栏' : '收起侧边栏'}
              aria-pressed={collapsed}
              onClick={toggleCollapsed}
            >
              {collapsed ? <PanelLeftOpen size={18} /> : <PanelLeftClose size={18} />}
            </button>
            <span className="crumb-group">{route?.group ?? '工作台'}</span>
            <ChevronRight size={14} className="crumb-sep" />
            <strong>{title}</strong>
          </div>
          <button
            className="search-trigger"
            aria-label="快速跳转页面"
            aria-keyshortcuts="Meta+K Control+K"
            onClick={() => setPaletteOpen(true)}
          >
            <Search size={15} />
            <span>跳转到页面…</span>
            <kbd>{shortcutLabel}</kbd>
          </button>
        </header>
        <main id="content" className="content" tabIndex={-1}>
          <PageBoundary key={page}>
            <Suspense fallback={<PageSkeleton />}>
              {!route ? (
                <EmptyState title="页面不存在">
                  <button onClick={() => go(user.role === 'viewer' ? 'orders' : 'overview')}>
                    返回工作台
                  </button>
                </EmptyState>
              ) : !allowed(user.role, route.role) ? (
                <EmptyState title="当前账号没有此页面的访问权限">
                  <button onClick={() => go('orders')}>查看订单</button>
                </EmptyState>
              ) : (
                <Page page={page} user={user} />
              )}
            </Suspense>
          </PageBoundary>
          <footer className="app-footer">
            <span>OmniPanel</span>
            <span>数据随导入更新 · 非实时交易系统</span>
          </footer>
        </main>
      </div>
      {paletteOpen && (
        <CommandPalette
          items={visible}
          current={page}
          onGo={go}
          onClose={() => setPaletteOpen(false)}
        />
      )}
    </div>
  );
}
