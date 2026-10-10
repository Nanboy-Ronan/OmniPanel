import { useEffect, useRef, useState } from 'react';
import { z } from 'zod';
import { useQuery } from '@tanstack/react-query';
import { request, type User } from '../lib/api';
import {
  endpoint,
  paged,
  useAction,
  useResource,
  rowsSchema,
  text,
  type Row,
} from '../lib/resources';
import {
  ConfirmAction,
  Heading,
  Pagination,
  QueryView,
  RecordTable,
  Refresh,
} from '../components/workspace';
import { DataTable, EmptyState, ErrorState, Panel } from '../components/ui';
import { formatTimestamp } from '../lib/time';
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
  const create = useAction({ success: '新账号创建成功', error: '创建账号失败' });
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
  const action = useAction({ success: '账号设置已更新', error: '账号设置未保存' });
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
              onConfirm={() =>
                action.mutate(
                  { path, method: 'DELETE', success: `已删除 ${text(row.email)}` },
                  { onSuccess: close },
                )
              }
            />
          )}
        </details>
        {action.isError && <ErrorState error={action.error} />}{' '}
        {action.isSuccess && <p role="status">操作已完成。</p>}
      </Panel>
    </dialog>
  );
}
const LOG_CATEGORIES = [
  ['', '全部'],
  ['auth', '登录与账号'],
  ['failed', '登录失败'],
  ['access', '数据访问与导出'],
  ['data', '数据变更'],
  ['admin', '权限管理'],
] as const;
const ACTION_LABELS: Record<string, string> = {
  login: '密码登录',
  wecom_login: '企业微信登录',
  login_failed: '密码登录失败',
  wecom_login_failed: '企业微信登录失败',
  logout: '退出登录',
  register: '注册账号',
  wecom_register: '企业微信首次登录',
  download: '导出订单',
  export_client: '导出表格',
  view_customer: '查看客户档案',
  view_order_raw: '查看订单原始记录',
  sql_query: 'SQL 查询',
  nl_sql_query: '中文问数',
  upload: '上传订单',
  xhs_upload: '导入小红书数据',
  xhs_upload_overview: '导入小红书概览',
  zhihu_upload: '导入知乎数据',
  pgy_upload: '导入蒲公英数据',
  channels_upload: '导入视频号数据',
  wechat_sync: '同步公众号',
  clear_db: '清空商城数据',
  weekly_report_run: '手动生成周报',
  media_account_create: '新增公众号账号',
  xhs_account_create: '新增小红书账号',
  xhs_account_update: '修改小红书账号',
  xhs_account_delete: '删除小红书账号',
  channels_account_create: '新增视频号账号',
  channels_account_update: '修改视频号账号',
  channels_account_delete: '删除视频号账号',
  collector_session_upload: '更新采集登录态',
  collector_session_delete: '删除采集登录态',
  saved_query_create: '保存筛选视图',
  saved_query_delete: '删除筛选视图',
  create_user: '创建账号',
  update_role: '修改角色',
  update_active: '启用/停用账号',
  update_password: '重置密码',
  update_wecom_alert: '修改告警接收',
  delete_user: '删除账号',
};
const FAILURE_REASONS: Record<string, string> = {
  invalid_state: '登录校验不符（可能是过期或伪造的登录链接）',
  pending_approval: '账号待管理员开通',
  inactive: '账号已停用',
  rate_limited: '尝试次数过多，已临时限制',
  wecom_unavailable: '企业微信服务异常',
  not_member: '非企业微信成员',
  auto_create_disabled: '未开放自动开通',
  bad_credentials: '用户名或密码错误',
};
const DETAIL_LABELS: Record<string, string> = {
  customer_id: '客户',
  account_id: '账号编号',
  name: '名称',
  changed: '修改字段',
  filename: '文件',
  batch_id: '批次',
  order_id: '订单号',
  order_pk: '订单编号',
  platform: '平台',
  wecom_userid: '企业微信账号',
  target_user: '目标账号',
  email: '邮箱',
  new_role: '新角色',
  is_active: '启用',
  start_date: '开始',
  end_date: '结束',
  week_start: '周报周期',
  status: '状态',
  rows: '行数',
};
const LOG_PAGE = 50;
function summarize(action: string, detail: unknown): string {
  if (!detail || typeof detail !== 'object') return detail == null ? '—' : String(detail);
  const d = detail as Record<string, unknown>;
  if (typeof d.reason === 'string') {
    const who = d.username ?? d.wecom_userid;
    return `${FAILURE_REASONS[d.reason] ?? d.reason}${who ? ` · ${who}` : ''}`;
  }
  if (action === 'export_client') return `${d.source ?? ''} · ${d.rows ?? 0} 行`;
  if (action === 'sql_query' || action === 'nl_sql_query')
    return String(d.question ?? d.sql ?? '').slice(0, 120);
  return Object.entries(d)
    .map(([key, value]) => [key, Array.isArray(value) ? value.join('、') : value] as const)
    .filter(([, value]) => value !== null && value !== '' && typeof value !== 'object')
    .slice(0, 4)
    .map(([key, value]) => `${DETAIL_LABELS[key] ?? key}：${value}`)
    .join(' · ');
}
function deviceOf(agent: unknown): string {
  const ua = typeof agent === 'string' ? agent : '';
  if (!ua) return '';
  const client = /wxwork/i.test(ua)
    ? '企业微信'
    : /Edg\//.test(ua)
      ? 'Edge'
      : /Chrome\//.test(ua)
        ? 'Chrome'
        : /Safari\//.test(ua)
          ? 'Safari'
          : /Firefox\//.test(ua)
            ? 'Firefox'
            : '其他';
  const os = /iPhone|iPad/.test(ua)
    ? 'iOS'
    : /Android/.test(ua)
      ? 'Android'
      : /Mac OS X/.test(ua)
        ? 'macOS'
        : /Windows/.test(ua)
          ? 'Windows'
          : '';
  return [client, os].filter(Boolean).join(' · ');
}
export function LogsPage() {
  const [filters, setFilters] = useState({
    user_id: '',
    category: '',
    start_date: '',
    end_date: '',
    q: '',
  });
  const [draftQ, setDraftQ] = useState('');
  const [page, setPage] = useState(0);
  const users = useResource('/admin/users', rowsSchema);
  const path = endpoint('/admin/logs', {
    ...filters,
    limit: String(LOG_PAGE),
    offset: String(page * LOG_PAGE),
  });
  const query = useQuery({
    queryKey: ['audit-logs', path],
    queryFn: ({ signal }) => paged(path, signal),
    placeholderData: (previous) => previous,
  });
  const update = (patch: Partial<typeof filters>) => {
    setFilters((current) => ({ ...current, ...patch }));
    setPage(0);
  };
  return (
    <>
      <Heading
        title="操作日志"
        description="登录、退出、数据访问与导出、数据变更和权限管理的审计记录，含来源 IP 与设备。"
        action={<Refresh busy={query.isFetching} onClick={() => void query.refetch()} />}
      />
      <div className="filter-bar range-filter audit-filters">
        <div className="segmented" role="group" aria-label="日志分类">
          {LOG_CATEGORIES.map(([key, label]) => (
            <button
              key={key}
              aria-pressed={filters.category === key}
              onClick={() => update({ category: key })}
            >
              {label}
            </button>
          ))}
        </div>
        <label>
          用户
          <select
            aria-label="按用户筛选"
            value={filters.user_id}
            onChange={(e) => update({ user_id: e.target.value })}
          >
            <option value="">全部用户</option>
            {users.data?.map((row) => (
              <option key={text(row.id)} value={text(row.id)}>
                {text(row.email)}
              </option>
            ))}
          </select>
        </label>
        <label>
          开始日期
          <input
            type="date"
            value={filters.start_date}
            onChange={(e) => update({ start_date: e.target.value })}
          />
        </label>
        <label>
          结束日期
          <input
            type="date"
            value={filters.end_date}
            onChange={(e) => update({ end_date: e.target.value })}
          />
        </label>
        <form
          className="audit-search"
          onSubmit={(e) => {
            e.preventDefault();
            update({ q: draftQ.trim() });
          }}
        >
          <input
            type="search"
            aria-label="搜索日志"
            placeholder="搜索账号、IP、客户或内容"
            value={draftQ}
            onChange={(e) => setDraftQ(e.target.value)}
          />
          <button type="submit">搜索</button>
        </form>
      </div>
      <QueryView query={query}>
        {({ rows, total }) => (
          <Panel
            title="审计记录"
            subtitle={total != null ? `共 ${total.toLocaleString()} 条` : undefined}
          >
            {rows.length ? (
              <>
                <DataTable
                  caption="操作日志"
                  rows={rows}
                  rowKey={(row) => String(row.id)}
                  columns={[
                    {
                      key: 'timestamp',
                      title: '时间',
                      render: (row) => (
                        <span className="audit-time">
                          {formatTimestamp(text(row.timestamp)) ?? '—'}
                        </span>
                      ),
                    },
                    {
                      key: 'email',
                      title: '账号',
                      render: (row) =>
                        row.email ? (
                          text(row.email)
                        ) : (
                          <span className="badge tone-neutral">未知账号</span>
                        ),
                    },
                    {
                      key: 'action',
                      title: '操作',
                      render: (row) => {
                        const action = text(row.action);
                        const failed = action.endsWith('_failed');
                        return (
                          <span className={failed ? 'badge tone-danger' : undefined}>
                            {ACTION_LABELS[action] ?? action}
                          </span>
                        );
                      },
                    },
                    {
                      key: 'detail',
                      title: '详情',
                      render: (row) => {
                        const summary = summarize(text(row.action), row.detail);
                        return (
                          <span
                            className="cell-value cell-long-text"
                            title={JSON.stringify(row.detail ?? '')}
                          >
                            {summary || '—'}
                          </span>
                        );
                      },
                    },
                    {
                      key: 'ip',
                      title: '来源',
                      render: (row) => (
                        <span className="audit-source" title={text(row.user_agent ?? '')}>
                          {text(row.ip ?? '') || '—'}
                          {deviceOf(row.user_agent) && <small>{deviceOf(row.user_agent)}</small>}
                        </span>
                      ),
                    },
                  ]}
                />
                <Pagination
                  page={page}
                  total={total}
                  pageSize={LOG_PAGE}
                  hasNext={total != null ? (page + 1) * LOG_PAGE < total : rows.length === LOG_PAGE}
                  onPage={setPage}
                />
              </>
            ) : (
              <EmptyState title="当前条件下没有日志" />
            )}
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
  const action = useAction({ success: '采集登录态已更新', error: '登录态操作失败' });
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
