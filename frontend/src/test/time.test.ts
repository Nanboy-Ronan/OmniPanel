import { describe, expect, it } from 'vitest';
import { daysBehind, formatTimestamp, freshness, todayInShanghai } from '../lib/time';
import { formatField } from '../lib/presentation';

describe('timestamp display', () => {
  it('converts offset timestamps to Beijing time', () => {
    expect(formatTimestamp('2026-10-04T01:30:00+00:00')).toBe('2026-10-04 09:30');
    expect(formatTimestamp('2026-10-03T20:15:42Z', { seconds: true })).toBe('2026-10-04 04:15:42');
    expect(formatTimestamp('2026-10-04T09:30:00.123456+08:00')).toBe('2026-10-04 09:30');
    expect(formatTimestamp('2026-10-04T16:00:00-0400')).toBe('2026-10-05 04:00');
  });
  it('keeps naive timestamps as recorded, since they are already Beijing time', () => {
    expect(formatTimestamp('2026-10-04T09:30:00')).toBe('2026-10-04 09:30');
    expect(formatTimestamp('2026-10-04 09:30:00', { seconds: true })).toBe('2026-10-04 09:30:00');
    expect(formatTimestamp(null)).toBeNull();
    expect(formatTimestamp('')).toBeNull();
  });
  it('formats zoned timestamps inside table cells and leaves other strings alone', () => {
    expect(formatField('created_at', '2026-10-04T01:30:00+00:00')).toBe('2026-10-04 09:30:00');
    expect(formatField('title', '2026-10-04')).toBe('2026-10-04');
  });
  it('grades freshness against a fixed Beijing calendar day', () => {
    // 2026-10-09 17:00 UTC is already 2026-10-10 in Beijing.
    const now = new Date('2026-10-09T17:00:00Z');
    expect(todayInShanghai(now)).toBe('2026-10-10');
    expect(daysBehind('2026-10-08', now)).toBe(2);
    expect(freshness('2026-10-08', now)).toBe('fresh');
    expect(freshness('2026-10-05', now)).toBe('aging');
    expect(freshness('2026-10-03', now)).toBe('aging');
    expect(freshness('2026-10-02', now)).toBe('stale');
    expect(freshness(null, now)).toBe('unknown');
  });
});
