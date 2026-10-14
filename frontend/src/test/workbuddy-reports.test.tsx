import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import ReportsPage from '../pages/ReportsPage';
import { response } from './fixtures';

function mount(state: string, admin = true) {
  const row = {
    id: 1,
    week_start: '2026-09-07',
    week_end: '2026-09-13',
    status: 'queued',
    wecom_sent: false,
    job_state: state,
    narrative_status: 'pending',
    html_content: null,
    narrative: null,
    error_message: null,
    delivery_error: state === 'delivery_unknown' ? '请核对企业微信后重试。' : null,
  };
  const fetch = vi.fn(async (path: string, options?: RequestInit) => {
    if (options?.method === 'POST') return response(row);
    return response(path.includes('?limit=') ? [row] : row);
  });
  vi.stubGlobal('fetch', fetch);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={client}>
      <ReportsPage admin={admin} />
    </QueryClientProvider>,
  );
  return { row, fetch, client, ...view };
}

it('polls pending work and updates the detail when the job finishes', async () => {
  const { row, fetch } = mount('queued');
  await screen.findByText('周报正在后台处理，完成后此页面会自动更新。');
  row.job_state = 'sent';
  row.status = 'success';
  row.wecom_sent = true;
  await screen.findByText('已推送企业微信', {}, { timeout: 4500 });
  await screen.findByText('已推送', { selector: '[role=status]' });
  expect(fetch.mock.calls.filter(([url]) => url.includes('?limit=')).length).toBeGreaterThan(1);
});

it('asks the admin to acknowledge an uncertain send before retrying', async () => {
  const { fetch } = mount('delivery_unknown');
  fireEvent.click(await screen.findByRole('button', { name: '重试推送' }));
  expect(screen.getByText(/确认重试可能造成重复推送/)).toBeInTheDocument();
  expect(fetch.mock.calls.some(([, options]) => options?.method === 'POST')).toBe(false);
  fireEvent.click(screen.getByRole('button', { name: '确认重试推送' }));
  await waitFor(() =>
    expect(
      fetch.mock.calls.some(
        ([url, options]) =>
          url.endsWith('/1/delivery') &&
          JSON.parse(String(options?.body)).acknowledged_uncertain_delivery === true,
      ),
    ).toBe(true),
  );
});

it('does not expose delivery mutations to non-admin readers', async () => {
  mount('delivery_unknown', false);
  await screen.findByText('推送结果待确认');
  expect(screen.queryByRole('button', { name: '重试推送' })).not.toBeInTheDocument();
});
