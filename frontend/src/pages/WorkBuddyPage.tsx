import { useState } from 'react';
import {
  CheckCircle2,
  Copy,
  FileText,
  MessageSquare,
  MonitorCheck,
  ShieldCheck,
  Users,
  Wrench,
} from 'lucide-react';
import '../styles/workbuddy.css';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { z } from 'zod';
import { request, type User } from '../lib/api';
import { formatTimestamp } from '../lib/time';
import { ConfirmAction, Heading, QueryView } from '../components/workspace';
import { ErrorState, Panel } from '../components/ui';
import { repairTargetsSchema } from './CollectorRepairPage';

const tokenSchema = z.object({
  id: z.string(),
  name: z.string(),
  scopes: z.array(z.string()),
  created_at: z.string(),
  expires_at: z.string(),
  revoked_at: z.string().nullable(),
  authorization_expires_at: z.string().nullable().optional(),
});
const adminTokenSchema = tokenSchema.extend({
  user_email: z.string(),
  user_name: z.string().nullable().optional(),
  kind: z.enum(['oauth', 'token']),
});
const readinessSchema = z.object({
  ready: z.boolean(),
  browser_ready: z.boolean(),
  sessions_writable: z.boolean(),
  browsers_path_configured: z.boolean(),
  requests_stalled: z.boolean().nullish(),
});
type Token = z.infer<typeof tokenSchema>;
const scopeLabels: Record<string, string> = {
  'metadata:read': '指标口径与数据更新时间',
  'analytics:ecommerce': '商城汇总分析',
  'analytics:media': '自媒体明细、账号指标与来源分析',
  'reports:read': '已生成的周报',
};
const outcomeLabels: Record<string, [string, string]> = {
  success: ['成功', 'success'],
  denied: ['无权限', 'warning'],
  rate_limited: ['频率受限', 'warning'],
};
const loginLabels: Record<string, [string, string]> = {
  success: ['登录有效', 'success'],
  session_expired: ['登录已过期', 'danger'],
  session_missing: ['缺少登录', 'danger'],
  wrong_account: ['登录账号不对', 'danger'],
};
const base = '/integrations/workbuddy';

const repairPrompt = '帮我修复采集登录，请让我选择需要更新的小红书、蒲公英或视频号账号。';
const draftPrompt =
  '请用企业数据工作台生成最近一个完整周（北京时间周一至周日）的自媒体运营周报草稿。先确认日期范围和媒体账号，逐账号取完内容明细、逐日指标和来源数据的所有分页。分析内容表现、涨粉、阅读质量、来源和下周建议；区分周增量与累计值，列出数据更新时间、缺失项、统计口径、来源链接和查询编号。输出可编辑 HTML，不推送。';
const readReportPrompt =
  '请查找最近一个完整周已生成的平台周报，说明报告日期、数据缺失和主要结论，并给出原始报告链接；如果没有对应周报，请明确说明，不用旧周报代替。';
const dailyPrompts = [
  '查询上周商城销售表现，注明数据更新时间和统计口径。',
  '对比最近两周各小红书账号的涨粉和主页访问，标出数据缺失的日期。',
  '列出上个月阅读量最高的 10 篇公众号文章，区分新发文章和历史长尾。',
];

function tokenState(row: Token) {
  if (row.revoked_at) return ['已撤销', 'neutral'] as const;
  return new Date(row.authorization_expires_at ?? row.expires_at) <= new Date()
    ? (['已过期', 'neutral'] as const)
    : (['有效', 'success'] as const);
}

function Badge({ label, tone }: { label: string; tone: string }) {
  return <span className={`wb-pill tone-${tone}`}>{label}</span>;
}

function PromptCard({
  title,
  text,
  label,
  copied,
  onCopy,
}: {
  title?: string;
  text: string;
  label: string;
  copied: boolean;
  onCopy: (text: string) => void;
}) {
  return (
    <div className="wb-prompt">
      <div>
        {title && <strong>{title}</strong>}
        <p>{text}</p>
      </div>
      <button onClick={() => onCopy(text)} aria-label={label}>
        <Copy size={15} aria-hidden />
        {copied ? '已复制' : '复制'}
      </button>
    </div>
  );
}

function MyRepairAccounts() {
  const targets = useQuery({
    queryKey: ['collector-repair-targets'],
    queryFn: () => request('/collector/repair/targets', repairTargetsSchema),
  });
  return (
    <div className="wb-accounts" aria-label="我可修复的采集账号">
      <h3>我可修复的账号</h3>
      <QueryView query={targets}>
        {(rows) =>
          rows.length ? (
            <ul>
              {rows.map((row) => {
                const [label, tone] = row.login_missing
                  ? ['缺少登录', 'danger']
                  : row.verify_status
                    ? (loginLabels[row.verify_status] ?? ['检查未通过', 'warning'])
                    : ['尚未检查', 'neutral'];
                return (
                  <li key={`${row.platform}:${row.account_id}`}>
                    <span>{row.label}</span>
                    <span className="wb-account-state">
                      <Badge label={label} tone={tone} />
                      {row.collect_pending && <Badge label="采集排队中" tone="neutral" />}
                      {row.verified_at && <small>{formatTimestamp(row.verified_at)}</small>}
                    </span>
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="wb-muted">
              暂无负责的账号。需要修复时，请管理员在「自动采集 → 采集账号负责人」分配。
            </p>
          )
        }
      </QueryView>
    </div>
  );
}

function CollectorLoginHelp({
  copyPrompt,
  copied,
}: {
  copyPrompt: (text: string) => void;
  copied: string | null;
}) {
  return (
    <section id="collector-login" className="wb-guide-section" aria-label="采集登录修复指引">
      <Panel
        title="收到巡检异常：如何更新采集登录"
        subtitle="适用于登录过期、登录缺失或登录账号不对；小红书、蒲公英、视频号支持自助修复。"
      >
        <div className="wb-repair">
          <div className="wb-repair-guide">
            <ol className="wb-timeline">
              <li>
                <strong>在自己的电脑打开 WorkBuddy</strong>
                <p>
                  添加企业技能「采集登录修复」，把下面的话发到对话里，并补充通知中的平台和账号名。首次准备可能需要几分钟，请保持助手运行。
                </p>
              </li>
              <li>
                <strong>打开助手给出的公司授权页，输入校验码</strong>
                <p>
                  用企业微信登录，选择通知中的账号，输入助手显示的校验码（形如
                  ABCD-EFGH）并确认。别人转发给你的修复链接不要确认。小红书和蒲公英的负责人分别设置。
                </p>
              </li>
              <li>
                <strong>在助手弹出的浏览器完成扫码或短信登录</strong>
                <p>
                  确认选中了正确账号，再切换到同一浏览器的「完成登录」标签页，点击「确认已登录目标账号，更新凭证」。验证码由你本人输入。
                </p>
              </li>
              <li>
                <strong>等到助手提示修复成功、复检通过</strong>
                <p>
                  平台会自动为该账号排队一次立即采集，通常几分钟内完成，账号旁会显示「采集排队中」。修复成功本身不代表数据已采集。
                </p>
              </li>
            </ol>
            <PromptCard
              text={repairPrompt}
              label="复制登录修复指令"
              copied={copied === repairPrompt}
              onCopy={copyPrompt}
            />
            <small className="wb-muted">
              找不到企业技能时，请管理员安装或分发「采集登录修复」技能。无需运行命令、创建访问令牌、复制
              Cookie 或手动上传文件；登录修复不需要开通问数连接。
            </small>
            <details className="wb-troubleshooting">
              <summary>修复没有成功，或通知不是登录过期？</summary>
              <ul>
                <li>知乎、京东暂不支持此自助流程，请联系管理员更新登录。</li>
                <li>
                  提示采集正在进行：平台已等待该账号的采集结束仍未完成，稍后再试；授权超时：回到
                  WorkBuddy 重新发起。
                </li>
                <li>校验码不一致：确认这次修复是你本人在 WorkBuddy 发起的，再核对校验码。</li>
                <li>复检失败或账号不匹配：原登录保持不变，核对账号后重试；仍失败时联系管理员。</li>
                <li>
                  仅导出超时、接口或上传失败，且登录有效：联系管理员排查采集故障，无需反复更新登录。
                </li>
              </ul>
            </details>
          </div>
          <MyRepairAccounts />
        </div>
      </Panel>
    </section>
  );
}

function AdminConnections() {
  const cache = useQueryClient();
  const [error, setError] = useState<Error | null>(null);
  const [busy, setBusy] = useState(false);
  const connections = useQuery({
    queryKey: ['workbuddy-admin-tokens'],
    queryFn: () => request(`/admin${base}/tokens`, z.array(adminTokenSchema)),
  });
  const readiness = useQuery({
    queryKey: ['collector-repair-readiness'],
    queryFn: () => request('/collector/repair/readiness', readinessSchema),
    staleTime: 5 * 60_000,
  });
  async function revoke(id: string) {
    setBusy(true);
    setError(null);
    try {
      await request(`/admin${base}/tokens/${id}`, z.null(), { method: 'DELETE' });
      await cache.invalidateQueries({ queryKey: ['workbuddy-admin-tokens'] });
    } catch (e) {
      setError(e as Error);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section id="admin" className="wb-guide-section" aria-label="管理员">
      <Panel
        title="全员 WorkBuddy 连接"
        subtitle="怀疑连接泄露时可单独撤销，无需停用员工账号。停用账号或降为查看者会自动撤销其全部连接。"
      >
        {error && <ErrorState error={error} />}
        <QueryView query={connections}>
          {(rows) =>
            rows.length ? (
              <div className="wb-table-wrap">
                <table className="wb-table">
                  <thead>
                    <tr>
                      <th>员工</th>
                      <th>连接</th>
                      <th>允许查询</th>
                      <th>到期</th>
                      <th aria-label="操作" />
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => (
                      <tr key={row.id}>
                        <td>
                          <strong>{row.user_name || row.user_email}</strong>
                          {row.user_name && <small>{row.user_email}</small>}
                        </td>
                        <td>
                          {row.kind === 'oauth' ? '一键连接' : '手动令牌'}
                          <small>{row.name}</small>
                        </td>
                        <td>{row.scopes.map((s) => scopeLabels[s] ?? s).join('、')}</td>
                        <td>{formatTimestamp(row.authorization_expires_at ?? row.expires_at)}</td>
                        <td>
                          <ConfirmAction
                            title="撤销连接"
                            description={`${row.user_name || row.user_email} 的 WorkBuddy 将立即无法查询，需要重新授权才能再次连接。`}
                            busy={busy}
                            onConfirm={() => void revoke(row.id)}
                          />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="wb-panel-text wb-muted">当前没有有效的员工连接。</p>
            )
          }
        </QueryView>
      </Panel>
      <QueryView query={readiness}>
        {(data) => (
          <div className={`wb-readiness ${data.ready ? 'ready' : ''}`} role="status">
            <MonitorCheck size={18} aria-hidden />
            <div>
              <strong>
                登录修复复检环境：{data.ready ? '就绪' : '未就绪，员工修复会显示复检失败'}
              </strong>
              {!data.ready && (
                <p>
                  {!data.browser_ready &&
                    `后端服务找不到 Chromium${data.browsers_path_configured ? '' : '（未设置 PLAYWRIGHT_BROWSERS_PATH）'}。`}
                  {!data.sessions_writable && '登录态目录不可写。'}
                  重新部署会写入后端 drop-in；详情见 docs/collector.md 与后端日志「collector repair
                  verification error」。
                </p>
              )}
            </div>
          </div>
        )}
      </QueryView>
      {readiness.data?.requests_stalled && (
        <div className="wb-readiness" role="alert">
          <MonitorCheck size={18} aria-hidden />
          <div>
            <strong>有排队超过 15 分钟的立即采集请求</strong>
            <p>
              执行排队请求的定时任务（python -m app.collector run-requests）可能未运行，
              请检查采集服务的调度配置。
            </p>
          </div>
        </div>
      )}
    </section>
  );
}

export default function WorkBuddyPage({ role }: { role: User['role'] }) {
  const canQuery = role !== 'viewer';
  const admin = role === 'admin';
  const cache = useQueryClient();
  const status = useQuery({
    queryKey: ['workbuddy-status'],
    enabled: canQuery,
    queryFn: () =>
      request(
        `${base}/status`,
        z.object({ mcp_enabled: z.boolean(), report_api_ready: z.boolean() }),
      ),
  });
  const tokens = useQuery({
    queryKey: ['workbuddy-tokens'],
    enabled: canQuery,
    queryFn: () => request(`${base}/tokens`, z.array(tokenSchema)),
  });
  const queries = useQuery({
    queryKey: ['workbuddy-queries'],
    enabled: canQuery,
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
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  function copyPrompt(text: string) {
    navigator.clipboard.writeText(text).then(
      () => setCopied(text),
      () => setError(new Error('复制失败，请选中文字手动复制。')),
    );
  }
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

  const feedback = (
    <span className="wb-copy-feedback" role="status">
      {copied ? '已复制，可粘贴到 WorkBuddy 对话中。' : ''}
    </span>
  );

  if (!canQuery) {
    return (
      <div className="workbuddy-page">
        <Heading
          title="WorkBuddy 使用指引"
          description="账号负责人可在自己的电脑上更新采集登录。"
        />
        {error && <ErrorState error={error} />}
        <CollectorLoginHelp copyPrompt={copyPrompt} copied={copied} />
        {feedback}
        <Panel title="问数据与生成周报">
          <p className="wb-panel-text">
            问数连接、周报草稿和已生成周报需要分析员或管理员权限；请联系管理员开通。登录修复只需对应账号的负责人权限。
          </p>
        </Panel>
      </div>
    );
  }

  const sections = [
    ['connect', '连接与问数'],
    ['weekly-reports', '周报'],
    ['collector-login', '采集登录修复'],
    ...(admin ? [['admin', '管理员']] : []),
  ];

  return (
    <div className="workbuddy-page">
      <Heading
        title="WorkBuddy 连接"
        description="在 WorkBuddy 对话中查询业务数据、生成周报草稿，或更新采集登录。"
      />
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
              <Badge
                label={data.mcp_enabled ? '服务就绪' : '待配置'}
                tone={data.mcp_enabled ? 'success' : 'warning'}
              />
            </div>
            <div className="wb-status-card">
              <CheckCircle2 size={20} aria-hidden />
              <div>
                <strong>周报助手</strong>
                <p>
                  {data.report_api_ready
                    ? 'WorkBuddy 自动周报已配置，管理员可在周报页生成并查看投递结果。'
                    : 'WorkBuddy 自动周报待配置；对话草稿和已有周报不受此配置影响。'}
                </p>
              </div>
              <a href="?page=reports" className="wb-link">
                查看周报 →
              </a>
            </div>
          </div>
        )}
      </QueryView>
      <nav className="wb-jump" aria-label="页面分区">
        {sections.map(([id, label]) => (
          <a key={id} href={`#${id}`}>
            {label}
          </a>
        ))}
      </nav>
      {error && <ErrorState error={error} />}
      <section id="connect" className="wb-connect wb-guide-section" aria-label="连接与问数">
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
                <p>
                  点击连接，登录企业微信，核对授权页显示的客户端与时间，再点击「授权并连接
                  WorkBuddy」。
                </p>
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
            <span>
              按你的平台权限读取已授权类别的数据。不提供客户个人信息、账号密钥、采集凭据或任意
              SQL；每次查询记录操作者、参数、结果状态与耗时，不记录数据正文。
            </span>
          </div>
        </Panel>
        <Panel title="我的连接与授权">
          <QueryView query={tokens}>
            {(rows) =>
              rows.length ? (
                <ul className="wb-connections">
                  {rows.map((row) => {
                    const [label, tone] = tokenState(row);
                    return (
                      <li key={row.id}>
                        <div className="wb-connection-info">
                          <div className="wb-connection-title">
                            <strong>{row.name}</strong>
                            <Badge label={label} tone={tone} />
                          </div>
                          <small>
                            {row.authorization_expires_at ? '授权到期' : '到期时间'}{' '}
                            {formatTimestamp(row.authorization_expires_at ?? row.expires_at)}
                          </small>
                          <p>{row.scopes.map((scope) => scopeLabels[scope] ?? scope).join('、')}</p>
                        </div>
                        {!row.revoked_at && (
                          <button disabled={busy} onClick={() => void revoke(row.id)}>
                            撤销 {row.name}
                          </button>
                        )}
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <div className="wb-empty">
                  <ShieldCheck size={26} aria-hidden />
                  <strong>尚未连接 WorkBuddy。</strong>
                  <p>按照左侧步骤完成授权后，连接会显示在这里。</p>
                </div>
              )
            }
          </QueryView>
          <details className="wb-queries">
            <summary>我的最近查询</summary>
            <QueryView query={queries}>
              {(rows) =>
                rows.length ? (
                  <div className="wb-table-wrap">
                    <table className="wb-table">
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
                        {rows.map((row, index) => {
                          const [label, tone] = outcomeLabels[row.outcome ?? ''] ?? [
                            '失败',
                            'danger',
                          ];
                          return (
                            <tr key={row.request_id ?? index}>
                              <td>{formatTimestamp(row.timestamp)}</td>
                              <td>{row.tool}</td>
                              <td>
                                <Badge label={label} tone={tone} />
                              </td>
                              <td>{row.duration_ms == null ? '—' : `${row.duration_ms} ms`}</td>
                              <td>{row.returned_rows ?? '—'}</td>
                              <td className="wb-mono">{row.request_id}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <p className="wb-panel-text wb-muted">暂无查询记录。</p>
                )
              }
            </QueryView>
          </details>
        </Panel>
      </section>
      <Panel title="日常问数据" subtitle="复制到已启用企业数据工作台的 WorkBuddy 对话中。">
        <div className="wb-prompts">
          {dailyPrompts.map((prompt) => (
            <PromptCard
              key={prompt}
              text={prompt}
              label={`复制：${prompt}`}
              copied={copied === prompt}
              onCopy={copyPrompt}
            />
          ))}
        </div>
      </Panel>
      {feedback}
      <section
        id="weekly-reports"
        className="wb-help-grid wb-guide-section"
        aria-label="周报使用指引"
      >
        <Panel title="在对话里生成周报草稿" subtitle="适合自选账号、调整时间范围、反复修改分析。">
          <div className="wb-guide-body">
            <ol className="wb-guide-list">
              <li>先按上方步骤连接企业数据工作台，并在当前 WorkBuddy 对话中启用连接器。</li>
              <li>复制下面的指令，可把日期、账号和分析重点改成自己的需求。</li>
              <li>检查报告的日期、数据缺失和来源，再让 WorkBuddy 修改或导出可编辑 HTML。</li>
            </ol>
            <PromptCard
              text={draftPrompt}
              label="复制周报草稿指令"
              copied={copied === draftPrompt}
              onCopy={copyPrompt}
            />
            <p className="wb-muted">
              对话生成的是草稿，不会自动归档到平台周报页，也不会自动推送企业微信。
            </p>
          </div>
        </Panel>
        <Panel
          title="查看或生成平台正式周报"
          subtitle="平台周报保留历史记录，生成与企业微信投递状态分别显示。"
        >
          <div className="wb-guide-body">
            <ol className="wb-guide-list">
              <li>打开「周报」选择报告周期，查看已有报告；也可把下面的指令发给 WorkBuddy 查询。</li>
              <li>
                需要新报告时，由管理员在周报页点击「生成本周周报」并确认。报告覆盖最近一个完整周，生成后会按配置推送企业微信；已有任务不会重复创建。
              </li>
              <li>
                等待页面自动更新，核对「已推送企业微信」状态。若推送结果待确认，先核对收件人是否收到，再由管理员重试，避免重复发送。生成或推送失败时，告警收件人会收到企业微信提醒。
              </li>
            </ol>
            <PromptCard
              text={readReportPrompt}
              label="复制查看周报指令"
              copied={copied === readReportPrompt}
              onCopy={copyPrompt}
            />
            <div className="wb-guide-foot">
              <span className="wb-muted">
                <FileText size={14} aria-hidden /> 平台正式周报的创建与推送由平台完成。
              </span>
              <a href="?page=reports" className="wb-link">
                {admin ? '前往周报页生成或查看 →' : '查看已生成周报 →'}
              </a>
            </div>
          </div>
        </Panel>
      </section>
      <CollectorLoginHelp copyPrompt={copyPrompt} copied={copied} />
      {admin && <AdminConnections />}
      <details className="wb-advanced">
        <summary>
          <Wrench size={14} aria-hidden /> 高级设置：手动访问令牌（兼容旧连接）
        </summary>
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
          <pre className="wb-config">{config}</pre>
        </Panel>
      </details>
      {admin && (
        <p className="wb-footnote">
          <Users size={13} aria-hidden /> 全员查询记录在「审计日志 → WorkBuddy 与采集修复 /
          数据访问」。
        </p>
      )}
    </div>
  );
}
