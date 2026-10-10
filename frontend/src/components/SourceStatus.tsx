import { z } from 'zod';
import { useQuery } from '@tanstack/react-query';
import { request } from '../lib/api';
import { freshnessSchema } from '../lib/data';
import { platformNames } from '../lib/labels';
import { TriangleAlert } from 'lucide-react';
import { endpoint, useResource } from '../lib/resources';
import { navigate } from '../lib/navigation';
import { ErrorState } from './ui';
import { daysBehind, formatTimestamp, freshness } from '../lib/time';
export type Source = 'orders' | 'wechat' | 'traffic' | 'xhs' | 'zhihu' | 'channels' | 'pgy';
export const sourceSchema = z.object({
  source: z.string(),
  records: z.number(),
  undated: z.number(),
  first_date: z.string().nullable(),
  last_date: z.string().nullable(),
  date_basis: z.string(),
  updated_at: z.string().nullable(),
  runs: z.array(
    z.object({
      account_id: z.number().nullable(),
      status: z.string(),
      started_at: z.string(),
      finished_at: z.string().nullable(),
      content_type: z.string().nullable().optional(),
    }),
  ),
});
export type SourceData = z.infer<typeof sourceSchema>;
export const sourceNames: Record<Source, string> = {
  orders: '商城订单',
  wechat: '公众号 API',
  traffic: '公众号后台导出',
  xhs: '小红书',
  zhihu: '知乎',
  channels: '视频号',
  pgy: '蒲公英合作',
};
export function useSourceStatus(source: Source, account = '', contentType = '') {
  return useResource(
    endpoint('/data/source-status', { source, account_id: account, content_type: contentType }),
    sourceSchema,
  );
}
const stateNames: Record<string, string> = {
  success: '采集成功',
  running: '正在采集',
  session_expired: '登录已失效',
  empty_export: '导出文件为空',
  download_failed: '下载失败',
  upload_failed: '入库失败',
  error: '采集失败',
  partial: '部分失败',
};
export const failedRuns = (d: SourceData) =>
  d.runs.filter((r) => !['success', 'running'].includes(r.status));
/** Page that analyses a source; orders are read on the analysis page. */
export const sourcePage = (source: Source) => (source === 'orders' ? 'analysis' : source);

/** Jump to the latest 30 days with data, or the full history, on the page for this source. */
export function RangeButtons({
  source,
  d,
  page,
}: {
  source: Source;
  d: SourceData;
  /** Page to open; by default orders go to analysis and other sources stay on the current page. */
  page?: string;
}) {
  const target = page ?? (source === 'orders' ? 'analysis' : undefined);
  return (
    <>
      {d.last_date && (
        <button
          onClick={() => {
            const end = d.last_date!;
            const start = new Date(new Date(`${end}T00:00:00Z`).getTime() - 29 * 86400000)
              .toISOString()
              .slice(0, 10);
            navigate({
              ...(target ? { page: target } : {}),
              start,
              end,
              range: 'dates',
              q: null,
              blogger: null,
              campaign: null,
              offset: null,
            });
          }}
        >
          查看最近有数据的 30 天
        </button>
      )}
      {d.first_date && d.last_date && d.first_date !== d.last_date && (
        <button
          onClick={() =>
            navigate({
              ...(target ? { page: target } : {}),
              start: d.first_date,
              end: d.last_date,
              range: 'dates',
              q: null,
              blogger: null,
              campaign: null,
              note: null,
              offset: null,
            })
          }
        >
          查看完整历史区间
        </button>
      )}
    </>
  );
}
export function FailureNotice({ failed }: { failed: SourceData['runs'] }) {
  if (!failed.length) return null;
  return (
    <span>
      {failed.some((r) => r.status === 'session_expired')
        ? '采集登录已失效，需要重新登录对应平台；当前仍可查看已入库的历史数据。'
        : '最近采集存在失败或空导出，请核对数据完整性。'}
    </span>
  );
}
export function RunList({ d }: { d: SourceData }) {
  return d.runs.length ? (
    <ul>
      {d.runs.map((r, i) => (
        <li key={i}>
          账号 {r.account_id ?? '默认'}
          {r.content_type ? ` / ${r.content_type}` : ''} · {stateNames[r.status] || r.status} ·{' '}
          {formatTimestamp(r.finished_at || r.started_at, { seconds: true })}
        </li>
      ))}
    </ul>
  ) : (
    <p>暂无自动采集执行记录，数据可能来自文件导入。</p>
  );
}
export const PROVENANCE =
  '覆盖日期说明业务记录所在的时间；最近入库说明数据进入系统的时间，均不等于本次页面刷新时间。时间统一按北京时间展示。';

/** Amber notice when the newest record of the source shown is more than 7 days old. */
export function StaleBanner({ name, lastDate }: { name: string; lastDate: string | null }) {
  if (freshness(lastDate) !== 'stale') return null;
  return (
    <p className="stale-banner">
      <TriangleAlert size={15} aria-hidden />
      <span>
        {name}数据最新至 <strong>{lastDate}</strong>，已 {daysBehind(lastDate)}{' '}
        天未更新；近期指标可能不完整，请先确认采集或导入状态。
      </span>
    </p>
  );
}

export function SourceStatus({
  source,
  account = '',
  contentType = '',
  visibleCount,
  onAll,
  admin = false,
  platform = '',
}: {
  source: Source;
  account?: string;
  contentType?: string;
  visibleCount?: number;
  onAll?: () => void;
  admin?: boolean;
  /** Order platform filter; when the server reports per-platform coverage, staleness uses it. */
  platform?: string;
}) {
  const query = useSourceStatus(source, account, contentType);
  const perPlatform = useQuery({
    queryKey: ['freshness'],
    queryFn: ({ signal }) => request('/data/freshness', freshnessSchema, { signal }),
    enabled: source === 'orders' && !!platform,
  });
  const platformCoverage =
    source === 'orders' && platform ? perPlatform.data?.platforms?.[platform] : undefined;
  if (query.isError)
    return (
      <ErrorState
        compact
        error={new Error('暂时无法核对数据覆盖和采集状态，请勿将页面取数时间视为数据更新时间。')}
        retry={() => void query.refetch()}
      />
    );
  if (!query.data)
    return (
      <div className="source-health" role="status">
        正在核对数据来源与覆盖范围…
      </div>
    );
  const d = query.data;
  const failed = failedRuns(d);
  const excluded = visibleCount === 0 && d.records > 0;
  return (
    <>
      {platformCoverage ? (
        <StaleBanner
          name={`${sourceNames[source]}（${platformNames[platform] ?? platform}）`}
          lastDate={platformCoverage.coverage_through ?? null}
        />
      ) : (
        <StaleBanner name={sourceNames[source]} lastDate={d.last_date} />
      )}
      <section
        className={`source-health ${failed.length || excluded ? 'attention' : ''}`}
        aria-label={`${sourceNames[source]}数据状态`}
      >
        <div className="source-health-line">
          <strong>{sourceNames[source]}</strong>
          <span>{d.records.toLocaleString()} 条源记录</span>
          <span>
            {d.date_basis}覆盖 {d.first_date || '未知'} — {d.last_date || '未知'}
          </span>
          <span>最近入库 {formatTimestamp(d.updated_at) || '暂无记录'}</span>
          <RangeButtons source={source} d={d} />
        </div>
        {(failed.length > 0 || excluded || !d.records || d.undated > 0) && (
          <div className="source-health-notice">
            <FailureNotice failed={failed} />
            {excluded && <span>当前筛选未命中记录；该数据源实际有 {d.records} 条历史数据。</span>}
            {!d.records && <span>该账号或数据源尚无记录。没有记录不代表业务指标为零。</span>}
            {d.undated > 0 && <span>{d.undated} 条记录缺少发布日期，按日期筛选时不会计入。</span>}
            {onAll && <button onClick={onAll}>查看全部合作</button>}
            {failed.length > 0 && admin && (
              <button
                onClick={() =>
                  navigate({ page: 'collector', account: null, start: null, end: null })
                }
              >
                查看采集状态 →
              </button>
            )}
          </div>
        )}
        <details>
          <summary>来源与统计口径</summary>
          <p>{PROVENANCE}</p>
          {source === 'wechat' && (
            <p>
              账号与日期筛选对应指标日期；每篇文章取区间内最新快照，发布日历则按文章实际发布日期归类。
            </p>
          )}
          <RunList d={d} />
        </details>
      </section>
    </>
  );
}
