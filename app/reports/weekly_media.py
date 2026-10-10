"""Deterministic data aggregation for the weekly 公众号 + 小红书 report.

Every function here is a pure(ish) query against the existing tables — no
LLM calls, no network calls except the one WeChat follower-summary fetch
(which is itself wrapped so a failure degrades that one section instead of
aborting the report). This is deliberate: the report's numbers must be
reproducible and unit-testable without touching an API key — narration is a
thin layer added on top in app/reports/narrative.py, never the source of a
number.

Two production facts drive the shape of this module (verified live against
a real WeChat account and the real DB before writing any of this):

1. MediaPostMetricDaily rows are CUMULATIVE snapshots, not daily deltas —
   read_user_count etc. only ever go up across a post's rows. So a week's
   value is a difference between two snapshots (`_as_of(end) - _as_of(start
   - 1 day)`), never a sum across the week's rows (summing would inflate
   reads by ~7x). Rate fields (read_avg_time, read_finish_rate) are
   themselves cumulative-to-date rolling values, so the right "as of this
   week" reading is the latest snapshot, not a mean across days.

2. WeChat's API has no per-article follow attribution anywhere (confirmed
   against the real getusersummary/getarticletotaldetail payloads) — only
   account-level new/cancel follows broken down by acquisition scene. Don't
   try to reconstruct per-article attribution here; it doesn't exist.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings as app_settings
from ..connectors.wechat_official import WeChatOfficialClient
from ..db.models import (
    CollectorRun,
    MediaAccount,
    MediaPost,
    MediaPostMetricDaily,
    MediaSyncRun,
    Order,
    PgyNote,
    WxChannelsAccount,
    WxChannelsPost,
    XhsAccount,
    XhsAccountDailyMetric,
    XhsAudienceSourceDaily,
    XhsPost,
    ZhihuPost,
)
from ..db.order_status import counted, net_amount

logger = logging.getLogger(__name__)

WECHAT_PLATFORM = "wechat_official"

# Cumulative-to-date counters — a week's value is a snapshot difference.
_COUNT_FIELDS = (
    "read_user_count", "read_count", "like_user",
    "share_user_count", "comment_count", "collection_user",
)
# Cumulative-to-date rates/averages — a week's value is the latest snapshot,
# not a mean across the week's rows.
_RATE_FIELDS = ("read_avg_time", "read_finish_rate")


@dataclass(frozen=True)
class WeekBounds:
    this_week_start: date
    this_week_end: date
    last_week_start: date
    last_week_end: date


def week_bounds(reference_date: date) -> WeekBounds:
    """Bounds for the most recently *completed* ISO week (Mon-Sun) as of
    reference_date, and the week before it.

    Called "本周"/"上周" in the report regardless of which day it's
    generated on — a report generated on a Tuesday still reports on "本周"
    = the week that ended the previous Sunday, not the in-progress week.
    """
    monday_of_current_week = reference_date - timedelta(days=reference_date.weekday())
    this_week_end = monday_of_current_week - timedelta(days=1)
    this_week_start = this_week_end - timedelta(days=6)
    last_week_end = this_week_start - timedelta(days=1)
    last_week_start = last_week_end - timedelta(days=6)
    return WeekBounds(this_week_start, this_week_end, last_week_start, last_week_end)


def is_week_due(week_end: date, today: date, lag_days: int = 2) -> bool:
    """True once WeChat's 1-2 day DataCube lag has had time to clear for the
    week ending on week_end. Used by the scheduler gate instead of a fixed
    weekday, so a restart or clock drift can't silently skip a week.
    """
    return (today - week_end).days >= lag_days


async def _snapshots_as_of(
    session: AsyncSession, post_ids: list[int], target_date: date
) -> dict[int, MediaPostMetricDaily]:
    """Latest metric row at or before target_date, per post — one bulk
    Postgres DISTINCT ON query rather than one query per post."""
    if not post_ids:
        return {}
    stmt = (
        select(MediaPostMetricDaily)
        .where(
            MediaPostMetricDaily.post_id.in_(post_ids),
            MediaPostMetricDaily.metric_date <= target_date,
        )
        .distinct(MediaPostMetricDaily.post_id)
        .order_by(MediaPostMetricDaily.post_id, MediaPostMetricDaily.metric_date.desc())
    )
    result = await session.execute(stmt)
    return {row.post_id: row for row in result.scalars().all()}


def _count_delta(end_row, start_row, field: str) -> int:
    # Some legacy-synced rows have NULL for the newer getarticletotaldetail
    # fields (like_user/comment_count/collection_user are nullable=True with
    # no server_default — see models.py) — treat missing as 0, not a crash.
    end_val = int(getattr(end_row, field) or 0) if end_row is not None else 0
    start_val = int(getattr(start_row, field) or 0) if start_row is not None else 0
    return max(0, end_val - start_val)


async def _daily_read_deltas(
    session: AsyncSession, post_id: int, window_start: date, window_end: date
) -> list[tuple[date, int]]:
    """Per-day new-reads series for one post over [window_start, window_end],
    computed from the cumulative snapshots (see module docstring) — not the
    raw cumulative curve, which would just look like a monotonic ramp and
    hide the real day-to-day pattern."""
    baseline = await _snapshots_as_of(session, [post_id], window_start - timedelta(days=1))
    prev_val = int(getattr(baseline.get(post_id), "read_user_count", 0) or 0)

    stmt = (
        select(MediaPostMetricDaily.metric_date, MediaPostMetricDaily.read_user_count)
        .where(
            MediaPostMetricDaily.post_id == post_id,
            MediaPostMetricDaily.metric_date.between(window_start, window_end),
        )
        .order_by(MediaPostMetricDaily.metric_date)
    )
    rows = (await session.execute(stmt)).all()
    deltas: list[tuple[date, int]] = []
    for metric_date, value in rows:
        value = int(value or 0)
        deltas.append((metric_date, max(0, value - prev_val)))
        prev_val = value
    return deltas


async def _build_follower_section(
    wechat_client: WeChatOfficialClient | None, bounds: WeekBounds
) -> dict[str, Any]:
    """Account-level follower gain/loss + acquisition-scene breakdown.

    No per-article attribution — WeChat's API doesn't expose that anywhere
    (see module docstring). Network call is wrapped: a failure here degrades
    only this section, never the whole report.
    """
    if wechat_client is None:
        return {"available": False, "error": "账号未配置 app_id/app_secret"}
    try:
        rows = await asyncio.to_thread(
            wechat_client.fetch_user_summary_rows, bounds.last_week_start, bounds.this_week_end
        )
    except Exception as exc:  # noqa: BLE001 - degrade this section only
        logger.warning("weekly_report: getusersummary failed: %s", exc, exc_info=True)
        return {"available": False, "error": str(exc)}

    def _sum(field: str, start: date, end: date) -> int:
        return sum(r[field] for r in rows if start <= r["ref_date"] <= end)

    by_scene: dict[str, int] = {}
    daily: dict[date, dict[str, int]] = {}
    for r in rows:
        if not (bounds.this_week_start <= r["ref_date"] <= bounds.this_week_end):
            continue
        by_scene[r["user_source_label"]] = by_scene.get(r["user_source_label"], 0) + r["new_user"]
        day = daily.setdefault(r["ref_date"], {"new_user": 0, "cancel_user": 0})
        day["new_user"] += r["new_user"]
        day["cancel_user"] += r["cancel_user"]

    this_new = _sum("new_user", bounds.this_week_start, bounds.this_week_end)
    this_cancel = _sum("cancel_user", bounds.this_week_start, bounds.this_week_end)
    last_new = _sum("new_user", bounds.last_week_start, bounds.last_week_end)
    last_cancel = _sum("cancel_user", bounds.last_week_start, bounds.last_week_end)

    return {
        "available": True,
        "this_week": {"new_user": this_new, "cancel_user": this_cancel, "net": this_new - this_cancel},
        "last_week": {"new_user": last_new, "cancel_user": last_cancel, "net": last_new - last_cancel},
        "by_scene": sorted(by_scene.items(), key=lambda kv: kv[1], reverse=True),
        "daily_table": sorted(daily.items()),
    }


async def build_wechat_section(
    session: AsyncSession,
    account: MediaAccount,
    bounds: WeekBounds,
    wechat_client: WeChatOfficialClient | None,
    *,
    trend_top_n: int = 12,  # matches the template's top_articles = section.articles[:12]
) -> dict[str, Any]:
    window_start = bounds.last_week_start - timedelta(days=1)
    window_end = bounds.this_week_end

    posts_with_activity = (
        select(MediaPostMetricDaily.post_id)
        .where(MediaPostMetricDaily.metric_date.between(window_start, window_end))
        .distinct()
    )
    posts_stmt = select(MediaPost).where(
        MediaPost.account_id == account.id,
        (MediaPost.id.in_(posts_with_activity)) | (MediaPost.publish_date >= bounds.this_week_start),
    )
    posts = (await session.execute(posts_stmt)).scalars().all()
    post_ids = [p.id for p in posts]

    snap_this_end = await _snapshots_as_of(session, post_ids, bounds.this_week_end)
    snap_this_baseline = await _snapshots_as_of(session, post_ids, bounds.this_week_start - timedelta(days=1))
    snap_last_end = await _snapshots_as_of(session, post_ids, bounds.last_week_end)
    snap_last_baseline = await _snapshots_as_of(session, post_ids, bounds.last_week_start - timedelta(days=1))

    articles: list[dict[str, Any]] = []
    totals_this = {f: 0 for f in _COUNT_FIELDS}
    totals_last = {f: 0 for f in _COUNT_FIELDS}
    first_snapshot_used: date | None = None
    last_snapshot_used: date | None = None

    for post in posts:
        end_row = snap_this_end.get(post.id)
        start_row = snap_this_baseline.get(post.id)
        last_end_row = snap_last_end.get(post.id)
        last_start_row = snap_last_baseline.get(post.id)

        # A post first published this week has no (and shouldn't have a)
        # baseline snapshot before week_start — its full end-of-week value
        # *is* the complete weekly figure. Only flag "incomplete" when an
        # *older* post is missing the baseline it should have (a real gap,
        # e.g. sync window didn't reach back far enough).
        is_new_this_week = bool(
            post.publish_date and bounds.this_week_start <= post.publish_date <= bounds.this_week_end
        )
        complete = is_new_this_week or start_row is not None or end_row is None

        this_counts = {f: _count_delta(end_row, start_row, f) for f in _COUNT_FIELDS}
        last_counts = {f: _count_delta(last_end_row, last_start_row, f) for f in _COUNT_FIELDS}
        for f in _COUNT_FIELDS:
            totals_this[f] += this_counts[f]
            totals_last[f] += last_counts[f]

        rates = {f: (getattr(end_row, f) if end_row is not None else None) for f in _RATE_FIELDS}

        if end_row is not None and (last_snapshot_used is None or end_row.metric_date > last_snapshot_used):
            last_snapshot_used = end_row.metric_date
        if start_row is not None and (first_snapshot_used is None or start_row.metric_date < first_snapshot_used):
            first_snapshot_used = start_row.metric_date

        read_avg_time = getattr(end_row, "read_avg_time", None) if end_row else None
        read_finish_rate = getattr(end_row, "read_finish_rate", None) if end_row else None
        read_subscribe_user = getattr(end_row, "read_subscribe_user", None) if end_row else None

        articles.append(
            {
                "post_id": post.id,
                "title": post.title,
                "publish_date": post.publish_date,
                "is_new_this_week": is_new_this_week,
                "complete": complete,
                "counts_this_week": this_counts,
                "counts_last_week": last_counts,
                "rates_as_of_this_week": rates,
                "read_avg_time": read_avg_time,
                "read_finish_rate": read_finish_rate,
                "new_followers": read_subscribe_user,
            }
        )

    articles.sort(key=lambda a: a["counts_this_week"]["read_user_count"], reverse=True)

    for article in articles[:trend_top_n]:
        deltas = await _daily_read_deltas(session, article["post_id"], window_start, window_end)
        article["daily_reads"] = deltas

    follower_section = await _build_follower_section(wechat_client, bounds)

    this_week_articles = [a for a in articles if a["is_new_this_week"]]
    older_articles = [a for a in articles if not a["is_new_this_week"]]

    return {
        "account": account,
        "totals_this_week": totals_this,
        "totals_last_week": totals_last,
        "articles": articles,
        "this_week_articles": this_week_articles,
        "older_articles": older_articles,
        "follower": follower_section,
        "snapshot_range_used": (first_snapshot_used, last_snapshot_used),
    }


def _avg(posts: list[XhsPost], field: str) -> float | None:
    values = [getattr(p, field) for p in posts if getattr(p, field) is not None]
    return sum(values) / len(values) if values else None


def _summarize_xhs_posts(posts: list[XhsPost]) -> dict[str, Any]:
    return {
        "count": len(posts),
        "total_views": sum(p.views or 0 for p in posts),
        "avg_views": _avg(posts, "views"),
        "avg_watch_time": _avg(posts, "avg_watch_time"),
        "avg_new_followers": _avg(posts, "new_followers"),
        "total_new_followers": sum(p.new_followers or 0 for p in posts),
    }


async def build_xhs_section(session: AsyncSession, account: XhsAccount, bounds: WeekBounds) -> dict[str, Any]:
    this_week_posts = (
        await session.execute(
            select(XhsPost).where(
                XhsPost.account_id == account.id,
                XhsPost.publish_date.between(bounds.this_week_start, bounds.this_week_end),
            )
        )
    ).scalars().all()
    last_week_posts = (
        await session.execute(
            select(XhsPost).where(
                XhsPost.account_id == account.id,
                XhsPost.publish_date.between(bounds.last_week_start, bounds.last_week_end),
            )
        )
    ).scalars().all()

    posts_detail = []
    for p in sorted(this_week_posts, key=lambda p: p.views or 0, reverse=True):
        posts_detail.append(
            {
                "title": p.title,
                "publish_date": p.publish_date,
                "views": p.views,
                "avg_watch_time": p.avg_watch_time,
                # Not a platform-reported figure — see module-level caveat below.
                "estimated_total_watch_time": (p.avg_watch_time or 0) * (p.views or 0),
                "new_followers": p.new_followers,
                "likes": p.likes,
                "comments": p.comments,
                "collects": p.collects,
                "shares": p.shares,
            }
        )

    thirty_days_ago = bounds.this_week_end - timedelta(days=30)
    top_follower_posts = (
        await session.execute(
            select(XhsPost)
            .where(XhsPost.account_id == account.id, XhsPost.publish_date >= thirty_days_ago)
            .order_by(XhsPost.new_followers.desc().nullslast())
            .limit(5)
        )
    ).scalars().all()

    # Query XhsAccountDailyMetric for follower dynamics
    daily_metrics_this = (
        await session.execute(
            select(XhsAccountDailyMetric).where(
                XhsAccountDailyMetric.account_id == account.id,
                XhsAccountDailyMetric.metric_date.between(bounds.this_week_start, bounds.this_week_end),
            )
        )
    ).scalars().all()

    daily_metrics_last = (
        await session.execute(
            select(XhsAccountDailyMetric).where(
                XhsAccountDailyMetric.account_id == account.id,
                XhsAccountDailyMetric.metric_date.between(bounds.last_week_start, bounds.last_week_end),
            )
        )
    ).scalars().all()

    follower_dynamics = None
    if daily_metrics_this:
        rise_fans = sum(m.rise_fans_count or 0 for m in daily_metrics_this)
        loss_fans = sum(m.loss_fans_count or 0 for m in daily_metrics_this)
        net_rise_fans = sum(m.net_rise_fans_count or 0 for m in daily_metrics_this)

        last_rise_fans = sum(m.rise_fans_count or 0 for m in daily_metrics_last)
        last_loss_fans = sum(m.loss_fans_count or 0 for m in daily_metrics_last)
        last_net_rise_fans = sum(m.net_rise_fans_count or 0 for m in daily_metrics_last)

        valid_full_view = [m.video_full_view_rate for m in daily_metrics_this if m.video_full_view_rate is not None]
        avg_full_view_rate = (sum(valid_full_view) / len(valid_full_view)) if valid_full_view else None

        follower_dynamics = {
            "this_week": {
                "rise_fans": rise_fans,
                "loss_fans": loss_fans,
                "net_rise_fans": net_rise_fans,
                "avg_full_view_rate": avg_full_view_rate,
            },
            "last_week": {
                "rise_fans": last_rise_fans,
                "loss_fans": last_loss_fans,
                "net_rise_fans": last_net_rise_fans,
            },
        }

    # Query XhsAudienceSourceDaily for traffic source breakdown
    latest_source_date = (
        await session.execute(
            select(func.max(XhsAudienceSourceDaily.snapshot_date)).where(
                XhsAudienceSourceDaily.account_id == account.id
            )
        )
    ).scalar_one_or_none()

    audience_sources = []
    if latest_source_date:
        source_rows = (
            await session.execute(
                select(XhsAudienceSourceDaily).where(
                    XhsAudienceSourceDaily.account_id == account.id,
                    XhsAudienceSourceDaily.snapshot_date == latest_source_date,
                    XhsAudienceSourceDaily.window_label == "seven",
                ).order_by(XhsAudienceSourceDaily.value_pct.desc().nullslast())
            )
        ).scalars().all()
        audience_sources = [
            {"title": s.title or f"来源{s.source_type}", "value_pct": s.value_pct}
            for s in source_rows
            if s.value_pct and s.value_pct > 0
        ]

    not_collected = ["完播率", "无法归因单篇涨粉来源"]
    if not follower_dynamics:
        not_collected.extend(["取消关注", "净增关注"])
    if not audience_sources:
        not_collected.append("观看来源占比")

    return {
        "account": account,
        "this_week_posts": posts_detail,
        "this_week_summary": _summarize_xhs_posts(this_week_posts),
        "last_week_summary": _summarize_xhs_posts(last_week_posts),
        "follower_dynamics": follower_dynamics,
        "audience_sources": audience_sources,
        "top_follower_sources": [
            {"title": p.title, "publish_date": p.publish_date, "new_followers": p.new_followers}
            for p in top_follower_posts
        ],
        "not_collected": not_collected,
        "wow_caveat": "小红书数据对比为同期发布内容表现，粉丝变动来源于数据概览采集器。",
        "estimated_watch_time_caveat": "观看总时长为推算值（人均观看时长 × 观看量），非平台直接给出的数值。",
        "follower_source_caveat": "单篇涨粉数值为发布至今的累计带粉数。",
    }


# Zhihu / 视频号 posts only carry the platform's current cumulative totals
# (overwritten on every upload, no per-day history), so last week's posts
# have had ~7 more days to accumulate than this week's. An equal-age
# comparison (e.g. first 7 days) is impossible without history; the least
# invasive correct option is to keep both numbers but stop presenting the
# cumulative ones as a week-over-week change. Post count stays comparable.
CUMULATIVE_COHORT_CAVEAT = "本周与上周均为「当周新发布内容」截至今天的累计数据；上周内容多积累了约 7 天，累计指标不可直接比较涨跌（平台导出只有累计快照，无法按同等发布天数对齐），仅发布篇数可直接对比。"


# ── 视频号 (WeChat Channels) ────────────────────────────────────────────────

def _summarize_channels_posts(posts: list[WxChannelsPost]) -> dict[str, Any]:
    return {
        "count": len(posts),
        "total_plays": sum(p.plays or 0 for p in posts),
        "total_recommends": sum(p.recommends or 0 for p in posts),
        "total_comments": sum(p.comments or 0 for p in posts),
        "total_shares": sum(p.shares or 0 for p in posts),
        "total_new_fans": sum(p.new_fans or 0 for p in posts),
        "avg_completion_rate": (
            sum(p.completion_rate or 0 for p in posts) / len(posts)
            if posts else None
        ),
        "avg_watch_duration": (
            sum(p.avg_watch_duration or 0 for p in posts) / len(posts)
            if posts else None
        ),
    }


async def build_channels_section(
    session: AsyncSession, account: WxChannelsAccount, bounds: WeekBounds
) -> dict[str, Any]:
    this_week_posts = (
        await session.execute(
            select(WxChannelsPost).where(
                WxChannelsPost.account_id == account.id,
                WxChannelsPost.publish_date.between(bounds.this_week_start, bounds.this_week_end),
            )
        )
    ).scalars().all()
    last_week_posts = (
        await session.execute(
            select(WxChannelsPost).where(
                WxChannelsPost.account_id == account.id,
                WxChannelsPost.publish_date.between(bounds.last_week_start, bounds.last_week_end),
            )
        )
    ).scalars().all()

    posts_detail = []
    for p in sorted(this_week_posts, key=lambda p: p.plays or 0, reverse=True):
        posts_detail.append({
            "title": p.title,
            "publish_date": p.publish_date,
            "plays": p.plays,
            "recommends": p.recommends,
            "likes_thumb": p.likes_thumb,
            "comments": p.comments,
            "shares": p.shares,
            "new_fans": p.new_fans,
            "avg_watch_duration": p.avg_watch_duration,
            "completion_rate": p.completion_rate,
        })

    return {
        "account": account,
        "this_week_posts": posts_detail,
        "this_week_summary": _summarize_channels_posts(this_week_posts),
        "last_week_summary": _summarize_channels_posts(last_week_posts),
        "wow_caveat": CUMULATIVE_COHORT_CAVEAT,
    }


# ── 知乎 ────────────────────────────────────────────────────────────────────

def _summarize_zhihu_posts(posts: list[ZhihuPost]) -> dict[str, Any]:
    return {
        "count": len(posts),
        "total_reads": sum(p.reads or 0 for p in posts),
        "total_likes": sum(p.likes or 0 for p in posts),
        "total_comments": sum(p.comments or 0 for p in posts),
        "total_collects": sum(p.collects or 0 for p in posts),
        "total_shares": sum(p.shares or 0 for p in posts),
    }


async def build_zhihu_section(
    session: AsyncSession, bounds: WeekBounds
) -> dict[str, Any] | None:
    """Zhihu has no account model — it's a single-account platform in this
    system. Returns None when there's no zhihu data at all."""
    total_count = (await session.execute(
        select(func.count(ZhihuPost.id))
    )).scalar_one()
    if total_count == 0:
        return None

    this_week_posts = (
        await session.execute(
            select(ZhihuPost).where(
                ZhihuPost.publish_date.between(bounds.this_week_start, bounds.this_week_end),
            )
        )
    ).scalars().all()
    last_week_posts = (
        await session.execute(
            select(ZhihuPost).where(
                ZhihuPost.publish_date.between(bounds.last_week_start, bounds.last_week_end),
            )
        )
    ).scalars().all()

    posts_detail = []
    for p in sorted(this_week_posts, key=lambda p: p.reads or 0, reverse=True):
        posts_detail.append({
            "content_type": p.content_type,
            "title": p.title,
            "publish_date": p.publish_date,
            "reads": p.reads,
            "likes": p.likes,
            "comments": p.comments,
            "collects": p.collects,
            "shares": p.shares,
        })

    return {
        "this_week_posts": posts_detail,
        "this_week_summary": _summarize_zhihu_posts(this_week_posts),
        "last_week_summary": _summarize_zhihu_posts(last_week_posts),
        "wow_caveat": CUMULATIVE_COHORT_CAVEAT,
    }


# ── 蒲公英 (Pugongying) ─────────────────────────────────────────────────────

def _summarize_pgy_notes(notes: list[PgyNote]) -> dict[str, Any]:
    non_null_cpr = [n.cost_per_read for n in notes if n.cost_per_read is not None]
    non_null_cpi = [n.cost_per_interaction for n in notes if n.cost_per_interaction is not None]
    return {
        "count": len(notes),
        "total_impressions": sum(n.impressions or 0 for n in notes),
        "total_reads": sum(n.reads or 0 for n in notes),
        "total_interactions": sum(n.interactions or 0 for n in notes),
        "total_follows": sum(n.follows or 0 for n in notes),
        "avg_cost_per_read": (
            sum(non_null_cpr) / len(non_null_cpr) if non_null_cpr else None
        ),
        "avg_cost_per_interaction": (
            sum(non_null_cpi) / len(non_null_cpi) if non_null_cpi else None
        ),
    }


async def build_pgy_section(
    session: AsyncSession, account, bounds: WeekBounds
) -> dict[str, Any]:
    this_week_notes_orm = (
        await session.execute(
            select(PgyNote).where(
                PgyNote.account_id == account.id,
                PgyNote.publish_date.between(bounds.this_week_start, bounds.this_week_end),
            )
        )
    ).scalars().all()
    last_week_notes_orm = (
        await session.execute(
            select(PgyNote).where(
                PgyNote.account_id == account.id,
                PgyNote.publish_date.between(bounds.last_week_start, bounds.last_week_end),
            )
        )
    ).scalars().all()

    notes_detail = []
    for n in sorted(this_week_notes_orm, key=lambda n: n.impressions or 0, reverse=True):
        notes_detail.append({
            "note_title": n.note_title,
            "blogger_nickname": n.blogger_nickname,
            "publish_date": n.publish_date,
            "impressions": n.impressions,
            "reads": n.reads,
            "interactions": n.interactions,
            "likes": n.likes,
            "comments": n.comments,
            "collects": n.collects,
            "shares": n.shares,
            "follows": n.follows,
            "cost_per_read": n.cost_per_read,
            "cost_per_interaction": n.cost_per_interaction,
        })

    return {
        "account": account,
        "this_week_notes": notes_detail,
        "this_week_summary": _summarize_pgy_notes(this_week_notes_orm),
        "last_week_summary": _summarize_pgy_notes(last_week_notes_orm),
    }


# ── 商城 (E-commerce: 有赞 + 京东 + 天猫) ──────────────────────────────────

_PLATFORM_LABELS = {"youzan": "有赞", "jd": "京东", "tmall": "天猫"}


async def _aggregate_orders(
    session: AsyncSession, start: date, end: date
) -> dict[str, Any]:
    """Aggregate orders across all platforms for [start, end]."""
    rows = (
        await session.execute(
            select(
                Order.platform,
                func.count(Order.id).label("order_count"),
                func.coalesce(func.sum(net_amount()), 0).label("gmv"),
                func.count(func.distinct(Order.sku)).label("sku_count"),
            )
            .where(Order.order_date.between(start, end), counted())
            .group_by(Order.platform)
        )
    ).all()

    total = {"order_count": 0, "gmv": 0, "sku_count": 0}
    platforms = []
    for row in rows:
        total["order_count"] += row.order_count
        total["gmv"] += float(row.gmv)
        total["sku_count"] += row.sku_count
        platforms.append({
            "platform": _PLATFORM_LABELS.get(row.platform, row.platform),
            "this_week": {"order_count": row.order_count, "gmv": float(row.gmv)},
        })
    return {"total": total, "platforms": platforms}


async def build_ecommerce_section(
    session: AsyncSession, bounds: WeekBounds
) -> dict[str, Any] | None:
    """Combined e-commerce section across all platforms using the unified
    orders table."""
    total_count = (await session.execute(
        select(func.count(Order.id))
    )).scalar_one()
    if total_count == 0:
        return None

    # Orders arrive by manual/batch upload. If the newest order on file is
    # older than the report week's last day, the week isn't fully uploaded
    # yet and a "GMV ¥0 ▼" would be an artefact of missing data, not a sales
    # drop — the report then says how far the data goes instead.
    orders_through = (await session.execute(select(func.max(Order.order_date)))).scalar_one()
    complete = orders_through is not None and orders_through >= bounds.this_week_end

    this_agg = await _aggregate_orders(session, bounds.this_week_start, bounds.this_week_end)
    last_agg = await _aggregate_orders(session, bounds.last_week_start, bounds.last_week_end)

    # Merge platform data so each platform has both this_week and last_week
    last_by_name = {p["platform"]: p["this_week"] for p in last_agg["platforms"]}
    all_platform_names = set(p["platform"] for p in this_agg["platforms"]) | set(last_by_name.keys())
    this_by_name = {p["platform"]: p["this_week"] for p in this_agg["platforms"]}

    platforms_merged = []
    for name in sorted(all_platform_names):
        platforms_merged.append({
            "platform": name,
            "this_week": this_by_name.get(name, {"order_count": 0, "gmv": 0}),
            "last_week": last_by_name.get(name, {"order_count": 0, "gmv": 0}),
        })

    # Top SKUs this week
    top_sku_rows = (
        await session.execute(
            select(
                Order.sku,
                func.sum(Order.quantity).label("quantity"),
                func.sum(net_amount()).label("gmv"),
            )
            .where(Order.order_date.between(bounds.this_week_start, bounds.this_week_end), counted())
            .group_by(Order.sku)
            .order_by(func.sum(net_amount()).desc())
            .limit(10)
        )
    ).all()
    top_skus = [
        {"sku": r.sku or "未知商品", "quantity": int(r.quantity or 0), "gmv": float(r.gmv or 0)}
        for r in top_sku_rows
    ]

    # Top provinces this week
    top_prov_rows = (
        await session.execute(
            select(
                Order.province,
                func.count(Order.id).label("order_count"),
                func.sum(net_amount()).label("gmv"),
            )
            .where(
                counted(),
                Order.order_date.between(bounds.this_week_start, bounds.this_week_end),
                Order.province.isnot(None),
                Order.province != "",
            )
            .group_by(Order.province)
            .order_by(func.count(Order.id).desc())
            .limit(5)
        )
    ).all()
    top_provinces = [
        {"province": r.province, "order_count": r.order_count, "gmv": float(r.gmv or 0)}
        for r in top_prov_rows
    ]

    return {
        "complete": complete,
        "orders_through": orders_through,
        "incomplete_note": (
            None if complete
            else f"订单数据截至 {orders_through.isoformat() if orders_through else '—'}，未覆盖完整周"
        ),
        "this_week_total": this_agg["total"],
        "last_week_total": last_agg["total"],
        "platforms": platforms_merged,
        "top_skus": top_skus,
        "top_provinces": top_provinces,
    }


def _now_local() -> datetime:
    """Report generation time in APP_TIMEZONE (rendered to the minute)."""
    try:
        return datetime.now(ZoneInfo(app_settings.app_timezone))
    except ZoneInfoNotFoundError:
        return datetime.now(timezone.utc)


async def _latest_success_time(session: AsyncSession, model, **filters) -> datetime | None:
    stmt = select(func.max(model.finished_at))
    for key, value in filters.items():
        stmt = stmt.where(getattr(model, key) == value)
    return (await session.execute(stmt)).scalar_one()


async def build_report_context(session: AsyncSession, reference_date: date | None = None) -> dict[str, Any]:
    reference_date = reference_date or date.today()
    bounds = week_bounds(reference_date)

    wechat_accounts = (
        await session.execute(
            select(MediaAccount).where(
                MediaAccount.platform == WECHAT_PLATFORM, MediaAccount.is_active.is_(True)
            )
        )
    ).scalars().all()
    xhs_accounts = (
        await session.execute(select(XhsAccount).where(XhsAccount.is_active.is_(True)))
    ).scalars().all()

    # Resolve credentials through the same service used by manual and scheduled sync.
    from ..services.wechat import _wechat_secret_for_account

    wechat_sections = []
    for account in wechat_accounts:
        secret = _wechat_secret_for_account(account)
        client = WeChatOfficialClient(app_id=account.app_id, app_secret=secret) if account.app_id and secret else None
        section = await build_wechat_section(session, account, bounds, client)
        section["last_sync_at"] = await _latest_success_time(
            session, MediaSyncRun, account_id=account.id, status="success"
        )
        wechat_sections.append(section)

    xhs_sections = []
    for account in xhs_accounts:
        section = await build_xhs_section(session, account, bounds)
        xhs_sections.append(section)
    xhs_last_run = await _latest_success_time(session, CollectorRun, platform="xhs", status="success")

    # ── 视频号 ──
    channels_accounts = (
        await session.execute(
            select(WxChannelsAccount).where(WxChannelsAccount.is_active.is_(True))
        )
    ).scalars().all()
    channels_sections = []
    for account in channels_accounts:
        section = await build_channels_section(session, account, bounds)
        channels_sections.append(section)

    # ── 知乎 ──
    zhihu_section = await build_zhihu_section(session, bounds)

    # ── 蒲公英 ──
    pgy_sections = []
    for account in xhs_accounts:  # PgyNote.account_id -> xhs_accounts
        section = await build_pgy_section(session, account, bounds)
        pgy_sections.append(section)

    # ── 商城 ──
    ecommerce_section = await build_ecommerce_section(session, bounds)

    return {
        "bounds": bounds,
        "generated_at": _now_local(),
        "wechat_sections": wechat_sections,
        "xhs_sections": xhs_sections,
        "xhs_last_collector_run": xhs_last_run,
        "channels_sections": channels_sections,
        "zhihu_section": zhihu_section,
        "pgy_sections": pgy_sections,
        "ecommerce_section": ecommerce_section,
    }
