import { Component, type ReactNode } from 'react';
import { AlertCircle, ArrowDownRight, ArrowUpRight, LoaderCircle, RefreshCw } from 'lucide-react';
import { ApiError } from '../lib/api';

export function Loading({ label = '正在加载数据' }: { label?: string }) {
  return (
    <div className="state" role="status">
      <LoaderCircle className="spin" size={22} />
      <p>{label}</p>
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
export function DataTable<T>({
  caption,
  rows,
  columns,
  rowKey,
}: {
  caption: string;
  rows: T[];
  columns: Column<T>[];
  rowKey: (row: T) => string | number;
}) {
  return (
    <div className="table-scroll" role="region" aria-label={caption} tabIndex={0}>
      <table>
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key} scope="col" className={c.numeric ? 'number' : ''}>
                {c.title}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={rowKey(row)}>
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
