import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { z } from 'zod';
import { request, type User } from '../lib/api';
import { ErrorState, Loading } from '../components/ui';

const labels: Record<string, string> = {
  'metadata:read': '指标说明与数据更新时间',
  'analytics:ecommerce': '商城经营汇总数据',
  'analytics:media': '公司媒体账号、内容明细、逐日指标与来源分析',
  'reports:read': '已生成的经营周报',
};
export default function WorkBuddyAuthorizePage({
  user,
  onLogout,
}: {
  user: User;
  onLogout: () => void;
}) {
  const id = new URLSearchParams(window.location.search).get('authorization') ?? '';
  const path = `/integrations/workbuddy/oauth/requests/${encodeURIComponent(id)}`;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [redirect, setRedirect] = useState<string | null>(null);
  const details = useQuery({
    queryKey: ['workbuddy-authorization', id],
    queryFn: ({ signal }) =>
      request(path, z.object({ scopes: z.array(z.string()), can_authorize: z.boolean() }), {
        signal,
      }),
    retry: false,
  });
  async function decide(approve: boolean) {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const result = await request(path, z.object({ redirect_url: z.string() }), {
        body: { approve },
      });
      setRedirect(result.redirect_url);
      window.location.assign(result.redirect_url);
    } catch (e) {
      setError(e instanceof Error ? e : new Error('连接失败，请重试。'));
      setBusy(false);
    }
  }
  return (
    <main className="login-layout">
      <section className="login-card">
        <h1>连接企业数据工作台</h1>
        {redirect ? (
          <>
            <p>已完成操作，请返回 WorkBuddy 继续。</p>
            <a className="button primary" href={redirect}>
              返回 WorkBuddy
            </a>
          </>
        ) : (
          <>
            <p>当前账号：{user.display_name || user.email}</p>
            <button onClick={onLogout} disabled={busy}>
              切换账号
            </button>
            {details.isPending ? (
              <Loading label="正在检查连接请求" />
            ) : details.isError ? (
              <>
                <ErrorState error={details.error} />
                <p>请返回 WorkBuddy，重新点击连接。</p>
              </>
            ) : (
              details.data && (
                <>
                  <p>授权后，WorkBuddy 可以通过你的账号读取以下公司数据：</p>
                  <ul>
                    {details.data.scopes.map((scope) => (
                      <li key={scope}>{labels[scope] ?? scope}</li>
                    ))}
                  </ul>
                  <p>
                    授权不包含修改数据或发送消息。你可以随时在工作台的「WorkBuddy 连接」中撤销授权。
                  </p>
                  {!details.data.can_authorize && (
                    <p role="alert">
                      你的账号尚未开通数据分析权限。请联系平台管理员，在「用户管理」中开通分析师权限后重新连接。
                    </p>
                  )}
                  {error && <ErrorState error={error} />}
                  <div className="login-actions">
                    <button
                      className="primary"
                      disabled={busy || !details.data.can_authorize}
                      onClick={() => void decide(true)}
                    >
                      {busy ? '正在处理…' : '授权并连接 WorkBuddy'}
                    </button>
                    <button disabled={busy} onClick={() => void decide(false)}>
                      取消
                    </button>
                  </div>
                </>
              )
            )}
          </>
        )}
      </section>
    </main>
  );
}
