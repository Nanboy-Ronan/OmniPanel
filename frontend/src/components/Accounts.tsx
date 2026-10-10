import { useEffect, useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { z } from 'zod';
import { upload, type UploadProgress } from '../lib/api';
import {
  notifyFailure,
  useAction,
  rowsSchema,
  useResource,
  text,
  type Row,
} from '../lib/resources';
import { useToast } from './Toast';
import { ConfirmAction, QueryView } from './workspace';
import { Detail } from './workspace';
import { ErrorState, isAbort, Panel, UploadMeter } from './ui';
export function AccountManager({
  path,
  admin,
  xhs = false,
}: {
  path: string;
  admin: boolean;
  xhs?: boolean;
}) {
  const query = useResource(path, rowsSchema);
  const action = useAction({ success: '已添加账号', error: '添加账号失败' });
  if (!admin) return null;
  if (path === '/media/accounts')
    return <p className="footnote">公众号账号从服务端配置同步，凭据由管理员在服务器维护。</p>;
  return (
    <details className="account-manager">
      <summary>账号管理</summary>
      <QueryView query={query}>
        {(rows) => (
          <>
            {rows.map((row) => (
              <AccountEditor key={text(row.id)} row={row} path={path} xhs={xhs} />
            ))}
          </>
        )}
      </QueryView>
      <form
        className="inline-form"
        onSubmit={(e) => {
          e.preventDefault();
          const data = new FormData(e.currentTarget);
          action.mutate({
            path,
            body: {
              name: String(data.get('name')).trim(),
              ...(xhs ? { account_type: String(data.get('account_type')) } : {}),
            },
          });
        }}
      >
        <label>
          新账号名称
          <input name="name" required maxLength={100} />
        </label>
        {xhs && (
          <label>
            类型
            <select name="account_type">
              <option value="company">企业号</option>
              <option value="self_media">个人号</option>
            </select>
          </label>
        )}
        <button disabled={action.isPending}>添加账号</button>
      </form>
      {action.isError && <ErrorState error={action.error} />}
    </details>
  );
}
function AccountEditor({ row, path, xhs }: { row: Row; path: string; xhs: boolean }) {
  const action = useAction({ success: '账号已更新', error: '账号更新失败' });
  const [name, setName] = useState(text(row.name));
  const editable = path !== '/media/accounts';
  return (
    <div className="account-row">
      <strong>#{text(row.id)}</strong>
      {editable ? (
        <>
          <input
            aria-label={`账号名称 ${row.id}`}
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={100}
          />
          <button
            disabled={action.isPending || !name.trim()}
            onClick={() =>
              action.mutate({
                path: `${path}/${row.id}`,
                method: 'PATCH',
                body: { name: name.trim() },
              })
            }
          >
            保存名称
          </button>
          {xhs && (
            <label>
              <input
                type="checkbox"
                checked={row.pgy_enabled === true}
                disabled={action.isPending}
                onChange={(e) =>
                  action.mutate({
                    path: `${path}/${row.id}`,
                    method: 'PATCH',
                    body: { pgy_enabled: e.target.checked },
                  })
                }
              />
              启用蒲公英
            </label>
          )}
          <ConfirmAction
            title="删除账号"
            danger
            description={`删除「${text(row.name)}」及关联内容数据，此操作无法在界面恢复。`}
            onConfirm={() =>
              action.mutate({
                path: `${path}/${row.id}`,
                method: 'DELETE',
                success: `已删除账号「${text(row.name)}」`,
              })
            }
            busy={action.isPending}
          />
        </>
      ) : (
        <span>{text(row.name)}</span>
      )}
      {action.isError && <ErrorState error={action.error} />}
    </div>
  );
}
export function FileImport({
  path,
  fields,
  disabled = false,
  accept = '.xlsx,.xls,.csv',
  title = '导入内容数据',
}: {
  path: string;
  fields: Record<string, string>;
  disabled?: boolean;
  accept?: string;
  title?: string;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState('');
  const [progress, setProgress] = useState<UploadProgress | null>(null);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  const client = useQueryClient();
  const toast = useToast();
  const action = useMutation({
    mutationFn: (body: FormData) => {
      controller.current = new AbortController();
      setProgress({ loaded: 0, total: file?.size ?? null, percent: 0 });
      return upload(path, z.unknown(), {
        body,
        timeoutMs: 120000,
        signal: controller.current.signal,
        onProgress: setProgress,
      });
    },
    onSuccess: async () => {
      toast.success(`${file?.name ?? '文件'} 已导入`);
      await client.invalidateQueries();
    },
    onError: (error) => notifyFailure(toast, error, '文件导入失败'),
    onSettled: () => {
      controller.current = null;
    },
    retry: false,
  });
  const cancelled = action.isError && isAbort(action.error);
  return (
    <details className="file-import">
      <summary>{title}</summary>
      <form
        className="inline-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (action.isPending) return;
          if (!file || file.size > 100 * 1024 * 1024) {
            setError('请选择不超过 100 MB 的文件。');
            return;
          }
          setError('');
          const body = new FormData();
          body.set('file', file);
          for (const [key, value] of Object.entries(fields)) if (value) body.set(key, value);
          action.mutate(body);
        }}
      >
        <input
          aria-label={title}
          type="file"
          accept={accept}
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          disabled={action.isPending}
        />
        <button disabled={disabled || !file || action.isPending}>
          {action.isPending ? '正在导入…' : '上传文件'}
        </button>
        {disabled && <small>请先选择具体账号。</small>}
        {error && <p role="alert">{error}</p>}
      </form>
      {action.isPending && file && progress && (
        <UploadMeter
          name={file.name}
          progress={progress}
          onCancel={() => controller.current?.abort()}
        />
      )}
      {cancelled && (
        <p role="status" className="footnote">
          已取消上传，文件未提交。
        </p>
      )}
      {action.isError && !cancelled && (
        <>
          <ErrorState error={action.error} />
          <p className="footnote">提交结果未知时，请先刷新核对数据，再决定是否重试。</p>
        </>
      )}
      {action.isSuccess && (
        <Panel title="导入结果">
          <Detail
            row={
              typeof action.data === 'object' && action.data !== null
                ? (action.data as Row)
                : { result: action.data }
            }
          />
        </Panel>
      )}
    </details>
  );
}
