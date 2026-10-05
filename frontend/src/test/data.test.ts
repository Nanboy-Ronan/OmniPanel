import { describe, expect, it } from 'vitest';
import {
  batchPollInterval,
  comparisonWindow,
  delta,
  kpiSchema,
  validDate,
  type Batch,
} from '../lib/data';
import { kpi, batch } from './fixtures';

describe('KPI parity with the existing backend', () => {
  it('uses a Monday-based partial week across a year boundary', () => {
    expect(comparisonWindow('2026-01-01', 'week')).toEqual({
      start: '2025-12-29',
      end: '2026-01-01',
      priorStart: '2025-12-22',
      priorEnd: '2025-12-25',
    });
  });
  it('caps prior month at its last day including leap years', () => {
    expect(comparisonWindow('2026-03-31', 'month').priorEnd).toBe('2026-02-28');
    expect(comparisonWindow('2024-03-31', 'month').priorEnd).toBe('2024-02-29');
    expect(comparisonWindow('2026-01-03', 'month').priorStart).toBe('2025-12-01');
  });
  it('does not invent a percentage when baseline is zero', () => {
    expect(delta(10, 0)).toBeNull();
    expect(delta(80, 100)).toBe(-20);
  });
  it('rejects missing periods, non-finite metrics, and nonexistent dates', () => {
    expect(kpiSchema.safeParse({ ...kpi, prior_month: undefined }).success).toBe(false);
    expect(kpiSchema.safeParse({ ...kpi, day: { ...kpi.day, aov: Infinity } }).success).toBe(false);
    expect(validDate('2026-02-30')).toBe(false);
    expect(validDate('2024-02-29')).toBe(true);
  });
  it('polls only when there are unfinished tasks', () => {
    expect(batchPollInterval([batch as Batch])).toBe(false);
    expect(batchPollInterval([{ ...batch, status: 'processing' } as Batch])).toBe(5000);
    expect(batchPollInterval([])).toBe(false);
  });
});
