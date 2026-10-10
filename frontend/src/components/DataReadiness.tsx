import { useState } from 'react';
import { useQueries } from '@tanstack/react-query';
import { ArrowRight, Database, TriangleAlert } from 'lucide-react';
import { request } from '../lib/api';
import { navigate } from '../lib/navigation';
import { platformNames } from '../lib/labels';
import type { FreshnessData } from '../lib/data';
import { daysBehind, formatTimestamp, freshness, type Freshness } from '../lib/time';
import {
  failedRuns,
  FailureNotice,
  PROVENANCE,
  RangeButtons,
  RunList,
  sourceNames,
  sourcePage,
  sourceSchema,
  type Source,
  type SourceData,
} from './SourceStatus';
import { Drawer } from './Drawer';

const sources: Source[] = ['orders', 'wechat', 'xhs', 'channels', 'zhihu', 'pgy'];
const toneNames: Record<Freshness, string> = {
  fresh: '数据及时',
  aging: '更新放缓',
  stale: '超过 7 天未更新',
  unknown: '暂无数据日期',
};
const ageText = (lastDate: string | null) => {
  const days = daysBehind(lastDate);
  return days === null ? '' : days === 0 ? '当天' : `${days} 天前`;
};

/**
 * One-line provenance for the overview: a freshness chip per source (green ≤2 days, amber ≤7,
 * red beyond) plus the order coverage date and page fetch time. Details open in a drawer.
 */
export default function DataReadiness({
  latestDate,
  fetchedAt,
  refreshing,
  orders,
  admin = false,
}: {
  latestDate?: string | null;
  fetchedAt?: number;
  refreshing?: boolean;
  orders?: { data?: FreshnessData; error: boolean; retry: () => void };
  admin?: boolean;
}) {
  const [open, setOpen] = useState<Source | null>(null);
  const queries = useQueries({
    queries: sources.map((source) => ({
      queryKey: ['resource', `/data/source-status?source=${source}`],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        request(`/data/source-status?source=${source}`, sourceSchema, { signal }),
    })),
  });
  const failures = queries.filter((q) => q.data && failedRuns(q.data).length).length;
  const unreadable = queries.filter((q) => q.isError);
  const index = open ? sources.indexOf(open) : -1;
  const current = index >= 0 ? queries[index] : null;
  return (
    <section className="status-strip" aria-label="数据来源状态">
      <span
        className="status-strip-label"
        title="绿色：2 天内有新数据；琥珀：3–7 天；红色：超过 7 天"
      >
        <Database size={14} aria-hidden />
        数据来源
      </span>
      <ul className="status-chips">
        {queries.map((q, i) => {
          const source = sources[i];
          const d = q.data;
          const tone: Freshness | 'error' | 'pending' = q.isError
            ? 'error'
            : !d
              ? 'pending'
              : freshness(d.last_date);
          const failed = d ? failedRuns(d).length > 0 : false;
          const state = q.isError
            ? '状态读取失败'
            : !d
              ? '核对中'
              : `${d.date_basis}至 ${d.last_date ?? '—'}${
                  d.last_date ? `（${ageText(d.last_date)}）` : ''
                }，${toneNames[tone as Freshness]}${failed ? '，采集需处理' : ''}`;
          return (
            <li key={source}>
              <button
                type="button"
                className={`status-chip tone-${tone} ${failed ? 'attention' : ''}`}
                aria-label={`${sourceNames[source]}：${state}。查看详情`}
                title={state}
                onClick={() => setOpen(source)}
              >
                <span className="status-dot" aria-hidden />
                <span className="status-chip-name">{sourceNames[source]}</span>
                <span className="status-chip-date">
                  {q.isError
                    ? '读取失败'
                    : !d
                      ? '核对中'
                      : d.last_date
                        ? d.last_date.slice(5)
                        : '无数据'}
                </span>
                {failed && <TriangleAlert className="status-chip-flag" size={13} aria-hidden />}
              </button>
            </li>
          );
        })}
      </ul>
      <div className="status-strip-meta">
        {failures > 0 && <span className="status-strip-warn">{failures} 个来源需处理</span>}
        {unreadable.length > 0 && (
          <button
            type="button"
            className="text-button"
            onClick={() => unreadable.forEach((q) => void q.refetch())}
          >
            重新核对来源
          </button>
        )}
        {latestDate && (
          <span>
            源数据覆盖至 <strong>{latestDate}</strong>
          </span>
        )}
        <span>
          {fetchedAt
            ? `页面取数 ${new Date(fetchedAt).toLocaleTimeString('zh-CN', { hour12: false })}`
            : '等待统计数据'}
        </span>
        {refreshing && <span role="status">正在刷新…</span>}
      </div>
      {open && (
        <Drawer
          title={`${sourceNames[open]}数据状态`}
          subtitle="覆盖日期、最近入库与采集记录"
          onClose={() => setOpen(null)}
        >
          {current?.isError ? (
            <div className="source-detail">
              <p className="stale-notice">
                暂时无法核对数据覆盖和采集状态，请勿将页面取数时间视为数据更新时间。
              </p>
              <button onClick={() => void current.refetch()}>重新核对</button>
            </div>
          ) : current?.data ? (
            <SourceDetail
              source={open}
              d={current.data}
              admin={admin}
              orders={open === 'orders' ? orders : undefined}
            />
          ) : (
            <p role="status">正在核对数据来源与覆盖范围…</p>
          )}
        </Drawer>
      )}
    </section>
  );
}

function SourceDetail({
  source,
  d,
  admin,
  orders,
}: {
  source: Source;
  d: SourceData;
  admin: boolean;
  orders?: { data?: FreshnessData; error: boolean; retry: () => void };
}) {
  const failed = failedRuns(d);
  const tone = freshness(d.last_date);
  const platforms = Object.entries(orders?.data?.platforms ?? {});
  return (
    <div className="source-detail">
      <p className={`source-detail-tone tone-${tone}`}>
        <span className="status-dot" aria-hidden />
        {toneNames[tone]}
        {d.last_date && tone !== 'fresh' ? ` · 最新数据为 ${ageText(d.last_date)}` : ''}
      </p>
      <dl className="definitions">
        <div>
          <dt>源记录</dt>
          <dd>{d.records.toLocaleString()} 条</dd>
        </div>
        <div>
          <dt>{d.date_basis}覆盖</dt>
          <dd>
            {d.first_date || '未知'} — {d.last_date || '未知'}
          </dd>
        </div>
        <div>
          <dt>最近入库</dt>
          <dd>{formatTimestamp(d.updated_at) || '暂无记录'}</dd>
        </div>
        {orders && (
          <div>
            <dt>最近成功导入</dt>
            <dd>
              {orders.error
                ? '入库时间暂时无法获取'
                : (formatTimestamp(orders.data?.orders.last_import_at, { seconds: true }) ??
                  '暂无记录')}
            </dd>
          </div>
        )}
      </dl>
      {platforms.length > 0 && (
        <ul className="source-platforms" aria-label="各平台覆盖">
          {platforms.map(([platform, value]) => (
            <li key={platform} className={`tone-${freshness(value.coverage_through)}`}>
              <span className="status-dot" aria-hidden />
              <strong>{platformNames[platform] ?? platform}</strong>
              <span>至 {value.coverage_through ?? '—'}</span>
              <small>导入 {formatTimestamp(value.last_import_at) ?? '暂无记录'}</small>
            </li>
          ))}
        </ul>
      )}
      {(failed.length > 0 || !d.records || d.undated > 0) && (
        <div className="source-health-notice">
          <FailureNotice failed={failed} />
          {!d.records && <span>该账号或数据源尚无记录。没有记录不代表业务指标为零。</span>}
          {d.undated > 0 && <span>{d.undated} 条记录缺少发布日期，按日期筛选时不会计入。</span>}
        </div>
      )}
      <div className="source-detail-actions">
        <RangeButtons source={source} d={d} page={sourcePage(source)} />
        <button
          className="primary"
          onClick={() =>
            navigate({
              page: sourcePage(source),
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
          打开{sourceNames[source]}页面
          <ArrowRight size={15} aria-hidden />
        </button>
        {failed.length > 0 && admin && (
          <button
            onClick={() => navigate({ page: 'collector', account: null, start: null, end: null })}
          >
            查看采集状态 →
          </button>
        )}
      </div>
      <section className="source-detail-runs">
        <h3>最近采集</h3>
        <RunList d={d} />
      </section>
      <p className="footnote">{PROVENANCE}</p>
    </div>
  );
}
