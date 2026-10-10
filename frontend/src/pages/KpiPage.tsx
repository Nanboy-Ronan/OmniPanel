import { lazy, Suspense, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowRight, Info } from 'lucide-react';
import { request } from '../lib/api';
import {
  comparisonWindow,
  delta,
  formatMetric,
  freshnessSchema,
  kpiSchema,
  latestSchema,
  metricDefinitions,
  periods,
  priorKeys,
  validDate,
  type MetricKey,
  type Period,
} from '../lib/data';
import { navigate, useLocationSearch } from '../lib/navigation';
import { Change, DataTable, EmptyState, ErrorState, Loading, Panel } from '../components/ui';
import DataReadiness from '../components/DataReadiness';
import { AnalysisLink } from '../components/AnalysisLink';
import { FilterBar } from '../components/FilterBar';
import { formatTimestamp } from '../lib/time';
const CommerceDashboard = lazy(() => import('../components/CommerceDashboard'));
const ComparisonChart = lazy(() => import('../components/ComparisonChart'));

export default function KpiPage({ admin = false }: { admin?: boolean }) {
  const cache = useQueryClient();
  const search = useLocationSearch();
  const params = new URLSearchParams(search);
  const selectedAnchor = params.get('anchor') ?? '';
  const selectedPeriod = params.get('period');
  const period: Period =
    selectedPeriod === 'week' || selectedPeriod === 'month' ? selectedPeriod : 'day';
  const [metric, setMetric] = useState<MetricKey>('revenue');
  const latest = useQuery({
    queryKey: ['latest'],
    queryFn: ({ signal }) => request('/analysis/latest_order_date', latestSchema, { signal }),
  });
  const latestDate = latest.data?.latest_order_date;
  const anchor = selectedAnchor || latestDate || '';
  const invalidAnchor =
    !!selectedAnchor &&
    (!validDate(selectedAnchor) || (!!latestDate && selectedAnchor > latestDate));
  const kpi = useQuery({
    queryKey: ['kpi', anchor],
    queryFn: ({ signal }) =>
      request(`/analysis/kpi-periods?anchor=${encodeURIComponent(anchor)}`, kpiSchema, { signal }),
    enabled: !!latestDate && !invalidAnchor,
  });
  const freshness = useQuery({
    queryKey: ['freshness'],
    queryFn: ({ signal }) => request('/data/freshness', freshnessSchema, { signal }),
  });
  const refresh = () => {
    void latest.refetch();
    void freshness.refetch();
    void cache.invalidateQueries({
      predicate: (q) =>
        ['/analysis/dashboard?', '/data/source-status?'].some((prefix) =>
          String(q.queryKey[1]).startsWith(prefix),
        ),
    });
    if (latestDate && !invalidAnchor) void kpi.refetch();
  };

  const readiness = (
    <DataReadiness
      latestDate={latestDate}
      fetchedAt={invalidAnchor ? undefined : kpi.dataUpdatedAt}
      refreshing={!invalidAnchor && kpi.isFetching && !!kpi.data}
      orders={{
        data: freshness.data,
        error: freshness.isError,
        retry: () => void freshness.refetch(),
      }}
      admin={admin}
    />
  );
  return (
    <>
      <header className="page-heading">
        <div>
          <h1>经营概览</h1>
          <p>先核对数据覆盖，再识别经营变化与需要追查的渠道。</p>
        </div>
        <div className="heading-actions">
          <AnalysisLink />
          <a className="button" href={'?page=upload'}>
            导入订单
            <ArrowRight size={16} />
          </a>
        </div>
      </header>
      {latest.isPending ? (
        <Loading />
      ) : latest.isError && !latest.data ? (
        <>
          {readiness}
          <ErrorState error={latest.error} retry={() => void latest.refetch()} />
        </>
      ) : !latestDate ? (
        <>
          {readiness}
          <EmptyState title="还没有可分析的订单">
            请先导入订单，完成后刷新此页面。<a href={'?page=upload'}>前往数据上传</a>
          </EmptyState>
        </>
      ) : (
        <>
          <FilterBar
            key={anchor}
            anchor={invalidAnchor ? latestDate : anchor}
            latest={latestDate}
            period={period}
            busy={latest.isFetching || kpi.isFetching}
            onDate={(date) => navigate({ anchor: date || null })}
            onPeriod={(value) => navigate({ period: value })}
            onRefresh={refresh}
          />
          {readiness}
          {invalidAnchor ? (
            <ErrorState
              error={new Error('链接中的统计日期无效或晚于最新数据日。')}
              retry={() => navigate({ anchor: null })}
            />
          ) : (
            <>
              {latest.isError && (
                <ErrorState
                  compact
                  error={new Error('数据覆盖时间刷新失败，当前使用上次已获取的日期。')}
                  retry={() => void latest.refetch()}
                />
              )}
              {kpi.isError && (
                <ErrorState
                  compact={!!kpi.data}
                  error={kpi.error}
                  retry={() => void kpi.refetch()}
                />
              )}
              {kpi.isError && kpi.data && (
                <p className="stale-notice" role="status">
                  刷新失败，以下保留上次成功获取的数据，请勿视为最新结果。
                </p>
              )}
              {kpi.isPending ? (
                <Loading label="正在汇总经营指标" />
              ) : (
                kpi.data && (
                  <>
                    <div className="section-caption">
                      <h2>{periods[period]}</h2>
                      <WindowLabel anchor={anchor} period={period} />
                    </div>
                    <div className="metric-grid">
                      {metricDefinitions.map((def) => (
                        <article className="metric-card" key={def.key}>
                          <div className="metric-label">
                            {def.label}
                            <span title={def.description} aria-label={def.description}>
                              <Info size={14} />
                            </span>
                          </div>
                          <div className="metric-value">
                            {def.unit === '元' && <span className="currency">¥</span>}
                            {formatMetric(def.key, kpi.data[period][def.key])}
                            <span className="metric-unit">{def.unit === '元' ? '' : def.unit}</span>
                          </div>
                          <div className="metric-foot">
                            <Change
                              value={delta(
                                kpi.data[period][def.key],
                                kpi.data[priorKeys[period]][def.key],
                              )}
                            />
                            <span>较对比期</span>
                          </div>
                        </article>
                      ))}
                    </div>
                    <div className="bi-overview-context">
                      <h2>近 90 天经营全景</h2>
                      <p>
                        趋势与渠道结构使用独立的 90 天观察窗口；上方指标仍按所选日、周、月统计。
                      </p>
                    </div>
                    <Suspense fallback={<Loading label="正在加载经营全景" />}>
                      <CommerceDashboard
                        start={new Date(new Date(`${anchor}T00:00:00Z`).getTime() - 89 * 86400000)
                          .toISOString()
                          .slice(0, 10)}
                        end={anchor}
                        showMetrics={false}
                        onRange={(start, end) => navigate({ page: 'analysis', start, end })}
                        onPlatform={(platform) =>
                          navigate({
                            page: 'analysis',
                            start: new Date(
                              new Date(`${anchor}T00:00:00Z`).getTime() - 89 * 86400000,
                            )
                              .toISOString()
                              .slice(0, 10),
                            end: anchor,
                            platform: platform || null,
                          })
                        }
                      />
                    </Suspense>
                    <details className="data-disclosure bi-data-details">
                      <summary>日 / 周 / 月同期对照与统计口径</summary>
                      <div className="analysis-grid kpi-analysis-grid">
                        <Panel
                          title="同期表现"
                          subtitle="各周期分别比较本期与对比期；柱形从零开始。"
                          action={
                            <label className="chart-select">
                              <span className="sr-only">图表指标</span>
                              <select
                                value={metric}
                                onChange={(e) => setMetric(e.target.value as MetricKey)}
                              >
                                {metricDefinitions.map((def) => (
                                  <option value={def.key} key={def.key}>
                                    {def.label}（{def.unit}）
                                  </option>
                                ))}
                              </select>
                            </label>
                          }
                        >
                          <Suspense fallback={<Loading label="正在加载图表" />}>
                            <ComparisonChart data={kpi.data} metric={metric} />
                          </Suspense>
                        </Panel>
                        <Panel title="数据口径" subtitle="读数之前，先确认统计边界。">
                          <dl className="definitions">
                            <div>
                              <dt>统计截止日</dt>
                              <dd>{anchor}</dd>
                            </div>
                            <div>
                              <dt>最近成功入库</dt>
                              <dd>
                                {formatTimestamp(freshness.data?.orders.last_import_at, {
                                  seconds: true,
                                }) ?? '暂无记录'}
                                <small>北京时间</small>
                              </dd>
                            </div>
                            <div>
                              <dt>对比规则</dt>
                              <dd>周按相同星期区间比较；月按相同日数，最多截至上月末。</dd>
                            </div>
                            <div>
                              <dt>金额与客户</dt>
                              <dd>金额非空记录参与金额统计；系统客户标识可能不等于真实人数。</dd>
                            </div>
                          </dl>
                          {freshness.isError && (
                            <ErrorState
                              compact
                              error={new Error('入库时间暂时无法获取。')}
                              retry={() => void freshness.refetch()}
                            />
                          )}
                        </Panel>
                      </div>
                      <Panel title="对比明细" subtitle="日、周、月完整数值，支持横向滚动查看。">
                        <DataTable
                          caption="经营指标同期对比"
                          rows={(Object.keys(periods) as Period[]).flatMap((p) =>
                            metricDefinitions.map((def) => ({ period: p, def })),
                          )}
                          rowKey={(row) => `${row.period}-${row.def.key}`}
                          columns={[
                            {
                              key: 'period',
                              title: '周期 / 日期',
                              render: (row) => (
                                <>
                                  <strong>{periods[row.period]}</strong>
                                  <WindowLabel anchor={anchor} period={row.period} />
                                </>
                              ),
                            },
                            {
                              key: 'metric',
                              title: '指标',
                              render: (row) => `${row.def.label}（${row.def.unit}）`,
                            },
                            {
                              key: 'current',
                              title: '本期',
                              numeric: true,
                              render: (row) =>
                                formatMetric(row.def.key, kpi.data[row.period][row.def.key]),
                            },
                            {
                              key: 'prior',
                              title: '对比期',
                              numeric: true,
                              render: (row) =>
                                formatMetric(
                                  row.def.key,
                                  kpi.data[priorKeys[row.period]][row.def.key],
                                ),
                            },
                            {
                              key: 'delta',
                              title: '变化率',
                              numeric: true,
                              render: (row) => (
                                <Change
                                  value={delta(
                                    kpi.data[row.period][row.def.key],
                                    kpi.data[priorKeys[row.period]][row.def.key],
                                  )}
                                />
                              ),
                            },
                          ]}
                        />
                      </Panel>
                    </details>
                    <p className="footnote">
                      订单数与营业额不含已关闭、已取消、未付款和京东已删除订单，有赞订单已扣除退款；京东、天猫导出没有退款金额，部分退款无法扣除。客单价为金额非空订单的平均值。对比期为零时不计算变化率。
                    </p>
                  </>
                )
              )}
            </>
          )}
        </>
      )}
    </>
  );
}
function WindowLabel({ anchor, period }: { anchor: string; period: Period }) {
  const range = comparisonWindow(anchor, period);
  return (
    <span className="window-label">
      {range.start} 至 {range.end}
      <span>
        对比 {range.priorStart} 至 {range.priorEnd}
      </span>
    </span>
  );
}
