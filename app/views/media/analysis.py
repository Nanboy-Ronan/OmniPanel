"""Pure analysis helpers for WeChat media topic analysis.

These functions contain no I/O and are safe to unit-test without a database.
"""
from __future__ import annotations

from datetime import date, timedelta

from ...utils.topic_matching import match_article_topics

__all__ = [
    "aggregate_read_sources",
    "build_topic_source_matrix",
    "match_article_topics",
    "compute_content_impact",
]


def aggregate_read_sources(source_lists: list) -> dict[str, int]:
    """Merge multiple read_user_source JSON arrays into a single {scene_desc: count} dict.

    Entries with scene_desc == '全部' are the aggregate total and are excluded.
    None or empty inner lists are silently skipped.
    """
    totals: dict[str, int] = {}
    for sources in source_lists:
        if not sources:
            continue
        for item in sources:
            scene = item.get("scene_desc", "未知")
            if scene == "全部":
                continue
            totals[scene] = totals.get(scene, 0) + int(item.get("user_count", 0))
    return totals


def build_topic_source_matrix(
    posts: list[dict],
    post_sources: dict[str, dict[str, int]],
    topic_keywords: dict[str, list[str]],
) -> dict[str, dict[str, int]]:
    """Cross-aggregate topic labels against traffic sources.

    For each post, determine its topics via keyword matching then accumulate
    its per-source counts into every matched topic bucket.

    Args:
        posts: list of post dicts with at least 'id' and 'title'.
        post_sources: {str(post_id): {scene_desc: user_count}} as returned by
            /media/source-by-post.
        topic_keywords: {topic_name: [keyword, ...]} used for title matching.

    Returns:
        {topic_name: {scene_desc: total_user_count}}
    """
    matrix: dict[str, dict[str, int]] = {}
    for post in posts:
        sources = post_sources.get(str(post["id"]), {})
        if not sources:
            continue
        topics = match_article_topics(post.get("title", ""), topic_keywords)
        for topic in topics:
            bucket = matrix.setdefault(topic, {})
            for scene, count in sources.items():
                bucket[scene] = bucket.get(scene, 0) + count
    return matrix


# ── Content × Ecommerce correlation ──────────────────────────────────────────

def _date_range(start: date, end: date):
    """Yield every date from start to end inclusive."""
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def compute_content_impact(
    posts: list[dict],
    daily_totals: dict[str, dict],
    window_days: int = 7,
) -> list[dict]:
    """Compare order/revenue volume before vs. after each article's publish date.

    Args:
        posts: list of post dicts with at least 'id', 'title', 'publish_date',
            'read_user_count', 'share_user_count'.
        daily_totals: {date_str: {"orders": int, "revenue": float}} — daily
            aggregates for the entire period of interest.
        window_days: days in each window.  Pre-window is
            [publish_date - window_days, publish_date - 1]; post-window is
            [publish_date, publish_date + window_days - 1].

    Returns:
        List of impact dicts sorted by order_lift_pct descending (None last).
    """
    results = []
    for post in posts:
        pub = date.fromisoformat(str(post["publish_date"]))
        pre_start = pub - timedelta(days=window_days)
        pre_end = pub - timedelta(days=1)
        post_end = pub + timedelta(days=window_days - 1)

        pre_orders = sum(daily_totals.get(str(d), {}).get("orders", 0) for d in _date_range(pre_start, pre_end))
        post_orders = sum(daily_totals.get(str(d), {}).get("orders", 0) for d in _date_range(pub, post_end))
        pre_rev = sum(daily_totals.get(str(d), {}).get("revenue", 0.0) for d in _date_range(pre_start, pre_end))
        post_rev = sum(daily_totals.get(str(d), {}).get("revenue", 0.0) for d in _date_range(pub, post_end))

        order_lift = (post_orders - pre_orders) / pre_orders * 100 if pre_orders > 0 else None
        rev_lift = (post_rev - pre_rev) / pre_rev * 100 if pre_rev > 0 else None

        results.append({
            "post_id": post["id"],
            "title": post["title"],
            "publish_date": str(pub),
            "read_user_count": post.get("read_user_count", 0),
            "share_user_count": post.get("share_user_count", 0),
            "pre_orders": pre_orders,
            "post_orders": post_orders,
            "pre_revenue": round(pre_rev, 2),
            "post_revenue": round(post_rev, 2),
            "order_lift_pct": round(order_lift, 1) if order_lift is not None else None,
            "revenue_lift_pct": round(rev_lift, 1) if rev_lift is not None else None,
        })

    return sorted(results, key=lambda x: (x["order_lift_pct"] is None, -(x["order_lift_pct"] or 0)))
