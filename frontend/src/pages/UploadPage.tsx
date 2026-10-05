import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { z } from 'zod';
import { request } from '../lib/api';
import { asRows, recordSchema, useResource } from '../lib/resources';
import { batchSchema } from '../lib/data';
import { navigate } from '../lib/navigation';
import { Heading, QueryView, RecordTable, Stats } from '../components/workspace';
import { ErrorState, Panel } from '../components/ui';
import TasksPage from './TasksPage';
export default function UploadPage() {
  const [platform, setPlatform] = useState('youzan');
  const [file, setFile] = useState<File | null>(null);
  const [batch, setBatch] = useState<number | null>(null);
  const [validation, setValidation] = useState('');
  const upload = useMutation({
    mutationFn: () => {
      if (!file) throw new Error('请选择文件。');
      const body = new FormData();
      body.set('file', file);
      return request(
        `/upload/?expected_platform=${platform}`,
        z.object({ batch_id: z.number(), status: z.string() }),
        { body, timeoutMs: 120000 },
      );
    },
    onSuccess: (data) => setBatch(data.batch_id),
    retry: false,
  });
  return (
    <>
      <Heading
        title="数据上传"
        description="导入有赞、京东、天猫导出的订单文件，后台完成校验和去重。"
      />
      <Panel title="导入订单">
        <form
          className="editor-form"
          onSubmit={(e) => {
            e.preventDefault();
            if (upload.isPending) return;
            if (!file || file.size > 100 * 1024 * 1024 || !/\.(csv|xlsx|xls)$/i.test(file.name)) {
              setValidation('请选择不超过 100 MB 的 CSV、XLSX 或 XLS 文件。');
              return;
            }
            setValidation('');
            upload.mutate();
          }}
        >
          <label>
            来源平台
            <select
              value={platform}
              onChange={(e) => setPlatform(e.target.value)}
              disabled={upload.isPending}
            >
              <option value="youzan">有赞</option>
              <option value="jd">京东</option>
              <option value="tmall">天猫</option>
            </select>
          </label>
          <label>
            订单文件
            <input
              type="file"
              accept=".csv,.xlsx,.xls"
              disabled={upload.isPending}
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
          </label>
          <p className="footnote">
            文件平台必须与所选平台一致。重复记录会由后端识别；提交后不要重复上传同一文件。
          </p>
          <button className="primary" disabled={!file || upload.isPending}>
            {upload.isPending ? '正在提交…' : '提交导入'}
          </button>
          {validation && <p role="alert">{validation}</p>}
        </form>
        {upload.isError && (
          <>
            <ErrorState error={upload.error} />
            <p className="stale-notice">若提交超时，请先检查导入任务，确认是否已受理后再重试。</p>
          </>
        )}
      </Panel>
      {batch !== null && <BatchDetail id={batch} />}
      <TasksPage embedded onSelect={setBatch} />
    </>
  );
}
export function BatchDetail({ id }: { id: number }) {
  const cache = useQueryClient();
  const query = useQuery({
    queryKey: ['batch', id],
    queryFn: ({ signal }) => request(`/upload/batches/${id}`, batchSchema, { signal }),
    refetchInterval: (q) =>
      q.state.error
        ? false
        : ['processing', 'recovering'].includes(q.state.data?.status ?? '')
          ? 3000
          : false,
  });
  useEffect(() => {
    if (query.data?.status === 'completed') {
      void cache.invalidateQueries({
        predicate: (q) => !['batch'].includes(String(q.queryKey[0])),
      });
    }
  }, [query.data?.status, id, cache]);
  return (
    <Panel
      title={`导入批次 #${id}`}
      action={
        <button onClick={() => void query.refetch()} disabled={query.isFetching}>
          刷新状态
        </button>
      }
    >
      <QueryView query={query}>
        {(data) => (
          <>
            <Stats
              items={[
                {
                  title: '状态',
                  value: {
                    processing: '处理中',
                    recovering: '恢复中',
                    completed: '已完成',
                    failed: '失败',
                  }[data.status],
                },
                { title: '新增订单', value: data.inserted_orders },
                { title: '重复行', value: data.duplicate_rows },
                { title: '无效行', value: data.invalid_rows },
              ]}
            />
            {data.error_message && <p className="stale-notice">{data.error_message}</p>}
            {data.status === 'completed' && (
              <button onClick={() => navigate({ page: 'orders' })}>查看导入数据</button>
            )}
            {(data.invalid_rows ?? 0) > 0 && <RejectedRows id={id} />}
          </>
        )}
      </QueryView>
    </Panel>
  );
}
function RejectedRows({ id }: { id: number }) {
  const query = useResource(`/upload/batches/${id}/rejected`, recordSchema);
  return (
    <QueryView query={query}>
      {(data) => <RecordTable rows={asRows(data.rows)} caption="拒绝行与原因" />}
    </QueryView>
  );
}
