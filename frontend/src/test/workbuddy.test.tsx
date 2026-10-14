import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import WorkBuddyPage from '../pages/WorkBuddyPage';
import { response } from './fixtures';

function mount(enabled = true) {
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
      <WorkBuddyPage />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByText('高级设置：手动访问令牌（兼容旧连接）'));
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
