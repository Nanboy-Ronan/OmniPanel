import { useState } from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from 'recharts';
import { compact, contentQuadrants, palette, publicationCalendar, quadrantLabels } from '../lib/bi';
import { label } from '../lib/labels';
import { numeric, text, type Row } from '../lib/resources';
import { contentConfig, type ContentPlatform } from '../lib/content';
import { EmptyState, Panel } from './ui';
import { RecordTable } from './workspace';
export default function ContentExplorer({
  rows,
  platform,
  end,
  onDetail,
}: {
  rows: Row[];
  platform: ContentPlatform;
  end: string;
  onDetail: (row: Row) => void;
}) {
  const cfg = contentConfig[platform];
  const classified = contentQuadrants(rows, cfg.read);
  const maxReach = Math.max(2, ...classified.rows.map((r) => r.reach));
  const maxEfficiency = Math.ceil(Math.max(1, ...classified.rows.map((r) => r.efficiency)));
  const maxInteractions = Math.max(1, ...classified.rows.map((r) => r.size));
  const [quadrant, setQuadrant] = useState<number | null>(null),
    [day, setDay] = useState<string | null>(null);
  const [focused, setFocused] = useState<Row | null>(null),
    [log, setLog] = useState(maxReach / Math.max(1, classified.x) > 20);
  const scoped = classified.rows.filter((r) => quadrant === null || r.quadrant === quadrant);
  const visible = scoped.filter((r) => !day || r.date === day);
  const calendar = publicationCalendar(scoped, end);
  const maxDay = Math.max(1, ...calendar.map((d) => d.count));
  const leading = (new Date(`${calendar[0].date}T00:00:00Z`).getUTCDay() + 6) % 7;
  const fields = cfg.interactions.filter((k) => visible.some((r) => numeric(r[k]) !== null));
  const accounts = new Map<string, Row>();
  visible.forEach((row) => {
    const name = row.account_name ? text(row.account_name) : '全部内容';
    const a = accounts.get(name) || { name };
    for (const key of fields) a[key] = Number(a[key] || 0) + Number(row[key] || 0);
    accounts.set(name, a);
  });
  const totals = fields.map((key) => ({
    key,
    value: visible.reduce((s, r) => s + (numeric(r[key]) ?? 0), 0),
  }));
  const sorted = [...visible].sort((a, b) => b.reach - a.reach);
  const selectQuadrant = (index: number | null) => {
    setQuadrant(index);
    setDay(null);
    setFocused(null);
  };
  return (
    <div className="bi-content-explorer">
      <div className="bi-explorer-heading">
        <div>
          <span className="bi-overline">CONTENT INTELLIGENCE</span>
          <h2>内容表现探索</h2>
          <p>从传播规模、互动效率和发布时间，定位值得进一步分析的内容。</p>
        </div>
        <div className="bi-sample-count">
          <strong>{classified.rows.length}</strong>
          <span>篇有阅读 / 播放的内容</span>
        </div>
      </div>
      <div className="bi-selection-bar">
        <span>
          {quadrant === null ? '全部象限' : quadrantLabels[quadrant]}
          {day && ` / ${day}`} · 当前 {visible.length} 篇
        </span>
        {(quadrant !== null || day) && (
          <button
            onClick={() => {
              selectQuadrant(null);
            }}
          >
            清除图表筛选 ×
          </button>
        )}
        <small>图表筛选联动下方视图，上方总览保持原筛选口径。</small>
      </div>
      <div className="bi-explore-grid">
        <Panel
          title="传播规模 × 互动效率"
          subtitle={`虚线为全体可绘制样本中位数；气泡大小表示互动量。点击气泡查看内容。`}
          action={
            <label className="bi-checkbox">
              <input type="checkbox" checked={log} onChange={(e) => setLog(e.target.checked)} />
              对数横轴
            </label>
          }
        >
          {visible.length ? (
            <div className="bi-chart-stage" role="figure" aria-label="内容传播与互动效率象限气泡图">
              <ResponsiveContainer width="100%" height={370} minWidth={0}>
                <ScatterChart margin={{ top: 28, right: 25, bottom: 25, left: 15 }}>
                  <CartesianGrid stroke="#e6edf6" strokeDasharray="3 3" />
                  <XAxis
                    type="number"
                    dataKey="reach"
                    name={label(cfg.read)}
                    scale={log ? 'log' : 'auto'}
                    domain={[log ? 1 : 0, maxReach]}
                    ticks={
                      log
                        ? Array.from(
                            { length: Math.floor(Math.log10(maxReach)) + 1 },
                            (_, i) => 10 ** i,
                          )
                        : undefined
                    }
                    tickFormatter={compact}
                    tick={{ fontSize: 11 }}
                    label={{
                      value: label(cfg.read),
                      position: 'insideBottom',
                      offset: -15,
                      fontSize: 11,
                    }}
                  />
                  <YAxis
                    type="number"
                    dataKey="efficiency"
                    domain={[0, maxEfficiency]}
                    name="互动率"
                    unit="%"
                    tick={{ fontSize: 11 }}
                    width={52}
                  />
                  <ZAxis
                    type="number"
                    dataKey="size"
                    domain={[0, maxInteractions]}
                    range={[40, 440]}
                    name="互动量"
                  />
                  <ReferenceLine x={classified.x} stroke="#8a9bb2" strokeDasharray="5 5" />
                  <ReferenceLine y={classified.y} stroke="#8a9bb2" strokeDasharray="5 5" />
                  <Tooltip
                    cursor={{ stroke: '#cbd5e1', strokeDasharray: '3 3' }}
                    content={({ active, payload }) =>
                      active && payload?.length ? (
                        <div className="bi-tooltip">
                          <strong>{payload[0].payload.title}</strong>
                          <p>
                            {label(cfg.read)} {compact(payload[0].payload.reach)} · 互动率{' '}
                            {payload[0].payload.efficiency.toFixed(2)}%
                          </p>
                          <p>{quadrantLabels[payload[0].payload.quadrant]}</p>
                        </div>
                      ) : null
                    }
                  />
                  <Scatter
                    data={visible}
                    isAnimationActive={false}
                    onClick={(r) => setFocused(r.payload)}
                  >
                    {visible.map((r, i) => (
                      <Cell
                        key={`${r.id}-${i}`}
                        fill={palette[r.quadrant]}
                        fillOpacity={focused && focused.id !== r.id ? 0.28 : 0.72}
                        stroke={palette[r.quadrant]}
                        strokeWidth={focused?.id === r.id ? 3 : 1}
                      />
                    ))}
                  </Scatter>
                </ScatterChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <EmptyState title="当前筛选下没有可绘制内容">
              气泡图需要大于零的阅读 / 播放量，以及可计算的互动率。
            </EmptyState>
          )}
          <div className="bi-chart-footer">
            <span>
              传播中位数 {compact(classified.x)} · 互动率中位数 {classified.y.toFixed(2)}
              %。
              {log ? '横轴按倍数分布，适合阅读量差距较大的内容。' : '横轴按绝对阅读 / 播放量分布。'}
              互动率为互动次数 / 阅读或播放，不代表独立用户转化。
            </span>
          </div>
        </Panel>
        <Panel title="表现象限" subtitle="点击象限，联动气泡、日历与明细">
          <div className="bi-quadrants">
            {quadrantLabels.map((name, i) => (
              <button
                key={name}
                aria-pressed={quadrant === i}
                onClick={() => selectQuadrant(quadrant === i ? null : i)}
                style={{ '--quadrant-color': palette[i] } as React.CSSProperties}
              >
                <i />
                <span>{name}</span>
                <strong>
                  {classified.rows.filter((r) => r.quadrant === i).length}
                  <small>篇</small>
                </strong>
              </button>
            ))}
          </div>
          <div className="bi-content-focus">
            <span className="bi-overline">{focused ? '已选中内容' : '图表阅读提示'}</span>
            {focused ? (
              <>
                <h3>{text(focused.title)}</h3>
                <p>
                  {text(focused.publish_date)} · {text(focused.account_name)}
                </p>
                <div>
                  <strong>
                    {compact(Number(focused.reach))}
                    <small>{label(cfg.read)}</small>
                  </strong>
                  <strong>
                    {Number(focused.efficiency).toFixed(2)}%<small>互动率</small>
                  </strong>
                </div>
                <button onClick={() => onDetail(focused)}>查看完整数据 →</button>
              </>
            ) : (
              <>
                <h3>同样的阅读量，互动质量可能不同。</h3>
                <p>
                  右上方是传播和互动均高于中位数的内容。左上方可作为高互动、小规模内容的观察样本。
                </p>
                <p>象限只描述相对表现，不构成投放或因果结论。</p>
              </>
            )}
          </div>
        </Panel>
      </div>
      <div className="bi-secondary-grid">
        <Panel
          title="内容发布日历"
          subtitle={`截至 ${end} 的 12 周 · 按发布时间统计有阅读 / 播放的内容`}
        >
          <div className="bi-calendar-scroll">
            <div className="bi-calendar-labels">
              <span>一</span>
              <span>二</span>
              <span>三</span>
              <span>四</span>
              <span>五</span>
              <span>六</span>
              <span>日</span>
            </div>
            <div className="bi-calendar" role="group" aria-label="按发布日期筛选内容">
              {Array.from({ length: leading }, (_, i) => (
                <span key={`blank${i}`} />
              ))}
              {calendar.map((d) => (
                <button
                  key={d.date}
                  title={`${d.date} · ${d.count} 篇`}
                  aria-label={`${d.date}：${d.count} 篇`}
                  aria-pressed={day === d.date}
                  disabled={!d.count}
                  onClick={() => {
                    setDay(day === d.date ? null : d.date);
                    setFocused(null);
                  }}
                  style={{
                    background: d.count
                      ? `rgba(13,148,136,${0.2 + (d.count / maxDay) * 0.8})`
                      : '#eef2f7',
                  }}
                />
              ))}
            </div>
          </div>
          <div className="bi-calendar-key">
            <span>{calendar[0].date}</span>
            <span>
              少 <i style={{ background: '#eef2f7' }} />
              <i style={{ background: '#b4ddd8' }} />
              <i style={{ background: '#0d9488' }} /> 多
            </span>
            <span>{end}</span>
          </div>
          <p className="bi-panel-note">
            颜色越深，当天发布篇数越多；点击日期可筛选其他图表。日历外的历史文章仍可出现在气泡图中。
          </p>
        </Panel>
        <Panel title="互动构成" subtitle="按账号汇总当前图表筛选下的互动次数">
          <div className="bi-interaction-totals">
            {totals.map((r, i) => (
              <span key={r.key}>
                <i style={{ background: palette[i] }} />
                {label(r.key)}
                <strong>{compact(r.value)}</strong>
              </span>
            ))}
          </div>
          {visible.length && fields.length ? (
            <div className="bi-chart-stage" role="figure" aria-label="各账号互动构成堆叠条形图">
              <ResponsiveContainer width="100%" height={170}>
                <BarChart
                  layout="vertical"
                  data={[...accounts.values()]}
                  margin={{ left: 0, right: 20, top: 10, bottom: 10 }}
                >
                  <XAxis type="number" tickFormatter={compact} tick={{ fontSize: 11 }} />
                  <YAxis
                    type="category"
                    dataKey="name"
                    width={80}
                    tick={{ fontSize: 11 }}
                    axisLine={false}
                    tickLine={false}
                  />
                  <Tooltip formatter={(v, n) => [Number(v).toLocaleString(), String(n)]} />
                  {fields.map((key, i) => (
                    <Bar
                      key={key}
                      dataKey={key}
                      name={label(key)}
                      stackId="interactions"
                      fill={palette[i]}
                      maxBarSize={30}
                      isAnimationActive={false}
                    />
                  ))}
                </BarChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <EmptyState title="没有互动数据" />
          )}
        </Panel>
      </div>
      <Panel
        title="图中内容"
        subtitle={`当前 ${visible.length} 篇，按${label(cfg.read)}排序；点击“查看”可继续查看详细指标。`}
      >
        <RecordTable
          rows={sorted}
          columns={[
            'title',
            'publish_date',
            'account_name',
            cfg.read,
            'total_engagement',
            'engagement_rate',
          ]}
          caption="图表联动内容明细"
          onSelect={(row) => {
            setFocused(row);
            onDetail(row);
          }}
        />
      </Panel>
    </div>
  );
}
