import { display, numeric, type Row } from './resources';
import { platformNames } from './labels';

export const moneyFields = new Set([
  'price',
  'spend',
  'calculated_cpe',
  'revenue',
  'prior_revenue',
  'prior_aov',
  'aov',
  'paid_sum',
  'gmv',
  'total_revenue',
  'total_spend',
  'blogger_quote',
  'service_fee',
  'cost_per_interaction',
  'avg_cpe',
  'pre_revenue',
  'post_revenue',
]);
const identifiers = /(^id$|_id$|_key$|^phone$|^mobile$)/;
const statuses: Record<string, string> = {
  success: '成功',
  completed: '已完成',
  processing: '处理中',
  pending: '等待中',
  failed: '失败',
  error: '失败',
  partial: '部分完成',
  running: '执行中',
  cancelled: '已取消',
  admin: '管理员',
  analyst: '分析员',
  viewer: '查看者',
};
export function formatField(key: string, value: unknown): string {
  if (value == null) return '—';
  if (identifiers.test(key)) return String(value);
  if (typeof value === 'number' && moneyFields.has(key))
    return Number.isFinite(value)
      ? value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
      : '—';
  if (key === 'platform' && typeof value === 'string') return platformNames[value] ?? value;
  if (['status', 'role'].includes(key) && typeof value === 'string')
    return statuses[value] ?? value;
  if (Array.isArray(value) && value.every((item) => typeof item !== 'object'))
    return (
      value
        .map((item) => (key === 'platforms' ? (platformNames[String(item)] ?? item) : item))
        .join('、') || '—'
    );
  return display(value);
}
export function numericField(key: string, rows: Row[]) {
  return !identifiers.test(key) && rows.some((row) => numeric(row[key]) !== null);
}
export function rankedRows(rows: Row[], key: string, limit: number) {
  return rows
    .filter((row) => numeric(row[key]) !== null)
    .sort((a, b) => Number(b[key]) - Number(a[key]))
    .slice(0, limit);
}
