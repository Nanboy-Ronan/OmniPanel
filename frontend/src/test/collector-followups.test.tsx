import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { AccountManager } from '../components/Accounts';
import { ToastProvider } from '../components/Toast';
import CollectorRepairPage from '../pages/CollectorRepairPage';
import { response } from './fixtures';

function wrap(node: React.ReactNode) {
  return render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <ToastProvider>{node}</ToastProvider>
    </QueryClientProvider>,
  );
}

it('tells the owner an immediate collect was queued after a successful repair', async () => {
  window.history.replaceState(null, '', '/console/?repair=req');
  let status = 'pending';
  const target = {
    platform: 'xhs',
    account_id: 7,
    name: '目标号',
    label: '小红书 · 目标号',
    owner_id: 'u',
    can_repair: true,
    verify_status: null,
    login_missing: false,
  };
  vi.stubGlobal(
    'fetch',
    vi.fn(async (_path: string, options?: RequestInit) => {
      if (options?.method === 'POST') {
        status = 'success';
        return response({ status: 'approved' });
      }
      return response({ status, targets: [{ ...target, collect_pending: status === 'success' }] });
    }),
  );
  wrap(
    <CollectorRepairPage
      user={{ id: 'u', email: 'owner@example.com', role: 'viewer' }}
      onLogout={vi.fn()}
    />,
  );
  fireEvent.change(await screen.findByRole('combobox', { name: '采集账号' }), {
    target: { value: 'xhs:7' },
  });
  fireEvent.change(screen.getByLabelText('校验码'), { target: { value: 'ABCDEFGH' } });
  fireEvent.click(screen.getByRole('button', { name: '确认账号，开始本地登录' }));
  expect(await screen.findByText(/已安排立即采集该账号/)).toBeInTheDocument();
});

it('saves portal display names, with the 蒲公英 one only when 蒲公英 is enabled', async () => {
  const fetch = vi.fn(async (_path: string, options?: RequestInit) =>
    options?.method === 'PATCH'
      ? response({ id: 3 })
      : response([
          {
            id: 3,
            name: '阳光小铺（主号）',
            pgy_enabled: true,
            portal_name: null,
            pgy_portal_name: null,
          },
        ]),
  );
  vi.stubGlobal('fetch', fetch);
  wrap(<AccountManager path="/media/xhs/accounts" admin xhs />);
  fireEvent.click(await screen.findByText(/门户显示名（同账号名称）/));
  fireEvent.change(screen.getByLabelText('门户显示名 3'), { target: { value: ' 阳光小铺 ' } });
  fireEvent.change(screen.getByLabelText('蒲公英门户显示名 3'), {
    target: { value: '阳光小铺品牌' },
  });
  fireEvent.click(screen.getByRole('button', { name: '保存门户显示名' }));
  await waitFor(() =>
    expect(
      fetch.mock.calls.some(
        ([path, options]) =>
          String(path).endsWith('/media/xhs/accounts/3') &&
          options?.body ===
            JSON.stringify({ portal_name: '阳光小铺', pgy_portal_name: '阳光小铺品牌' }),
      ),
    ).toBe(true),
  );
});

it('lets an admin queue an immediate collect from the session list', async () => {
  const { CollectorPage } = await import('../pages/AdminPage');
  const fetch = vi.fn(async (path: string, options?: RequestInit) => {
    if (options?.method === 'POST') return response({ status: 'queued' }, 202);
    if (String(path).includes('/admin/collector/sessions'))
      return response([
        { platform: 'xhs', account_id: 3, account_name: '品牌号', updated_at: 'x' },
      ]);
    return response([]);
  });
  vi.stubGlobal('fetch', fetch);
  wrap(<CollectorPage />);
  fireEvent.click(await screen.findByRole('button', { name: '立即采集' }));
  fireEvent.click(screen.getByRole('button', { name: '确认立即采集' }));
  await waitFor(() =>
    expect(
      fetch.mock.calls.some(
        ([path, options]) =>
          String(path).endsWith('/admin/collector/requests') &&
          options?.body === JSON.stringify({ platform: 'xhs', account_id: 3 }),
      ),
    ).toBe(true),
  );
});
