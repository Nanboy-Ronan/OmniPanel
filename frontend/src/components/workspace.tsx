import { useState, type ReactNode } from 'react';
import type { UseQueryResult } from '@tanstack/react-query';
import { Download, RefreshCw, Search } from 'lucide-react';
import { navigate } from '../lib/navigation';
import { display, exportCsv, useResource, type Row } from '../lib/resources';
import { z } from 'zod';
import { label, platformNames } from '../lib/labels';
import { DataTable, EmptyState, ErrorState, Loading, type SortState } from './ui';
import { validDate } from '../lib/data';
import { formatField, numericField, statusTone } from '../lib/presentation';

export function Heading({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <header className="page-heading">
      <div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {action}
    </header>
  );
}
export function QueryView<T>({
  query,
  children,
}: {
  query: UseQueryResult<T, Error>;
  children: (data: T) => ReactNode;
}) {
  if (query.isPending)
    return query.fetchStatus === 'idle' ? <EmptyState title="请选择有效的筛选条件" /> : <Loading />;
  return (
    <>
      {query.isError && <ErrorState error={query.error} retry={() => void query.refetch()} />}
      {query.isError && query.data !== undefined && (
        <p className="stale-notice">当前保留上次成功加载的结果，可能不是最新数据。</p>
      )}
      {query.data !== undefined && children(query.data)}
    </>
  );
}
export function RangeFilter({
  values,
  platform = true,
  search = false,
  children,
}: {
  values: { start_date: string; end_date: string; platform?: string; q?: string };
  platform?: boolean;
  search?: boolean;
  children?: ReactNode;
}) {
  const [error, setError] = useState('');
  const latest = useResource(
    '/analysis/latest_order_date',
    z.object({ latest_order_date: z.string().nullable() }),
    platform,
  );
  return (
    <form
      className="filter-bar range-filter"
      onSubmit={(event) => {
        event.preventDefault();
        const form = new FormData(event.currentTarget);
        const start = String(form.get('start'));
        const end = String(form.get('end'));
        if (!validDate(start) || !validDate(end) || start > end) {
          setError('请选择有效日期，开始日期不能晚于结束日期。');
          return;
        }
        setError('');
        navigate({
          start,
          end,
          ...(platform ? { platform: String(form.get('platform')) } : {}),
          ...(search ? { q: String(form.get('q')) } : {}),
          offset: null,
        });
      }}
    >
      <label>
        开始日期
        <input
          aria-label="开始日期"
          name="start"
          type="date"
          defaultValue={values.start_date}
          required
        />
      </label>
      <label>
        结束日期
        <input
          aria-label="结束日期"
          name="end"
          type="date"
          defaultValue={values.end_date}
          required
        />
      </label>
      {platform && (
        <label>
          平台
          <select aria-label="平台" name="platform" defaultValue={values.platform ?? ''}>
            <option value="">全部平台</option>
            {['youzan', 'jd', 'tmall'].map((p) => (
              <option key={p} value={p}>
                {platformNames[p]}
              </option>
            ))}
          </select>
        </label>
      )}
      {search && (
        <label>
          搜索
          <input name="q" defaultValue={values.q ?? ''} maxLength={200} placeholder="输入关键词" />
        </label>
      )}
      {children}
      <button type="submit">
        <Search size={15} />
        应用筛选
      </button>
      {error && (
        <p className="field-error" role="alert">
          {error}
        </p>
      )}
      {platform && latest.data?.latest_order_date && (
        <div className="range-presets">
          <span>
            订单数据截至 <strong>{latest.data.latest_order_date}</strong>
          </span>
          {[30, 90].map((days) => (
            <button
              key={days}
              type="button"
              onClick={() => {
                const end = latest.data!.latest_order_date!;
                const start = new Date(
                  new Date(`${end}T00:00:00Z`).getTime() - (days - 1) * 86400000,
                )
                  .toISOString()
                  .slice(0, 10);
                navigate({ start, end, offset: null });
              }}
            >
              最新 {days} 天数据
            </button>
          ))}
        </div>
      )}
    </form>
  );
}
export function Stats({ items }: { items: { title: string; value: unknown; hint?: string }[] }) {
  return (
    <>
      <div className="metric-grid">
        {items.slice(0, 4).map((item) => (
          <article className="metric-card" key={item.title}>
            <div className="metric-label">{item.title}</div>
            <div className="metric-value small-value">
              {typeof item.value === 'number' && item.title.includes('（元）')
                ? formatField('price', item.value)
                : display(item.value)}
            </div>
            {item.hint && <small>{item.hint}</small>}
          </article>
        ))}
      </div>
      {items.length > 4 && (
        <div className="supporting-metrics" aria-label="补充指标">
          {items.slice(4).map((item) => (
            <div key={item.title}>
              <span>{item.title}</span>
              <strong>{display(item.value)}</strong>
              {item.hint && <small>{item.hint}</small>}
            </div>
          ))}
        </div>
      )}
    </>
  );
}
export function Tabs({
  items,
  value,
  onChange,
}: {
  items: { key: string; label: string }[];
  value: string;
  onChange: (key: string) => void;
}) {
  return (
    <div className="page-tabs" role="group" aria-label="视图">
      {items.map((item) => (
        <button key={item.key} aria-pressed={item.key === value} onClick={() => onChange(item.key)}>
          {item.label}
        </button>
      ))}
    </div>
  );
}
export function RecordTable({
  rows,
  columns,
  caption = '数据明细',
  onSelect,
  selectLabel = '查看',
  exportable = true,
  paginate = true,
  defaultColumns,
}: {
  rows: Row[];
  columns?: string[];
  caption?: string;
  onSelect?: (row: Row) => void;
  selectLabel?: string;
  exportable?: boolean;
  paginate?: boolean;
  defaultColumns?: string[];
}) {
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = usePageSize();
  const [filter, setFilter] = useState('');
  const [sort, setSort] = useState<SortState>(null);
  const keys = columns ?? [...new Set(rows.flatMap((row) => Object.keys(row)))];
  const [chosen, setChosen] = useState<string[] | null>(null);
  const initialKeys = defaultColumns ? keys.filter((key) => defaultColumns.includes(key)) : keys;
  const visibleKeys = chosen ? keys.filter((key) => chosen.includes(key)) : initialKeys;
  const filtered = rows.filter(
    (row) =>
      !filter ||
      keys.some((key) =>
        `${formatField(key, row[key])} ${display(row[key])}`
          .toLowerCase()
          .includes(filter.toLowerCase()),
      ),
  );
  const sorted = sort
    ? [...filtered].sort((a, b) => {
        const av = a[sort.key],
          bv = b[sort.key];
        // Missing values sink to the bottom in both directions.
        if (av == null || bv == null) return av == null ? (bv == null ? 0 : 1) : -1;
        const value =
          typeof av === 'number' && typeof bv === 'number'
            ? av - bv
            : display(av).localeCompare(display(bv), 'zh-CN');
        return sort.desc ? -value : value;
      })
    : filtered;
  // Header clicks cycle ascending → descending → original order.
  const toggleSort = (key: string) => {
    setSort((current) =>
      current?.key !== key ? { key, desc: false } : !current.desc ? { key, desc: true } : null,
    );
    setPage(0);
  };
  const current = Math.min(page, Math.max(0, Math.ceil(sorted.length / pageSize) - 1));
  if (!rows.length) return <EmptyState title="当前条件下暂无数据" />;
  return (
    <>
      <div className="table-toolbar">
        <label className="table-search">
          <span className="sr-only">表内搜索</span>
          <Search size={14} aria-hidden />
          <input
            type="search"
            placeholder="搜索已加载数据"
            value={filter}
            onChange={(e) => {
              setFilter(e.target.value);
              setPage(0);
            }}
          />
        </label>
        {keys.length > 5 && (
          <details className="column-picker">
            <summary>
              显示列 {visibleKeys.length}/{keys.length}
            </summary>
            <div>
              {keys.map((key) => (
                <label key={key}>
                  <input
                    type="checkbox"
                    checked={visibleKeys.includes(key)}
                    disabled={visibleKeys.length === 1 && visibleKeys.includes(key)}
                    onChange={(event) =>
                      setChosen(
                        event.target.checked
                          ? [...visibleKeys, key]
                          : visibleKeys.filter((value) => value !== key),
                      )
                    }
                  />
                  {label(key)}
                </label>
              ))}
              <button type="button" onClick={() => setChosen(null)}>
                恢复默认列
              </button>
            </div>
          </details>
        )}
        {exportable && (
          <button onClick={() => exportCsv(sorted, keys, `${caption}.csv`, caption)}>
            <Download size={14} />
            导出当前结果
          </button>
        )}
        <small>{filtered.length.toLocaleString()} 条</small>
      </div>
      <DataTable
        caption={caption}
        rows={paginate ? sorted.slice(current * pageSize, (current + 1) * pageSize) : sorted}
        rowKey={(row) => sorted.indexOf(row)}
        sort={sort}
        onSort={toggleSort}
        onRowClick={onSelect}
        columns={[
          ...(onSelect
            ? [
                {
                  key: '_open',
                  title: selectLabel === '查看' ? '详情' : '操作',
                  render: (row: Row) => (
                    <button className="ghost" onClick={() => onSelect(row)}>
                      {selectLabel}
                    </button>
                  ),
                },
              ]
            : []),
          ...visibleKeys.map((key) => ({
            key,
            title: label(key),
            numeric: numericField(key, rows),
            render: (row: Row) =>
              row[key] !== null && typeof row[key] === 'object' && !Array.isArray(row[key]) ? (
                <details className="cell-object">
                  <summary>查看结构化数据</summary>
                  <pre>{JSON.stringify(row[key], null, 2)}</pre>
                </details>
              ) : statusTone(key, row[key]) ? (
                <span className={`badge tone-${statusTone(key, row[key])}`}>
                  {formatField(key, row[key])}
                </span>
              ) : (
                <span
                  className={`cell-value ${['title', 'note_title', 'sku'].includes(key) ? 'cell-long-text' : ''}`}
                  title={formatField(key, row[key])}
                >
                  {formatField(key, row[key])}
                </span>
              ),
          })),
        ]}
      />
      {paginate && (
        <Pagination
          page={current}
          hasNext={(current + 1) * pageSize < filtered.length}
          onPage={setPage}
          total={filtered.length}
          pageSize={pageSize}
          onPageSize={(size) => {
            // Keep the first visible row on screen when the page grows or shrinks.
            setPage(Math.floor((current * pageSize) / size));
            setPageSize(size);
          }}
        />
      )}
    </>
  );
}
export const PAGE_SIZES = [25, 50, 100] as const;
const PAGE_SIZE_KEY = 'rpa.console.pageSize';
function readPageSize() {
  try {
    const value = Number(sessionStorage.getItem(PAGE_SIZE_KEY));
    return (PAGE_SIZES as readonly number[]).includes(value) ? value : 25;
  } catch {
    return 25;
  }
}
/** Rows per page for client-side tables, shared across tables for this browser session. */
function usePageSize() {
  const [size, setSize] = useState(readPageSize);
  const update = (value: number) => {
    setSize(value);
    try {
      sessionStorage.setItem(PAGE_SIZE_KEY, String(value));
    } catch {
      // Falls back to this table only.
    }
  };
  return [size, update] as const;
}
/**
 * Pager. With `pageSize` and a known `total` it shows "第 N / M 页" with a jump field; server
 * lists that only know whether a next page exists keep plain previous / next.
 */
export function Pagination({
  page,
  hasNext,
  onPage,
  total,
  pageSize,
  onPageSize,
}: {
  page: number;
  hasNext: boolean;
  onPage: (page: number) => void;
  total?: number | null;
  pageSize?: number;
  onPageSize?: (size: number) => void;
}) {
  const pages = pageSize && total != null ? Math.max(1, Math.ceil(total / pageSize)) : null;
  const [draft, setDraft] = useState(String(page + 1));
  const [shown, setShown] = useState(page);
  if (shown !== page) {
    setShown(page);
    setDraft(String(page + 1));
  }
  const jump = () => {
    const target = Math.round(Number(draft));
    if (!pages || !Number.isFinite(target) || target < 1) return setDraft(String(page + 1));
    const next = Math.min(pages, target) - 1;
    setDraft(String(next + 1));
    if (next !== page) onPage(next);
  };
  return (
    <div className="pagination">
      <span className="pagination-summary">
        {pages === null && `第 ${page + 1} 页`}
        {total != null ? `${pages === null ? ' · ' : ''}共 ${total.toLocaleString()} 条` : ''}
      </span>
      {onPageSize && pageSize && total != null && total > PAGE_SIZES[0] && (
        <label className="pagination-size">
          每页
          <select
            aria-label="每页行数"
            value={pageSize}
            onChange={(e) => onPageSize(Number(e.target.value))}
          >
            {PAGE_SIZES.map((size) => (
              <option key={size} value={size}>
                {size}
              </option>
            ))}
          </select>
          条
        </label>
      )}
      <div className="pagination-nav">
        <button disabled={page === 0} onClick={() => onPage(page - 1)}>
          上一页
        </button>
        {pages !== null && pages > 1 && (
          <form
            className="pagination-jump"
            onSubmit={(e) => {
              e.preventDefault();
              jump();
            }}
          >
            第
            <input
              aria-label="跳转到页码"
              type="number"
              inputMode="numeric"
              min={1}
              max={pages}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onBlur={jump}
            />
            / {pages.toLocaleString()} 页
          </form>
        )}
        <button disabled={!hasNext} onClick={() => onPage(page + 1)}>
          下一页
        </button>
      </div>
    </div>
  );
}
export function Refresh({ onClick, busy }: { onClick: () => void; busy: boolean }) {
  return (
    <button onClick={onClick} disabled={busy}>
      <RefreshCw size={15} className={busy ? 'spin' : ''} />
      刷新
    </button>
  );
}
export function Detail({ row }: { row: Row }) {
  return (
    <dl className="detail-list">
      {Object.entries(row).map(([key, value]) => (
        <div key={key}>
          <dt>{label(key)}</dt>
          <dd>{formatField(key, value)}</dd>
        </div>
      ))}
    </dl>
  );
}
export function ConfirmAction({
  title,
  description,
  onConfirm,
  busy,
  danger = false,
}: {
  title: string;
  description: string;
  onConfirm: () => void;
  busy: boolean;
  danger?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [confirm, setConfirm] = useState('');
  return (
    <div className="confirm-action">
      {!open ? (
        <button className={danger ? 'danger' : ''} onClick={() => setOpen(true)}>
          {title}
        </button>
      ) : (
        <div className="confirmation">
          <p>{description}</p>
          {danger && (
            <label>
              输入「确认」继续
              <input
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                aria-label={`确认${title}`}
              />
            </label>
          )}
          <button
            disabled={busy || (danger && confirm !== '确认')}
            onClick={() => {
              onConfirm();
              setOpen(false);
              setConfirm('');
            }}
          >
            确认{title}
          </button>
          <button onClick={() => setOpen(false)} disabled={busy}>
            取消
          </button>
        </div>
      )}
    </div>
  );
}
