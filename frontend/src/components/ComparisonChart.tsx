import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  LabelList,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import {
  formatMetric,
  metricDefinitions,
  periods,
  priorKeys,
  type Kpi,
  type MetricKey,
  type Period,
} from '../lib/data';

export default function ComparisonChart({ data, metric }: { data: Kpi; metric: MetricKey }) {
  const definition = metricDefinitions.find((item) => item.key === metric)!;
  const rows = (Object.keys(periods) as Period[]).map((key) => ({
    name: periods[key],
    current: data[key][metric],
    prior: data[priorKeys[key]][metric],
  }));
  return (
    <div
      className="chart"
      role="img"
      aria-label={`${definition.label}日、周、月同期对比；完整数值见下方对比明细表。`}
    >
      <ResponsiveContainer width="100%" height={280} minWidth={0}>
        <BarChart data={rows} margin={{ top: 12, right: 12, left: 12, bottom: 4 }} barGap={6}>
          <CartesianGrid vertical={false} stroke="#e5eaf0" strokeDasharray="3 3" />
          <XAxis
            dataKey="name"
            axisLine={false}
            tickLine={false}
            tick={{ fontSize: 12, fill: '#526477' }}
            dy={8}
          />
          <YAxis
            width={70}
            axisLine={false}
            tickLine={false}
            tick={{ fontSize: 11, fill: '#526477' }}
            tickFormatter={(value) =>
              new Intl.NumberFormat('zh-CN', {
                notation: 'compact',
                maximumFractionDigits: 1,
              }).format(value)
            }
          />
          <Tooltip
            cursor={{ fill: '#f4f7fa' }}
            formatter={(value) =>
              typeof value === 'number' ? `${formatMetric(metric, value)} ${definition.unit}` : '—'
            }
          />
          <Legend iconType="square" iconSize={9} wrapperStyle={{ fontSize: 12, paddingTop: 16 }} />
          <Bar
            dataKey="prior"
            name="对比期"
            fill="#9cabbc"
            maxBarSize={38}
            isAnimationActive={false}
          >
            <LabelList
              dataKey="prior"
              position="top"
              fontSize={10}
              fill="#63758a"
              formatter={(value: unknown) =>
                typeof value === 'number'
                  ? new Intl.NumberFormat('zh-CN', {
                      notation: 'compact',
                      maximumFractionDigits: 1,
                    }).format(value)
                  : ''
              }
            />
          </Bar>
          <Bar
            dataKey="current"
            name="本期"
            fill="#2263a6"
            maxBarSize={38}
            isAnimationActive={false}
          >
            <LabelList
              dataKey="current"
              position="top"
              fontSize={10}
              fill="#2263a6"
              formatter={(value: unknown) =>
                typeof value === 'number'
                  ? new Intl.NumberFormat('zh-CN', {
                      notation: 'compact',
                      maximumFractionDigits: 1,
                    }).format(value)
                  : ''
              }
            />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
