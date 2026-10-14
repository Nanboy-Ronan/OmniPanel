import { useState } from 'react';
import { z } from 'zod';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { request } from '../lib/api';
import { Panel, ErrorState } from './ui';
import { repairTargetsSchema } from '../pages/CollectorRepairPage';

export default function CollectorOwners() {
  const cache = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const targets = useQuery({
    queryKey: ['collector-owners'],
    queryFn: () => request('/collector/repair/targets', repairTargetsSchema),
  });
  const users = useQuery({
    queryKey: ['collector-owner-users'],
    queryFn: () =>
      request(
        '/admin/users',
        z.array(z.object({ id: z.string(), email: z.string(), is_active: z.boolean() })),
      ),
  });
  async function save(platform: string, account_id: number, user_id: string) {
    setBusy(true);
    setError(null);
    try {
      await request('/collector/repair/owner', z.object({ status: z.string() }), {
        method: 'PUT',
        body: { platform, account_id, user_id: user_id || null },
      });
      await cache.invalidateQueries({ queryKey: ['collector-owners'] });
    } catch (e) {
      setError(e instanceof Error ? e : new Error('保存失败'));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Panel title="采集账号负责人">
      <p>负责人可在 WorkBuddy 发起本地登录修复；管理员始终可以操作。未分配的账号仅管理员可修复。</p>
      {targets.data?.map((t) => (
        <label key={`${t.platform}:${t.account_id}`} style={{ display: 'block', marginBottom: 12 }}>
          {t.label}
          <select
            aria-label={`${t.label}负责人`}
            disabled={busy || !users.data}
            value={t.owner_id ?? ''}
            onChange={(e) => void save(t.platform, t.account_id, e.target.value)}
          >
            <option value="">仅管理员</option>
            {users.data
              ?.filter((u) => u.is_active)
              .map((u) => (
                <option key={u.id} value={u.id}>
                  {u.email}
                </option>
              ))}
          </select>
        </label>
      ))}
      {(error || targets.error || users.error) && (
        <ErrorState error={error || targets.error || users.error!} />
      )}
    </Panel>
  );
}
