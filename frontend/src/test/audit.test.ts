import { describe, expect, it, vi } from 'vitest';
import { exportCsv } from '../lib/resources';
import { session } from '../lib/api';

describe('browser exports are audited', () => {
  it('saves the file and reports source, row count and columns', async () => {
    session.set('test-token');
    const fetch = vi.fn(async () => new Response(null, { status: 204 }));
    vi.stubGlobal('fetch', fetch);
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(() => undefined);
    vi.stubGlobal('URL', {
      ...URL,
      createObjectURL: () => 'blob:x',
      revokeObjectURL: () => undefined,
    });
    exportCsv(
      [{ mobile: '13800000001' }, { mobile: '13800000002' }],
      ['mobile'],
      '客户.csv',
      '客户列表',
    );
    expect(click).toHaveBeenCalledOnce();
    await vi.waitFor(() => expect(fetch).toHaveBeenCalled());
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/audit/export');
    expect(JSON.parse(String(init.body))).toEqual({
      source: '客户列表',
      rows: 2,
      columns: ['mobile'],
    });
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer test-token');
  });

  it('never surfaces an audit failure to the user', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('{}', { status: 500 })),
    );
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    vi.stubGlobal('URL', {
      ...URL,
      createObjectURL: () => 'blob:x',
      revokeObjectURL: () => undefined,
    });
    expect(() => exportCsv([], ['a'], 'x.csv', 'x')).not.toThrow();
  });
});
