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

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..connectors.wechat_official import WeChatOfficialClient
from ..db.models import (
    CollectorRun,
    MediaAccount,
    MediaPost,
    MediaPostMetricDaily,
    MediaSyncRun,
    XhsAccount,
    XhsPost,
)

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
        is_new_this_week = bool(post.publish_date and post.publish_date >= bounds.this_week_start)
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
                "new_followers_note": "平台不提供单篇归因",
            }
        )

    articles.sort(key=lambda a: a["counts_this_week"]["read_user_count"], reverse=True)

    for article in articles[:trend_top_n]:
        deltas = await _daily_read_deltas(session, article["post_id"], window_start, window_end)
        article["daily_reads"] = deltas

    follower_section = await _build_follower_section(wechat_client, bounds)

    return {
        "account": account,
        "totals_this_week": totals_this,
        "totals_last_week": totals_last,
        "articles": articles,
        "follower": follower_section,
        "snapshot_range_used": (first_snapshot_used, last_snapshot_used),
    }


def _avg(posts: list[XhsPost], field: str) -> float | None:
    values = [getattr(p, field) for p in posts if getattr(p, field) is not None]
    return sum(values) / len(values) if values else None


def _summarize_xhs_posts(posts: list[XhsPost]) -> dict[str, Any]:
    return {
        "count": len(posts),
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

    return {
        "account": account,
        "this_week_posts": posts_detail,
        "this_week_summary": _summarize_xhs_posts(this_week_posts),
        "last_week_summary": _summarize_xhs_posts(last_week_posts),
        "top_follower_sources": [
            {"title": p.title, "publish_date": p.publish_date, "new_followers": p.new_followers}
            for p in top_follower_posts
        ],
        "not_collected": ["完播率", "取消关注", "净增关注", "观看来源"],
        "wow_caveat": "小红书暂无历史快照，此对比为同期发布内容对比，非流量环比。",
        "estimated_watch_time_caveat": "观看总时长为推算值（人均观看时长 × 观看量），非平台直接给出的数值。",
        "follower_source_caveat": "涨粉数值为当前导出口径下的累计涨粉，非精确周增量。",
    }


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

    # MediaAccount.app_secret is essentially never populated in practice — real
    # secrets live in env vars (WECHAT_APP_SECRET_N) and are resolved by app_id
    # via _wechat_secret_for_account, same as the manual-sync flow in
    # app/views/media/routes.py. Using account.app_secret directly (as an
    # earlier version of this function did) silently produced "no client" for
    # every real account — confirmed against production, where configured
    # accounts have app_id but NULL app_secret in the DB. Lazy import: avoids a
    # module-level circular import with app.views.media.routes.
    from ..views.media.routes import _wechat_secret_for_account

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

    return {
        "bounds": bounds,
        "generated_at": datetime.now(timezone.utc),
        "wechat_sections": wechat_sections,
        "xhs_sections": xhs_sections,
        "xhs_last_collector_run": xhs_last_run,
    }
