import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import App from '../App';
import { Pagination, RecordTable } from '../components/workspace';
import { session } from '../lib/api';
import { kpi, response, user } from './fixtures';

vi.mock('../components/ComparisonChart', () => ({ default: () => <div aria-label="对比图表" /> }));

describe('record table interactions', () => {
  const rows = [
    { title: '乙', views: 5, status: 'failed' },
    { title: '甲', views: 20, status: 'completed' },
    { title: '丙', views: null, status: 'running' },
  ];
  const titles = (table: HTMLElement) =>
    within(table)
      .getAllByRole('row')
      .slice(1)
      .map((row) => row.querySelectorAll('td')[0].textContent);
  it('cycles header sort ascending, descending, then original order with missing values last', () => {
    render(<RecordTable rows={rows} caption="排序验收" exportable={false} />);
    const table = screen.getByRole('table', { name: '排序验收' });
    const header = within(table).getByRole('columnheader', { name: /阅读/ });
    const toggle = within(header).getByRole('button');
    fireEvent.click(toggle);
    expect(header).toHaveAttribute('aria-sort', 'ascending');
    expect(titles(table)).toEqual(['乙', '甲', '丙']);
    fireEvent.click(toggle);
    expect(header).toHaveAttribute('aria-sort', 'descending');
    expect(titles(table)).toEqual(['甲', '乙', '丙']);
    fireEvent.click(toggle);
    expect(header).toHaveAttribute('aria-sort', 'none');
    expect(titles(table)).toEqual(['乙', '甲', '丙']);
  });
  it('shows statuses as toned badges', () => {
    render(<RecordTable rows={rows} caption="状态验收" exportable={false} />);
    expect(screen.getByText('失败')).toHaveClass('badge', 'tone-danger');
    expect(screen.getByText('已完成')).toHaveClass('badge', 'tone-success');
  });
  it('opens a row from anywhere in it, once, and the explicit button still works', () => {
    const onSelect = vi.fn();
    render(<RecordTable rows={rows} caption="点击验收" onSelect={onSelect} exportable={false} />);
    const table = screen.getByRole('table', { name: '点击验收' });
    fireEvent.click(within(table).getByText('甲'));
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenLastCalledWith(expect.objectContaining({ title: '甲' }));
    fireEvent.click(within(table).getAllByRole('button', { name: '查看' })[0]);
    expect(onSelect).toHaveBeenCalledTimes(2);
  });
});

describe('table paging', () => {
  const many = Array.from({ length: 130 }, (_, i) => ({ title: `行${i + 1}`, views: i }));
  const bodyRows = () =>
    within(screen.getByRole('table', { name: '分页验收' })).getAllByRole('row');
  it('changes rows per page, remembers it for the session, and jumps to a page', () => {
    const { unmount } = render(<RecordTable rows={many} caption="分页验收" exportable={false} />);
    expect(bodyRows()).toHaveLength(26);
    expect(screen.getByText('共 130 条')).toBeInTheDocument();
    const jump = screen.getByRole('spinbutton', { name: '跳转到页码' });
    expect(jump).toHaveValue(1);
    expect(screen.getByText('/ 6 页', { exact: false })).toBeInTheDocument();
    fireEvent.change(jump, { target: { value: '4' } });
    fireEvent.submit(jump.closest('form')!);
    expect(within(bodyRows()[1]).getByText('行76')).toBeInTheDocument();
    // Out-of-range input clamps to the last page.
    fireEvent.change(jump, { target: { value: '99' } });
    fireEvent.submit(jump.closest('form')!);
    expect(jump).toHaveValue(6);
    expect(within(bodyRows()[1]).getByText('行126')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('combobox', { name: '每页行数' }), {
      target: { value: '50' },
    });
    expect(bodyRows()).toHaveLength(31);
    expect(screen.getByRole('spinbutton', { name: '跳转到页码' })).toHaveValue(3);
    expect(sessionStorage.getItem('omnipanel.console.pageSize')).toBe('50');
    unmount();
    render(<RecordTable rows={many} caption="分页验收" exportable={false} />);
    expect(bodyRows()).toHaveLength(51);
  });
  it('keeps plain previous / next when the server only knows whether more rows exist', () => {
    const onPage = vi.fn();
    render(<Pagination page={2} hasNext onPage={onPage} total={null} pageSize={25} />);
    expect(screen.getByText('第 3 页')).toBeInTheDocument();
    expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument();
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    expect(onPage).toHaveBeenCalledWith(3);
  });
});

describe('page switcher', () => {
  function setup(role: string) {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (path: string) => {
        const key = path.replace('/api', '').split('?')[0];
        if (key === '/auth/me') return response({ ...user, role });
        if (key === '/analysis/kpi-periods') return response(kpi);
        if (key === '/analysis/latest_order_date')
          return response({ latest_order_date: '2026-10-03' });
        return response([]);
      }),
    );
    session.set('test-token');
    render(
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
      >
        <App />
      </QueryClientProvider>,
    );
  }
  it('opens with Ctrl+K, filters pages, and navigates on Enter', async () => {
    setup('analyst');
    await screen.findByRole('heading', { level: 1 });
    fireEvent.keyDown(window, { key: 'k', ctrlKey: true });
    const dialog = await screen.findByRole('dialog', { name: '快速跳转' });
    const input = within(dialog).getByRole('combobox', { name: '搜索页面' });
    fireEvent.change(input, { target: { value: '留存' } });
    expect(within(dialog).getAllByRole('option')).toHaveLength(1);
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(window.location.search).toContain('page=retention');
    expect(screen.queryByRole('dialog', { name: '快速跳转' })).not.toBeInTheDocument();
  });
  it('only offers pages the role may open', async () => {
    setup('viewer');
    await screen.findByRole('heading', { level: 1 });
    fireEvent.click(screen.getByRole('button', { name: '快速跳转页面' }));
    const dialog = await screen.findByRole('dialog', { name: '快速跳转' });
    const options = within(dialog)
      .getAllByRole('option')
      .map((o) => o.textContent);
    expect(options.some((text) => text?.includes('订单明细'))).toBe(true);
    expect(options.some((text) => text?.includes('经营概览'))).toBe(false);
    expect(options.some((text) => text?.includes('用户管理'))).toBe(false);
  });
});
