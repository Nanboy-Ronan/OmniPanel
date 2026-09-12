"""Orchestrates one weekly-report run: aggregate -> narrate -> render ->
persist -> notify. The only entry point other modules should call.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date

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
    return lines


async def generate_weekly_report(session: AsyncSession, reference_date: date | None = None) -> WeeklyReportRun:
    reference_date = reference_date or date.today()
    bounds = week_bounds(reference_date)

    try:
        context = await build_report_context(session, reference_date)
        narrative = await generate_narrative(context)
        html_content = render_html(context, narrative)
        status = "success"
        error_message = None
    except Exception as exc:  # noqa: BLE001 - persist the failure, don't just raise
        logger.error("weekly_report: generation failed for week %s: %s", bounds.this_week_start, exc, exc_info=True)
        context = None
        narrative = None
        html_content = None
        status = "error"
        error_message = str(exc)

    # Send the WeCom notification (and build wecom_sent) *before* the upsert
    # below, not after: `context`'s ORM objects (account.name etc.) are only
    # guaranteed live while this same session/transaction holds them, and an
    # object handed back from `insert(...).returning(...)` isn't reliably
    # attached to the session's identity map — mutating it post-commit and
    # re-committing can silently write nothing. Deciding wecom_sent up front
    # and folding it into the single insert sidesteps both problems.
    if status == "success" and context is not None:
        summary_text = "\n".join(_summary_lines(context))
        if narrative:
            summary_text += f"\n\n{narrative}"
        summary_text += f"\n\n登录 {settings.public_base_url} 查看完整周报（周报页）"
        wecom_sent = await asyncio.to_thread(send_wecom_alert, summary_text)
    else:
        wecom_sent = await asyncio.to_thread(
            send_wecom_alert,
            f"周报生成失败（{bounds.this_week_start} ~ {bounds.this_week_end}）：{error_message}",
        )

    stmt = (
        pg_insert(WeeklyReportRun)
        .values(
            week_start=bounds.this_week_start,
            week_end=bounds.this_week_end,
            status=status,
            html_content=html_content,
            narrative=narrative,
            error_message=error_message,
            wecom_sent=wecom_sent,
        )
        .on_conflict_do_update(
            index_elements=["week_start"],
            set_={
                "week_end": bounds.this_week_end,
                "generated_at": pg_insert(WeeklyReportRun).excluded.generated_at,
                "status": status,
                "html_content": html_content,
                "narrative": narrative,
                "error_message": error_message,
                "wecom_sent": wecom_sent,
            },
        )
        .returning(WeeklyReportRun)
    )
    result = await session.execute(stmt)
    await session.commit()
    return result.scalar_one()
