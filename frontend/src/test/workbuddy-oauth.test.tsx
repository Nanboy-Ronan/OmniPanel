import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import WorkBuddyAuthorizePage from '../pages/WorkBuddyAuthorizePage';
import { response } from './fixtures';

function mount(allowed: boolean) {
  window.history.replaceState(null, '', '/console/?page=workbuddy&authorization=request-id');
  const fetch = vi.fn(async (_path: string, options?: RequestInit) =>
    options?.method === 'POST'
      ? response({ detail: 'expired' }, 400)
      : response({
          scopes: ['metadata:read', 'analytics:media'],
          can_authorize: allowed,
          client: 'WorkBuddy 桌面客户端',
          requested_at: '2026-10-14T02:00:00Z',
        }),
  );
  vi.stubGlobal('fetch', fetch);
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <WorkBuddyAuthorizePage
        user={{
          id: 'user',
          email: 'test@example.com',
          role: allowed ? 'analyst' : 'viewer',
        }}
        onLogout={vi.fn()}
      />
    </QueryClientProvider>,
  );
  return fetch;
}
it('requires an explicit click before authorizing and displays requested data scopes', async () => {
  const fetch = mount(true);
  const approve = await screen.findByRole('button', { name: '授权并连接 WorkBuddy' });
  expect(await screen.findByText('公司媒体账号、内容明细、逐日指标与来源分析')).toBeInTheDocument();
  expect(screen.getByText('请求来自 WorkBuddy 桌面客户端')).toBeInTheDocument();
  expect(fetch.mock.calls.some(([, options]) => options?.method === 'POST')).toBe(false);
  fireEvent.click(approve);
  await waitFor(() =>
    expect(
      fetch.mock.calls.some(([, options]) => options?.body === JSON.stringify({ approve: true })),
    ).toBe(true),
  );
  expect(await screen.findByText('请求未能完成，请检查输入或重新登录。')).toBeInTheDocument();
});
it('explains missing permissions and lets a viewer cancel without granting access', async () => {
  const fetch = mount(false);
  expect(await screen.findByRole('button', { name: '授权并连接 WorkBuddy' })).toBeDisabled();
  expect(screen.getByRole('alert')).toHaveTextContent('请联系平台管理员');
  fireEvent.click(screen.getByRole('button', { name: '取消' }));
  await waitFor(() =>
    expect(
      fetch.mock.calls.some(([, options]) => options?.body === JSON.stringify({ approve: false })),
    ).toBe(true),
  );
});
