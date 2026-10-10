import { z } from 'zod';
import { numeric, type Row } from './resources';
export const palette = ['#2563eb', '#0d9488', '#f59e0b', '#8b5cf6', '#e85d75', '#64748b'];
export const compact = (value: number) =>
  new Intl.NumberFormat('zh-CN', {
    notation: 'compact',
    maximumFractionDigits: Math.abs(value) < 10000 ? 2 : 1,
  }).format(value);
const metrics = z.object({
  orders: z.number(),
  priced_orders: z.number(),
  revenue: z.number(),
  customers: z.number(),
  aov: z.number().nullable(),
  missing_amount: z.number(),
});
export const dashboardSchema = z.object({
  status: z
    .object({
      excluded_orders: z.number(),
      excluded_amount: z.number(),
      refunds: z.number(),
      unknown_orders: z.number(),
      deleted_orders: z.number().default(0),
      deleted_amount: z.number().default(0),
    })
    .optional(),
  start_date: z.string(),
  end_date: z.string(),
  prior_start: z.string(),
  prior_end: z.string(),
  current: metrics,
  prior: metrics,
  series: z.array(
    metrics.extend({
      date: z.string(),
      prior_date: z.string(),
      prior_revenue: z.number(),
      prior_orders: z.number(),
      prior_aov: z.number().nullable(),
      prior_customers: z.number(),
    }),
  ),
  channels: z.array(z.object({ platform: z.string(), current: metrics, prior: metrics })),
  products: z.array(metrics.extend({ name: z.string() })),
  regions: z.array(metrics.extend({ name: z.string() })),
});
export type Dashboard = z.infer<typeof dashboardSchema>;
export function median(values: number[]) {
  const sorted = [...values].sort((a, b) => a - b),
    middle = Math.floor(sorted.length / 2);
  return sorted.length
    ? sorted.length % 2
      ? sorted[middle]
      : (sorted[middle - 1] + sorted[middle]) / 2
    : 0;
}
export const quadrantLabels = [
  '高传播 · 高互动',
  '低传播 · 高互动',
  '高传播 · 低互动',
  '低传播 · 低互动',
];
export function contentQuadrants(rows: Row[], readKey: string) {
  const eligible = rows.filter(
    (r) => (numeric(r[readKey]) ?? 0) > 0 && numeric(r.engagement_rate) !== null,
  );
  const x = median(eligible.map((r) => Number(r[readKey]))),
    y = median(eligible.map((r) => Number(r.engagement_rate)));
  return {
    x,
    y,
    rows: eligible.map(
      (r): Row & { reach: number; efficiency: number; size: number; quadrant: number } => ({
        ...r,
        reach: Number(r[readKey]),
        efficiency: Number(r.engagement_rate),
        size: Math.max(1, Number(r.total_engagement) || 0),
        quadrant:
          Number(r.engagement_rate) >= y
            ? Number(r[readKey]) >= x
              ? 0
              : 1
            : Number(r[readKey]) >= x
              ? 2
              : 3,
      }),
    ),
  };
}
export function publicationCalendar(rows: Row[], end: string, days = 84) {
  const totals = new Map<string, number>();
  for (const r of rows)
    if (typeof r.date === 'string') totals.set(r.date, (totals.get(r.date) || 0) + 1);
  const until = new Date(`${end}T00:00:00Z`).getTime();
  return Array.from({ length: days }, (_, i) => {
    const date = new Date(until - (days - i - 1) * 86400000).toISOString().slice(0, 10);
    return { date, count: totals.get(date) || 0 };
  });
}
