import { z } from 'zod';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { ApiError, request } from './api';
import { useToast, type Toaster } from '../components/Toast';
import { useLocationSearch } from './navigation';
import { validDate } from './data';

export const recordSchema = z.record(z.string(), z.unknown());
export const rowsSchema = z.array(recordSchema);
export type Row = z.infer<typeof recordSchema>;
export type Params = Record<string, string | number | boolean | undefined | null>;
export function endpoint(path: string, params: Params = {}) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params))
    if (value !== '' && value !== null && value !== undefined) query.set(key, String(value));
  return path + (query.size ? `?${query}` : '');
}
export function useResource<T>(path: string, schema: z.ZodType<T>, enabled = true) {
  return useQuery({
    queryKey: ['resource', path],
    queryFn: ({ signal }) => request(path, schema, { signal, timeoutMs: 45000 }),
    enabled,
  });
}
export type ActionArgs = {
  path: string;
  method?: 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  body?: unknown;
  timeoutMs?: number;
  /** Per-call success notice, overriding the hook's default. */
  success?: string;
};
type Notice = string | { message: string; tone: 'success' | 'error' };
/**
 * Mutation with a cache refresh on success. Outcomes are also announced as toasts: failures
 * always, successes when a message is given. An expired session is left to the login screen.
 */
export function useAction(
  messages: { success?: Notice | ((data: unknown) => Notice); error?: string } = {},
) {
  const client = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: ({ success: _success, ...args }: ActionArgs) =>
      request(args.path, z.unknown(), {
        ...args,
        method: args.method ?? 'POST',
        timeoutMs: args.timeoutMs ?? 120000,
      }),
    onSuccess: async (data, args) => {
      const notice =
        args.success ??
        (typeof messages.success === 'function' ? messages.success(data) : messages.success);
      if (typeof notice === 'string') toast.success(notice);
      else if (notice) toast[notice.tone](notice.message);
      await client.invalidateQueries();
    },
    onError: (error) => notifyFailure(toast, error, messages.error),
    retry: false,
  });
}
/** Toast a failed action unless the session expired or the user cancelled it. */
export function notifyFailure(toast: Toaster, error: Error, prefix = '操作失败') {
  if (error.name === 'AbortError' || (error instanceof ApiError && error.status === 401)) return;
  toast.error(`${prefix}：${error.message}`);
}
export function useFilters({
  commerce = false,
  dataThrough,
  waitingForCoverage = false,
}: {
  commerce?: boolean;
  dataThrough?: string | null;
  waitingForCoverage?: boolean;
} = {}) {
  const search = useLocationSearch();
  const params = new URLSearchParams(search);
  const latest = useResource(
    '/analysis/latest_order_date',
    z.object({ latest_order_date: z.string().nullable() }),
    commerce,
  );
  const today =
    (commerce ? latest.data?.latest_order_date : dataThrough) ??
    new Date().toISOString().slice(0, 10);
  const start = new Date(new Date(`${today}T00:00:00Z`).getTime() - 29 * 86400000)
    .toISOString()
    .slice(0, 10);
  const values = {
    start_date: params.get('start') ?? start,
    end_date: params.get('end') ?? today,
    platform: params.get('platform') ?? '',
    q: params.get('q') ?? '',
    account_id: params.get('account') ?? '',
  };
  const valid =
    (!waitingForCoverage || (params.has('start') && params.has('end'))) &&
    (!commerce || latest.isFetched || (params.has('start') && params.has('end'))) &&
    validDate(values.start_date) &&
    validDate(values.end_date) &&
    values.start_date <= values.end_date;
  return { values, valid, params, signature: `${search}:${values.start_date}:${values.end_date}` };
}
export const text = (value: unknown) =>
  value === null || value === undefined ? '—' : String(value);
export const numeric = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null;
export const display = (value: unknown): string => {
  if (value == null) return '—';
  if (typeof value === 'number')
    return Number.isFinite(value)
      ? value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
      : '—';
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
};
export function asRows(value: unknown): Row[] {
  return rowsSchema.safeParse(value).data ?? [];
}
export function asRecord(value: unknown): Row {
  return recordSchema.safeParse(value).data ?? {};
}
export function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
/** Save a CSV built in the browser and record the export in the audit log. */
export function exportCsv(rows: Row[], columns: string[], filename: string, source: string) {
  saveBlob(new Blob([csv(rows, columns)], { type: 'text/csv;charset=utf-8' }), filename);
  // The file is already saved; a failed audit call must not interrupt the user.
  void request('/audit/export', z.null(), {
    body: { source: source.slice(0, 120), rows: rows.length, columns: columns.slice(0, 200) },
  }).catch(() => undefined);
}
export async function download(path: string, filename: string) {
  const blob = await request(path, z.instanceof(Blob), { format: 'blob', timeoutMs: 180000 });
  saveBlob(blob, filename);
}
export function csv(rows: Row[], columns: string[]) {
  const cell = (value: unknown) => {
    let str =
      value == null ? '' : typeof value === 'object' ? JSON.stringify(value) : String(value);
    if (typeof value === 'string' && /^[=+\-@\t\r]/.test(str)) str = `'${str}`;
    return `"${str.replaceAll('"', '""')}"`;
  };
  return (
    '\ufeff' +
    [columns, ...rows.map((row) => columns.map((key) => row[key]))]
      .map((row) => row.map(cell).join(','))
      .join('\r\n')
  );
}
export async function paged(
  path: string,
  signal: AbortSignal,
): Promise<{ rows: Row[]; total: number | null }> {
  let total: number | null = null;
  const rows = await request(path, rowsSchema, {
    signal,
    timeoutMs: 45000,
    onHeaders: (headers) => {
      const value = headers.get('X-Total-Count');
      if (value !== null && /^\d+$/.test(value)) total = Number(value);
    },
  });
  return { rows, total };
}
// Full-period content analytics must never silently use just the first page.
export async function allPosts(path: string, params: Params, signal: AbortSignal) {
  const rows: Row[] = [];
  const ids = new Set<unknown>();
  for (let offset = 0; offset < 100000; offset += 1000) {
    const page = await request(endpoint(path, { ...params, limit: 1000, offset }), rowsSchema, {
      signal,
      timeoutMs: 45000,
    });
    for (const row of page) {
      if (row.id == null || ids.has(row.id)) throw new Error('分页结果发生变化，请刷新后重试。');
      ids.add(row.id);
    }
    rows.push(...page);
    if (page.length < 1000) return rows;
  }
  throw new Error('当前区间内容超过 10 万条，请缩小日期范围。');
}
