const OFFSET = /(Z|[+-]\d{2}:?\d{2})$/i;
const shanghai = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Asia/Shanghai',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hourCycle: 'h23',
});

/**
 * Render a server timestamp as Beijing time ("YYYY-MM-DD HH:mm").
 * Strings with an offset (timestamptz, e.g. "…+00:00" or "…Z") are converted; naive strings are
 * already recorded in Beijing time and are only reformatted.
 */
export function formatTimestamp(
  value: string | null | undefined,
  { seconds = false }: { seconds?: boolean } = {},
): string | null {
  if (!value) return null;
  const length = seconds ? 19 : 16;
  const trimmed = value.trim();
  if (!OFFSET.test(trimmed)) return trimmed.replace('T', ' ').slice(0, length);
  const date = new Date(trimmed);
  if (!Number.isFinite(date.getTime())) return trimmed.replace('T', ' ').slice(0, length);
  const part = Object.fromEntries(shanghai.formatToParts(date).map((p) => [p.type, p.value]));
  const text = `${part.year}-${part.month}-${part.day} ${part.hour}:${part.minute}:${part.second}`;
  return text.slice(0, length);
}

/** Today's calendar date in Beijing, as "YYYY-MM-DD". */
export function todayInShanghai(now: Date = new Date()): string {
  return formatTimestamp(now.toISOString())!.slice(0, 10);
}

export type Freshness = 'fresh' | 'aging' | 'stale' | 'unknown';
/** Whole days between a data date ("YYYY-MM-DD…") and today in Beijing; null when unknown. */
export function daysBehind(lastDate: string | null | undefined, now: Date = new Date()) {
  if (!lastDate || !/^\d{4}-\d{2}-\d{2}/.test(lastDate)) return null;
  const today = Date.parse(`${todayInShanghai(now)}T00:00:00Z`);
  const last = Date.parse(`${lastDate.slice(0, 10)}T00:00:00Z`);
  if (!Number.isFinite(last)) return null;
  return Math.max(0, Math.round((today - last) / 86400000));
}
/** Up to 2 days behind is fresh, up to 7 is aging, beyond that the source is stale. */
export function freshness(lastDate: string | null | undefined, now: Date = new Date()): Freshness {
  const days = daysBehind(lastDate, now);
  if (days === null) return 'unknown';
  return days > 7 ? 'stale' : days > 2 ? 'aging' : 'fresh';
}
