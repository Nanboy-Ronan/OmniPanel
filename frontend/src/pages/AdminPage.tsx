import { useEffect, useRef, useState } from 'react';
import { z } from 'zod';
import { useQuery } from '@tanstack/react-query';
import { request, type User } from '../lib/api';
import { endpoint, useAction, useResource, rowsSchema, text, type Row } from '../lib/resources';
import { ConfirmAction, Heading, QueryView, RecordTable, Refresh } from '../components/workspace';
import { ErrorState, Panel } from '../components/ui';
const roles = ['viewer', 'analyst', 'admin'];
const names: Record<string, string> = { viewer: '查看者', analyst: '分析员', admin: '管理员' };
const roleDescriptions: Record<string, string> = {
  viewer: '浏览订单、上传文件和查看导入任务。',
  analyst: '具备查看者权限，并可访问经营分析、内容分析和 SQL 控制台。',
  admin: '具备分析员权限，并可管理用户、采集配置和数据库维护。',
};
export default function UsersPage({ user }: { user: User }) {
  const query = useResource('/admin/users', rowsSchema);
  const [selected, setSelected] = useState<Row | null>(null);
  const create = useAction();
  return (
    <>
      <Heading
        title="用户管理"
        description="点击账号对应的“管理权限”，修改角色与访问状态。"
        action={<Refresh busy={query.isFetching} onClick={() => void query.refetch()} />}
      />
      <Panel title="账号列表">
        <QueryView query={query}>
          {(rows) => (
            <RecordTable
              rows={rows}
              onSelect={setSelected}
              selectLabel="管理权限"
              columns={['email', 'role', 'is_active', 'wecom_linked', 'wecom_alert_enabled']}
              caption="用户账号"
            />
          )}
        </QueryView>
      </Panel>
      {selected && (
        <UserEditor
          key={text(selected.id)}
          row={query.data?.find((row) => row.id === selected.id) ?? selected}
          self={selected.id === user.id}
          close={() => setSelected(null)}
        />
      )}
      <details className="data-tools">
        <summary>创建新账号</summary>
        <Panel title="创建账号">
          <form
            className="inline-form"
            onSubmit={(e) => {
              e.preventDefault();
              const form = e.currentTarget;
              const data = new FormData(form);
              create.mutate(
                {
                  path: '/admin/users',
                  body: {
                    email: data.get('email'),
                    password: data.get('password'),
                    role: data.get('role'),
                  },
                },
                { onSuccess: () => form.reset() },
              );
            }}
          >
            <label>
              邮箱
              <input type="email" name="email" required autoComplete="off" />
            </label>
            <label>
              初始密码
              <input
                type="password"
                name="password"
                required
                minLength={12}
                autoComplete="new-password"
              />
            </label>
            <label>
              角色
              <select name="role">
                {roles.map((role) => (
                  <option key={role} value={role}>
                    {names[role]}
                  </option>
                ))}
              </select>
            </label>
            <button disabled={create.isPending}>创建账号</button>
          </form>
          {create.isError && <ErrorState error={create.error} />}{' '}
          {create.isSuccess && <p role="status">账号已创建。</p>}
        </Panel>
      </details>
    </>
  );
}
function UserEditor({ row, self, close }: { row: Row; self: boolean; close: () => void }) {
  const action = useAction();
  const [role, setRole] = useState(text(row.role));
  const path = `/admin/users/${row.id}`;
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const el = dialog.current!;
    el.showModal();
    return () => el.close();
  }, []);
  useEffect(() => setRole(text(row.role)), [row.role]);
  return (
    <dialog
      ref={dialog}
      className="user-editor-dialog"
      aria-label={`管理权限：${text(row.email)}`}
      onCancel={close}
    >
      <Panel
        title="管理账号权限"
        action={
          <button onClick={close} aria-label="关闭账号设置">
            关闭
          </button>
        }
      >
        <div className="user-editor-summary">
          <strong>{text(row.email)}</strong>
          <span>
            当前角色：{names[text(row.role)]} · {row.is_active ? '已启用' : '已停用'}
            {self ? ' · 当前登录账号' : ''}
          </span>
        </div>
        <div className="admin-actions">
          <label>
            角色
            <select value={role} onChange={(e) => setRole(e.target.value)}>
              {roles.map((role) => (
                <option key={role} value={role}>
                  {names[role]}
                </option>
              ))}
            </select>
          </label>
          <p className="role-description">{roleDescriptions[role]}</p>
          {role !== row.role ? (
            <ConfirmAction
              title="保存角色"
              description={`将 ${text(row.email)} 从${names[text(row.role)]}改为${names[role]}。${self ? '这会影响您当前账号的权限。' : ''}`}
              busy={action.isPending}
              onConfirm={() =>
                action.mutate({ path: `${path}/role`, method: 'PUT', body: { role } })
              }
            />
          ) : (
            <button disabled>角色未更改</button>
          )}
          {!self && (
            <ConfirmAction
              title={row.is_active ? '停用账号' : '启用账号'}
              description={`确认变更 ${text(row.email)} 的登录权限？`}
              busy={action.isPending}
              onConfirm={() =>
                action.mutate({
                  path: `${path}/active`,
                  method: 'PUT',
                  body: { is_active: !row.is_active },
                })
              }
            />
          )}
          <label>
            <input
              type="checkbox"
              checked={row.wecom_alert_enabled === true}
              disabled={!row.wecom_linked || action.isPending}
              onChange={(e) =>
                action.mutate({
                  path: `${path}/wecom-alert`,
                  method: 'PUT',
                  body: { enabled: e.target.checked },
                })
              }
            />
            接收企业微信告警{!row.wecom_linked ? '（尚未绑定）' : ''}
          </label>
        </div>
        <details className="account-security">
          <summary>密码与账号删除</summary>
          <form
            className="inline-form"
            onSubmit={(e) => {
              e.preventDefault();
              const form = e.currentTarget;
              action.mutate(
                {
                  path: `${path}/password`,
                  method: 'PUT',
                  body: { password: new FormData(form).get('password') },
                },
                { onSuccess: () => form.reset() },
              );
            }}
          >
            <label>
              新密码
              <input
                name="password"
                type="password"
                minLength={12}
                required
                autoComplete="new-password"
              />
            </label>
            <button disabled={action.isPending}>重置密码</button>
          </form>
          {!self && (
            <ConfirmAction
              title="删除用户"
              danger
              description={`永久删除 ${text(row.email)} 的账号。`}
              busy={action.isPending}
              onConfirm={() => action.mutate({ path, method: 'DELETE' }, { onSuccess: close })}
            />
          )}
        </details>
        {action.isError && <ErrorState error={action.error} />}{' '}
        {action.isSuccess && <p role="status">操作已完成。</p>}
      </Panel>
    </dialog>
  );
}
export function LogsPage() {
  const [id, setId] = useState('');
  const users = useResource('/admin/users', rowsSchema);
  const query = useResource(endpoint('/admin/logs', { user_id: id }), rowsSchema);
  return (
    <>
      <Heading
        title="操作日志"
        description="最近 100 条操作记录，支持按用户筛选。"
        action={<Refresh busy={query.isFetching} onClick={() => void query.refetch()} />}
      />
      <label className="field-inline">
        用户
        <select value={id} onChange={(e) => setId(e.target.value)}>
          <option value="">全部用户</option>
          {users.data?.map((row) => (
            <option key={text(row.id)} value={text(row.id)}>
              {text(row.email)}
            </option>
          ))}
        </select>
      </label>
      <QueryView query={query}>
        {(rows) => (
          <Panel title="审计记录">
            <RecordTable rows={rows} caption="操作日志" />
          </Panel>
        )}
      </QueryView>
    </>
  );
}
export { default as DatabasePage } from './DatabasePage';
export function CollectorPage() {
  const [platform, setPlatform] = useState('xhs');
  const [account, setAccount] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState('');
  const action = useAction();
  const sessions = useResource('/admin/collector/sessions', rowsSchema);
  const runs = useQuery({
    queryKey: ['resource', '/admin/collector/runs?limit=100'],
    queryFn: ({ signal }) => request('/admin/collector/runs?limit=100', rowsSchema, { signal }),
    refetchInterval: (q) =>
      q.state.error ? false : q.state.data?.some((row) => row.status === 'running') ? 5000 : false,
    refetchIntervalInBackground: false,
  });
  const perAccount = ['xhs', 'channels', 'pugongying'].includes(platform);
  const accounts = useResource(
    platform === 'channels' ? '/media/channels/accounts' : '/media/xhs/accounts',
    rowsSchema,
    perAccount,
  );
  return (
    <>
      <Heading
        title="自动采集"
        description="管理采集登录态，查看定时采集与登录验证记录。"
        action={
          <Refresh
            busy={sessions.isFetching || runs.isFetching}
            onClick={() => {
              void sessions.refetch();
              void runs.refetch();
            }}
          />
        }
      />
      <Panel title="登录态管理">
        <QueryView query={sessions}>
          {(rows) => (
            <>
              {rows.map((row) => (
                <div className="account-row" key={`${row.platform}:${row.account_id}`}>
                  <strong>
                    {text(row.platform)} · {text(row.account_name ?? '默认账号')}
                  </strong>
                  <span>更新于 {text(row.updated_at)}</span>
                  <span>
                    验证：{text(row.last_verify_status)} · 采集：{text(row.last_run_status)}
                  </span>
                  <ConfirmAction
                    title="删除登录态"
                    danger
                    description="删除后该账号的自动采集将停止，直到重新上传有效登录态。"
                    busy={action.isPending}
                    onConfirm={() =>
                      action.mutate({
                        path: endpoint('/admin/collector/sessions', {
                          platform: text(row.platform),
                          account_id: row.account_id as number | null,
                        }),
                        method: 'DELETE',
                      })
                    }
                  />
                </div>
              ))}
            </>
          )}
        </QueryView>
        <form
          className="inline-form"
          onSubmit={async (e) => {
            e.preventDefault();
            if (!file || file.size > 1024 * 1024) {
              setError('请选择不超过 1 MB 的登录态 JSON 文件。');
              return;
            }
            try {
              const value = JSON.parse(await file.text());
              if (!z.object({ cookies: z.array(z.unknown()) }).safeParse(value).success)
                throw new Error();
            } catch {
              setError('文件缺少有效的 cookies 数组。');
              return;
            }
            setError('');
            const body = new FormData();
            body.set('platform', platform);
            if (perAccount) body.set('account_id', account);
            body.set('file', file);
            action.mutate({ path: '/admin/collector/sessions', body });
          }}
        >
          <label>
            平台
            <select
              value={platform}
              onChange={(e) => {
                setPlatform(e.target.value);
                setAccount('');
              }}
            >
              {['xhs', 'zhihu', 'pugongying', 'channels', 'jd'].map((p) => (
                <option key={p}>{p}</option>
              ))}
            </select>
          </label>
          {perAccount && (
            <label>
              账号
              <select required value={account} onChange={(e) => setAccount(e.target.value)}>
                <option value="">请选择</option>
                {accounts.data?.map((row) => (
                  <option key={text(row.id)} value={text(row.id)}>
                    {text(row.name)}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label>
            登录态文件
            <input
              type="file"
              accept=".json"
              required
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
          </label>
          <button disabled={action.isPending || !file || (perAccount && !account)}>
            上传登录态
          </button>
        </form>
        {error && <p role="alert">{error}</p>}
        {action.isError && <ErrorState error={action.error} />}{' '}
        {action.isSuccess && <p role="status">登录态操作已完成。</p>}
      </Panel>
      <Panel title="最近 100 次执行记录">
        <QueryView query={runs}>
          {(rows) => <RecordTable rows={rows} caption="采集记录" />}
        </QueryView>
      </Panel>
    </>
  );
}
