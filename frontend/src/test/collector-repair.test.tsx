import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import CollectorRepairPage from '../pages/CollectorRepairPage';
import { response } from './fixtures';
function mount(owned: boolean) {
  window.history.replaceState(null, '', '/console/?repair=public-request');
  let approved = false;
  const fetch = vi.fn(async (_path: string, options?: RequestInit) => {
    if (options?.method === 'POST') {
      approved = true;
      return response({ status: 'approved' });
    }
    return response({
      status: approved ? 'approved' : 'pending',
      targets: owned
        ? [
            {
              platform: 'xhs',
              account_id: 7,
              name: '目标号',
              label: '小红书 · 目标号',
              owner_id: 'u',
              can_repair: true,
              verify_status: 'session_expired',
              login_missing: false,
            },
          ]
        : [],
    });
  });
  vi.stubGlobal('fetch', fetch);
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <CollectorRepairPage
        user={{ id: 'u', email: 'employee@example.com', role: 'viewer' }}
        onLogout={vi.fn()}
      />
    </QueryClientProvider>,
  );
  return fetch;
}
it('requires account choice and explicit confirmation, then shows local login instructions', async () => {
  const fetch = mount(true);
  const button = await screen.findByRole('button', { name: '确认账号，开始本地登录' });
  expect(button).toBeDisabled();
  fireEvent.change(screen.getByRole('combobox', { name: '采集账号' }), {
    target: { value: 'xhs:7' },
  });
  expect(screen.getByText(/当前登录已过期/)).toBeInTheDocument();
  // The pairing code printed by the local helper is required.
  expect(button).toBeDisabled();
  fireEvent.change(screen.getByLabelText('校验码'), { target: { value: 'abcd-efgh' } });
  expect(fetch.mock.calls.some(([, o]) => o?.method === 'POST')).toBe(false);
  fireEvent.click(button);
  await waitFor(() =>
    expect(
      fetch.mock.calls.some(
        ([, o]) =>
          o?.body === JSON.stringify({ platform: 'xhs', account_id: 7, user_code: 'abcd-efgh' }),
      ),
    ).toBe(true),
  );
  expect(await screen.findByRole('status')).toHaveTextContent('本地弹出的浏览器');
});
it('explains missing ownership without asking employee for a token', async () => {
  mount(false);
  expect(await screen.findByRole('alert')).toHaveTextContent('尚未分配负责账号');
  expect(screen.getByRole('button', { name: '确认账号，开始本地登录' })).toBeDisabled();
});
