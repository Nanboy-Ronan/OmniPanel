import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import App from '../App';
import workspace from './workspace-fixtures.json';
import { session } from '../lib/api';
import { batch, kpi, response, user } from './fixtures';

vi.mock('../components/ComparisonChart', () => ({ default: () => <div aria-label="对比图表" /> }));
function setup(overrides: Record<string, unknown> = {}, role = 'analyst') {
  const payloads: Record<string, unknown> = {
    '/auth/me': { ...user, role },
    '/analysis/latest_order_date': { latest_order_date: '2026-10-03' },
    '/analysis/kpi-periods': kpi,
    '/analysis/dashboard': workspace['/analysis/dashboard'],
    '/data/source-status': workspace['/data/source-status'],
    '/data/freshness': { orders: { coverage_through: '2026-10-03', last_import_at: null } },
    '/upload/batches': [batch],
    '/auth/wecom/status': { enabled: true },
    ...overrides,
  };
  const fetch = vi.fn(async (path: string) => {
    const payload = payloads[path.replace('/api', '').split('?')[0]];
    if (payload instanceof Response) return payload.clone();
    return response(payload);
  });
  vi.stubGlobal('fetch', fetch);
  session.set('test-token');
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const view = render(
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>,
  );
  return { fetch, payloads, client, ...view };
}
describe('working console', () => {
  it('renders real metrics and changes the period without reloading data', async () => {
    const { fetch } = setup();
    await screen.findByText('30,960.00', { selector: '.metric-value' });
    const before = fetch.mock.calls.length;
    fireEvent.click(screen.getByRole('button', { name: '本周至今' }));
    await screen.findByText('171,400.00', { selector: '.metric-value' });
    expect(window.location.search).toContain('period=week');
    expect(fetch.mock.calls.length).toBe(before);
    expect(screen.getByRole('table', { name: '经营指标同期对比' })).toBeInTheDocument();
  });
  it('summarises every source in one status strip with details on demand', async () => {
    setup(
      {
        '/data/freshness': {
          orders: { coverage_through: '2026-10-03', last_import_at: '2026-10-04T01:30:00+00:00' },
          platforms: { jd: { coverage_through: '2026-10-02', last_import_at: null } },
        },
      },
      'admin',
    );
    await screen.findByText('30,960.00', { selector: '.metric-value' });
    const strip = screen.getByRole('region', { name: '数据来源状态' });
    for (const name of ['商城订单', '公众号 API', '小红书', '视频号', '知乎', '蒲公英合作'])
      expect(within(strip).getByRole('button', { name: new RegExp(`^${name}：`) })).toBeVisible();
    expect(within(strip).getByText(/源数据覆盖至/)).toHaveTextContent('源数据覆盖至 2026-10-03');
    expect(within(strip).getByText(/页面取数 \d{2}:\d{2}:\d{2}/)).toBeInTheDocument();
    // Details stay out of the way until a chip is opened.
    expect(
      screen.queryByRole('button', { name: '查看最近有数据的 30 天' }),
    ).not.toBeInTheDocument();
    fireEvent.click(within(strip).getByRole('button', { name: /^商城订单：/ }));
    const drawer = await screen.findByRole('dialog', { name: '商城订单数据状态' });
    expect(within(drawer).getByText('2026-10-04 09:30:00')).toBeInTheDocument();
    expect(within(drawer).getByRole('list', { name: '各平台覆盖' })).toHaveTextContent('京东');
    fireEvent.click(within(drawer).getByRole('button', { name: '查看最近有数据的 30 天' }));
    const params = new URLSearchParams(window.location.search);
    expect(params.get('page')).toBe('analysis');
    expect(params.get('end')).toBe('2026-08-12');
  });
  it('does not send KPI requests for an invalid shared date', async () => {
    window.history.replaceState(null, '', '/console/?anchor=2026-02-30');
    const { fetch } = setup();
    await screen.findByText('链接中的统计日期无效或晚于最新数据日。');
    expect(fetch.mock.calls.some(([url]) => url.includes('kpi-periods'))).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    await screen.findByText('30,960.00', { selector: '.metric-value' });
  });
  it('shows no-data without fake zero KPI cards', async () => {
    setup({ '/analysis/latest_order_date': { latest_order_date: null } });
    await screen.findByText('还没有可分析的订单');
    expect(screen.queryByText('30,960.00')).not.toBeInTheDocument();
  });
  it('preserves cached data with an explicit stale warning after a failed refresh', async () => {
    const { payloads } = setup();
    await screen.findByText('30,960.00', { selector: '.metric-value' });
    payloads['/analysis/kpi-periods'] = response({ detail: 'private' }, 503);
    fireEvent.click(screen.getByRole('button', { name: '刷新数据' }));
    await screen.findByText('刷新失败，以下保留上次成功获取的数据，请勿视为最新结果。');
    expect(screen.getByText('30,960.00', { selector: '.metric-value' })).toBeInTheDocument();
    payloads['/analysis/kpi-periods'] = kpi;
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    await waitFor(() => expect(screen.queryByText(/刷新失败，以下/)).not.toBeInTheDocument());
  });
  it('blocks analyst-only data for viewers while leaving tasks usable', async () => {
    window.history.replaceState(null, '', '/console/?page=overview');
    const { fetch } = setup({}, 'viewer');
    await screen.findByText('当前账号没有此页面的访问权限');
    expect(fetch.mock.calls.some(([url]) => url.includes('/analysis/'))).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: '导入任务' }));
    await screen.findByText('订单.csv');
  });
  it('clears user data on logout and shows the login screen', async () => {
    const { client } = setup();
    await screen.findByText('30,960.00', { selector: '.metric-value' });
    fireEvent.click(screen.getByRole('button', { name: '退出登录' }));
    await screen.findByText('企业微信扫码登录');
    expect(session.get()).toBeNull();
    expect(client.getQueryData(['kpi', '2026-10-03'])).toBeUndefined();
    expect(screen.queryByText('30,960.00')).not.toBeInTheDocument();
  });
  it('removes cached metrics when a refresh reports an expired session', async () => {
    const { payloads, client } = setup();
    await screen.findByText('30,960.00', { selector: '.metric-value' });
    payloads['/analysis/kpi-periods'] = response({}, 401);
    fireEvent.click(screen.getByRole('button', { name: '刷新数据' }));
    await screen.findByText('登录已过期，请重新登录。');
    expect(session.get()).toBeNull();
    expect(client.getQueryData(['kpi', '2026-10-03'])).toBeUndefined();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });
  it('does not relabel old metrics while a different date is loading', async () => {
    const { fetch } = setup();
    await screen.findByText('30,960.00', { selector: '.metric-value' });
    let finish!: (value: Response) => void;
    fetch.mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }),
    );
    fireEvent.change(screen.getByLabelText('统计截止日'), { target: { value: '2026-10-01' } });
    fireEvent.click(screen.getByRole('button', { name: '应用' }));
    await screen.findByText('正在汇总经营指标');
    expect(screen.queryByText('30,960.00')).not.toBeInTheDocument();
    expect(window.location.search).toContain('anchor=2026-10-01');
    finish(response({ ...kpi, day: { ...kpi.day, revenue: 9900 } }));
    await screen.findByText('9,900.00', { selector: '.metric-value' });
  });
  it('does not render missing KPI fields as zero', async () => {
    setup({ '/analysis/kpi-periods': {} });
    await screen.findByText('数据结构不完整，无法可靠展示，请联系管理员。');
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });
});
