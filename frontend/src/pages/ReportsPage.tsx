import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { z } from 'zod';
import { BellOff, BellRing, CalendarRange, FileText, Sparkles } from 'lucide-react';
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
});
const detail = summary.extend({
  narrative: z.string().nullable(),
  html_content: z.string().nullable(),
  error_message: z.string().nullable(),
});
type Summary = z.infer<typeof summary>;

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
  const action = useAction({ success: '本周周报已生成', error: '周报生成失败' });
  const rows = list.data ?? [];
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
              description="将生成或重试本周报告；如已配置企业微信通知，会发送到已配置群。"
              onConfirm={() =>
                action.mutate({ path: '/admin/reports/weekly/run', timeoutMs: 180000 })
              }
              busy={action.isPending}
            />
          ) : undefined
        }
      />
      {action.isError && <ErrorState error={action.error} />}
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
                      </button>
                    </li>
                  ))}
                </ol>
              </nav>
              {current && <Report key={current.id} row={current} />}
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

function Report({ row }: { row: Summary }) {
  const query = useResource(`/reports/weekly/${row.id}`, detail);
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
      <QueryView query={query}>
        {(data) =>
          !prepared ? (
            <ErrorState error={new Error(data.error_message ?? '报告尚未生成成功。')} />
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
