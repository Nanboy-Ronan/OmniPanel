import { describe, expect, it, vi } from 'vitest';
import { z } from 'zod';
import { allPosts, csv, paged } from '../lib/resources';
import { request } from '../lib/api';
import { groupContent, matchTopics, normalizeContent, ratio } from '../lib/content';
import { allowed, resolveRoute, routes } from '../lib/routes';
import definitions from '../lib/fieldDefinitions.json';
import { response } from './fixtures';
describe('complete workbench data contracts', () => {
  it('loads all pages and preserves stable request filters', async () => {
    const fetch = vi.fn(async (path: string) => {
      const offset = Number(new URL(path, 'https://test').searchParams.get('offset'));
      return response(
        Array.from({ length: offset === 0 ? 1000 : 1 }, (_, i) => ({
          id: offset + i,
          title: 'test',
        })),
      );
    });
    vi.stubGlobal('fetch', fetch);
    const rows = await allPosts(
      '/media/pgy/notes',
      { account_id: 7, start_date: '2026-01-01' },
      new AbortController().signal,
    );
    expect(rows).toHaveLength(1001);
    expect(
      fetch.mock.calls.map(([url]) => new URL(url, 'https://test').searchParams.get('offset')),
    ).toEqual(['0', '1000']);
    expect(fetch.mock.calls[1][0]).toContain('account_id=7');
  });
  it('rejects unstable pagination instead of showing a partial total', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => response(Array.from({ length: 1000 }, (_, id) => ({ id })))),
    );
    await expect(allPosts('/media/xhs/posts', {}, new AbortController().signal)).rejects.toThrow(
      '分页结果发生变化',
    );
  });
  it('reads server totals independently of loaded page size', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(JSON.stringify([{ id: 1 }]), { headers: { 'X-Total-Count': '1001' } }),
      ),
    );
    expect(await paged('/orders_all/?limit=25', new AbortController().signal)).toEqual({
      rows: [{ id: 1 }],
      total: 1001,
    });
  });
  it('keeps the browser-generated multipart boundary and accepts empty delete responses', async () => {
    const fetch = vi.fn(async () => new Response(null, { status: 204 }));
    vi.stubGlobal('fetch', fetch);
    const body = new FormData();
    body.set('file', new File(['x'], 'orders.csv'));
    await request('/upload/', z.unknown(), { body });
    expect((fetch.mock.calls[0] as unknown as [string, RequestInit])[1].headers).not.toHaveProperty(
      'Content-Type',
    );
    expect(await request('/saved-queries/1', z.unknown(), { method: 'DELETE' })).toBeNull();
  });
  it('guards CSV formulas and correctly escapes quotes and newlines', () => {
    expect(
      csv([{ title: '=1+1', detail: 'a,"b"\nc', amount: -2 }], ['title', 'detail', 'amount']),
    ).toContain('"\'=1+1","a,""b""\nc","-2"');
  });
  it('uses percentage points, keeps zero denominators missing and includes all interactions', () => {
    expect(ratio(5, 100)).toBe(5);
    expect(ratio(3, 0)).toBeNull();
    const [row] = normalizeContent(
      [
        {
          plays: 200,
          recommends: 2,
          likes_thumb: 3,
          comments: 4,
          shares: 1,
          new_fans: 2,
          publish_date: '2026-10-03',
        },
      ],
      'channels',
    );
    expect(row.total_engagement).toBe(10);
    expect(row.engagement_rate).toBe(5);
    expect(row.follower_rate).toBe(1);
    expect(row.weekday).toBe('周六');
  });
  it('does not fabricate aggregate zeros for missing metrics', () => {
    expect(
      groupContent(
        [
          { date: '2026-10-03', views: null },
          { date: '2026-10-03', views: null },
        ],
        'date',
        ['views'],
      ),
    ).toEqual([{ date: '2026-10-03', posts: 2, views: null }]);
    expect(
      matchTopics('NEW新品', [
        { name: '新品', keywords: 'new,新品' },
        { name: '发布', keywords: '新品' },
      ]),
    ).toEqual(['新品', '发布']);
  });
  it('maps all retired Chinese links and enforces role groups', () => {
    expect(routes).toHaveLength(22);
    for (const route of routes) {
      expect(resolveRoute(route.alias)).toEqual(route);
      expect(allowed('admin', route.role)).toBe(true);
    }
    expect(allowed('viewer', resolveRoute('SQL 控制台')!.role)).toBe(false);
    expect(allowed('analyst', resolveRoute('自动采集')!.role)).toBe(false);
    expect(allowed('viewer', resolveRoute('数据浏览')!.role)).toBe(true);
  });
  it('retains field definitions and examples for all three data tables', () => {
    expect(Object.keys(definitions).sort()).toEqual(['customers', 'orders', 'upload_batches']);
    expect(Object.keys(definitions.orders)).toHaveLength(16);
    for (const fields of Object.values(definitions))
      for (const def of Object.values(fields)) {
        expect(typeof def.nullable).toBe('boolean');
        expect(def.example).not.toBe('');
        expect(def.zh).not.toBe('');
      }
  });
});
