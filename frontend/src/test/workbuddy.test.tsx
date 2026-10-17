import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import WorkBuddyPage from '../pages/WorkBuddyPage';
import type { User } from '../lib/api';
import { response } from './fixtures';

function mount(enabled = true, role: User['role'] = 'analyst') {
  const credential = {
    id: 'credential-id',
    name: '我的 WorkBuddy',
    scopes: ['metadata:read'],
    created_at: '2026-10-12T00:00:00Z',
    expires_at: '2099-01-01T00:00:00Z',
    revoked_at: null as string | null,
  };
  let created = false;
  const fetch = vi.fn(async (path: string, options?: RequestInit) => {
    if (path.includes('/collector/repair/targets')) return response([]);
    if (path.includes('/collector/repair/readiness'))
      return response({
        ready: false,
        browser_ready: false,
        sessions_writable: true,
        browsers_path_configured: false,
      });
    if (path.includes('/admin/integrations/workbuddy/tokens')) {
      if (options?.method === 'DELETE') return new Response(null, { status: 204 });
      return response([
        {
          ...credential,
          id: 'other-id',
          user_email: 'colleague@example.com',
          user_name: '同事',
          kind: 'oauth',
          authorization_expires_at: '2099-01-01T00:00:00Z',
        },
      ]);
    }
    if (path.endsWith('/status'))
      return response({ mcp_enabled: enabled, report_api_ready: false });
    if (options?.method === 'POST') {
      created = true;
      return response({ ...credential, token: 'synthetic-secret-do-not-persist' });
    }
    if (options?.method === 'DELETE') {
      credential.revoked_at = '2026-10-12T01:00:00Z';
      return new Response(null, { status: 204 });
    }
    return response(created ? [credential] : []);
  });
  vi.stubGlobal('fetch', fetch);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={client}>
      <WorkBuddyPage role={role} />
    </QueryClientProvider>,
  );
  if (role !== 'viewer') fireEvent.click(screen.getByText('高级设置：手动访问令牌（兼容旧连接）'));
  return { client, fetch, ...view };
}

it('keeps the one-time credential out of query caches and browser storage, and hides it on revocation', async () => {
  localStorage.clear();
  const { client, fetch } = mount();
  await screen.findByText('问数接入已启用。');
  fireEvent.click(screen.getByRole('button', { name: '创建访问令牌' }));
  expect(await screen.findByLabelText('新访问令牌')).toHaveValue('synthetic-secret-do-not-persist');
  expect(
    JSON.stringify(
      client
        .getQueryCache()
        .getAll()
        .map((q) => q.state.data),
    ),
  ).not.toContain('synthetic-secret');
  expect(JSON.stringify(localStorage)).not.toContain('synthetic-secret');
  expect(JSON.stringify(sessionStorage)).not.toContain('synthetic-secret');
  fireEvent.click(await screen.findByRole('button', { name: '撤销 我的 WorkBuddy' }));
  await waitFor(() => expect(screen.queryByLabelText('新访问令牌')).not.toBeInTheDocument());
  expect(
    fetch.mock.calls.some(
      ([path, options]) => path.endsWith('/credential-id') && options?.method === 'DELETE',
    ),
  ).toBe(true);
});

it('prevents credential creation while the service is disabled', async () => {
  mount(false);
  await screen.findByText('问数接入尚未启用，请联系管理员完成服务配置。');
  expect(screen.getByRole('button', { name: '创建访问令牌' })).toBeDisabled();
});

it('lets account owners read repair guidance without requesting analyst-only APIs', async () => {
  const { fetch } = mount(true, 'viewer');
  expect(screen.getByRole('region', { name: '采集登录修复指引' })).toBeInTheDocument();
  expect(screen.getByText(/登录修复不需要开通问数连接/)).toBeInTheDocument();
  expect(screen.getByText(/知乎、京东暂不支持此自助流程/)).toBeInTheDocument();
  expect(screen.queryByText('高级设置：手动访问令牌（兼容旧连接）')).not.toBeInTheDocument();
  expect(await screen.findByText(/暂无负责的账号/)).toBeInTheDocument();
  // Only the owner's own repair targets; no analyst-only WorkBuddy APIs.
  expect(
    fetch.mock.calls.every(([path]) => String(path).includes('/collector/repair/targets')),
  ).toBe(true);
});

it('copies actionable repair and draft instructions without creating or sending a report', async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal('navigator', { clipboard: { writeText } });
  const { fetch } = mount();
  await screen.findByText('问数接入已启用。');
  fireEvent.click(screen.getByRole('button', { name: '复制登录修复指令' }));
  await waitFor(() =>
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining('帮我修复采集登录')),
  );
  fireEvent.click(screen.getByRole('button', { name: '复制周报草稿指令' }));
  await waitFor(() =>
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining('输出可编辑 HTML，不推送')),
  );
  expect(screen.getByText(/不会自动归档到平台周报页/)).toBeInTheDocument();
  expect(screen.getByText(/最近一个完整周，生成后会按配置推送企业微信/)).toBeInTheDocument();
  expect(
    fetch.mock.calls.every(([, options]) => !options?.method || options.method === 'GET'),
  ).toBe(true);
  expect(screen.getByRole('link', { name: '查看已生成周报 →' })).toHaveAttribute(
    'href',
    '?page=reports',
  );
});

it('directs administrators to the existing confirmation flow for formal reports', async () => {
  mount(true, 'admin');
  await screen.findByText('问数接入已启用。');
  expect(screen.getByRole('link', { name: '前往周报页生成或查看 →' })).toHaveAttribute(
    'href',
    '?page=reports',
  );
  expect(screen.getByText(/先核对收件人是否收到/)).toBeInTheDocument();
});

it('lets administrators revoke a colleague connection and see repair readiness', async () => {
  const { fetch } = mount(true, 'admin');
  expect(await screen.findByText('同事')).toBeInTheDocument();
  expect(await screen.findByText(/复检环境：未就绪/)).toBeInTheDocument();
  expect(screen.getByText(/未设置 PLAYWRIGHT_BROWSERS_PATH/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '撤销连接' }));
  expect(screen.getByText(/同事 的 WorkBuddy 将立即无法查询/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '确认撤销连接' }));
  await waitFor(() =>
    expect(
      fetch.mock.calls.some(
        ([path, options]) =>
          String(path).endsWith('/admin/integrations/workbuddy/tokens/other-id') &&
          options?.method === 'DELETE',
      ),
    ).toBe(true),
  );
});

it('does not request admin connection data for analysts', async () => {
  const { fetch } = mount(true, 'analyst');
  await screen.findByText('问数接入已启用。');
  expect(fetch.mock.calls.some(([path]) => String(path).includes('/admin/'))).toBe(false);
  expect(screen.queryByRole('region', { name: '管理员' })).not.toBeInTheDocument();
});
