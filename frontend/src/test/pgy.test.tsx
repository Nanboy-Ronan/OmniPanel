import { describe, it, expect } from 'vitest';
import { pgyRows, pgySummary, knownSum } from '../lib/pgy';
describe('cooperation analysis data semantics', () => {
  it('preserves unknown costs and uses a weighted comparable cost per interaction', () => {
    const rows = pgyRows([
      { blogger_quote: 100, service_fee: 10, interactions: 10 },
      { blogger_quote: 200, service_fee: 20, interactions: 100 },
      { blogger_quote: 50, service_fee: null, interactions: 1000 },
      { blogger_quote: null, service_fee: null, interactions: null },
    ]);
    const summary = pgySummary(rows);
    expect(summary.spend).toBe(380);
    expect(summary.cpe).toBe(3);
    expect(summary.missingCost).toBe(2);
    expect(rows[3].spend).toBeNull();
    expect(knownSum([{ impressions: null }], 'impressions')).toBeNull();
    expect(knownSum([{ impressions: 0 }], 'impressions')).toBe(0);
  });
});
