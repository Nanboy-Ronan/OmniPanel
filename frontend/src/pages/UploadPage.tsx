import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { z } from 'zod';
import { FileSpreadsheet, UploadCloud } from 'lucide-react';
import { request, upload as uploadForm, type UploadProgress } from '../lib/api';
import { asRows, notifyFailure, recordSchema, useResource } from '../lib/resources';
import { useToast } from '../components/Toast';
import { batchSchema } from '../lib/data';
import { navigate } from '../lib/navigation';
import { Heading, QueryView, RecordTable, Stats } from '../components/workspace';
import { ErrorState, isAbort, Panel, UploadMeter } from '../components/ui';
import TasksPage from './TasksPage';
export default function UploadPage() {
  const [platform, setPlatform] = useState('youzan');
  const [file, setFile] = useState<File | null>(null);
  const [batch, setBatch] = useState<number | null>(null);
  const [validation, setValidation] = useState('');
  const [dragging, setDragging] = useState(false);
  const [progress, setProgress] = useState<UploadProgress | null>(null);
  const controller = useRef<AbortController | null>(null);
  const toast = useToast();
  // Leaving the page cancels an unfinished transfer instead of letting it finish unseen.
  useEffect(() => () => controller.current?.abort(), []);
  const upload = useMutation({
    mutationFn: () => {
      if (!file) throw new Error('请选择文件。');
      const body = new FormData();
      body.set('file', file);
      controller.current = new AbortController();
      setProgress({ loaded: 0, total: file.size, percent: 0 });
      return uploadForm(
        `/upload/?expected_platform=${platform}`,
        z.object({ batch_id: z.number(), status: z.string() }),
        {
          body,
          timeoutMs: 120000,
          signal: controller.current.signal,
          onProgress: setProgress,
        },
      );
    },
    onSuccess: (data) => {
      setBatch(data.batch_id);
      toast.success(`${file?.name ?? '订单文件'} 已提交，导入批次 #${data.batch_id} 正在校验`);
    },
    onError: (error) => notifyFailure(toast, error, '订单文件提交失败'),
    onSettled: () => {
      controller.current = null;
    },
    retry: false,
  });
  const cancelled = upload.isError && isAbort(upload.error);
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
          <div className="form-field">
            <span className="form-label" id="upload-platform">
              来源平台
            </span>
            <div className="segmented" role="group" aria-labelledby="upload-platform">
              {(
                [
                  ['youzan', '有赞'],
                  ['jd', '京东'],
                  ['tmall', '天猫'],
                ] as const
              ).map(([value, name]) => (
                <button
                  key={value}
                  type="button"
                  aria-pressed={platform === value}
                  disabled={upload.isPending}
                  onClick={() => setPlatform(value)}
                >
                  {name}
                </button>
              ))}
            </div>
          </div>
          <div className="form-field">
            <span className="form-label">订单文件</span>
            <label
              className={`dropzone ${dragging ? 'dragging' : ''}`}
              onDragOver={(e) => {
                e.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragging(false);
                if (!upload.isPending) setFile(e.dataTransfer.files[0] ?? null);
              }}
            >
              {file ? <FileSpreadsheet size={28} /> : <UploadCloud size={28} />}
              {file ? (
                <>
                  <span className="dropzone-file">{file.name}</span>
                  <span>{(file.size / 1024 / 1024).toFixed(2)} MB · 点击或拖入其他文件可替换</span>
                </>
              ) : (
                <>
                  <strong>拖入订单文件，或点击选择</strong>
                  <span>支持 CSV、XLSX、XLS，单个文件不超过 100 MB</span>
                </>
              )}
              <input
                type="file"
                aria-label="订单文件"
                accept=".csv,.xlsx,.xls"
                disabled={upload.isPending}
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              />
            </label>
          </div>
          <p className="footnote">
            文件平台必须与所选平台一致。重复记录会由后端识别；提交后不要重复上传同一文件。
          </p>
          {upload.isPending && file && progress ? (
            <UploadMeter
              name={file.name}
              progress={progress}
              onCancel={() => controller.current?.abort()}
            />
          ) : (
            <button className="primary" disabled={!file}>
              提交导入
            </button>
          )}
          {validation && (
            <p role="alert" className="field-error">
              {validation}
            </p>
          )}
        </form>
        {cancelled && (
          <p role="status" className="footnote">
            已取消上传，文件未提交。可重新选择文件后再次提交。
          </p>
        )}
        {upload.isError && !cancelled && (
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
