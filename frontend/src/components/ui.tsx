import { Component, type ReactNode } from 'react';
import {
  AlertCircle,
  ArrowDown,
  ArrowDownRight,
  ArrowUp,
  ArrowUpDown,
  ArrowUpRight,
  Inbox,
  LoaderCircle,
  RefreshCw,
} from 'lucide-react';
import { ApiError, type UploadProgress } from '../lib/api';

export function Loading({ label = '正在加载数据' }: { label?: string }) {
  return (
    <div className="state" role="status">
      <LoaderCircle className="spin" size={22} />
      <p>{label}</p>
    </div>
  );
}
/** Page-shaped placeholder while a lazily loaded page arrives. */
export function PageSkeleton() {
  return (
    <div className="skeleton" role="status" aria-label="正在加载页面">
      <div className="skeleton-row">
        {[0, 1, 2, 3].map((i) => (
          <div className="skeleton-block" key={i} />
        ))}
      </div>
      <div className="skeleton-block tall" />
    </div>
  );
}
export function ErrorState({
  error,
  retry,
  compact = false,
}: {
  error: Error;
  retry?: () => void;
  compact?: boolean;
}) {
  return (
    <div className={`error-state ${compact ? 'compact' : ''}`} role="alert">
      <AlertCircle size={20} />
      <div>
        <strong>{error.message}</strong>
        {error instanceof ApiError && error.requestId && <p>问题编号：{error.requestId}</p>}
      </div>
      {retry && (
        <button onClick={retry}>
          <RefreshCw size={15} />
          重试
        </button>
      )}
    </div>
  );
}
export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="state">
      <span className="state-icon" aria-hidden>
        <Inbox size={20} />
      </span>
      <strong>{title}</strong>
      <p>{children}</p>
    </div>
  );
}
export function Panel({
  title,
  subtitle,
  action,
  children,
}: {
  title: string;
  subtitle?: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="panel">
      <header className="panel-heading">
        <div>
          <h2>{title}</h2>
          {subtitle && <p>{subtitle}</p>}
        </div>
        {action}
      </header>
      {children}
    </section>
  );
}
export function Change({ value }: { value: number | null }) {
  if (value === null) return <span className="delta neutral">— 无对比基准</span>;
  const Icon = value < 0 ? ArrowDownRight : ArrowUpRight;
  return (
    <span className={`delta ${value === 0 ? 'neutral' : value < 0 ? 'negative' : 'positive'}`}>
      {value !== 0 && <Icon size={15} />}
      {value > 0 ? '+' : ''}
      {value.toFixed(1)}%
    </span>
  );
}
export type Column<T> = {
  key: string;
  title: string;
  numeric?: boolean;
  render: (row: T) => ReactNode;
};
export type SortState = { key: string; desc: boolean } | null;
export function DataTable<T>({
  caption,
  rows,
  columns,
  rowKey,
  sort,
  onSort,
  onRowClick,
}: {
  caption: string;
  rows: T[];
  columns: Column<T>[];
  rowKey: (row: T) => string | number;
  /** When set, headers of columns other than `_`-prefixed ones become sort toggles. */
  sort?: SortState;
  onSort?: (key: string) => void;
  onRowClick?: (row: T) => void;
}) {
  return (
    <div className="table-scroll" role="region" aria-label={caption} tabIndex={0}>
      <table>
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            {columns.map((c) => {
              const sortable = onSort && !c.key.startsWith('_');
              const active = sort?.key === c.key;
              return (
                <th
                  key={c.key}
                  scope="col"
                  className={c.numeric ? 'number' : ''}
                  aria-sort={
                    sortable
                      ? active
                        ? sort.desc
                          ? 'descending'
                          : 'ascending'
                        : 'none'
                      : undefined
                  }
                >
                  {sortable ? (
                    <button className="sort-button" onClick={() => onSort(c.key)}>
                      {c.title}
                      {active && sort.desc ? (
                        <ArrowDown size={13} aria-hidden />
                      ) : active ? (
                        <ArrowUp size={13} aria-hidden />
                      ) : (
                        <ArrowUpDown size={13} aria-hidden />
                      )}
                    </button>
                  ) : (
                    c.title
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={rowKey(row)}
              className={onRowClick ? 'row-clickable' : undefined}
              onClick={
                onRowClick
                  ? (event) => {
                      // Controls inside the row keep their own behaviour.
                      if (
                        (event.target as HTMLElement).closest('button, a, input, select, summary')
                      )
                        return;
                      onRowClick(row);
                    }
                  : undefined
              }
            >
              {columns.map((c) => (
                <td key={c.key} className={c.numeric ? 'number' : ''}>
                  {c.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
const megabytes = (bytes: number) => (bytes / 1024 / 1024).toFixed(1);
/** Determinate upload bar; once every byte is sent the server is still validating the file. */
export function UploadMeter({
  name,
  progress,
  onCancel,
}: {
  name: string;
  progress: UploadProgress;
  onCancel: () => void;
}) {
  const sent = progress.percent === 100;
  return (
    <div className="upload-meter">
      <div className="upload-meter-head">
        <span className="upload-meter-name">
          {sent ? '已上传，等待服务器受理' : '正在上传'} · {name}
        </span>
        <strong>{progress.percent === null ? '—' : `${progress.percent}%`}</strong>
      </div>
      <div
        className={`upload-meter-track ${progress.percent === null ? 'indeterminate' : ''}`}
        role="progressbar"
        aria-label={`上传 ${name}`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={progress.percent ?? undefined}
      >
        <span style={{ width: `${progress.percent ?? 100}%` }} />
      </div>
      <div className="upload-meter-foot">
        <small>
          {megabytes(progress.loaded)}
          {progress.total ? ` / ${megabytes(progress.total)}` : ''} MB
        </small>
        {sent ? (
          <small>上传已完成，请勿重复提交</small>
        ) : (
          <button type="button" className="text-button" onClick={onCancel}>
            取消上传
          </button>
        )}
      </div>
    </div>
  );
}
export const isAbort = (error: unknown) =>
  typeof error === 'object' &&
  error !== null &&
  (error as { name?: unknown }).name === 'AbortError';
export class PageBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? (
      <ErrorState
        error={new Error('页面显示异常，请重新加载。')}
        retry={() => window.location.reload()}
      />
    ) : (
      this.props.children
    );
  }
}
