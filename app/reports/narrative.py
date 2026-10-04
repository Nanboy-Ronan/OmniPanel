"""Optional one-paragraph LLM narration on top of the deterministic weekly
report data (app/reports/weekly_media.py).

This is deliberately thin: the report's numbers are already complete and
correct without this module — narration is a summary/highlight layer, not a
data source. Never configured, or a failed call, both degrade to "no
narrative section" rather than blocking report generation.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

from ..config import settings
from ..utils.nl_to_sql import PROVIDERS

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """你是一个帮内部团队解读自媒体和商城周报数据的助手。
输入是本周的结构化统计数字（已经算好，不需要你验证或重新计算）。
涵盖公众号、小红书、视频号、知乎、蒲公英（KOL投放）和商城（有赞/京东/天猫）。
用 3-5 句中文写出本周的亮点、值得关注的异常或变化趋势，语气客观、简洁，
不要复述所有数字，只挑重要的说。如果某个平台的数据不完整或缺失，直接说明，
不要编造。不要使用 markdown 格式，输出纯文本。"""


def _summarize_for_prompt(context: dict[str, Any]) -> str:
    """Compress build_report_context()'s output into a compact text summary
    for the model — the model narrates, it doesn't compute."""
    bounds = context["bounds"]
    lines = [f"周期：{bounds.this_week_start} ~ {bounds.this_week_end}（上周：{bounds.last_week_start} ~ {bounds.last_week_end}）"]

    for section in context.get("wechat_sections", []):
        account = section["account"]
        lines.append(f"\n【公众号】{account.name}")
        lines.append(f"本周阅读人数合计：{section['totals_this_week']['read_user_count']}（上周：{section['totals_last_week']['read_user_count']}）")
        lines.append(f"本周点赞：{section['totals_this_week']['like_user']}，评论：{section['totals_this_week']['comment_count']}")
        follower = section.get("follower", {})
        if follower.get("available"):
            tw = follower["this_week"]
            lines.append(f"本周新增关注 {tw['new_user']}，取消关注 {tw['cancel_user']}，净增 {tw['net']}")
        else:
            lines.append(f"关注数据获取失败：{follower.get('error')}")
        top_articles = section["articles"][:5]
        if top_articles:
            lines.append("本周阅读量最高的文章：" + "、".join(a["title"] for a in top_articles))

    for section in context.get("xhs_sections", []):
        account = section["account"]
        lines.append(f"\n【小红书】{account.name}")
        s = section["this_week_summary"]
        lines.append(f"本周新发布 {s['count']} 篇，平均观看量 {s['avg_views']}，累计涨粉 {s['total_new_followers']}")

    for section in context.get("channels_sections", []):
        account = section["account"]
        lines.append(f"\n【视频号】{account.name}")
        s = section["this_week_summary"]
        ls = section["last_week_summary"]
        lines.append(f"本周新发布 {s['count']} 条视频，总播放量 {s['total_plays']}（上周 {ls['total_plays']}），新增粉丝 {s['total_new_fans']}")

    zhihu = context.get("zhihu_section")
    if zhihu:
        lines.append("\n【知乎】")
        s = zhihu["this_week_summary"]
        ls = zhihu["last_week_summary"]
        lines.append(f"本周新发布 {s['count']} 篇，总阅读量 {s['total_reads']}（上周 {ls['total_reads']}）")

    for section in context.get("pgy_sections", []):
        if section["this_week_summary"]["count"] > 0:
            lines.append(f"\n【蒲公英】{section['account'].name}")
            s = section["this_week_summary"]
            lines.append(f"本周 {s['count']} 篇笔记，总曝光 {s['total_impressions']}，总互动 {s['total_interactions']}")

    ecom = context.get("ecommerce_section")
    if ecom:
        tw = ecom["this_week_total"]
        lw = ecom["last_week_total"]
        if tw["order_count"] > 0 or lw["order_count"] > 0:
            lines.append("\n【商城】")
            lines.append(f"本周总 GMV ¥{tw['gmv']:,.0f}（上周 ¥{lw['gmv']:,.0f}），订单 {tw['order_count']} 笔（上周 {lw['order_count']}）")

    return "\n".join(lines)


def _resolve_model() -> str:
    """Reuse the same model-id registry as 中文问数据 (nl_to_sql.py) instead
    of pinning a second, possibly-stale model id here."""
    models = PROVIDERS["anthropic"].models
    if settings.nl_sql_model and settings.nl_sql_model in models:
        return settings.nl_sql_model
    return models[0]


async def generate_narrative(context: dict[str, Any]) -> str | None:
    if not settings.anthropic_api_key:
        return None
    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        logger.info("weekly_report: anthropic SDK not installed, skipping narrative")
        return None

    prompt = _summarize_for_prompt(context)
    try:
        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        resp = await client.messages.create(
            model=_resolve_model(),
            max_tokens=500,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(
            getattr(block, "text", "") for block in resp.content
            if getattr(block, "type", None) == "text"
        ).strip()
        return text or None
    except Exception as exc:  # noqa: BLE001 - narration must never break the report
        logger.warning("weekly_report: narrative generation failed: %s", exc, exc_info=True)
        return None
