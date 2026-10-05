import { numeric, type Row } from './resources';
export function pgyRows(rows: Row[]) {
  return rows.map(
    (r): Row & { spend: number | null; cost_complete: boolean; calculated_cpe: number | null } => {
      const quote = numeric(r.blogger_quote),
        fee = numeric(r.service_fee),
        engagement = numeric(r.interactions);
      const complete = quote !== null && fee !== null;
      const spend = quote === null && fee === null ? null : (quote ?? 0) + (fee ?? 0);
      return {
        ...r,
        spend,
        cost_complete: complete,
        calculated_cpe:
          complete && engagement !== null && engagement > 0 ? spend! / engagement : null,
      };
    },
  );
}
export function knownSum(rows: Row[], key: string) {
  const values = rows.map((r) => numeric(r[key])).filter((v): v is number => v !== null);
  return values.length ? values.reduce((a, b) => a + b, 0) : null;
}
export function pgySummary(rows: Row[]) {
  const comparable = rows.filter((r) => r.cost_complete && numeric(r.interactions) !== null);
  const interactions = knownSum(comparable, 'interactions');
  return {
    spend: knownSum(rows, 'spend'),
    impressions: knownSum(rows, 'impressions'),
    interactions: knownSum(rows, 'interactions'),
    missingCost: rows.filter((r) => !r.cost_complete).length,
    cpe:
      interactions && interactions > 0 ? (knownSum(comparable, 'spend') ?? 0) / interactions : null,
  };
}
export function groupPgy(rows: Row[], field: string) {
  const groups = new Map<string, Row[]>();
  rows.forEach((r) => {
    const key = typeof r[field] === 'string' && r[field] ? String(r[field]) : '未标注';
    groups.set(key, [...(groups.get(key) || []), r]);
  });
  return [...groups.entries()]
    .map(([name, items]) => ({ name, notes: items.length, ...pgySummary(items) }))
    .sort((a, b) => (b.interactions ?? 0) - (a.interactions ?? 0));
}
