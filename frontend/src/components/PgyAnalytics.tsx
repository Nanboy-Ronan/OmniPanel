import {
  Scatter,
  ScatterChart,
  CartesianGrid,
  XAxis,
  YAxis,
  ZAxis,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from 'recharts';
import { compact, palette } from '../lib/bi';
import { groupPgy, pgySummary } from '../lib/pgy';
import { numeric, text, type Row } from '../lib/resources';
import { formatField } from '../lib/presentation';
import { Panel, EmptyState } from './ui';
export function PgyGroups({
  rows,
  field,
  onSelect,
}: {
  rows: Row[];
  field: 'blogger_nickname' | 'cooperation_name';
  onSelect: (name: string) => void;
}) {
  const groups = groupPgy(rows, field),
    max = Math.max(1, ...groups.map((r) => r.interactions ?? 0));
  return (
    <div className="pgy-group-list">
      {groups.map((r, i) => (
        <button key={r.name} disabled={r.name === '未标注'} onClick={() => onSelect(r.name)}>
          <span className="pgy-group-rank">{String(i + 1).padStart(2, '0')}</span>
          <span className="pgy-group-body">
            <strong>{r.name}</strong>
            <small>
              {r.notes} 篇 · 已知投入 ¥{formatField('price', r.spend)} · 综合 CPE{' '}
              {formatField('price', r.cpe) ?? '—'}
            </small>
            <i style={{ width: `${Math.max(1, ((r.interactions ?? 0) / max) * 100)}%` }} />
          </span>
          <span>
            <b>{r.interactions === null ? '—' : compact(r.interactions)}</b>
            <small>互动</small>
          </span>
        </button>
      ))}
    </div>
  );
}
export default function PgyAnalytics({
  rows,
  selected,
  onNote,
  onBlogger,
  onCampaign,
}: {
  rows: Row[];
  selected: Row | undefined;
  onNote: (row: Row) => void;
  onBlogger: (name: string) => void;
  onCampaign: (name: string) => void;
}) {
  const summary = pgySummary(rows);
  const points = rows.filter(
    (r) => r.cost_complete && numeric(r.interactions) !== null && Number(r.spend) >= 0,
  );
  const focused =
    selected ??
    [...rows].sort((a, b) => Number(b.interactions ?? -1) - Number(a.interactions ?? -1))[0];
  return (
    <>
      <div className="pgy-kpi-grid pgy-decision-strip">
        {[
          ['合作笔记', rows.length.toLocaleString()],
          ['已知合作投入（元）', formatField('price', summary.spend)],
          ['曝光量', summary.impressions?.toLocaleString() ?? '—'],
          ['互动量', summary.interactions?.toLocaleString() ?? '—'],
          ['综合互动成本（元）', formatField('price', summary.cpe)],
        ].map(([label, value]) => (
          <article className="metric-card" key={label}>
            <div className="metric-label">{label}</div>
            <strong>{value}</strong>
          </article>
        ))}
      </div>
      <p className="pgy-cost-note">
        综合互动成本 = 完整成本样本总投入 ÷ 总互动。
        {summary.missingCost
          ? `${summary.missingCost} 篇费用不完整，显示已知投入并排除成本效率比较。`
          : '投入包含报价与服务费。'}
        互动效率不等于销售回报。
      </p>
      <div className="bi-explore-grid">
        <Panel
          title="合作投入 × 互动表现"
          subtitle="每个气泡代表一篇合作笔记；大小表示曝光，点击查看完整数据。"
        >
          {points.length ? (
            <div className="bi-chart-stage" role="figure" aria-label="蒲公英合作投入与互动气泡图">
              <ResponsiveContainer width="100%" height={330} minWidth={0}>
                <ScatterChart margin={{ top: 20, right: 26, bottom: 30, left: 10 }}>
                  <CartesianGrid stroke="#e6edf6" strokeDasharray="3 3" />
                  <XAxis
                    type="number"
                    dataKey="spend"
                    name="合作投入"
                    tickFormatter={compact}
                    tick={{ fontSize: 11 }}
                    label={{
                      value: '报价 + 服务费（元）',
                      position: 'insideBottom',
                      offset: -15,
                      fontSize: 11,
                    }}
                  />
                  <YAxis
                    type="number"
                    dataKey="interactions"
                    name="互动量"
                    tickFormatter={compact}
                    tick={{ fontSize: 11 }}
                    width={55}
                  />
                  <ZAxis dataKey="impressions" range={[45, 450]} name="曝光量" />
                  <Tooltip
                    content={({ active, payload }) =>
                      active && payload?.length ? (
                        <div className="bi-tooltip">
                          <strong>{text(payload[0].payload.note_title)}</strong>
                          <p>{text(payload[0].payload.blogger_nickname)}</p>
                          <p>
                            投入 ¥{formatField('price', payload[0].payload.spend)} · 互动{' '}
                            {text(payload[0].payload.interactions)}
                          </p>
                        </div>
                      ) : null
                    }
                  />
                  <Scatter
                    data={points}
                    isAnimationActive={false}
                    onClick={(r) => onNote(r.payload)}
                  >
                    {points.map((r) => (
                      <Cell
                        key={text(r.id)}
                        fill={palette[0]}
                        fillOpacity={selected && selected.id !== r.id ? 0.25 : 0.75}
                      />
                    ))}
                  </Scatter>
                </ScatterChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <EmptyState title="缺少可比较的完整成本数据">
              补齐报价、服务费和互动量后可进行成本效率比较。
            </EmptyState>
          )}
        </Panel>
        <Panel
          title={selected ? '选中合作' : '互动量最高的合作'}
          subtitle="点击气泡或明细，核对单篇原始指标与成本"
        >
          {focused ? (
            <div className="pgy-note-detail">
              <h3>{text(focused.note_title)}</h3>
              <p>
                {text(focused.blogger_nickname)} · {text(focused.publish_date)}
              </p>
              <dl>
                {[
                  ['合作项目', 'cooperation_name'],
                  ['报价', 'blogger_quote'],
                  ['服务费', 'service_fee'],
                  ['总投入', 'spend'],
                  ['曝光', 'impressions'],
                  ['互动', 'interactions'],
                  ['综合互动成本', 'calculated_cpe'],
                ].map(([name, key]) => (
                  <div key={key}>
                    <dt>{name}</dt>
                    <dd>
                      {['blogger_quote', 'service_fee', 'spend', 'calculated_cpe'].includes(key)
                        ? formatField('price', focused[key])
                        : text(focused[key])}
                    </dd>
                  </div>
                ))}
              </dl>
            </div>
          ) : (
            <ol className="analysis-steps">
              <li>
                <strong>看投入与产出分布</strong>
                <p>比较相近投入下的互动差异。</p>
              </li>
              <li>
                <strong>选择达人或合作项目</strong>
                <p>点击下方排行，联动指标、气泡和明细。</p>
              </li>
              <li>
                <strong>核对单篇记录</strong>
                <p>确认费用是否完整，再判断下一轮合作。</p>
              </li>
            </ol>
          )}
        </Panel>
      </div>
      <div className="bi-secondary-grid">
        <Panel title="达人互动贡献" subtitle="按互动量排序，点击筛选全部视图">
          <PgyGroups rows={rows} field="blogger_nickname" onSelect={onBlogger} />
        </Panel>
        <Panel title="合作项目贡献" subtitle="投入包含达人报价与服务费">
          <PgyGroups rows={rows} field="cooperation_name" onSelect={onCampaign} />
        </Panel>
      </div>
    </>
  );
}
