import { z } from 'zod';
import { request } from './api';

export function validDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || value.startsWith('0000')) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
}
const dateSchema = z.string().refine(validDate);
const metricsSchema = z.object({
  orders: z.number().int().nonnegative(),
  revenue: z.number(),
  aov: z.number(),
  unique_customers: z.number().int().nonnegative(),
});
export const kpiSchema = z.object({
  day: metricsSchema,
  prior_day: metricsSchema,
  week: metricsSchema,
  prior_week: metricsSchema,
  month: metricsSchema,
  prior_month: metricsSchema,
});
export type Metrics = z.infer<typeof metricsSchema>;
export type Kpi = z.infer<typeof kpiSchema>;
export type Period = 'day' | 'week' | 'month';
export const periods: Record<Period, string> = {
  day: '数据日',
  week: '本周至今',
  month: '本月至今',
};
export const priorKeys = { day: 'prior_day', week: 'prior_week', month: 'prior_month' } as const;
export const metricDefinitions = [
  { key: 'revenue', label: '营业额', unit: '元', description: '非空订单金额合计' },
  { key: 'orders', label: '订单数', unit: '笔', description: '已导入的订单记录数' },
  { key: 'aov', label: '客单价', unit: '元', description: '金额非空记录的平均值' },
  { key: 'unique_customers', label: '独立客户数', unit: '人', description: '按系统客户标识去重' },
] as const;
export type MetricKey = (typeof metricDefinitions)[number]['key'];
export function formatMetric(key: MetricKey, value: number) {
  return new Intl.NumberFormat('zh-CN', {
    minimumFractionDigits: ['aov', 'revenue'].includes(key) ? 2 : 0,
    maximumFractionDigits: ['aov', 'revenue'].includes(key) ? 2 : 0,
  }).format(value);
}
export function delta(current: number, previous: number) {
  return previous === 0 ? null : ((current - previous) / previous) * 100;
}
const iso = (d: Date) => d.toISOString().slice(0, 10);
const shift = (d: Date, days: number) => new Date(d.getTime() + days * 86400000);
export function comparisonWindow(anchor: string, period: Period) {
  const end = new Date(`${anchor}T00:00:00Z`);
  let start = end,
    priorStart = shift(end, -1),
    priorEnd = priorStart;
  if (period === 'week') {
    start = shift(end, -((end.getUTCDay() + 6) % 7));
    priorStart = shift(start, -7);
    priorEnd = shift(end, -7);
  } else if (period === 'month') {
    start = new Date(end);
    start.setUTCDate(1);
    const last = shift(start, -1);
    priorStart = new Date(last);
    priorStart.setUTCDate(1);
    priorEnd = new Date(priorStart);
    priorEnd.setUTCDate(Math.min(end.getUTCDate(), last.getUTCDate()));
  }
  return { start: iso(start), end: iso(end), priorStart: iso(priorStart), priorEnd: iso(priorEnd) };
}
export const latestSchema = z.object({ latest_order_date: dateSchema.nullable() });
export const freshnessSchema = z.object({
  orders: z.object({
    coverage_through: z.string().nullable(),
    last_import_at: z.string().nullable(),
  }),
});
export const batchSchema = z.object({
  id: z.number().int(),
  filename: z.string(),
  platform: z.string().nullable(),
  uploaded_at: z.string().nullable(),
  row_count: z.number().nullable(),
  inserted_orders: z.number().nullable(),
  duplicate_rows: z.number().nullable(),
  invalid_rows: z.number().nullable(),
  status: z.enum(['processing', 'recovering', 'completed', 'failed']),
  error_message: z.string().nullable(),
});
export type Batch = z.infer<typeof batchSchema>;
export const getBatches = (signal: AbortSignal) =>
  request('/upload/batches?limit=10', z.array(batchSchema), { signal });
export const batchPollInterval = (rows?: Batch[]) =>
  rows?.some((row) => ['processing', 'recovering'].includes(row.status)) ? 5000 : false;
