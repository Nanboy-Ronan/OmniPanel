import { useState } from 'react';
import { z } from 'zod';
import { useAction, useResource } from '../lib/resources';
import { navigate } from '../lib/navigation';
import { ConfirmAction, Heading, QueryView, Refresh, Stats, Tabs } from '../components/workspace';
import { DataTable, ErrorState, Panel } from '../components/ui';
const schema = z.object({
  checked_at: z.string(),
  health: z.enum(['healthy', 'attention']),
  analysis_ready: z.boolean(),
  counts: z.record(z.string(), z.number()),
  table_details: z.array(
    z.object({
      name: z.string(),
      present: z.boolean(),
      rows: z.number().nullable(),
      missing_columns: z.array(z.string()),
    }),
  ),
  backups: z.object({
    status: z.enum(['found', 'none', 'unavailable']),
    files: z.array(z.object({ name: z.string(), bytes: z.number(), modified_at: z.string() })),
  }),
});
const catalogs: Record<string, [string, string]> = {
  orders: ['统一订单', 'commerce'],
  customers: ['客户', 'commerce'],
  upload_batches: ['导入批次', 'commerce'],
  upload_rejected_rows: ['导入拒绝记录', 'commerce'],
  youzan_orders: ['有赞原始订单', 'commerce'],
  jd_orders: ['京东原始订单', 'commerce'],
  tmall_orders: ['天猫原始订单', 'commerce'],
  media_accounts: ['公众号账号', 'content'],
  media_posts: ['公众号文章', 'content'],
  media_post_metrics_daily: ['公众号指标快照', 'content'],
  media_article_traffic: ['公众号后台导出', 'content'],
  media_sync_runs: ['公众号同步记录', 'content'],
  xhs_accounts: ['小红书账号', 'content'],
  xhs_posts: ['小红书内容', 'content'],
  xhs_account_daily_metrics: ['小红书账号指标', 'content'],
  xhs_audience_source_daily: ['小红书受众来源', 'content'],
  zhihu_posts: ['知乎内容', 'content'],
  wx_channels_accounts: ['视频号账号', 'content'],
  wx_channels_posts: ['视频号内容', 'content'],
  pgy_notes: ['蒲公英合作', 'content'],
  user: ['用户账号', 'system'],
  operation_log: ['操作审计', 'system'],
  collector_runs: ['自动采集记录', 'system'],
  saved_query: ['保存的查询', 'system'],
  weekly_report_runs: ['周报归档', 'system'],
};
const timestamp = (value: string) => new Date(value).toLocaleString('zh-CN', { hour12: false });
export default function DatabasePage() {
  const query = useResource('/admin/db-status', schema);
  const action = useAction();
  const [group, setGroup] = useState('all');
  return (
    <>
      <Heading
        title="数据库状态"
        description="核对实际数据规模、表结构和备份文件，定位需要处理的问题。"
        action={<Refresh busy={query.isFetching} onClick={() => void query.refetch()} />}
      />
      <QueryView query={query}>
        {(data) => {
          const issues = data.table_details.filter((t) => !t.present || t.missing_columns.length);
          const tables = data.table_details.filter(
            (t) => group === 'all' || (catalogs[t.name]?.[1] || 'system') === group,
          );
          return (
            <>
              <section
                className={`db-health ${data.health === 'attention' ? 'attention' : ''}`}
                aria-label="数据库检查结果"
              >
                <div>
                  <span className="db-health-dot" />
                  <strong>
                    {data.health === 'healthy'
                      ? '数据库可访问 · 结构检查通过'
                      : '数据库可访问 · 发现结构缺失'}
                  </strong>
                  <p>
                    {issues.length
                      ? `${issues.length} 张表需要检查，请查看下方结构详情。`
                      : `已核对 ${data.table_details.length} 张业务表及其字段。`}
                  </p>
                </div>
                <small>
                  最近检查 {timestamp(data.checked_at)}
                  <br />
                  {query.isFetching ? '正在刷新…' : '记录数为本次查询结果'}
                </small>
              </section>
              <Stats
                items={[
                  { title: '商城订单', value: data.counts.orders ?? null },
                  { title: '客户记录', value: data.counts.customers ?? null },
                  { title: '公众号文章', value: data.counts.media_posts ?? null },
                  { title: '结构异常表', value: issues.length },
                ]}
              />
              <div className="db-operations-grid">
                <Panel
                  title="日常管理"
                  subtitle={
                    data.analysis_ready
                      ? '商城数据已具备分析条件'
                      : '商城暂无可分析订单或结构尚未完整'
                  }
                >
                  <div className="db-shortcuts">
                    <button onClick={() => navigate({ page: 'upload' })}>
                      <strong>导入与更新数据</strong>
                      <span>上传文件、核对导入批次</span>
                    </button>
                    <button onClick={() => navigate({ page: 'collector' })}>
                      <strong>自动采集</strong>
                      <span>检查平台登录态与采集结果</span>
                    </button>
                    <button onClick={() => navigate({ page: 'logs' })}>
                      <strong>查看操作审计</strong>
                      <span>追踪导入、权限和维护变更</span>
                    </button>
                  </div>
                </Panel>
                <Panel title="备份文件" subtitle="当前配置目录中最近的备份文件">
                  <div className="db-backups">
                    {data.backups.status === 'unavailable' ? (
                      <p role="status">无法读取备份目录，请检查服务权限与备份任务。</p>
                    ) : data.backups.files.length ? (
                      <ul>
                        {data.backups.files.map((file) => (
                          <li key={file.name}>
                            <strong>{file.name}</strong>
                            <span>
                              {timestamp(file.modified_at)} ·{' '}
                              {(file.bytes / 1024 / 1024).toFixed(2)} MB
                              {file.bytes === 0 ? ' · 空文件，需检查' : ''}
                            </span>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p>当前目录未发现备份文件，请核对备份任务。</p>
                    )}
                    <small>文件存在不代表已验证可恢复；此处仅核对文件时间与大小。</small>
                  </div>
                </Panel>
              </div>
              <Panel
                title="数据表清单"
                subtitle="记录数为实际计数；空表、缺表和缺字段分别显示。原始订单与统一订单不可相加。"
              >
                <Tabs
                  value={group}
                  onChange={setGroup}
                  items={[
                    { key: 'all', label: '全部数据表' },
                    { key: 'commerce', label: '商城数据' },
                    { key: 'content', label: '内容数据' },
                    { key: 'system', label: '系统与审计' },
                  ]}
                />
                <DataTable
                  caption="数据库表检查"
                  rows={tables}
                  rowKey={(r) => r.name}
                  columns={[
                    {
                      key: 'name',
                      title: '数据表',
                      render: (r) => (
                        <div className="db-table-name">
                          <strong>{catalogs[r.name]?.[0] || r.name}</strong>
                          <small>{r.name}</small>
                        </div>
                      ),
                    },
                    {
                      key: 'rows',
                      title: '记录数',
                      numeric: true,
                      render: (r) => (r.rows === null ? '—' : r.rows.toLocaleString()),
                    },
                    {
                      key: 'state',
                      title: '状态',
                      render: (r) => (
                        <span
                          className={`db-table-state ${!r.present || r.missing_columns.length ? 'issue' : ''}`}
                        >
                          {!r.present
                            ? '表缺失'
                            : r.missing_columns.length
                              ? '字段缺失'
                              : r.rows === 0
                                ? '结构正常 · 暂无记录'
                                : '结构正常'}
                        </span>
                      ),
                    },
                    {
                      key: 'details',
                      title: '检查说明',
                      render: (r) =>
                        !r.present
                          ? '需检查数据库迁移'
                          : r.missing_columns.length
                            ? `缺少字段：${r.missing_columns.join('、')}`
                            : '已匹配当前应用所需字段',
                    },
                  ]}
                />
              </Panel>
            </>
          );
        }}
      </QueryView>
      <details className="db-danger">
        <summary>高风险维护</summary>
        <Panel title="清空商城数据" subtitle="仅适用于准备重新导入全部商城数据的维护场景。">
          <p>
            执行前会创建备份，然后删除统一订单、平台原始订单、客户、导入批次、拒绝记录及操作日志。保留用户账号和自媒体数据。
          </p>
          <ConfirmAction
            title="清空商城数据"
            danger
            description="这将删除所有商城业务记录。确认已安排重新导入，并知晓上述删除范围后，再输入确认。"
            busy={action.isPending}
            onConfirm={() => action.mutate({ path: '/admin/clear-db', timeoutMs: 180000 })}
          />
          {action.isError && <ErrorState error={action.error} />}
          {action.isSuccess && <p role="status">商城数据已清空，请核对备份记录并重新导入。</p>}
        </Panel>
      </details>
    </>
  );
}
