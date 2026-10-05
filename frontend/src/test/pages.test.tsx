import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import App from '../App';
import { session } from '../lib/api';
import { response, user } from './fixtures';
import fixtures from './workspace-fixtures.json';
vi.mock('../components/charts', () => ({
  SeriesChart: () => null,
  MetricScatter: () => null,
  ImpactChart: () => null,
}));
function setup(page: string, role = 'admin', overrides: Record<string, unknown> = {}) {
  window.history.replaceState(null, '', `/console/?page=${page}`);
  session.set('fixture-token');
  const payloads: Record<string, unknown> = {
    '/auth/me': { ...user, role },
    ...fixtures,
    ...overrides,
  };
  const fetch = vi.fn(async (path: string, options?: RequestInit) => {
    const url = new URL(path, 'https://test');
    const key = url.pathname.replace('/api', '');
    if (key === '/upload/' && options?.method === 'POST')
      return response({ batch_id: 17, status: 'processing' }, 202);
    if (key === '/orders_all/')
      return new Response(JSON.stringify(payloads[key]), { headers: { 'X-Total-Count': '26' } });
    if (!(key in payloads)) throw new Error(`Unmocked API: ${key}`);
    return response(
      typeof payloads[key] === 'function' ? payloads[key](url, options) : payloads[key],
    );
  });
  vi.stubGlobal('fetch', fetch);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>,
  );
  return { fetch, payloads };
}
describe('migrated workspace pages', () => {
  it('shows historical Pgy cooperation by default and shares linked blogger filters', async () => {
    const { fetch } = setup('pgy');
    await screen.findByRole('table', { name: '合作笔记' });
    const calls = fetch.mock.calls.filter(([url]) => url.startsWith('/api/media/pgy/notes'));
    expect(calls.length).toBeGreaterThan(0);
    expect(calls.every(([url]) => !url.includes('start_date') && !url.includes('end_date'))).toBe(
      true,
    );
    expect(screen.getByRole('button', { name: '全部合作' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    const blogger = fixtures['/media/pgy/notes'][0].blogger_nickname;
    fireEvent.click(screen.getAllByRole('button', { name: new RegExp(blogger + '.*已知投入') })[0]);
    await waitFor(() =>
      expect(new URLSearchParams(window.location.search).get('blogger')).toBe(blogger),
    );
    expect(screen.getByRole('button', { name: `达人：${blogger} ×` })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '清除分析筛选' }));
    await waitFor(() =>
      expect(new URLSearchParams(window.location.search).has('blogger')).toBe(false),
    );
  });
  it('explains Pgy date exclusion and restores history without losing account scope', async () => {
    const { fetch } = setup('pgy&range=dates&start=2026-09-01&end=2026-09-30&account=1', 'admin', {
      '/media/pgy/notes': (url: URL) =>
        url.searchParams.has('start_date') ? [] : fixtures['/media/pgy/notes'],
    });
    await screen.findByText(/当前筛选未命中记录；该数据源实际有 1 条历史数据/);
    fireEvent.click(screen.getByRole('button', { name: '查看全部合作' }));
    await screen.findByRole('table', { name: '合作笔记' });
    expect(new URLSearchParams(window.location.search).get('account')).toBe('1');
    expect(
      fetch.mock.calls.some(
        ([url]) => url.includes('/media/pgy/notes?account_id=1') && !url.includes('start_date'),
      ),
    ).toBe(true);
  });
  it('clears page-specific filters when using the sidebar', async () => {
    setup('analysis&start=2026-09-01&end=2026-09-30&account=2&platform=jd&metric=orders&note=3');
    fireEvent.click(await screen.findByRole('button', { name: '蒲公英合作' }));
    await screen.findByRole('heading', { name: '蒲公英合作', level: 1 });
    const params = new URLSearchParams(window.location.search);
    for (const key of ['start', 'end', 'account', 'platform', 'metric', 'note'])
      expect(params.has(key)).toBe(false);
    expect(screen.getByRole('button', { name: '全部合作' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  });
  it('opens traffic with actual API analytics and keeps export metrics separate', async () => {
    const { fetch } = setup('traffic', 'admin', {
      '/media/traffic': [],
      '/media/traffic/overview': { articles: 0 },
    });
    await screen.findByRole('heading', { name: '内容表现探索' });
    expect(screen.getByRole('button', { name: '自动同步数据' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(fetch.mock.calls.some(([url]) => url.includes('/media/posts?'))).toBe(true);
    expect(fetch.mock.calls.some(([url]) => url.includes('/media/traffic'))).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: '后台导出数据' }));
    await screen.findByText('尚无公众号后台导出记录');
    expect(screen.queryByRole('heading', { name: '内容表现探索' })).not.toBeInTheDocument();
    expect(new URLSearchParams(window.location.search).get('source')).toBe('export');
  });
  it('opens channels at the latest historical date before querying content', async () => {
    const { fetch } = setup('channels', 'admin', {
      '/data/source-status': {
        ...fixtures['/data/source-status'],
        source: 'channels',
        records: 92,
        last_date: '2026-08-28',
      },
    });
    await waitFor(() => expect(screen.getByLabelText('结束日期')).toHaveValue('2026-08-28'));
    expect(screen.getByLabelText('开始日期')).toHaveValue('2026-07-30');
    await screen.findByRole('heading', { name: '内容表现探索' });
    const calls = fetch.mock.calls.filter(([url]) => url.includes('/media/channels/posts?'));
    expect(calls.length).toBeGreaterThan(0);
    expect(calls.every(([url]) => url.includes('end_date=2026-08-28'))).toBe(true);
  });
  it('preserves explicit empty channel dates and recovers history without zero charts', async () => {
    setup('channels&start=2026-09-01&end=2026-09-30&account=1', 'admin', {
      '/data/source-status': {
        ...fixtures['/data/source-status'],
        source: 'channels',
        records: 92,
        last_date: '2026-08-28',
      },
      '/media/channels/posts': (url: URL) =>
        url.searchParams.get('end_date') === '2026-09-30' ? [] : fixtures['/media/channels/posts'],
    });
    await screen.findByText('当前账号与日期范围内没有内容记录');
    expect(screen.getByLabelText('结束日期')).toHaveValue('2026-09-30');
    expect(screen.queryByRole('heading', { name: '内容表现探索' })).not.toBeInTheDocument();
    expect(document.querySelectorAll('.metric-card')).toHaveLength(0);
    fireEvent.click(screen.getByRole('button', { name: '查看最近有数据的 30 天' }));
    await screen.findByRole('heading', { name: '内容表现探索' });
    expect(screen.getByLabelText('结束日期')).toHaveValue('2026-08-28');
    expect(new URLSearchParams(window.location.search).get('account')).toBe('1');
  });
  it('does not present historical articles without any in-range API snapshots as zero traffic', async () => {
    setup('traffic&start=2030-01-01&end=2030-01-30', 'admin', {
      '/media/overview': { posts: 0, read_user_count: 0, share_user_count: 0 },
    });
    await screen.findByText('当前账号与日期范围内没有内容记录');
    expect(document.querySelectorAll('.metric-card')).toHaveLength(0);
    expect(screen.queryByRole('heading', { name: '内容表现探索' })).not.toBeInTheDocument();
  });
  it('copies the resolved traffic window and source so shared links remain reproducible', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    setup('traffic');
    await screen.findByRole('heading', { name: '内容表现探索' });
    fireEvent.click(screen.getByRole('button', { name: '复制分析链接' }));
    await waitFor(() => expect(writeText).toHaveBeenCalled());
    const copied = new URL(writeText.mock.calls[0][0]);
    expect(copied.searchParams.get('source')).toBe('api');
    expect(copied.searchParams.get('start')).toBe('2026-07-14');
    expect(copied.searchParams.get('end')).toBe('2026-08-12');
  });
  it('explains an empty traffic source without presenting missing records as zero readership, and links to API data', async () => {
    const { fetch } = setup('traffic&source=export', 'admin', {
      '/media/traffic': [],
      '/media/traffic/overview': { articles: 0, read_user_count: 0 },
    });
    await screen.findByText('尚无公众号后台导出记录');
    expect(document.querySelectorAll('.metric-card')).toHaveLength(0);
    expect(screen.queryByRole('button', { name: '概览排行' })).not.toBeInTheDocument();
    expect(fetch.mock.calls.some(([url]) => url === '/api/media/traffic/overview')).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: '查看公众号 API 数据' }));
    await screen.findByRole('heading', { name: '公众号流量', level: 1 });
    await waitFor(() =>
      expect(fetch.mock.calls.some(([url]) => url.startsWith('/api/media/posts?'))).toBe(true),
    );
  });
  it('distinguishes an empty date range from an empty traffic source', async () => {
    setup('traffic&source=export', 'admin', {
      '/media/traffic': [],
      '/media/traffic/overview': (url: URL) => ({
        articles: url.searchParams.has('start_date') ? 0 : 12,
      }),
    });
    await screen.findByText('当前发布日期范围内没有导出记录');
    expect(screen.getByText(/全部时期共有 12 篇/)).toBeInTheDocument();
    expect(screen.queryByText('尚无公众号后台导出记录')).not.toBeInTheDocument();
  });
  it('anchors commerce filters to the latest available data rather than an empty current month', async () => {
    const { fetch } = setup('analysis');
    await waitFor(() => expect(screen.getByLabelText('结束日期')).toHaveValue('2026-10-03'));
    expect(screen.getByLabelText('开始日期')).toHaveValue('2026-09-04');
    await waitFor(() =>
      expect(
        fetch.mock.calls.some(
          ([url]) => url.includes('/analysis/dashboard?') && url.includes('end_date=2026-10-03'),
        ),
      ).toBe(true),
    );
    fireEvent.click(screen.getByRole('button', { name: '最新 90 天数据' }));
    await waitFor(() => expect(screen.getByLabelText('开始日期')).toHaveValue('2026-07-06'));
  });
  it.each([
    ['orders', '订单明细', '/orders_all/'],
    ['upload', '数据上传', '/upload/batches'],
    ['analysis', '数据分析', '/analysis/dashboard'],
    ['customers', '客户管理', '/analysis/customers'],
    ['identity', '跨平台客户', '/analysis/identity/clusters'],
    ['retention', '客户留存', '/analysis/cohort_retention'],
    ['dictionary', '数据字典', '/analysis/field_coverage'],
    ['sql', 'SQL 控制台', '/analysis/nl-sql/providers'],
    ['reports', '周报', '/reports/weekly/1'],
    ['traffic', '公众号流量', '/media/posts'],
    ['wechat', '公众号内容分析', '/media/posts'],
    ['impact', '内容带货分析', '/media/content-impact'],
    ['xhs', '小红书数据', '/media/xhs/posts'],
    ['pgy', '蒲公英合作', '/media/pgy/notes'],
    ['zhihu', '知乎数据', '/media/zhihu/posts'],
    ['channels', '视频号数据', '/media/channels/posts'],
    ['users', '用户管理', '/admin/users'],
    ['logs', '操作日志', '/admin/logs'],
    ['database', '数据库状态', '/admin/db-status'],
    ['collector', '自动采集', '/admin/collector/runs'],
  ])('renders %s with its real API route', async (page, title, path) => {
    const { fetch } = setup(page);
    await screen.findByRole('heading', { name: title, level: 1 });
    await waitFor(() =>
      expect(fetch.mock.calls.some(([url]) => url.startsWith('/api' + path))).toBe(true),
    );
    await waitFor(() => expect(screen.queryByText('正在加载数据')).not.toBeInTheDocument());
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.queryByText('原工作台')).not.toBeInTheDocument();
  });
  it('submits multipart uploads and displays accepted batch completion', async () => {
    const { fetch } = setup('upload');
    await screen.findByLabelText('订单文件');
    fireEvent.change(screen.getByLabelText('订单文件'), {
      target: { files: [new File(['data'], 'orders.csv', { type: 'text/csv' })] },
    });
    fireEvent.click(screen.getByRole('button', { name: '提交导入' }));
    await screen.findByRole('heading', { name: '导入批次 #17' });
    await screen.findByRole('button', { name: '查看导入数据' });
    const call = fetch.mock.calls.find(
      ([url, options]) => url.startsWith('/api/upload/?') && options?.method === 'POST',
    );
    expect(call?.[1]?.body).toBeInstanceOf(FormData);
    expect(call?.[0]).toContain('expected_platform=youzan');
  });
  it('advances server pagination without introducing a second table pager', async () => {
    const { fetch } = setup('orders');
    const next = await screen.findByRole('button', { name: '下一页' });
    fireEvent.click(next);
    await waitFor(() =>
      expect(fetch.mock.calls.some(([url]) => url.includes('offset=25'))).toBe(true),
    );
    expect(await screen.findAllByRole('button', { name: '下一页' })).toHaveLength(1);
  });
  it('blocks admin routes for analysts before making admin API requests', async () => {
    const { fetch } = setup('database', 'analyst');
    await screen.findByText('当前账号没有此页面的访问权限');
    expect(fetch.mock.calls.some(([url]) => url.startsWith('/api/admin/'))).toBe(false);
  });
  it('opens a visible permissions dialog and saves a confirmed role change', async () => {
    const rows = [{ ...fixtures['/admin/users'][0], role: 'viewer' }];
    const { fetch } = setup('users', 'admin', {
      '/admin/users': () => rows.map((r) => ({ ...r })),
      '/admin/users/preview/role': (_url: URL, options: RequestInit) => {
        rows[0].role = JSON.parse(String(options.body)).role;
        return { detail: 'Role updated' };
      },
    });
    fireEvent.click(await screen.findByRole('button', { name: '管理权限' }));
    const dialog = await screen.findByRole('dialog', { name: '管理权限：preview@example.test' });
    expect(dialog).toBeVisible();
    fireEvent.change(within(dialog).getByRole('combobox', { name: '角色' }), {
      target: { value: 'analyst' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: '保存角色' }));
    expect(fetch.mock.calls.some(([url]) => url.endsWith('/role'))).toBe(false);
    fireEvent.click(within(dialog).getByRole('button', { name: '确认保存角色' }));
    await within(dialog).findByText(/当前角色：分析员/);
    expect(fetch.mock.calls.find(([url]) => url.endsWith('/role'))?.[1]).toMatchObject({
      method: 'PUT',
      body: JSON.stringify({ role: 'analyst' }),
    });
    fireEvent.click(within(dialog).getByRole('button', { name: '关闭账号设置' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(
      within(screen.getByRole('table', { name: '用户账号' })).getByText('分析员'),
    ).toBeInTheDocument();
  });
  it('shows actual database inventory and distinguishes missing structure from an empty table', async () => {
    setup('database', 'admin', {
      '/admin/db-status': {
        ...fixtures['/admin/db-status'],
        health: 'attention',
        table_details: [
          { name: 'orders', present: true, rows: 0, missing_columns: [] },
          { name: 'customers', present: false, rows: null, missing_columns: [] },
        ],
        backups: { status: 'unavailable', files: [] },
      },
    });
    await screen.findByText('数据库可访问 · 发现结构缺失');
    expect(screen.getByText('结构正常 · 暂无记录')).toBeInTheDocument();
    expect(screen.getByText('表缺失')).toBeInTheDocument();
    expect(screen.getByText(/无法读取备份目录/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '清空商城数据' })).not.toBeVisible();
  });
  it('requires explicit typed confirmation before clearing data', async () => {
    const { fetch } = setup('database');
    fireEvent.click(await screen.findByText('高风险维护'));
    fireEvent.click(await screen.findByRole('button', { name: '清空商城数据' }));
    expect(screen.getByRole('button', { name: '确认清空商城数据' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('确认清空商城数据'), { target: { value: '确认' } });
    expect(screen.getByRole('button', { name: '确认清空商城数据' })).toBeEnabled();
    expect(fetch.mock.calls.some(([url]) => url.includes('clear-db'))).toBe(false);
  });
  it('preserves the report sandbox and old Chinese deep links', async () => {
    setup('周报');
    await screen.findByTitle('周报内容');
    expect(screen.getByTitle('周报内容')).toHaveAttribute(
      'sandbox',
      'allow-popups allow-popups-to-escape-sandbox',
    );
  });
  it('renders segmentation dictionary time series and actual order rows', async () => {
    setup('analysis');
    fireEvent.click(await screen.findByRole('button', { name: '新老客户' }));
    await screen.findByRole('table', { name: '老客户订单' });
    expect(screen.getAllByText('示例产品').length).toBeGreaterThan(0);
  });
});
