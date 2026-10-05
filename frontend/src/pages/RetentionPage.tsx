import { useState } from 'react';
import { z } from 'zod';
import { endpoint, useFilters, useResource } from '../lib/resources';
import { Heading, QueryView, RangeFilter, RecordTable, Tabs } from '../components/workspace';
import { EmptyState, Panel } from '../components/ui';
const schema = z.object({
  latest_data_month: z.string().nullable(),
  cohorts: z.array(
    z.object({
      cohort_month: z.string(),
      cohort_size: z.number(),
      per_period: z.array(z.number().nullable()),
      cumulative: z.array(z.number().nullable()),
    }),
  ),
});
export default function RetentionPage() {
  const { values, valid, signature } = useFilters({ commerce: true });
  const [mode, setMode] = useState('per_period');
  const [months, setMonths] = useState(12);
  const query = useResource(
    endpoint('/analysis/cohort_retention', { ...values, max_offset: months }),
    schema,
    valid,
  );
  return (
    <>
      <Heading
        title="客户留存"
        description="按首购月份观察留存与累计复购；未到观察期的单元格不记为零。"
      />
      <RangeFilter key={signature} values={values} />
      <div className="inline-form">
        <Tabs
          value={mode}
          onChange={setMode}
          items={[
            { key: 'per_period', label: '逐月留存' },
            { key: 'cumulative', label: '累计复购' },
          ]}
        />
        <label>
          观察月数
          <select value={months} onChange={(e) => setMonths(Number(e.target.value))}>
            {[3, 6, 12, 18, 24].map((n) => (
              <option key={n} value={n}>
                {n} 个月
              </option>
            ))}
          </select>
        </label>
      </div>
      <QueryView query={query}>
        {(data) => (
          <Panel title="队列热力图" subtitle={`最新数据月份：${data.latest_data_month ?? '暂无'}`}>
            {!data.cohorts.length ? (
              <EmptyState title="暂无留存数据" />
            ) : (
              <>
                <div className="table-scroll">
                  <table className="heatmap">
                    <caption className="sr-only">月度留存比例</caption>
                    <thead>
                      <tr>
                        <th>首购月份</th>
                        <th>客户数</th>
                        {Array.from({ length: months + 1 }, (_, i) => (
                          <th key={i}>M{i}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {data.cohorts.map((cohort) => (
                        <tr key={cohort.cohort_month}>
                          <th scope="row">{cohort.cohort_month}</th>
                          <td>{cohort.cohort_size}</td>
                          {cohort[mode === 'cumulative' ? 'cumulative' : 'per_period'].map(
                            (value, index) => (
                              <td
                                key={index}
                                style={{
                                  background:
                                    value === null
                                      ? '#f4f6f9'
                                      : `rgba(34,99,166,${0.08 + value * 0.72})`,
                                  color: value !== null && value > 0.5 ? 'white' : '#203246',
                                }}
                                title={
                                  value === null ? '尚未到观察期' : `${(value * 100).toFixed(1)}%`
                                }
                              >
                                {value === null ? '—' : `${(value * 100).toFixed(1)}%`}
                              </td>
                            ),
                          )}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="chart-note">
                  颜色越深，比例越高；“—”表示尚未到观察期。M0 为首购当月。
                </p>
                <details className="data-disclosure">
                  <summary>查看或导出留存明细</summary>
                  <RecordTable
                    rows={data.cohorts.map((cohort) => ({
                      cohort_month: cohort.cohort_month,
                      cohort_size: cohort.cohort_size,
                      ...Object.fromEntries(
                        cohort[mode === 'cumulative' ? 'cumulative' : 'per_period'].map(
                          (value, index) => [`M${index}`, value === null ? null : value * 100],
                        ),
                      ),
                    }))}
                    caption="留存明细（百分比）"
                  />
                </details>
              </>
            )}
          </Panel>
        )}
      </QueryView>
    </>
  );
}
