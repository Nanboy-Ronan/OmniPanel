import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { z } from 'zod';
import { request, type User } from '../lib/api';
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
    login_missing: z.boolean(),
  }),
);
const messages: Record<string, string> = {
  approved: '已授权，请在本地弹出的浏览器中登录所选账号。',
  verifying: '正在复检新登录态，请保持本地助手运行。',
  success: '登录态已更新并通过复检。后续采集会使用新的登录态。',
  verification_failed: '未能确认登录有效或账号名称一致。原登录态未替换，请重新发起并选择正确账号。',
  collector_busy: '采集器正在使用原登录态，本次未替换。请在采集完成后重新发起。',
};
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
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const details = useQuery({
    queryKey: ['collector-repair', id],
    queryFn: ({ signal }) =>
      request(path, z.object({ status: z.string(), targets: repairTargetsSchema }), { signal }),
    retry: false,
    refetchInterval: (q) =>
      ['approved', 'verifying'].includes(q.state.data?.status ?? '') ? 3000 : false,
  });
  async function approve() {
    const target = details.data?.targets.find((t) => `${t.platform}:${t.account_id}` === selected);
    if (!target || busy) return;
    setBusy(true);
    setError(null);
    try {
      await request(path, z.object({ status: z.string() }), {
        body: { platform: target.platform, account_id: target.account_id },
      });
      await details.refetch();
    } catch (e) {
      setError(e instanceof Error ? e : new Error('授权失败'));
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="login-layout">
      <section className="login-card">
        <h1>修复采集账号登录</h1>
        <p>当前操作人：{user.display_name || user.email}</p>
        <button onClick={onLogout} disabled={busy}>
          切换账号
        </button>
        {details.isPending ? (
          <Loading label="检查修复权限" />
        ) : details.isError ? (
          <ErrorState error={details.error} />
        ) : details.data?.status === 'pending' ? (
          <>
            <p>
              仅在你刚刚从本机 WorkBuddy
              发起修复时继续。请选择要更新的账号；管理员可操作全部账号，同事只能操作自己负责的账号。
            </p>
            <label>
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
            </label>
            {!details.data.targets.length && (
              <p role="alert">尚未分配负责账号，请联系管理员在「自动采集」中设置负责人。</p>
            )}
            <p>
              新的登录凭证将从本机发送到公司数据平台，仅用于该账号的自动采集。不会显示在 WorkBuddy
              对话中。平台确认登录及账号名称后才替换原凭证。
            </p>
            <button className="primary" disabled={!selected || busy} onClick={() => void approve()}>
              确认账号，开始本地登录
            </button>
          </>
        ) : (
          <p role="status">
            {messages[details.data?.status ?? ''] ?? '请返回 WorkBuddy 重新发起修复。'}
          </p>
        )}
        {error && <ErrorState error={error} />}
      </section>
    </main>
  );
}
