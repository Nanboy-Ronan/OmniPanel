import { useQueries } from '@tanstack/react-query';
import { request } from '../lib/api';
import { sourceSchema, sourceNames, type Source } from './SourceStatus';
import { navigate } from '../lib/navigation';
const sources: Source[] = ['orders', 'wechat', 'xhs', 'channels', 'zhihu', 'pgy'];
export default function DataReadiness() {
  const queries = useQueries({
    queries: sources.map((source) => ({
      queryKey: ['resource', `/data/source-status?source=${source}`],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        request(`/data/source-status?source=${source}`, sourceSchema, { signal }),
    })),
  });
  const failures = queries.filter((q) =>
    q.data?.runs.some((r) => !['success', 'running'].includes(r.status)),
  ).length;
  return (
    <details className="data-readiness">
      <summary>
        数据来源检查 ·{' '}
        {queries.some((q) => q.isPending)
          ? '正在核对'
          : queries.some((q) => q.isError)
            ? '状态未能全部核对'
            : `${failures} 个来源采集需处理`}{' '}
        <span>展开查看各平台覆盖与状态</span>
      </summary>
      <div>
        {queries.map((q, i) => {
          const d = q.data,
            failed = d?.runs.some((r) => !['success', 'running'].includes(r.status));
          return (
            <button
              key={sources[i]}
              className={failed ? 'attention' : ''}
              onClick={() =>
                navigate({
                  page: sources[i] === 'orders' ? 'analysis' : sources[i],
                  start: null,
                  end: null,
                  platform: null,
                  account: null,
                  view: null,
                  range: null,
                  q: null,
                  blogger: null,
                  campaign: null,
                  note: null,
                  metric: null,
                  offset: null,
                })
              }
            >
              <strong>{sourceNames[sources[i]]}</strong>
              <span>
                {q.isError
                  ? '状态读取失败'
                  : !d
                    ? '核对中…'
                    : failed
                      ? '采集需处理'
                      : d.records
                        ? '已有数据'
                        : '尚无数据'}
              </span>
              <small>
                {d?.date_basis}至 {d?.last_date || '—'} · {d?.records ?? '—'} 条
              </small>
            </button>
          );
        })}
      </div>
    </details>
  );
}
