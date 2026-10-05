import { useQuery } from '@tanstack/react-query';
import { RefreshCw } from 'lucide-react';
import { batchPollInterval, getBatches, type Batch } from '../lib/data';
import { navigate } from '../lib/navigation';
import { DataTable, EmptyState, ErrorState, Loading, Panel } from '../components/ui';

const statusLabels = {
  processing: '处理中',
  recovering: '恢复中',
  completed: '已完成',
  failed: '失败',
};
export default function TasksPage({
  embedded = false,
  onSelect,
}: {
  embedded?: boolean;
  onSelect?: (id: number) => void;
}) {
  const query = useQuery({
    queryKey: ['batches'],
    queryFn: ({ signal }) => getBatches(signal),
    refetchInterval: (q) => (q.state.error ? false : batchPollInterval(q.state.data)),
    refetchIntervalInBackground: false,
  });
  return (
    <>
      {!embedded && (
        <header className="page-heading">
          <div>
            <div className="eyebrow">OPERATIONS / IMPORTS</div>
            <h1>导入任务</h1>
            <p>查看最近 10 次订单导入的执行结果。</p>
          </div>
          <button onClick={() => navigate({ page: 'upload' })}>导入订单</button>
        </header>
      )}
      <Panel
        title="最近任务"
        subtitle="有进行中的任务时每 5 秒刷新；失败后停止自动刷新。"
        action={
          <button onClick={() => void query.refetch()} disabled={query.isFetching}>
            <RefreshCw size={15} className={query.isFetching ? 'spin' : ''} />
            刷新
          </button>
        }
      >
        {query.isError && (
          <ErrorState
            compact={!!query.data}
            error={query.error}
            retry={() => void query.refetch()}
          />
        )}
        {query.isError && query.data && (
          <p className="stale-notice">以下为上次成功获取的任务状态。</p>
        )}
        {query.isPending ? (
          <Loading />
        ) : query.data?.length === 0 ? (
          <EmptyState title="暂无导入任务">从数据上传页面提交文件后，可在这里查看结果。</EmptyState>
        ) : (
          query.data && (
            <DataTable<Batch>
              caption="最近订单导入任务"
              rows={query.data}
              rowKey={(row) => row.id}
              columns={[
                {
                  key: 'file',
                  title: '文件 / 任务',
                  render: (row) => (
                    <>
                      <strong className="filename">
                        {onSelect ? (
                          <button title={row.filename} onClick={() => onSelect(row.id)}>
                            {row.filename}
                          </button>
                        ) : (
                          row.filename
                        )}
                      </strong>
                      <small>
                        #{row.id} · {row.platform ?? '未识别'}
                      </small>
                    </>
                  ),
                },
                {
                  key: 'status',
                  title: '状态',
                  render: (row) => (
                    <>
                      <span className={`badge ${row.status}`}>{statusLabels[row.status]}</span>
                      {row.status === 'failed' && (
                        <details>
                          <summary>错误详情</summary>
                          <p className="task-error">
                            {row.error_message || '未提供错误详情，请联系管理员。'}
                          </p>
                        </details>
                      )}
                    </>
                  ),
                },
                {
                  key: 'orders',
                  title: '新增订单',
                  numeric: true,
                  render: (row) => row.inserted_orders?.toLocaleString('zh-CN') ?? '—',
                },
                {
                  key: 'duplicates',
                  title: '重复行',
                  numeric: true,
                  render: (row) => row.duplicate_rows?.toLocaleString('zh-CN') ?? '—',
                },
                {
                  key: 'invalid',
                  title: '无效行',
                  numeric: true,
                  render: (row) => row.invalid_rows?.toLocaleString('zh-CN') ?? '—',
                },
                {
                  key: 'time',
                  title: '提交时间（服务器）',
                  render: (row) => row.uploaded_at?.replace('T', ' ').slice(0, 19) ?? '—',
                },
              ]}
            />
          )
        )}
      </Panel>
    </>
  );
}
