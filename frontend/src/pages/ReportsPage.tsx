import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { z } from 'zod';
import {
  AlertTriangle,
  BellOff,
  BellRing,
  CalendarRange,
  CheckCircle2,
  FileText,
  Loader2,
  Sparkles,
} from 'lucide-react';
import { prepareReport, type ReportSection } from '../lib/report';
import { useAction, useResource } from '../lib/resources';
import { navigate, useLocationSearch } from '../lib/navigation';
import { formatField, statusTone } from '../lib/presentation';
import { formatTimestamp } from '../lib/time';
import { ConfirmAction, Heading, QueryView } from '../components/workspace';
import { EmptyState, ErrorState, Panel } from '../components/ui';

const summary = z.object({
  id: z.number(),
  week_start: z.string(),
  week_end: z.string(),
  generated_at: z.string().nullish(),
  status: z.string(),
  wecom_sent: z.boolean(),
  job_state: z.string().nullish(),
  narrative_status: z.string().nullish(),
  worker_enabled: z.boolean().nullish(),
});
const detail = summary.extend({
  narrative: z.string().nullable(),
  html_content: z.string().nullable(),
  error_message: z.string().nullable(),
  delivery_error: z.string().nullish(),
});
type Summary = z.infer<typeof summary>;
const pendingStates = ['queued', 'preparing', 'generating', 'ready', 'sending'];
const problemStates = ['failed', 'delivery_failed', 'delivery_unknown'];
// Stop auto-refresh after this long; a stuck job is surfaced instead of polled forever.
const POLL_LIMIT_MS = 15 * 60_000;
const jobLabels: Record<string, string> = {
  queued: '等待生成',
  preparing: '整理数据',
  generating: '生成智能解读',
  ready: '等待推送',
  sending: '正在推送',
  sent: '已推送',
  failed: '生成失败',
  delivery_failed: '推送失败',
  delivery_unknown: '推送结果待确认',
};

/** ISO-8601 week number of the report's Monday. */
function isoWeek(day: string) {
  const date = new Date(`${day}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + 3 - ((date.getUTCDay() + 6) % 7));
  const firstThursday = new Date(Date.UTC(date.getUTCFullYear(), 0, 4));
  return (
    1 +
    Math.round(
      ((date.getTime() - firstThursday.getTime()) / 86400000 -
        3 +
        ((firstThursday.getUTCDay() + 6) % 7)) /
        7,
    )
  );
}
const shortRange = (row: Summary) =>
  `${row.week_start.slice(5).replace('-', '.')} – ${row.week_end.slice(5).replace('-', '.')}`;
const timestamp = (value?: string | null) => formatTimestamp(value);

function jobTone(state: string) {
  if (state === 'sent') return 'success';
  if (pendingStates.includes(state)) return 'progress';
  return state === 'delivery_unknown' ? 'warning' : 'danger';
}

function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`badge tone-${statusTone('status', status) ?? 'neutral'}`}>
      {formatField('status', status)}
    </span>
  );
}

export default function ReportsPage({ admin }: { admin: boolean }) {
  const list = useResource('/reports/weekly?limit=100', z.array(summary));
  const search = useLocationSearch();
  const requested = Number(new URLSearchParams(search).get('report')) || null;
  const action = useAction({ success: '周报请求已处理，请查看报告状态', error: '周报请求失败' });
  const rows = list.data ?? [];
  // Only poll while a WorkBuddy worker can actually advance the job.
  const pending = rows.some(
    (row) => pendingStates.includes(row.job_state ?? '') && row.worker_enabled !== false,
  );
  const { refetch } = list;
  const [pollExpired, setPollExpired] = useState(false);
  useEffect(() => {
    if (!pending) {
      setPollExpired(false);
      return;
    }
    const started = Date.now();
    const timer = window.setInterval(() => {
      if (Date.now() - started > POLL_LIMIT_MS) {
        window.clearInterval(timer);
        setPollExpired(true);
        return;
      }
      void refetch();
    }, 3000);
    return () => window.clearInterval(timer);
  }, [pending, refetch]);
  const id = rows.some((row) => row.id === requested) ? requested! : rows[0]?.id;
  const current = rows.find((row) => row.id === id);
  return (
    <>
      <Heading
        title="周报"
        description="每周一份跨渠道数据报告：公众号、小红书、视频号、知乎、蒲公英与商城。"
        action={
          admin ? (
            <ConfirmAction
              title="生成本周周报"
              description="将生成最近一个完整周的报告，并推送给已配置的企业微信收件人。已有任务不会重复创建。"
              onConfirm={() =>
                action.mutate({ path: '/admin/reports/weekly/run', timeoutMs: 180000 })
              }
              busy={action.isPending}
            />
          ) : undefined
        }
      />
      {action.isError && <ErrorState error={action.error} />}
      {pollExpired && (
        <p className="stale-notice">
          已停止自动刷新：任务处理时间超过 15 分钟，请稍后手动刷新页面。
        </p>
      )}
      <QueryView query={list}>
        {(data) =>
          data.length ? (
            <div className="report-layout">
              <nav className="report-weeks" aria-label="报告周期">
                <div className="report-weeks-head">
                  <CalendarRange size={15} aria-hidden />
                  报告周期
                  <small>{data.length} 周</small>
                </div>
                <ol>
                  {data.map((row) => (
                    <li key={row.id}>
                      <button
                        className={`report-week ${row.id === id ? 'active' : ''}`}
                        aria-current={row.id === id ? 'true' : undefined}
                        onClick={() => {
                          navigate({ report: String(row.id) });
                          window.scrollTo({ top: 0 });
                        }}
                      >
                        <span className="report-week-title">
                          第 {isoWeek(row.week_start)} 周
                          <StatusBadge status={row.status} />
                        </span>
                        <span className="report-week-range">
                          {row.week_start.slice(0, 4)} · {shortRange(row)}
                        </span>
                        {row.job_state && problemStates.includes(row.job_state) && (
                          <span className="report-week-alert">
                            <AlertTriangle size={12} aria-hidden />
                            {jobLabels[row.job_state]}
                          </span>
                        )}
                      </button>
                    </li>
                  ))}
                </ol>
              </nav>
              {current && <Report key={current.id} row={current} admin={admin} />}
            </div>
          ) : (
            <EmptyState title="暂无周报">
              {admin ? '可在右上角生成本周周报。' : '周报每周二自动生成。'}
            </EmptyState>
          )
        }
      </QueryView>
    </>
  );
}

function Report({ row, admin }: { row: Summary; admin: boolean }) {
  const query = useResource(`/reports/weekly/${row.id}`, detail);
  const delivery = useAction({ success: '投递状态已更新', error: '投递操作失败' });
  const { refetch } = query;
  useEffect(() => {
    void refetch();
  }, [row.job_state, refetch]);
  const state = query.data?.job_state ?? row.job_state;
  const pending = pendingStates.includes(state ?? '');
  const uncertain = state === 'delivery_unknown';
  const prepared = useMemo(
    () =>
      query.data?.html_content ? prepareReport(query.data.html_content, { embedded: true }) : null,
    [query.data?.html_content],
  );
  const generated = timestamp(query.data?.generated_at ?? row.generated_at);
  return (
    <article className="report-main" aria-label={`第 ${isoWeek(row.week_start)} 周周报`}>
      <header className="report-head">
        <div>
          <div className="report-head-title">
            <h2>
              第 {isoWeek(row.week_start)} 周 · {row.week_start} 至 {row.week_end}
            </h2>
            <StatusBadge status={row.status} />
          </div>
          <p>
            {generated && <span>生成于 {generated}</span>}
            <span className={row.wecom_sent ? 'report-sent' : 'report-unsent'}>
              {row.wecom_sent ? <BellRing size={13} /> : <BellOff size={13} />}
              {row.wecom_sent ? '已推送企业微信' : '未推送企业微信'}
            </span>
          </p>
        </div>
      </header>
      {state && (
        <div className={`report-job tone-${jobTone(state)}`}>
          <span className="report-job-icon" aria-hidden>
            {state === 'sent' ? (
              <CheckCircle2 size={16} />
            ) : pending ? (
              <Loader2 size={16} />
            ) : (
              <AlertTriangle size={16} />
            )}
          </span>
          <div className="report-job-body">
            <p role="status">{jobLabels[state] ?? state}</p>
            {pending && row.worker_enabled === false && (
              <small>周报任务暂停：当前未启用 WorkBuddy 周报 worker，切回后会继续处理。</small>
            )}
            {query.data?.narrative_status === 'unavailable' && (
              <small>智能解读暂缺，数据报告仍可查看。</small>
            )}
            {query.data?.delivery_error && <small>{query.data.delivery_error}</small>}
            {state === 'failed' && query.data?.error_message && (
              <small>{query.data.error_message}</small>
            )}
          </div>
          {admin && (state === 'delivery_failed' || uncertain || state === 'failed') && (
            <div className="report-job-actions">
              {state === 'failed' ? (
                <ConfirmAction
                  title="重新生成"
                  description="重新整理这一周的数据并生成解读，完成后按配置推送企业微信。"
                  busy={delivery.isPending}
                  onConfirm={() =>
                    delivery.mutate({
                      path: `/admin/reports/weekly/${row.id}/delivery`,
                      body: { action: 'regenerate' },
                    })
                  }
                />
              ) : (
                <ConfirmAction
                  title="重试推送"
                  description={
                    uncertain
                      ? '请先核对企业微信是否已收到。确认重试可能造成重复推送。'
                      : '修正企业微信配置后，将向原收件人重新发送已保存的报告。'
                  }
                  busy={delivery.isPending}
                  onConfirm={() =>
                    delivery.mutate({
                      path: `/admin/reports/weekly/${row.id}/delivery`,
                      body: { action: 'retry', acknowledged_uncertain_delivery: uncertain },
                    })
                  }
                />
              )}
              {uncertain && (
                <ConfirmAction
                  title="确认已收到"
                  description="确认收件人已经收到本份周报，标记为已推送。"
                  busy={delivery.isPending}
                  onConfirm={() =>
                    delivery.mutate({
                      path: `/admin/reports/weekly/${row.id}/delivery`,
                      body: { action: 'mark_sent', acknowledged_uncertain_delivery: true },
                    })
                  }
                />
              )}
            </div>
          )}
        </div>
      )}
      {delivery.isError && <ErrorState error={delivery.error} />}
      <QueryView query={query}>
        {(data) =>
          !prepared ? (
            pending ? (
              <p className="report-pending">周报正在后台处理，完成后此页面会自动更新。</p>
            ) : (
              <ErrorState error={new Error(data.error_message ?? '报告尚未生成成功。')} />
            )
          ) : (
            <>
              {data.status !== 'success' && data.error_message && (
                <p className="stale-notice">部分数据未能生成：{data.error_message}</p>
              )}
              {data.narrative && !prepared.hasNarrative && (
                <Panel title="分析结论">
                  <div className="narrative">
                    <Sparkles size={15} aria-hidden />
                    {data.narrative}
                  </div>
                </Panel>
              )}
              <ReportFrame html={prepared.html} sections={prepared.sections} />
            </>
          )
        }
      </QueryView>
    </article>
  );
}

const TOPBAR = 56;
/**
 * The archived report renders in a script-free frame sized to its content, so
 * the console page is the only scroll container. Section navigation lives here,
 * outside the frame, and scrolls the page to the matching section.
 */
function ReportFrame({ html, sections }: { html: string; sections: ReportSection[] }) {
  const frame = useRef<HTMLIFrameElement>(null);
  const nav = useRef<HTMLElement>(null);
  const [height, setHeight] = useState(640);
  const [active, setActive] = useState('top');
  const measure = useCallback(() => {
    const doc = frame.current?.contentDocument;
    if (!doc?.body) return;
    setHeight(Math.max(doc.body.scrollHeight, doc.documentElement.scrollHeight) + 8);
  }, []);
  const offset = () => TOPBAR + (nav.current?.offsetHeight ?? 0) + 12;
  const sectionTop = (id: string) => {
    const el = frame.current?.contentDocument?.getElementById(id);
    const box = frame.current?.getBoundingClientRect();
    return el && box ? box.top + window.scrollY + el.offsetTop : null;
  };
  useEffect(() => {
    let pending = 0;
    const onResize = () => {
      cancelAnimationFrame(pending);
      pending = requestAnimationFrame(measure);
    };
    const onScroll = () => {
      const line = window.scrollY + offset() + 1;
      let current = 'top';
      for (const section of sections) {
        const top = sectionTop(section.id);
        if (top !== null && top <= line) current = section.id;
      }
      setActive(current);
    };
    // Width changes (window resize, sidebar collapse) reflow the report text.
    let width = 0;
    const observer =
      typeof ResizeObserver === 'undefined'
        ? null
        : new ResizeObserver(([entry]) => {
            if (entry && Math.abs(entry.contentRect.width - width) > 1) {
              width = entry.contentRect.width;
              onResize();
            }
          });
    if (frame.current) observer?.observe(frame.current);
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => {
      observer?.disconnect();
      cancelAnimationFrame(pending);
      window.removeEventListener('scroll', onScroll);
    };
  }, [measure, sections]);
  const jump = (id: string) => {
    const top = id === 'top' ? frame.current?.getBoundingClientRect().top : null;
    const target = id === 'top' ? (top != null ? top + window.scrollY : null) : sectionTop(id);
    if (target !== null) window.scrollTo({ top: target - offset(), behavior: 'smooth' });
  };
  return (
    <>
      {sections.length > 0 && (
        <nav className="report-sections" aria-label="周报分区" ref={nav}>
          {[{ id: 'top', label: '全平台总览', count: null }, ...sections].map((section) => (
            <button
              key={section.id}
              aria-current={active === section.id ? 'true' : undefined}
              onClick={() => jump(section.id)}
            >
              {section.id === 'top' && <FileText size={14} aria-hidden />}
              {section.label}
              {section.count !== null && <span className="report-count">{section.count}</span>}
            </button>
          ))}
        </nav>
      )}
      <iframe
        ref={frame}
        title="周报内容"
        className="report-frame"
        style={{ height }}
        // Same-origin lets the console read the content height; without
        // allow-scripts nothing inside the archived report can execute.
        sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox"
        srcDoc={html}
        onLoad={measure}
      />
    </>
  );
}
