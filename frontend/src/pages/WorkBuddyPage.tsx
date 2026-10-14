import { useState } from 'react';
import { CheckCircle2, MessageSquare, ShieldCheck, Wrench, Copy } from 'lucide-react';
import '../styles/workbuddy.css';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { z } from 'zod';
import { request } from '../lib/api';
import { formatTimestamp } from '../lib/time';
import { Heading, QueryView } from '../components/workspace';
import { ErrorState, Panel } from '../components/ui';

const tokenSchema = z.object({
  id: z.string(),
  name: z.string(),
  scopes: z.array(z.string()),
  created_at: z.string(),
  expires_at: z.string(),
  revoked_at: z.string().nullable(),
  authorization_expires_at: z.string().nullable().optional(),
});
const scopeLabels: Record<string, string> = {
  'metadata:read': '指标口径与数据更新时间',
  'analytics:ecommerce': '商城汇总分析',
  'analytics:media': '自媒体明细、账号指标与来源分析',
  'reports:read': '已生成的周报',
};
const base = '/integrations/workbuddy';

export default function WorkBuddyPage() {
  const cache = useQueryClient();
  const status = useQuery({
    queryKey: ['workbuddy-status'],
    queryFn: () =>
      request(
        `${base}/status`,
        z.object({ mcp_enabled: z.boolean(), report_api_ready: z.boolean() }),
      ),
  });
  const tokens = useQuery({
    queryKey: ['workbuddy-tokens'],
    queryFn: () => request(`${base}/tokens`, z.array(tokenSchema)),
  });
  const queries = useQuery({
    queryKey: ['workbuddy-queries'],
    queryFn: () =>
      request(
        `${base}/queries`,
        z.array(
          z.object({
            timestamp: z.string(),
            request_id: z.string().nullable(),
            tool: z.string().nullable(),
            outcome: z.string().nullable(),
            duration_ms: z.number().nullable(),
            returned_rows: z.number().nullable(),
          }),
        ),
      ),
  });
  const [name, setName] = useState('我的 WorkBuddy');
  const [days, setDays] = useState(30);
  const [scopes, setScopes] = useState(Object.keys(scopeLabels));
  const [issued, setIssued] = useState<{ id: string; token: string } | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  async function copyPrompt(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(text);
    } catch {
      setError(new Error('复制失败，请选中文字手动复制。'));
    }
  }
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const config = JSON.stringify(
    {
      mcpServers: {
        OmniPanel: {
          type: 'streamableHttp',
          url: `${window.location.origin}/api${base}/mcp`,
          headers: { Authorization: 'Bearer <替换为刚创建的访问令牌>' },
        },
      },
    },
    null,
    2,
  );

  async function create() {
    setBusy(true);
    setError(null);
    setIssued(null);
    try {
      const result = await request(`${base}/tokens`, tokenSchema.extend({ token: z.string() }), {
        body: { name, expires_in_days: days, scopes },
      });
      setIssued({ id: result.id, token: result.token });
      await cache.invalidateQueries({ queryKey: ['workbuddy-tokens'] });
    } catch (e) {
      setError(e as Error);
    } finally {
      setBusy(false);
    }
  }

  async function revoke(id: string) {
    setBusy(true);
    setError(null);
    try {
      await request(`${base}/tokens/${id}`, z.null(), { method: 'DELETE' });
      if (issued?.id === id) setIssued(null);
      await cache.invalidateQueries({ queryKey: ['workbuddy-tokens'] });
    } catch (e) {
      setError(e as Error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="workbuddy-page">
      <Heading
        title="WorkBuddy 连接"
        description="授权 WorkBuddy 查询你的平台数据，获取带统计口径和来源链接的回答。"
      />
      <Panel title="数据权限与查询记录">
        <p>
          管理员和分析员可按已授权类别读取公司业务数据，包括启用的媒体账号、内容明细及账号指标。普通查看者不可连接。登录修复仍仅限账号负责人或管理员。
        </p>
        <p>
          不提供客户个人信息、账号密钥、采集凭据或任意
          SQL。每次查询记录操作者、账号和日期范围、结果状态与耗时；不记录数据正文。管理员可在审计日志的“数据访问”分类查看全员记录。
        </p>
        <details>
          <summary>我的最近查询</summary>
          <QueryView query={queries}>
            {(rows) => (
              <div style={{ overflowX: 'auto' }}>
                <table>
                  <thead>
                    <tr>
                      <th>时间</th>
                      <th>工具</th>
                      <th>状态</th>
                      <th>耗时</th>
                      <th>返回行</th>
                      <th>查询编号</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row, index) => (
                      <tr key={row.request_id ?? index}>
                        <td>{formatTimestamp(row.timestamp)}</td>
                        <td>{row.tool}</td>
                        <td>
                          {row.outcome === 'success'
                            ? '成功'
                            : row.outcome === 'denied'
                              ? '无权限'
                              : '失败'}
                        </td>
                        <td>{row.duration_ms == null ? '—' : `${row.duration_ms} ms`}</td>
                        <td>{row.returned_rows ?? '—'}</td>
                        <td>{row.request_id}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {rows.length === 0 && <p>暂无查询记录。</p>}
              </div>
            )}
          </QueryView>
        </details>
      </Panel>
      {error && <ErrorState error={error} />}
      <QueryView query={status}>
        {(data) => (
          <div className="wb-status-grid" aria-label="服务状态">
            <div className="wb-status-card">
              <MessageSquare size={20} aria-hidden />
              <div>
                <strong>数据问答</strong>
                <p>
                  {data.mcp_enabled
                    ? '问数接入已启用。'
                    : '问数接入尚未启用，请联系管理员完成服务配置。'}
                </p>
              </div>
              <span className={`wb-badge ${data.mcp_enabled ? 'ready' : ''}`}>
                {data.mcp_enabled ? '服务就绪' : '待配置'}
              </span>
            </div>
            <div className="wb-status-card">
              <CheckCircle2 size={20} aria-hidden />
              <div>
                <strong>周报助手</strong>
                <p>
                  {data.report_api_ready
                    ? '周报服务已配置，可查看生成和投递结果。'
                    : '周报助手待配置，已有周报仍可查看。'}
                </p>
              </div>
              <a href="?page=reports" className="wb-link">
                查看周报 →
              </a>
            </div>
          </div>
        )}
      </QueryView>
      <Panel
        title="连接 WorkBuddy，开始问数据"
        subtitle="首次连接完成授权，以后直接在 WorkBuddy 对话中提问。"
      >
        <ol className="wb-steps">
          <li>
            <span className="wb-step-number">1</span>
            <div>
              <h3>找到企业连接器</h3>
              <p>在 WorkBuddy 的连接器中，打开「企业数据工作台 · 一键连接」。</p>
            </div>
          </li>
          <li>
            <span className="wb-step-number">2</span>
            <div>
              <h3>用企业微信授权</h3>
              <p>点击连接，登录企业微信，再点击「授权并连接 WorkBuddy」。</p>
            </div>
          </li>
          <li>
            <span className="wb-step-number">3</span>
            <div>
              <h3>回到对话，直接提问</h3>
              <p>
                在当前对话的「连接器」菜单中启用企业数据工作台，再提问。授权期间自动续期，无需复制令牌。
              </p>
            </div>
          </li>
        </ol>
        <div className="wb-note">
          <ShieldCheck size={17} aria-hidden />
          <span>回答遵循你的平台数据权限。下方可查看或撤销个人授权。</span>
        </div>
      </Panel>
      <div className="wb-help-grid">
        <Panel title="试着这样问" subtitle="复制一句话，发送到 WorkBuddy。">
          <div className="wb-prompts">
            {[
              '查询上周商城销售表现，注明数据更新时间和统计口径。',
              '请用企业数据工作台生成上周运营周报：先列出媒体账号，按账号查询内容明细、逐日指标和公众号区间增量，取完分页。列出内容标题与表现、涨粉、阅读质量、来源和建议；注明数据日期、缺失及查询编号，累计值不要当成周增量。生成可编辑 HTML，不推送。',
            ].map((prompt) => (
              <div className="wb-prompt" key={prompt}>
                <p>{prompt}</p>
                <button onClick={() => void copyPrompt(prompt)} aria-label={`复制：${prompt}`}>
                  <Copy size={15} aria-hidden />
                  {copied === prompt ? '已复制' : '复制'}
                </button>
              </div>
            ))}
          </div>
          <span className="wb-copy-feedback" role="status">
            {copied ? '已复制，可粘贴到 WorkBuddy 对话中。' : ''}
          </span>
        </Panel>
        <Panel title="采集账号登录过期了？" subtitle="由账号负责人或管理员在自己的电脑上完成登录。">
          <div className="wb-repair">
            <Wrench size={22} aria-hidden />
            <div>
              <h3>使用「采集登录修复」技能</h3>
              <p>在 WorkBuddy 的企业技能中添加，然后说：</p>
              <blockquote>帮我修复采集登录</blockquote>
              <p>按提示选择账号、扫码或短信登录，再点击「完成登录」。无需复制凭证。</p>
              <small>仅支持小红书、蒲公英和视频号。复检通过后，等待定时采集更新数据。</small>
            </div>
          </div>
        </Panel>
      </div>
      <Panel title="我的连接与授权">
        <QueryView query={tokens}>
          {(rows) =>
            rows.length ? (
              <ul className="wb-connections">
                {rows.map((row) => (
                  <li key={row.id}>
                    <div className="wb-connection-info">
                      <strong>{row.name}</strong> ·{' '}
                      {row.revoked_at
                        ? '已撤销'
                        : new Date(row.authorization_expires_at ?? row.expires_at) <= new Date()
                          ? '已过期'
                          : '有效'}{' '}
                      · {row.authorization_expires_at ? '授权到期' : '到期时间'}{' '}
                      {formatTimestamp(row.authorization_expires_at ?? row.expires_at)}
                      <p>{row.scopes.map((scope) => scopeLabels[scope] ?? scope).join('、')}</p>
                    </div>
                    {!row.revoked_at && (
                      <button disabled={busy} onClick={() => void revoke(row.id)}>
                        撤销 {row.name}
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <div className="wb-empty">
                <ShieldCheck size={26} aria-hidden />
                <strong>尚未连接 WorkBuddy。</strong>
                <p>按照上方步骤完成授权后，连接会显示在这里。</p>
              </div>
            )
          }
        </QueryView>
      </Panel>
      <details className="wb-advanced">
        <summary>高级设置：手动访问令牌（兼容旧连接）</summary>
        <Panel title="创建只读访问令牌">
          <p>
            令牌继承你的账号权限。当前分析员可查询全平台汇总数据；账号停用、降级或令牌撤销后，连接立即失效。
          </p>
          <form
            className="wb-token-form"
            onSubmit={(event) => {
              event.preventDefault();
              void create();
            }}
          >
            <label>
              名称{' '}
              <input
                value={name}
                maxLength={100}
                required
                onChange={(event) => setName(event.target.value)}
              />
            </label>
            <label>
              有效期{' '}
              <select value={days} onChange={(event) => setDays(Number(event.target.value))}>
                <option value={7}>7 天</option>
                <option value={30}>30 天</option>
                <option value={90}>90 天</option>
              </select>
            </label>
            <fieldset>
              <legend>允许查询</legend>
              {Object.entries(scopeLabels).map(([scope, label]) => (
                <label key={scope}>
                  <input
                    type="checkbox"
                    checked={scopes.includes(scope)}
                    onChange={(event) =>
                      setScopes(
                        event.target.checked
                          ? [...scopes, scope]
                          : scopes.filter((value) => value !== scope),
                      )
                    }
                  />
                  {label}
                </label>
              ))}
            </fieldset>
            <button
              className="primary"
              disabled={busy || !status.data?.mcp_enabled || !name.trim() || !scopes.length}
            >
              创建访问令牌
            </button>
          </form>
          {issued && (
            <div role="status">
              <p>请现在复制并保存。令牌仅显示一次，离开页面后无法再次查看。</p>
              <label>
                新访问令牌
                <input
                  aria-label="新访问令牌"
                  readOnly
                  value={issued.token}
                  onFocus={(event) => event.target.select()}
                  autoComplete="off"
                />
              </label>
              <button onClick={() => setIssued(null)}>已保存，隐藏令牌</button>
            </div>
          )}
        </Panel>
        <Panel title="在 WorkBuddy 中连接">
          <ol>
            <li>打开 WorkBuddy 的 MCP 服务器配置。</li>
            <li>将下方配置合并到现有 mcpServers 中，并填入你的访问令牌。</li>
            <li>连接后尝试询问：“查询上周商城销售表现，注明数据更新时间和统计口径。”</li>
          </ol>
          <pre style={{ overflowX: 'auto', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
            {config}
          </pre>
        </Panel>
      </details>
    </div>
  );
}
