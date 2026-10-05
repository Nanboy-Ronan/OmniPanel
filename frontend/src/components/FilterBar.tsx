import { useState } from 'react';
import { CalendarDays, RefreshCw } from 'lucide-react';
import { validDate, periods, type Period } from '../lib/data';

export function FilterBar({
  anchor,
  latest,
  period,
  busy,
  onDate,
  onPeriod,
  onRefresh,
}: {
  anchor: string;
  latest: string;
  period: Period;
  busy: boolean;
  onDate: (date: string) => void;
  onPeriod: (period: Period) => void;
  onRefresh: () => void;
}) {
  const [draft, setDraft] = useState(anchor);
  const [error, setError] = useState('');
  return (
    <div className="filter-bar">
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (!validDate(draft) || draft > latest) {
            setError('请选择不晚于最新数据日的有效日期。');
            return;
          }
          setError('');
          onDate(draft);
        }}
      >
        <label htmlFor="anchor">
          <CalendarDays size={16} />
          统计截止日
        </label>
        <input
          id="anchor"
          type="date"
          required
          value={draft}
          max={latest}
          aria-invalid={!!error}
          aria-describedby={error ? 'date-error' : undefined}
          onChange={(event) => setDraft(event.target.value)}
        />
        <button type="submit" disabled={draft === anchor}>
          应用
        </button>
        <button
          type="button"
          className="text-button"
          onClick={() => {
            setDraft(latest);
            setError('');
            onDate('');
          }}
        >
          最新数据日
        </button>
        {error && (
          <span id="date-error" className="field-error" role="alert">
            {error}
          </span>
        )}
      </form>
      <div className="filter-actions">
        <div className="segmented" role="group" aria-label="统计周期">
          {(Object.keys(periods) as Period[]).map((key) => (
            <button key={key} aria-pressed={period === key} onClick={() => onPeriod(key)}>
              {periods[key]}
            </button>
          ))}
        </div>
        <button aria-label="刷新数据" title="刷新数据" disabled={busy} onClick={onRefresh}>
          <RefreshCw size={16} className={busy ? 'spin' : ''} />
        </button>
      </div>
    </div>
  );
}
