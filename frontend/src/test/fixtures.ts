import type { Kpi } from '../lib/data';
export const metrics = { orders: 120, revenue: 30960, aov: 258, unique_customers: 92 };
export const kpi: Kpi = {
  day: metrics,
  prior_day: { ...metrics, orders: 100, revenue: 25000 },
  week: { ...metrics, orders: 684, revenue: 171400 },
  prior_week: { ...metrics, orders: 725, revenue: 186600 },
  month: { ...metrics, orders: 342, revenue: 86125 },
  prior_month: { ...metrics, orders: 301, revenue: 74750 },
};
export const user = {
  id: 'a1',
  email: 'analyst@example.test',
  role: 'analyst',
  display_name: '测试分析师',
};
export const batch = {
  id: 17,
  filename: '订单.csv',
  platform: 'jd',
  uploaded_at: '2026-10-03 15:00:00',
  row_count: 100,
  inserted_orders: 80,
  duplicate_rows: 20,
  invalid_rows: 0,
  status: 'completed',
  error_message: null,
};
export function response(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}
