import { useMemo, useState } from 'react';
import { z } from 'zod';
import { prepareReportHtml } from '../lib/report';
import { useAction, useResource } from '../lib/resources';
import { ConfirmAction, Heading, QueryView } from '../components/workspace';
import { EmptyState, ErrorState, Panel } from '../components/ui';
const summary = z.object({
  id: z.number(),
  week_start: z.string(),
  week_end: z.string(),
  status: z.string(),
  wecom_sent: z.boolean(),
});
const detail = summary.extend({
  narrative: z.string().nullable(),
  html_content: z.string().nullable(),
  error_message: z.string().nullable(),
});
export default function ReportsPage({ admin }: { admin: boolean }) {
  const list = useResource('/reports/weekly?limit=100', z.array(summary));
  const [selected, setSelected] = useState<number | null>(null);
  const action = useAction();
  const id = selected ?? list.data?.[0]?.id;
  return (
    <>
      <Heading title="周报" description="跨渠道每周数据报告、分析结论及发送状态。" />
      {admin && (
        <ConfirmAction
          title="生成本周周报"
          description="将生成或重试本周报告；如已配置企业微信通知，会发送到已配置群。"
          onConfirm={() => action.mutate({ path: '/admin/reports/weekly/run', timeoutMs: 180000 })}
          busy={action.isPending}
        />
      )}{' '}
      {action.isError && <ErrorState error={action.error} />}
      <QueryView query={list}>
        {(rows) =>
          rows.length ? (
            <label className="field-inline">
              报告周期
              <select value={id} onChange={(e) => setSelected(Number(e.target.value))}>
                {rows.map((row) => (
                  <option key={row.id} value={row.id}>
                    {row.week_start} 至 {row.week_end} · {row.status} ·{' '}
                    {row.wecom_sent ? '已通知' : '未通知'}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <EmptyState title="暂无周报" />
          )
        }
      </QueryView>
      {id !== undefined && <Report id={id} />}
    </>
  );
}
function Report({ id }: { id: number }) {
  const query = useResource(`/reports/weekly/${id}`, detail);
  const html = useMemo(
    () => (query.data?.html_content ? prepareReportHtml(query.data.html_content) : ''),
    [query.data?.html_content],
  );
  return (
    <Panel title="周报内容">
      <QueryView query={query}>
        {(data) =>
          data.status !== 'success' || !data.html_content ? (
            <ErrorState error={new Error(data.error_message ?? '报告尚未生成成功。')} />
          ) : (
            <>
              <iframe
                title="周报内容"
                className="report-frame"
                sandbox="allow-popups allow-popups-to-escape-sandbox"
                srcDoc={html}
              />
              {data.narrative && (
                <details>
                  <summary>文字报告</summary>
                  <div className="narrative">{data.narrative}</div>
                </details>
              )}
            </>
          )
        }
      </QueryView>
    </Panel>
  );
}
