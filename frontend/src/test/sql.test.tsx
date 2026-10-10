import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../App';
import SqlPage from '../pages/SqlPage';
import { session } from '../lib/api';
import * as resources from '../lib/resources';
import { kpi, response, user } from './fixtures';

vi.mock('../components/ComparisonChart', () => ({ default: () => <div aria-label="对比图表" /> }));

const result = { columns: ['platform', 'orders'], rows: [['jd', 3]], row_count: 1 };
function stub() {
  const fetch = vi.fn(async (path: string, _options?: RequestInit) => {
    const key = path.replace('/api', '').split('?')[0];
    if (key === '/analysis/sql') return response(result);
    if (key === '/analysis/nl-sql/providers') return response({ providers: [] });
    if (key === '/auth/me') return response({ ...user, role: 'admin' });
    if (key === '/analysis/kpi-periods') return response(kpi);
    if (key === '/analysis/latest_order_date') return response({ latest_order_date: '2026-10-03' });
    return response([]);
  });
  vi.stubGlobal('fetch', fetch);
  return fetch;
}
function renderPage() {
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <SqlPage />
    </QueryClientProvider>,
  );
}
const sqlCalls = (fetch: ReturnType<typeof stub>) =>
  fetch.mock.calls.filter(([url]) => url === '/api/analysis/sql');

describe('SQL console', () => {
  beforeEach(() => localStorage.clear());
  it('runs on Ctrl/⌘+Enter, reports rows and timing, and records history', async () => {
    const fetch = stub();
    renderPage();
    const editor = screen.getByRole('textbox', { name: 'SQL' });
    fireEvent.change(editor, { target: { value: 'SELECT 1' } });
    fireEvent.keyDown(editor, { key: 'Enter', metaKey: true });
    await screen.findByText(/1 行 · 用时 \d+\.\d{2} 秒/);
    expect(sqlCalls(fetch)).toHaveLength(1);
    expect(sqlCalls(fetch)[0][1]?.body).toBe(JSON.stringify({ sql: 'SELECT 1' }));
    fireEvent.change(editor, { target: { value: 'SELECT 2' } });
    fireEvent.keyDown(editor, { key: 'Enter', ctrlKey: true });
    await waitFor(() => expect(sqlCalls(fetch)).toHaveLength(2));
    // Plain Enter keeps editing.
    fireEvent.keyDown(editor, { key: 'Enter' });
    expect(sqlCalls(fetch)).toHaveLength(2);
    expect(JSON.parse(localStorage.getItem('rpa.console.sql.history')!)).toHaveLength(2);
    const history = screen.getByText(/最近查询 2/).closest('details')!;
    fireEvent.click(within(history).getByTitle('SELECT 1'));
    expect(editor).toHaveValue('SELECT 1');
    fireEvent.click(within(history).getByRole('button', { name: '清空历史' }));
    expect(screen.queryByText(/最近查询/)).not.toBeInTheDocument();
    expect(localStorage.getItem('rpa.console.sql.history')).toBeNull();
  });
  it('keeps at most 20 distinct queries and survives unreadable storage', async () => {
    localStorage.setItem('rpa.console.sql.history', '{not json');
    const fetch = stub();
    renderPage();
    const editor = screen.getByRole('textbox', { name: 'SQL' });
    for (let i = 0; i < 22; i++) {
      fireEvent.change(editor, { target: { value: `SELECT ${i % 21}` } });
      fireEvent.keyDown(editor, { key: 'Enter', ctrlKey: true });
      await waitFor(() => expect(sqlCalls(fetch)).toHaveLength(i + 1));
    }
    const saved = JSON.parse(localStorage.getItem('rpa.console.sql.history')!);
    expect(saved).toHaveLength(20);
    expect(saved[0].sql).toBe('SELECT 0');
  });
  it('exports the full result as CSV', async () => {
    stub();
    const save = vi.spyOn(resources, 'exportCsv').mockImplementation(() => undefined);
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: '执行查询' }));
    fireEvent.click(await screen.findByRole('button', { name: '导出 CSV' }));
    expect(save).toHaveBeenCalledOnce();
    const [rows, columns, name, source] = save.mock.calls[0];
    expect(name).toMatch(/^查询结果-\d{12}\.csv$/);
    expect(source).toBe('SQL 查询结果');
    expect(resources.csv(rows, columns)).toContain('"platform","orders"\r\n"jd","3"');
    save.mockRestore();
  });
});

describe('page switcher shortcut', () => {
  function setup() {
    stub();
    session.set('test-token');
    window.history.replaceState(null, '', '/console/?page=sql');
    render(
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
      >
        <App />
      </QueryClientProvider>,
    );
  }
  it('leaves Ctrl+K to text editing but still opens on ⌘K in a textarea', async () => {
    setup();
    const editor = await screen.findByRole('textbox', { name: 'SQL' });
    fireEvent.keyDown(editor, { key: 'k', ctrlKey: true });
    expect(screen.queryByRole('dialog', { name: '快速跳转' })).not.toBeInTheDocument();
    fireEvent.keyDown(editor, { key: 'k', metaKey: true });
    expect(await screen.findByRole('dialog', { name: '快速跳转' })).toBeInTheDocument();
  });
});
