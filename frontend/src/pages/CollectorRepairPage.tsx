import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, CheckCircle2, Loader2, ShieldCheck, Wrench } from 'lucide-react';
import { z } from 'zod';
import '../styles/workbuddy.css';
import { request, type User } from '../lib/api';
import { formatTimestamp } from '../lib/time';
import { ErrorState, Loading } from '../components/ui';

export const repairTargetsSchema = z.array(
  z.object({
    platform: z.string(),
    account_id: z.number(),
    name: z.string(),
    label: z.string(),
    owner_id: z.string().nullable(),
    can_repair: z.boolean(),
    verify_status: z.string().nullable(),
    verified_at: z.string().nullish(),
    collect_pending: z.boolean().nullish(),
    login_missing: z.boolean(),
  }),
);
const messages: Record<string, [string, 'success' | 'warning' | 'progress']> = {
  approved: ['已授权，请在本地弹出的浏览器中登录所选账号。', 'progress'],
  verifying: ['正在复检新登录态，请保持本地助手运行。', 'progress'],
  success: ['登录态已更新并通过复检。后续采集会使用新的登录态。', 'success'],
  verification_failed: [
    '未能确认登录有效或账号名称一致。原登录态未替换，请重新发起并选择正确账号。',
    'warning',
  ],
  collector_busy: ['该账号的采集一直在运行，本次未替换原登录态。请稍后重新发起。', 'warning'],
  revoked: ['复检期间负责人已变更，或账号已停用/改名。原登录态未替换，请联系管理员。', 'warning'],
};
const loginState: Record<string, string> = {
  success: '当前登录有效',
  session_expired: '当前登录已过期',
  session_missing: '当前缺少登录',
  wrong_account: '当前登录的账号不对',
};
const normalize = (value: string) => value.toUpperCase().replace(/[^A-Z0-9]/g, '');

export default function CollectorRepairPage({
  user,
  onLogout,
}: {
  user: User;
  onLogout: () => void;
}) {
  const id = new URLSearchParams(window.location.search).get('repair') ?? '';
  const path = `/collector/repair/requests/${encodeURIComponent(id)}`;
  const [selected, setSelected] = useState('');
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const details = useQuery({
    queryKey: ['collector-repair', id],
    queryFn: ({ signal }) =>
      request(
        path,
        z.object({
          status: z.string(),
          targets: repairTargetsSchema,
          requested_at: z.string().nullish(),
          expires_at: z.string().nullish(),
        }),
        { signal },
      ),
    retry: false,
    refetchInterval: (q) =>
      ['approved', 'verifying'].includes(q.state.data?.status ?? '') ? 3000 : false,
  });
  const target = details.data?.targets.find((t) => `${t.platform}:${t.account_id}` === selected);
  const codeReady = normalize(code).length === 8;
  async function approve() {
    if (!target || !codeReady || busy) return;
    setBusy(true);
    setError(null);
    try {
      await request(path, z.object({ status: z.string() }), {
        body: { platform: target.platform, account_id: target.account_id, user_code: code.trim() },
      });
      await details.refetch();
    } catch (e) {
      setError(e instanceof Error ? e : new Error('授权失败'));
    } finally {
      setBusy(false);
    }
  }
  const result = messages[details.data?.status ?? ''];
  // After success the server has queued an immediate collect of this account.
  const queued =
    details.data?.status === 'success' &&
    details.data.targets.some(
      (t) => `${t.platform}:${t.account_id}` === selected && t.collect_pending,
    );
  return (
    <main className="flow-layout">
      <section className="flow-card">
        <header className="flow-head">
          <span className="flow-icon">
            <Wrench size={20} aria-hidden />
          </span>
          <div>
            <h1>修复采集账号登录</h1>
            <p>确认要更新的账号后，在本机 WorkBuddy 助手弹出的浏览器中完成登录。</p>
          </div>
        </header>
        <div className="flow-identity">
          <span>
            当前操作人 <strong>{user.display_name || user.email}</strong>
          </span>
          <button onClick={onLogout} disabled={busy}>
            切换账号
          </button>
        </div>
        {details.isPending ? (
          <Loading label="检查修复权限" />
        ) : details.isError ? (
          <ErrorState error={details.error} />
        ) : details.data?.status === 'pending' ? (
          <>
            {details.data.requested_at && (
              <p className="flow-meta">
                <span>发起于 {formatTimestamp(details.data.requested_at)}</span>
                {details.data.expires_at && (
                  <span>有效至 {formatTimestamp(details.data.expires_at)}</span>
                )}
              </p>
            )}
            <label className="flow-field">
              采集账号
              <select
                aria-label="采集账号"
                value={selected}
                onChange={(e) => setSelected(e.target.value)}
              >
                <option value="">请选择账号</option>
                {details.data.targets.map((t) => (
                  <option
                    key={`${t.platform}:${t.account_id}`}
                    value={`${t.platform}:${t.account_id}`}
                  >
                    {t.label}
                  </option>
                ))}
              </select>
              {target && (
                <small>
                  {target.login_missing
                    ? '当前缺少登录'
                    : (loginState[target.verify_status ?? ''] ?? '尚无登录检查记录')}
                  {target.verified_at && ` · 检查于 ${formatTimestamp(target.verified_at)}`}
                </small>
              )}
            </label>
            {!details.data.targets.length && (
              <p role="alert" className="flow-alert">
                尚未分配负责账号，请联系管理员在「自动采集」中设置负责人。
              </p>
            )}
            <label className="flow-field">
              校验码
              <input
                className="flow-code"
                aria-label="校验码"
                value={code}
                maxLength={12}
                autoComplete="off"
                placeholder="ABCD-EFGH"
                onChange={(e) => setCode(e.target.value)}
              />
              <small>
                输入 WorkBuddy 对话中助手显示的 8 位校验码。不是你本人刚刚发起的修复，请不要确认。
              </small>
            </label>
            <div className="flow-note">
              <ShieldCheck size={16} aria-hidden />
              <span>
                新的登录凭证将从本机发送到公司数据平台，仅用于该账号的自动采集，不会显示在 WorkBuddy
                对话中。平台确认登录及账号名称后才替换原凭证。
              </span>
            </div>
            {error && <ErrorState error={error} />}
            <div className="flow-actions">
              <button
                className="primary"
                disabled={!target || !codeReady || busy}
                onClick={() => void approve()}
              >
                {busy ? '正在确认…' : '确认账号，开始本地登录'}
              </button>
            </div>
          </>
        ) : (
          <p role="status" className={`flow-result tone-${result?.[1] ?? 'warning'}`}>
            {result?.[1] === 'success' ? (
              <CheckCircle2 size={18} aria-hidden />
            ) : result?.[1] === 'progress' ? (
              <Loader2 size={18} aria-hidden />
            ) : (
              <AlertTriangle size={18} aria-hidden />
            )}
            <span>
              {result?.[0] ?? '请返回 WorkBuddy 重新发起修复。'}
              {queued && ' 已安排立即采集该账号，通常几分钟内完成，无需等到明天。'}
            </span>
          </p>
        )}
      </section>
    </main>
  );
}
