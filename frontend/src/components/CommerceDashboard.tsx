import { useId, useState } from 'react';
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  Brush,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  Pie,
  PieChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { dashboardSchema, compact, palette, type Dashboard } from '../lib/bi';
import { endpoint, useResource } from '../lib/resources';
import { formatField } from '../lib/presentation';
import { platformNames } from '../lib/labels';
import { delta, validDate } from '../lib/data';
import { navigate, useLocationSearch } from '../lib/navigation';
import DecisionBrief from './DecisionBrief';
import { Change, EmptyState, Panel } from './ui';
import { QueryView, RecordTable } from './workspace';
type Metric = 'revenue' | 'orders' | 'aov';
const metricLabels: Record<Metric, string> = {
  revenue: '营业额（元）',
  orders: '订单数',
  aov: '客单价（元）',
};
export default function CommerceDashboard({
  start,
  end,
  platform = '',
  showMetrics = true,
  onRange,
  onPlatform,
}: {
  start: string;
  end: string;
  platform?: string;
  showMetrics?: boolean;
  onRange?: (start: string, end: string) => void;
  onPlatform?: (platform: string) => void;
}) {
  const query = useResource(
    endpoint('/analysis/dashboard', { start_date: start, end_date: end, platform }),
    dashboardSchema,
    validDate(start) && validDate(end) && start <= end,
  );
  return (
    <QueryView query={query}>
      {(data) => (
        <CommerceBoard
          key={`${start}:${end}:${platform}`}
          data={data}
          platform={platform}
          showMetrics={showMetrics}
          onRange={onRange}
          onPlatform={onPlatform}
        />
      )}
    </QueryView>
  );
}
function CommerceBoard({
  data,
  platform,
  showMetrics,
  onRange,
  onPlatform,
}: {
  data: Dashboard;
  platform: string;
  showMetrics: boolean;
  onRange?: (start: string, end: string) => void;
  onPlatform?: (platform: string) => void;
}) {
  const params = new URLSearchParams(useLocationSearch());
  const chosen = params.get('metric');
  const metric: Metric = chosen === 'orders' || chosen === 'aov' ? chosen : 'revenue';
  const setMetric = (metric: Metric) => navigate({ metric });
  const [brush, setBrush] = useState({ startIndex: 0, endIndex: data.series.length - 1 });
  const [selectedProduct, setProduct] = useState<number | null>(null);
  const gradient = useId().replace(/:/g, '');
  const definitions = [
    ['revenue', '营业额', '元'],
    ['orders', '订单数', '笔'],
    ['aov', '客单价', '元'],
    ['customers', '独立客户', '人'],
  ] as const;
  const channelRows = data.channels.map((r, i) => ({
    name: platformNames[r.platform] || r.platform,
    platform: r.platform,
    revenue: r.current.revenue,
    orders: r.current.orders,
    prior: r.prior.revenue,
    change: Math.round((r.current.revenue - r.prior.revenue) * 100) / 100,
    color: palette[(['youzan', 'jd', 'tmall'].indexOf(r.platform) + 1 || i + 1) % palette.length],
  }));
  const mixValid =
    channelRows.length > 0 && channelRows.every((r) => r.revenue >= 0) && data.current.revenue > 0;
  let running = 0;
  const products = data.products.map((p, i) => ({
    ...p,
    rank: i + 1,
    cumulative:
      ((running += p.revenue) / (data.current.revenue > 0 ? data.current.revenue : 1)) * 100,
  }));
  const paretoValid =
    data.current.revenue > 0 &&
    data.products.every((p) => p.revenue >= 0) &&
    running <= data.current.revenue + 0.01;
  const product = selectedProduct !== null ? products[selectedProduct] : products[0];
  const drill = (field: string, value: string) =>
    navigate({
      page: 'orders',
      q: null,
      offset: null,
      platform: platform || null,
      columns: JSON.stringify([
        { field: 'order_date', min: data.start_date, max: data.end_date },
        { field, value },
      ]),
    });
  return (
    <div className="bi-dashboard">
      {showMetrics && (
        <div className="bi-kpis">
          {definitions.map(([key, title, unit], i) => (
            <article
              className="bi-kpi"
              key={key}
              style={{ '--series-color': palette[i] } as React.CSSProperties}
            >
              <span>
                {title}
                <small>{unit}</small>
              </span>
              <strong>{formatField(key, data.current[key])}</strong>
              <div className="bi-kpi-bottom">
                <Change
                  value={
                    data.current[key] === null || data.prior[key] === null
                      ? null
                      : delta(data.current[key], data.prior[key])
                  }
                />
                <span>较前一等长区间</span>
              </div>
              <div className="bi-spark" aria-hidden="true">
                <ResponsiveContainer width="100%" height={38}>
                  <AreaChart data={data.series}>
                    <Area
                      dataKey={key}
                      stroke={palette[i]}
                      fill={palette[i]}
                      fillOpacity={0.08}
                      strokeWidth={1.6}
                      isAnimationActive={false}
                      connectNulls={false}
                    />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </article>
          ))}
        </div>
      )}
      <DecisionBrief data={data} onPlatform={onPlatform} />
      {platform && onPlatform && (
        <div className="analysis-context">
          <span>当前渠道：{platformNames[platform] || platform}</span>
          <button onClick={() => onPlatform('')}>清除渠道筛选 ×</button>
        </div>
      )}
      <div className="bi-main-grid">
        <Panel
          title="经营趋势"
          subtitle={`${data.start_date} — ${data.end_date} · 对照 ${data.prior_start} — ${data.prior_end}`}
          action={
            <div className="bi-segment" aria-label="趋势指标">
              {(Object.keys(metricLabels) as Metric[]).map((k) => (
                <button key={k} aria-pressed={metric === k} onClick={() => setMetric(k)}>
                  {metricLabels[k].split('（')[0]}
                </button>
              ))}
            </div>
          }
        >
          <div className="bi-chart-legend">
            <span>
              <i style={{ background: palette[0] }} />
              本期
            </span>
            <span>
              <i className="dashed" />
              前一等长区间（按第 N 天对齐）
            </span>
            <small>{metricLabels[metric]}</small>
          </div>
          <div className="bi-chart-stage" role="figure" aria-label="经营趋势：本期与前期逐日对照">
            <ResponsiveContainer width="100%" height={320} minWidth={0}>
              <ComposedChart data={data.series} margin={{ top: 12, right: 18, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id={gradient} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={palette[0]} stopOpacity={0.3} />
                    <stop offset="100%" stopColor={palette[0]} stopOpacity={0.01} />
                  </linearGradient>
                </defs>
                <CartesianGrid vertical={false} stroke="#e8edf5" strokeDasharray="3 3" />
                <XAxis
                  dataKey="date"
                  minTickGap={42}
                  tickFormatter={(v) => String(v).slice(5)}
                  tick={{ fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  tickFormatter={compact}
                  width={62}
                  tick={{ fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                />
                <Tooltip
                  content={({ active, payload }) =>
                    active && payload?.length ? (
                      <div className="bi-tooltip">
                        <strong>{payload[0].payload.date}</strong>
                        <p>本期 {formatField(metric, payload[0].payload[metric])}</p>
                        <p>
                          前期 {payload[0].payload.prior_date}：
                          {formatField(metric, payload[0].payload[`prior_${metric}`])}
                        </p>
                      </div>
                    ) : null
                  }
                />
                <Area
                  type="monotone"
                  dataKey={metric}
                  stroke={palette[0]}
                  fill={`url(#${gradient})`}
                  strokeWidth={2.5}
                  connectNulls={false}
                  isAnimationActive={false}
                />
                <Line
                  type="monotone"
                  dataKey={`prior_${metric}`}
                  stroke="#9baac1"
                  strokeDasharray="5 5"
                  dot={false}
                  strokeWidth={1.7}
                  connectNulls={false}
                  isAnimationActive={false}
                />
                <ReferenceLine y={0} stroke="#d7e0ec" />
                {data.series.length > 14 && (
                  <Brush
                    dataKey="date"
                    height={26}
                    travellerWidth={8}
                    stroke="#c8d7ef"
                    fill="#f5f8fe"
                    tickFormatter={(v) => String(v).slice(5)}
                    onChange={(r) => {
                      if (r.startIndex !== undefined && r.endIndex !== undefined)
                        setBrush({ startIndex: r.startIndex, endIndex: r.endIndex });
                    }}
                  />
                )}
              </ComposedChart>
            </ResponsiveContainer>
          </div>
          <div className="bi-chart-footer">
            <span>拖动底部滑块缩放时间；无已导入订单的日期计为 0。</span>
            {onRange && (
              <button
                disabled={brush.startIndex === 0 && brush.endIndex === data.series.length - 1}
                onClick={() =>
                  onRange(data.series[brush.startIndex].date, data.series[brush.endIndex].date)
                }
              >
                按选中区间分析
              </button>
            )}
          </div>
        </Panel>
        <Panel title="渠道构成" subtitle="营业额占比 · 点击渠道筛选整张看板">
          {mixValid ? (
            <div className="bi-donut" role="figure" aria-label="渠道营业额占比环图">
              <ResponsiveContainer width="100%" height={240}>
                <PieChart>
                  <Pie
                    data={channelRows}
                    dataKey="revenue"
                    nameKey="name"
                    innerRadius={70}
                    outerRadius={98}
                    paddingAngle={3}
                    stroke="none"
                    isAnimationActive={false}
                    onClick={(_, i) => onPlatform?.(channelRows[i].platform)}
                  >
                    {channelRows.map((r) => (
                      <Cell key={r.platform} fill={r.color} />
                    ))}
                  </Pie>
                  <Tooltip formatter={(v) => formatField('revenue', v)} />
                </PieChart>
              </ResponsiveContainer>
              <div className="bi-donut-center">
                <strong>{compact(data.current.revenue)}</strong>
                <span>营业额 / 元</span>
              </div>
            </div>
          ) : (
            <EmptyState title="当前金额不适合计算占比">
              无营业额或包含负金额时，保留下方原始数值。
            </EmptyState>
          )}
          <div className="bi-channel-list">
            {channelRows.map((r) => (
              <button
                key={r.platform}
                onClick={() => onPlatform?.(r.platform)}
                disabled={!onPlatform}
              >
                <i style={{ background: r.color }} />
                <span>
                  {r.name}
                  <small>{r.orders.toLocaleString()} 笔订单</small>
                </span>
                <strong>
                  {formatField('revenue', r.revenue)}
                  <small>
                    {mixValid ? `${((r.revenue / data.current.revenue) * 100).toFixed(1)}%` : '—'}
                  </small>
                </strong>
              </button>
            ))}
          </div>
        </Panel>
      </div>
      <div className="bi-secondary-grid">
        <Panel title="渠道增减贡献" subtitle="本期减前期营业额 · 以零为界，区分增长与下降">
          {channelRows.length ? (
            <div className="bi-chart-stage" role="figure" aria-label="各渠道营业额增减贡献图">
              <ResponsiveContainer width="100%" height={260}>
                <BarChart
                  data={channelRows}
                  layout="vertical"
                  margin={{ left: 0, right: 45, top: 20, bottom: 15 }}
                >
                  <CartesianGrid horizontal={false} stroke="#e8edf5" />
                  <XAxis type="number" tickFormatter={compact} tick={{ fontSize: 11 }} />
                  <YAxis
                    type="category"
                    dataKey="name"
                    width={70}
                    tick={{ fontSize: 12 }}
                    axisLine={false}
                    tickLine={false}
                  />
                  <Tooltip
                    formatter={(v) => formatField('revenue', v)}
                    labelFormatter={(v) => String(v)}
                  />
                  <ReferenceLine x={0} stroke="#94a3b8" />
                  <Bar
                    dataKey="change"
                    name="营业额变化（元）"
                    barSize={24}
                    radius={3}
                    isAnimationActive={false}
                  >
                    {channelRows.map((r) => (
                      <Cell key={r.platform} fill={r.change >= 0 ? '#0d9488' : '#e85d75'} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <EmptyState title="没有渠道记录" />
          )}
        </Panel>
        <Panel title="地区销售分布" subtitle="营业额前 12 个地区 · 点击查看对应订单">
          <div className="bi-region-grid">
            {data.regions.map((r, i) => (
              <button
                key={r.name}
                onClick={() => drill('province', r.name)}
                disabled={r.name === '未标注地区'}
                style={{
                  background: `rgba(37,99,235,${0.04 + (Math.abs(r.revenue) / Math.max(1, ...data.regions.map((x) => Math.abs(x.revenue)))) * 0.14})`,
                }}
              >
                <span>
                  {String(i + 1).padStart(2, '0')} / {r.name}
                </span>
                <strong>¥{compact(r.revenue)}</strong>
                <small>
                  {r.orders} 笔 · {r.customers} 位客户
                </small>
              </button>
            ))}
          </div>
          {!data.regions.length && <EmptyState title="没有地区记录" />}
        </Panel>
      </div>
      <Panel
        title="商品销售集中度"
        subtitle="按营业额选取前 10 个商品；累计占比以全部商品营业额为分母。"
      >
        <div className="bi-product-grid">
          <div className="bi-chart-stage" role="figure" aria-label="商品营业额与累计占比组合图">
            <ResponsiveContainer width="100%" height={285}>
              <ComposedChart data={products} margin={{ top: 20, left: 0, right: 15, bottom: 15 }}>
                <CartesianGrid vertical={false} stroke="#e8edf5" />
                <XAxis dataKey="rank" tickFormatter={(v) => `#${v}`} tick={{ fontSize: 11 }} />
                <YAxis
                  yAxisId="amount"
                  tickFormatter={compact}
                  width={62}
                  tick={{ fontSize: 11 }}
                />
                {paretoValid && (
                  <YAxis
                    yAxisId="percent"
                    orientation="right"
                    domain={[0, 100]}
                    tickFormatter={(v) => `${v}%`}
                    width={45}
                    tick={{ fontSize: 11 }}
                  />
                )}
                <Tooltip
                  content={({ active, payload }) =>
                    active && payload?.length ? (
                      <div className="bi-tooltip">
                        <strong>{payload[0].payload.name}</strong>
                        <p>营业额 ¥{formatField('revenue', payload[0].payload.revenue)}</p>
                        {paretoValid && <p>累计占比 {payload[0].payload.cumulative.toFixed(1)}%</p>}
                      </div>
                    ) : null
                  }
                />
                <Bar
                  yAxisId="amount"
                  dataKey="revenue"
                  name="营业额"
                  maxBarSize={40}
                  radius={[4, 4, 0, 0]}
                  isAnimationActive={false}
                  onClick={(_, i) => setProduct(i)}
                >
                  {products.map((p, i) => (
                    <Cell key={p.rank} fill={selectedProduct === i ? '#0d9488' : '#76a0f3'} />
                  ))}
                </Bar>
                {paretoValid && (
                  <Line
                    yAxisId="percent"
                    dataKey="cumulative"
                    name="累计占比"
                    stroke="#f59e0b"
                    strokeWidth={2}
                    dot={{ r: 3 }}
                    isAnimationActive={false}
                  />
                )}
              </ComposedChart>
            </ResponsiveContainer>
          </div>
          <div className="bi-product-detail">
            <span className="bi-overline">选中商品 · #{product?.rank ?? '—'}</span>
            <h3>{product?.name ?? '暂无商品数据'}</h3>
            {product && (
              <>
                <strong>¥{formatField('revenue', product.revenue)}</strong>
                <p>
                  {product.orders} 笔订单 · {product.customers} 位客户
                </p>
                <button
                  onClick={() => drill('sku', product.name)}
                  disabled={product.name === '未标注商品'}
                >
                  查看该商品订单 →
                </button>
              </>
            )}
            <label>
              选择商品
              <select
                aria-label="选择分析商品"
                value={selectedProduct ?? 0}
                onChange={(e) => setProduct(Number(e.target.value))}
              >
                {products.map((p, i) => (
                  <option value={i} key={p.rank}>
                    #{p.rank} {p.name}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </div>
        <div className="bi-chart-footer">
          <span>
            蓝柱：营业额（左轴） · 橙线：累计占比（右轴）。金额无法形成有效占比时隐藏累计线。
          </span>
        </div>
      </Panel>
      <details className="data-disclosure bi-data-details">
        <summary>查看与导出每日数据 · 含完整对照日期</summary>
        <RecordTable
          rows={data.series}
          defaultColumns={['date', 'revenue', 'orders', 'aov', 'prior_date', 'prior_revenue']}
          caption="经营趋势完整数据"
        />
      </details>
      <p className="bi-method">
        区间按相同天数比较，不代表去年同期。金额非空记录参与客单价计算；本期{' '}
        {data.current.missing_amount}{' '}
        条订单缺少金额。客户按系统客户标识去重，不能跨天相加。所有图表统计完整筛选范围。
      </p>
    </div>
  );
}
