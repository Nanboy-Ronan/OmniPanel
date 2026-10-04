"""Orchestrates one weekly-report run: aggregate -> narrate -> render ->
persist -> notify. The only entry point other modules should call.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date

from sqlalchemy import and_, case, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db.models import WeeklyReportRun
from ..utils.wecom_bot import send_wecom_alert
from .narrative import generate_narrative
from .render import render_html
from .weekly_media import build_report_context, week_bounds

logger = logging.getLogger(__name__)


def _wow_arrow(diff: int) -> str:
    if diff > 0:
        return f"▲{diff}"
    if diff < 0:
        return f"▼{abs(diff)}"
    return "持平"


def _summary_lines(context: dict) -> list[str]:
    """Short plain-text summary for the WeCom push. This is the one surface
    guaranteed to actually reach people (the HTML report itself only shows
    up if they go log into the Streamlit 周报 page) — so unlike an early
    version of this function, it carries WoW direction and each account's
    top article, not just three bare numbers with no context."""
    bounds = context["bounds"]
    lines = [f"周报：{bounds.this_week_start} ~ {bounds.this_week_end}"]
    for section in context.get("wechat_sections", []):
        totals = section["totals_this_week"]
        last = section["totals_last_week"]
        reads = totals["read_user_count"]
        diff = reads - last["read_user_count"]
        follower = section.get("follower", {})
        follower_text = f"净增关注 {follower['this_week']['net']}" if follower.get("available") else "关注数据暂缺"
        line = f"【{section['account'].name}】阅读人数 {reads}（{_wow_arrow(diff)}），{follower_text}"
        top_articles = section.get("articles") or []
        if top_articles and top_articles[0]["counts_this_week"]["read_user_count"] > 0:
            line += f"\n　本周最高：{top_articles[0]['title']}"
        lines.append(line)
    for section in context.get("xhs_sections", []):
        s = section["this_week_summary"]
        lines.append(f"【{section['account'].name}】新发布 {s['count']} 篇，累计涨粉 {s['total_new_followers']}")
    for section in context.get("channels_sections", []):
        s = section["this_week_summary"]
        last_s = section["last_week_summary"]
        diff = s["total_plays"] - last_s["total_plays"]
        lines.append(f"【{section['account'].name}(视频号)】播放量 {s['total_plays']}（{_wow_arrow(diff)}），新增粉丝 {s['total_new_fans']}")
    zhihu = context.get("zhihu_section")
    if zhihu:
        s = zhihu["this_week_summary"]
        last_s = zhihu["last_week_summary"]
        diff = s["total_reads"] - last_s["total_reads"]
        lines.append(f"【知乎】阅读量 {s['total_reads']}（{_wow_arrow(diff)}），新发布 {s['count']} 篇")
    for section in context.get("pgy_sections", []):
        s = section["this_week_summary"]
        if s["count"] > 0:
            lines.append(f"【蒲公英·{section['account'].name}】曝光 {s['total_impressions']}，互动 {s['total_interactions']}，笔记 {s['count']} 篇")
    ecom = context.get("ecommerce_section")
    if ecom:
        tw = ecom["this_week_total"]
        lw = ecom["last_week_total"]
        diff = tw["gmv"] - lw["gmv"]
        if tw["order_count"] > 0 or lw["order_count"] > 0:
            lines.append(f"【商城】本周 GMV ¥{tw['gmv']:,.0f}（{_wow_arrow(int(diff))}），订单 {tw['order_count']} 笔")
    return lines


async def generate_weekly_report(session: AsyncSession, reference_date: date | None = None) -> WeeklyReportRun:
    reference_date = reference_date or date.today()
    bounds = week_bounds(reference_date)
    # Serialize generation for the same week across manual and scheduled runs.
    # The lock is released by the commit below (or by rollback on failure).
    await session.execute(
        text("SELECT pg_advisory_xact_lock(579343, :week)"),
        {"week": bounds.this_week_start.toordinal()},
    )
    existing = (
        await session.execute(
            select(WeeklyReportRun).where(WeeklyReportRun.week_start == bounds.this_week_start)
        )
    ).scalar_one_or_none()
    if existing is not None and existing.status == "success" and existing.wecom_sent:
        return existing

    # A rendered report is immutable while its notification is pending. Retrying
    # delivery must not depend on the data source or the AI provider still working.
    if existing is not None and existing.status == "success" and existing.notification_text:
        run_id = existing.id
        notification_text = existing.notification_text
        await session.rollback()
        return await _deliver_report_notification(session, run_id, notification_text, bounds.this_week_start)

    try:
        context = await build_report_context(session, reference_date)
        narrative = await generate_narrative(context)
        html_content = render_html(context, narrative)
        status = "success"
        error_message = None
    except Exception as exc:  # noqa: BLE001 - persist the failure, don't just raise
        logger.error("weekly_report: generation failed for week %s: %s", bounds.this_week_start, exc, exc_info=True)
        await session.rollback()
        context = None
        narrative = None
        html_content = None
        status = "error"
        error_message = str(exc)[:500]

    # Flatten ORM-backed context into plain text while the session is alive.
    # Persist the report before sending anything to recipients.
    if status == "success" and context is not None:
        notification_text = "\n".join(_summary_lines(context))
        if narrative:
            notification_text += f"\n\n{narrative}"
        notification_text += f"\n\n登录 {settings.public_base_url} 查看完整周报（周报页）"
    else:
        # Failure alerts are separate from successful report delivery. They
        # never mark the successful notification as sent.
        if existing is not None and existing.status == "success":
            run_id = existing.id
            await session.rollback()
            return await session.get(WeeklyReportRun, run_id)
        notification_text = None

    stmt = (
        pg_insert(WeeklyReportRun)
        .values(
            week_start=bounds.this_week_start,
            week_end=bounds.this_week_end,
            status=status,
            html_content=html_content,
            narrative=narrative,
            notification_text=notification_text,
            error_message=error_message,
            wecom_sent=False,
        )
        .on_conflict_do_update(
            index_elements=["week_start"],
            set_={
                "week_end": bounds.this_week_end,
                "generated_at": pg_insert(WeeklyReportRun).excluded.generated_at,
                "status": status,
                "html_content": html_content,
                "narrative": narrative,
                "notification_text": notification_text,
                "error_message": error_message,
                "wecom_sent": case(
                    (and_(WeeklyReportRun.status == "success", WeeklyReportRun.wecom_sent.is_(True)), True),
                    else_=False,
                ),
            },
        )
        .returning(WeeklyReportRun.id)
    )
    result = await session.execute(stmt)
    run_id = result.scalar_one()
    await session.commit()

    if status != "success":
        try:
            await asyncio.to_thread(
                send_wecom_alert,
                f"周报生成失败（{bounds.this_week_start} ~ {bounds.this_week_end}），请查看服务日志。",
            )
        except Exception:
            logger.exception("weekly_report: failure alert failed for week %s", bounds.this_week_start)
        return await session.get(WeeklyReportRun, run_id)

    return await _deliver_report_notification(session, run_id, notification_text, bounds.this_week_start)


async def _deliver_report_notification(
    session: AsyncSession, run_id: int, notification_text: str, week_start: date,
) -> WeeklyReportRun:

    # Serialize attempts to send the same week's notification. A concurrent
    # manual trigger waits here and observes the flag set by the first run.
    locked = (
        await session.execute(
            select(WeeklyReportRun).where(WeeklyReportRun.id == run_id).with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    if not locked.wecom_sent:
        try:
            locked.wecom_sent = bool(await asyncio.to_thread(send_wecom_alert, notification_text))
        except Exception:
            logger.exception("weekly_report: notification failed for week %s", week_start)
        await session.commit()
    else:
        await session.rollback()
    return await session.get(WeeklyReportRun, run_id)
