import { useState } from 'react';
import { endpoint, numeric, rowsSchema, useFilters, useResource } from '../lib/resources';
import { Heading, QueryView, RangeFilter, RecordTable, Stats } from '../components/workspace';
import { Panel } from '../components/ui';
import { ImpactChart, SeriesChart } from '../components/charts';
export default function ImpactPage() {
  const { values, valid, signature } = useFilters();
  const [source, setSource] = useState('wechat');
  const [windowDays, setWindow] = useState(7);
  const query = useResource(
    endpoint('/media/content-impact', { ...values, source, window_days: windowDays }),
    rowsSchema,
    valid,
  );
  return (
    <>
      <Heading
        title="内容带货分析"
        description="比较内容发布前后的订单与营业额；相关性不代表因果。"
      />
      <RangeFilter key={signature} values={values} />
      <div className="inline-form">
        <label>
          来源
          <select value={source} onChange={(e) => setSource(e.target.value)}>
            <option value="wechat">公众号</option>
            <option value="xhs">小红书</option>
            <option value="zhihu">知乎</option>
          </select>
        </label>
        <label>
          对比窗口（天）
          <input
            type="number"
            min={1}
            max={30}
            value={windowDays}
            onChange={(e) => setWindow(Math.min(30, Math.max(1, Number(e.target.value))))}
          />
        </label>
      </div>
      <QueryView query={query}>
        {(rows) => (
          <>
            <Stats
              items={[
                { title: '内容数', value: rows.length },
                {
                  title: '订单提升',
                  value: rows.filter((row) => (numeric(row.order_lift_pct) ?? 0) > 0).length,
                },
                {
                  title: '订单下降',
                  value: rows.filter(
                    (row) => numeric(row.order_lift_pct) !== null && Number(row.order_lift_pct) < 0,
                  ).length,
                },
                {
                  title: '无对比基准',
                  value: rows.filter((row) => numeric(row.order_lift_pct) === null).length,
                },
              ]}
            />
            <Panel title="流量与订单变化">
              <ImpactChart rows={rows} />
            </Panel>
            <Panel title="订单变化排行">
              <SeriesChart rows={rows} x="title" fields={['order_lift_pct', 'revenue_lift_pct']} />
              <RecordTable rows={rows} caption="内容带货明细" />
            </Panel>
          </>
        )}
      </QueryView>
    </>
  );
}
