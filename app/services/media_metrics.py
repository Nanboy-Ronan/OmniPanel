"""Shared media overview calculations; cumulative snapshots are not weekly deltas."""
from datetime import date
from sqlalchemy import case, select, func
from sqlalchemy.ext.asyncio import AsyncSession
from ..db.models import MediaPost, MediaPostMetricDaily, XhsPost, WxChannelsPost

def _latest_metric_subquery(
    start_date: date | None = None,
    end_date: date | None = None,
):
    """Build a subquery with each post's most recent metric snapshot.

    WeChat reports `read_user_count` (and friends) as a cumulative-to-date
    value per `metric_date`, not a daily delta. Summing across days double
    counts repeat readers, so we take the latest snapshot in range instead.
    """
    ranked = select(
        MediaPostMetricDaily.post_id.label("post_id"),
        MediaPostMetricDaily.read_user_count.label("read_user_count"),
        MediaPostMetricDaily.share_user_count.label("share_user_count"),
        MediaPostMetricDaily.add_to_fav_count.label("add_to_fav_count"),
        MediaPostMetricDaily.like_user.label("like_user"),
        MediaPostMetricDaily.comment_count.label("comment_count"),
        MediaPostMetricDaily.collection_user.label("collection_user"),
        MediaPostMetricDaily.read_finish_rate.label("read_finish_rate"),
        func.row_number()
        .over(
            partition_by=MediaPostMetricDaily.post_id,
            order_by=MediaPostMetricDaily.metric_date.desc(),
        )
        .label("rn"),
    )
    if start_date:
        ranked = ranked.where(MediaPostMetricDaily.metric_date >= start_date)
    if end_date:
        ranked = ranked.where(MediaPostMetricDaily.metric_date <= end_date)
    ranked_subq = ranked.subquery()
    return select(ranked_subq).where(ranked_subq.c.rn == 1).subquery()

def _weighted_rate(rate, weight):
    """Rate weighted by its volume (impressions/plays), ignoring posts without the rate.

    Same definition as the weekly report. Falls back to the plain mean of known rates when
    no post has a weight, and is NULL (not 0) when no post has the rate at all.
    """
    weighted = func.sum(rate * weight) / func.nullif(func.sum(case((rate.isnot(None), weight))), 0)
    return func.coalesce(weighted, func.avg(rate))


async def wechat_metrics(session: AsyncSession, start_date: date | None = None, end_date: date | None = None, account_id: int | None = None):
    metric_subq = _latest_metric_subquery(start_date, end_date)
    stmt = (
        select(
            func.count(func.distinct(MediaPost.id)).label("posts"),
            func.sum(metric_subq.c.read_user_count).label("read_user_count"),
            func.sum(metric_subq.c.share_user_count).label("share_user_count"),
            func.sum(metric_subq.c.like_user).label("like_user"),
            func.sum(metric_subq.c.comment_count).label("comment_count"),
            func.sum(metric_subq.c.collection_user).label("collection_user"),
        )
        .select_from(MediaPost)
        .join(metric_subq, MediaPost.id == metric_subq.c.post_id)
    )
    if account_id:
        stmt = stmt.where(MediaPost.account_id == account_id)

    row = (await session.execute(stmt)).one()
    posts = int(row.posts or 0)
    read_user_count = int(row.read_user_count or 0)
    return {
        "posts": posts,
        "read_user_count": read_user_count,
        "share_user_count": int(row.share_user_count or 0),
        "like_user": int(row.like_user or 0),
        "comment_count": int(row.comment_count or 0),
        "collection_user": int(row.collection_user or 0),
        "avg_read_user_count": round(read_user_count / posts, 2) if posts else 0,
    }

async def xhs_metrics(session: AsyncSession, start_date: date | None = None, end_date: date | None = None, account_id: int | None = None):
    stmt = select(
        func.count(XhsPost.id),
        func.sum(XhsPost.impressions),
        func.sum(XhsPost.views),
        func.sum(func.coalesce(XhsPost.likes, 0) + func.coalesce(XhsPost.comments, 0)
                 + func.coalesce(XhsPost.collects, 0) + func.coalesce(XhsPost.shares, 0)),
        func.sum(XhsPost.new_followers),
        _weighted_rate(XhsPost.cover_click_rate, XhsPost.impressions),
    )
    if account_id is not None:
        stmt = stmt.where(XhsPost.account_id == account_id)
    if start_date:
        stmt = stmt.where(XhsPost.publish_date >= start_date)
    if end_date:
        stmt = stmt.where(XhsPost.publish_date <= end_date)
    posts, impressions, views, engagement, followers, cover_rate = (await session.execute(stmt)).one()
    return dict(
        posts=posts or 0, impressions=impressions or 0, views=views or 0,
        engagement=engagement or 0, new_followers=followers or 0,
        avg_cover_click_rate=float(cover_rate) if cover_rate is not None else None,
    )

async def channels_metrics(session: AsyncSession, start_date: date | None = None, end_date: date | None = None, account_id: int | None = None):
    stmt = select(
        func.count(WxChannelsPost.id).label("posts"),
        func.sum(WxChannelsPost.plays).label("plays"),
        func.sum(WxChannelsPost.recommends).label("recommends"),
        func.sum(WxChannelsPost.likes_thumb).label("likes_thumb"),
        func.sum(WxChannelsPost.comments).label("comments"),
        func.sum(WxChannelsPost.shares).label("shares"),
        func.sum(WxChannelsPost.new_fans).label("new_fans"),
        func.sum(WxChannelsPost.forwards_chat_moments).label("forwards_chat_moments"),
        _weighted_rate(WxChannelsPost.completion_rate, WxChannelsPost.plays).label("avg_completion_rate"),
    )
    if account_id is not None:
        stmt = stmt.where(WxChannelsPost.account_id == account_id)
    if start_date:
        stmt = stmt.where(WxChannelsPost.publish_date >= start_date)
    if end_date:
        stmt = stmt.where(WxChannelsPost.publish_date <= end_date)

    row = (await session.execute(stmt)).one()
    posts = int(row.posts or 0)
    plays = int(row.plays or 0)
    return {
        "posts": posts,
        "plays": plays,
        "recommends": int(row.recommends or 0),
        "likes_thumb": int(row.likes_thumb or 0),
        "comments": int(row.comments or 0),
        "shares": int(row.shares or 0),
        "new_fans": int(row.new_fans or 0),
        "forwards_chat_moments": int(row.forwards_chat_moments or 0),
        "engagement": sum(int(value or 0) for value in (
            row.recommends, row.likes_thumb, row.comments, row.shares,
        )),
        "avg_completion_rate": float(row.avg_completion_rate) if row.avg_completion_rate is not None else None,
        "avg_plays_per_post": round(plays / posts, 1) if posts else 0,
    }
