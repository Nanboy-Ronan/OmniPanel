import { useState } from 'react';
import {
  Bar,
  BarChart,
  Line,
  LineChart,
  ScatterChart,
  Scatter,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from 'recharts';
import { display, numeric, text, type Row } from '../lib/resources';
import { label } from '../lib/labels';
import { EmptyState } from './ui';
import { formatField, rankedRows } from '../lib/presentation';
export function SeriesChart({
  rows,
  x,
  fields,
  kind = 'bar',
  limit = 10,
}: {
  rows: Row[];
  x: string;
  fields: string[];
  kind?: 'bar' | 'line';
  limit?: number;
}) {
  const available = [...new Set(fields)].filter((key) =>
    rows.some((row) => numeric(row[key]) !== null),
  );
  const [selected, setSelected] = useState(fields[0]);
  const metric = available.includes(selected) ? selected : available[0];
  if (!rows.length || !metric) return <EmptyState title="暂无可绘制的数据" />;
  const Chart = kind === 'line' ? LineChart : BarChart;
  const ranked =
    kind === 'bar' &&
    [
      'title',
      'note_title',
      'sku',
      'province',
      'topic',
      'blogger_nickname',
      'cooperation_name',
      'scene',
      'genre',
    ].includes(x);
  const rankedData = rankedRows(rows, metric, limit);
  const max = Math.max(1, ...rankedData.map((row) => Math.abs(Number(row[metric]))));
  return (
    <div className="chart-wrap">
      <label className="chart-field">
        图表指标
        <select value={metric} onChange={(e) => setSelected(e.target.value)}>
          {available.map((key) => (
            <option key={key} value={key}>
              {label(key)}
            </option>
          ))}
        </select>
      </label>
      {ranked ? (
        <div className="rank-chart" role="figure" aria-label={`${label(metric)}排行`}>
          <div className="rank-caption">
            <span>按{label(metric)}从高到低</span>
            <span>
              显示 {rankedData.length} /{' '}
              {rows.filter((row) => numeric(row[metric]) !== null).length} 项
            </span>
          </div>
          <ol>
            {rankedData.map((row, index) => (
              <li key={index}>
                <span className="rank-position">{String(index + 1).padStart(2, '0')}</span>
                <div className="rank-content">
                  <div className="rank-line">
                    <span className="rank-title" title={text(row[x])}>
                      {text(row[x])}
                    </span>
                    <strong>{formatField(metric, row[metric])}</strong>
                  </div>
                  <div className="rank-track">
                    <span
                      className={Number(row[metric]) < 0 ? 'negative-bar' : ''}
                      style={{ width: `${(Math.abs(Number(row[metric])) / max) * 100}%` }}
                    />
                  </div>
                </div>
              </li>
            ))}
          </ol>
          <p className="chart-note">
            条形长度表示绝对值，负值以红色及负号标识。完整名称可悬停查看。
          </p>
        </div>
      ) : (
        <div role="img" aria-label={`${label(metric)}图表，数值见明细表`}>
          <ResponsiveContainer width="100%" height={300} minWidth={0}>
            <Chart data={rows} margin={{ top: 15, right: 25, bottom: 10, left: 10 }}>
              <CartesianGrid vertical={false} stroke="#e5eaf0" />
              <XAxis
                dataKey={x}
                tick={{ fontSize: 11 }}
                tickFormatter={(value) => String(value).slice(0, 14)}
              />
              <YAxis
                width={65}
                tick={{ fontSize: 11 }}
                tickFormatter={(value) =>
                  new Intl.NumberFormat('zh-CN', { notation: 'compact' }).format(value)
                }
              />
              <Tooltip formatter={(value) => formatField(metric, value)} />
              {kind === 'line' ? (
                <Line
                  dataKey={metric}
                  name={label(metric)}
                  stroke="#2263a6"
                  strokeWidth={2}
                  dot={rows.length <= 45 ? { r: 3, fill: '#2263a6', strokeWidth: 0 } : false}
                  connectNulls={false}
                  isAnimationActive={false}
                />
              ) : (
                <Bar
                  dataKey={metric}
                  name={label(metric)}
                  fill="#2263a6"
                  maxBarSize={42}
                  isAnimationActive={false}
                />
              )}
            </Chart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
export function ImpactChart({ rows }: { rows: Row[] }) {
  const data = rows.filter(
    (row) => numeric(row.read_user_count) !== null && numeric(row.order_lift_pct) !== null,
  );
  if (!data.length) return <EmptyState title="没有可计算变化率的内容" />;
  const mean = (key: string) =>
    data.reduce((sum, row) => sum + (numeric(row[key]) ?? 0), 0) / data.length;
  return (
    <div className="chart-wrap" role="img" aria-label="阅读人数与订单变化率散点图">
      <ResponsiveContainer width="100%" height={360}>
        <ScatterChart margin={{ left: 30, right: 25, top: 20, bottom: 25 }}>
          <CartesianGrid stroke="#e5eaf0" />
          <XAxis type="number" dataKey="read_user_count" name="阅读人数" tick={{ fontSize: 11 }} />
          <YAxis
            type="number"
            dataKey="order_lift_pct"
            name="订单变化率"
            unit="%"
            tick={{ fontSize: 11 }}
          />
          <Tooltip
            cursor={{ strokeDasharray: '3 3' }}
            content={({ active, payload }) =>
              active && payload?.length ? (
                <div className="chart-tooltip">
                  <strong>{text(payload[0].payload.title)}</strong>
                  <p>阅读 {display(payload[0].payload.read_user_count)}</p>
                  <p>订单变化 {display(payload[0].payload.order_lift_pct)}%</p>
                </div>
              ) : null
            }
          />
          <ReferenceLine x={mean('read_user_count')} stroke="#92a3b6" strokeDasharray="4 4" />
          <ReferenceLine y={mean('order_lift_pct')} stroke="#92a3b6" strokeDasharray="4 4" />
          <Scatter data={data} fill="#2263a6" isAnimationActive={false} />
        </ScatterChart>
      </ResponsiveContainer>
      <p className="footnote">虚线表示样本均值。发布前后变化是相关性，不代表内容造成了订单增长。</p>
    </div>
  );
}

export function MetricScatter({ rows, fields }: { rows: Row[]; fields: string[] }) {
  const available = [...new Set(fields)].filter((key) =>
    rows.some((row) => numeric(row[key]) !== null),
  );
  const [x, setX] = useState(fields[0]);
  const [y, setY] = useState(fields[1] ?? fields[0]);
  const xKey = available.includes(x) ? x : available[0],
    yKey = available.includes(y) ? y : (available[1] ?? available[0]);
  const data = rows.filter((row) => numeric(row[xKey]) !== null && numeric(row[yKey]) !== null);
  if (!data.length) return <EmptyState title="暂无可绘制的数据" />;
  const mean = (key: string) => data.reduce((sum, row) => sum + Number(row[key]), 0) / data.length;
  return (
    <div className="chart-wrap">
      <div className="inline-form">
        <label>
          横轴
          <select value={xKey} onChange={(e) => setX(e.target.value)}>
            {available.map((key) => (
              <option key={key} value={key}>
                {label(key)}
              </option>
            ))}
          </select>
        </label>
        <label>
          纵轴
          <select value={yKey} onChange={(e) => setY(e.target.value)}>
            {available.map((key) => (
              <option key={key} value={key}>
                {label(key)}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div role="img" aria-label={`${label(xKey)}与${label(yKey)}散点图`}>
        <ResponsiveContainer width="100%" height={340}>
          <ScatterChart margin={{ left: 20, right: 25, top: 20, bottom: 20 }}>
            <CartesianGrid stroke="#e5eaf0" />
            <XAxis type="number" dataKey={xKey} name={label(xKey)} tick={{ fontSize: 11 }} />
            <YAxis type="number" dataKey={yKey} name={label(yKey)} tick={{ fontSize: 11 }} />
            <Tooltip
              content={({ active, payload }) =>
                active && payload?.length ? (
                  <div className="chart-tooltip">
                    <strong>
                      {text(payload[0].payload.title ?? payload[0].payload.note_title)}
                    </strong>
                    <p>
                      {label(xKey)}：{display(payload[0].payload[xKey])}
                    </p>
                    <p>
                      {label(yKey)}：{display(payload[0].payload[yKey])}
                    </p>
                  </div>
                ) : null
              }
            />
            <ReferenceLine x={mean(xKey)} stroke="#92a3b6" strokeDasharray="4 4" />
            <ReferenceLine y={mean(yKey)} stroke="#92a3b6" strokeDasharray="4 4" />
            <Scatter data={data} fill="#2263a6" isAnimationActive={false} />
          </ScatterChart>
        </ResponsiveContainer>
      </div>
      <p className="footnote">虚线为当前样本均值，指标相关性不代表因果关系。</p>
    </div>
  );
}
