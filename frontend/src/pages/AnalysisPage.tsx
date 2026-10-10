import { useState } from 'react';
import { z } from 'zod';
import {
  asRecord,
  asRows,
  endpoint,
  recordSchema,
  rowsSchema,
  useAction,
  useFilters,
  useResource,
  text,
} from '../lib/resources';
import { navigate } from '../lib/navigation';
import {
  ConfirmAction,
  Heading,
  QueryView,
  RangeFilter,
  RecordTable,
  Stats,
  Tabs,
} from '../components/workspace';
import { ErrorState, Panel } from '../components/ui';
import { SourceStatus } from '../components/SourceStatus';
import { AnalysisLink } from '../components/AnalysisLink';
import CommerceDashboard from '../components/CommerceDashboard';
import { SeriesChart } from '../components/charts';
const overviewSchema = z.object({
  orders: z.number(),
  revenue: z.number(),
  aov: z.number(),
  unique_customers: z.number(),
  top_sku: rowsSchema.nullable(),
  top_province: rowsSchema.nullable(),
  top_province_unique: rowsSchema.nullable(),
});
export default function AnalysisPage() {
  const { values, valid, signature } = useFilters({ commerce: true });
  const [tab, setTab] = useState('overview');
  const [windowDays, setWindowDays] = useState(30);
  const overview = useResource(
    endpoint('/analysis/overview', values),
    overviewSchema,
    valid && tab === 'segments',
  );
  const segments = useResource(
    endpoint('/analysis/', { ...values, include_rows: tab === 'segments' }),
    recordSchema,
    valid,
  );
  const repurchase = useResource(
    endpoint('/analysis/repurchase_rate', { ...values, window_days: windowDays }),
    recordSchema,
    valid,
  );
  return (
    <>
      <Heading
        title="数据分析"
        description="识别经营变化，追查渠道与商品贡献，并核对原始订单。"
        action={<AnalysisLink />}
      />
      <RangeFilter key={signature} values={values} />
      <SourceStatus source="orders" platform={values.platform} />
      {!valid && <ErrorState error={new Error('日期区间无效，请重新筛选。')} />}
      <Tabs
        value={tab}
        onChange={setTab}
        items={[
          { key: 'overview', label: '经营分析' },
          { key: 'segments', label: '新老客户' },
        ]}
      />
      {tab === 'segments' && (
        <QueryView query={overview}>
          {(data) => (
            <Stats
              items={[
                { title: '总订单数', value: data.orders },
                { title: '营业额（元）', value: data.revenue },
                { title: '客单价（元）', value: data.aov },
                { title: '独立客户', value: data.unique_customers },
              ]}
            />
          )}
        </QueryView>
      )}
      {tab === 'overview' ? (
        <>
          <CommerceDashboard
            start={values.start_date}
            end={values.end_date}
            platform={values.platform}
            onRange={(start, end) => navigate({ start, end })}
            onPlatform={(platform) => navigate({ platform: platform || null })}
          />
          <SavedViews filters={values} />
          <details className="data-disclosure bi-data-details">
            <summary>复购分析 · 观察窗口与购买频次</summary>
            <Panel
              title="复购表现"
              action={
                <label>
                  观察窗口
                  <select
                    value={windowDays}
                    onChange={(e) => setWindowDays(Number(e.target.value))}
                  >
                    {[7, 30, 60, 90, 180].map((n) => (
                      <option key={n} value={n}>
                        {n} 天
                      </option>
                    ))}
                  </select>
                </label>
              }
            >
              <QueryView query={repurchase}>
                {(data) => (
                  <>
                    <Stats
                      items={[
                        { title: '新客户', value: data.new_customers },
                        { title: '复购客户', value: data.repurchasing_customers },
                        {
                          title: '复购率',
                          value:
                            typeof data.repurchase_rate === 'number'
                              ? `${(data.repurchase_rate * 100).toFixed(1)}%`
                              : null,
                        },
                        { title: '平均复购间隔（天）', value: data.avg_days_to_repurchase },
                      ]}
                    />
                    <SeriesChart
                      rows={Object.entries(asRecord(data.frequency_distribution)).map(
                        ([bucket, customers]) => ({ bucket, customers }),
                      )}
                      x="bucket"
                      fields={['customers']}
                    />
                  </>
                )}
              </QueryView>
            </Panel>
          </details>
        </>
      ) : (
        <QueryView query={segments}>
          {(data) => (
            <>
              {['old', 'new'].map((segment) => {
                const group = asRecord(data[segment]);
                const daily = Object.entries(asRecord(data[`${segment}_daily`])).map(
                  ([date, customers]) => ({ date, customers }),
                );
                return (
                  <Panel title={segment === 'old' ? '老客户' : '新客户'} key={segment}>
                    <Stats
                      items={[
                        { title: '订单', value: group.count },
                        { title: '客户', value: group.customer_count },
                        { title: '营业额（元）', value: group.paid_sum },
                      ]}
                    />
                    <SeriesChart
                      rows={daily}
                      x="date"
                      fields={['customers', 'orders', 'revenue']}
                      kind="line"
                    />
                    {group.rows_capped === true && (
                      <p className="stale-notice">
                        明细达到接口上限，仅展示前 {text(group.rows_cap)} 条；统计指标为完整区间。
                      </p>
                    )}
                    <RecordTable
                      rows={asRows(group.rows)}
                      caption={`${segment === 'old' ? '老' : '新'}客户订单`}
                    />
                  </Panel>
                );
              })}
            </>
          )}
        </QueryView>
      )}
    </>
  );
}
function SavedViews({
  filters,
}: {
  filters: { start_date: string; end_date: string; platform: string };
}) {
  const views = useResource('/saved-queries/', rowsSchema);
  const action = useAction({ error: '筛选视图操作失败' });
  return (
    <details className="saved-views">
      <summary>保存与恢复筛选视图</summary>
      <form
        className="inline-form"
        onSubmit={(e) => {
          e.preventDefault();
          const form = new FormData(e.currentTarget);
          action.mutate({
            success: '筛选视图已保存',
            path: '/saved-queries/',
            body: {
              name: String(form.get('name')).trim(),
              filters_json: filters,
              is_shared: form.get('shared') === 'on',
            },
          });
        }}
      >
        <input name="name" placeholder="视图名称" required maxLength={100} />
        <label>
          <input name="shared" type="checkbox" />
          共享视图
        </label>
        <button disabled={action.isPending}>保存当前筛选</button>
      </form>
      {action.isError && <ErrorState error={action.error} />}
      <QueryView query={views}>
        {(rows) => (
          <ul className="saved-list">
            {rows.map((row) => (
              <li key={text(row.id)}>
                <button
                  onClick={() => {
                    const f = asRecord(row.filters_json);
                    navigate({
                      start: typeof f.start_date === 'string' ? f.start_date : null,
                      end: typeof f.end_date === 'string' ? f.end_date : null,
                      platform:
                        typeof f.platform === 'string' && f.platform !== '全部' ? f.platform : null,
                    });
                  }}
                >
                  {text(row.name)}
                </button>
                <ConfirmAction
                  title="删除视图"
                  description={`删除 ${text(row.name)}？`}
                  onConfirm={() =>
                    action.mutate({
                      path: `/saved-queries/${row.id}`,
                      method: 'DELETE',
                      success: `已删除视图「${text(row.name)}」`,
                    })
                  }
                  busy={action.isPending}
                />
              </li>
            ))}
          </ul>
        )}
      </QueryView>
    </details>
  );
}
