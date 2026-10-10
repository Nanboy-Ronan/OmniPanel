from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import current_admin_user, current_analyst_user
from ...db import get_session
from ...db.models import (
    MediaAccount,
    MediaArticleTraffic,
    MediaPost,
    MediaPostMetricDaily,
    Order,
    XhsPost,
    ZhihuPost,
)
from ...db.order_status import counted, net_amount
from ...services.wechat import (
    WECHAT_PLATFORM, _wechat_env_accounts, _ensure_env_wechat_accounts,
    _wechat_secret_for_account, _sync_one_wechat_account, _record_failed_wechat_sync,
)
from .analysis import aggregate_read_sources, compute_content_impact
from ...utils.logger import log_operation

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/media", tags=["media"])


class WeChatSyncRequest(BaseModel):
    start_date: date
    end_date: date
    account_id: int | None = None


async def _get_account(session: AsyncSession, account_id: int | None = None) -> MediaAccount:
    if account_id is not None:
        result = await session.execute(select(MediaAccount).where(MediaAccount.id == account_id))
        account = result.scalar_one_or_none()
        if account:
            return account
        raise HTTPException(status_code=404, detail="Media account not found")

    accounts = await _ensure_env_wechat_accounts(session)
    if len(accounts) == 1:
        return accounts[0]
    if len(accounts) > 1:
        raise HTTPException(status_code=400, detail="account_id is required when multiple WeChat accounts are configured")
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="No WeChat Official Account credentials are configured",
    )


def _account_json(account: MediaAccount) -> dict[str, Any]:
    return {
        "id": account.id,
        "platform": account.platform,
        "name": account.name,
        "app_id": account.app_id,
        "is_active": bool(account.is_active),
    }


@router.get("/accounts")
async def list_media_accounts(
    _u=Depends(current_analyst_user),
    session: AsyncSession = Depends(get_session),
):
    await _ensure_env_wechat_accounts(session)
    await session.commit()
    result = await session.execute(select(MediaAccount).order_by(MediaAccount.id))
    return [_account_json(row) for row in result.scalars().all()]


@router.post("/wechat/sync")
async def sync_wechat_official(
    payload: WeChatSyncRequest,
    request: Request,
    _u=Depends(current_admin_user),
    session: AsyncSession = Depends(get_session),
):
    if payload.start_date > payload.end_date:
        raise HTTPException(status_code=400, detail="start_date cannot be after end_date")

    if payload.account_id is not None:
        accounts = [await _get_account(session, payload.account_id)]
    else:
        accounts = await _ensure_env_wechat_accounts(session)
        if not accounts:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="No WeChat Official Account credentials are configured",
            )
    account_ids = [account.id for account in accounts]
    await session.commit()

    results = []
    failures = []
    for account_id in account_ids:
        account = await session.get(MediaAccount, account_id)
        account_id, account_name = account.id, account.name
        try:
            result = await _sync_one_wechat_account(session, account, payload.start_date, payload.end_date)
            await session.commit()
            results.append(result)
        except Exception as exc:
            await session.rollback()
            failures.append({"account_id": account_id, "account_name": account_name, "error": str(exc)})
            try:
                await _record_failed_wechat_sync(
                    session, account_id, payload.start_date, payload.end_date, exc
                )
            except Exception:
                await session.rollback()
                logger.exception("Could not record failed WeChat sync for account=%s", account_id)

    await log_operation(
        str(_u.id),
        "wechat_sync",
        {
            "start_date": payload.start_date,
            "end_date": payload.end_date,
            "accounts": account_ids,
            "failed_accounts": [f["account_id"] for f in failures],
        },
        request=request,
    )
    if failures and not results:
        raise HTTPException(status_code=502, detail={"accounts_failed": failures})

    return {
        "accounts_synced": len(results),
        "status": "partial" if failures else "success",
        "posts_upserted": sum(item["posts_upserted"] for item in results),
        "metrics_upserted": sum(item["metrics_upserted"] for item in results),
        "results": results,
        "failures": failures,
    }


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


@router.get("/posts")
async def list_media_posts(
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    account_id: int | None = Query(None),
    q: str | None = Query(None),
    _u=Depends(current_analyst_user),
    session: AsyncSession = Depends(get_session),
):
    metric_subq = _latest_metric_subquery(start_date, end_date)

    stmt = (
        select(
            MediaPost,
            MediaAccount.name.label("account_name"),
            metric_subq.c.read_user_count,
            metric_subq.c.share_user_count,
            metric_subq.c.add_to_fav_count,
            metric_subq.c.like_user,
            metric_subq.c.comment_count,
            metric_subq.c.collection_user,
            metric_subq.c.read_finish_rate,
        )
        .join(MediaAccount, MediaPost.account_id == MediaAccount.id)
        .join(metric_subq, MediaPost.id == metric_subq.c.post_id, isouter=True)
    )
    if account_id:
        stmt = stmt.where(MediaPost.account_id == account_id)
    if q:
        stmt = stmt.where(MediaPost.title.ilike(f"%{q}%"))
    stmt = stmt.order_by(func.coalesce(metric_subq.c.read_user_count, 0).desc(), MediaPost.publish_date.desc())

    result = await session.execute(stmt)
    rows = []
    for r in result.all():
        post = r.MediaPost
        rows.append(
            {
                "id": post.id,
                "account_id": post.account_id,
                "account_name": r.account_name,
                "platform": post.platform,
                "external_id": post.external_id,
                "title": post.title,
                "url": post.url,
                "publish_date": str(post.publish_date) if post.publish_date else None,
                "author": post.author,
                "read_user_count": int(r.read_user_count or 0),
                "share_user_count": int(r.share_user_count or 0),
                "add_to_fav_count": int(r.add_to_fav_count or 0),
                "like_user": int(r.like_user or 0),
                "comment_count": int(r.comment_count or 0),
                "collection_user": int(r.collection_user or 0),
                "read_finish_rate": round(float(r.read_finish_rate), 4) if r.read_finish_rate is not None else None,
            }
        )
    return rows


@router.get("/overview")
async def media_overview(
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    account_id: int | None = Query(None),
    _u=Depends(current_analyst_user),
    session: AsyncSession = Depends(get_session),
):
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


@router.get("/traffic")
async def list_article_traffic(
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    account_id: int | None = Query(None),
    q: str | None = Query(None),
    _u=Depends(current_analyst_user),
    session: AsyncSession = Depends(get_session),
):
    """Return manually-uploaded article traffic rows from media_article_traffic."""
    stmt = (
        select(MediaArticleTraffic, MediaAccount.name.label("account_name"))
        .join(MediaAccount, MediaArticleTraffic.account_id == MediaAccount.id)
    )
    if account_id:
        stmt = stmt.where(MediaArticleTraffic.account_id == account_id)
    if start_date:
        stmt = stmt.where(MediaArticleTraffic.publish_date >= start_date)
    if end_date:
        stmt = stmt.where(MediaArticleTraffic.publish_date <= end_date)
    if q:
        stmt = stmt.where(MediaArticleTraffic.title.ilike(f"%{q}%"))
    stmt = stmt.order_by(MediaArticleTraffic.read_user_count.desc(), MediaArticleTraffic.publish_date.desc())

    result = await session.execute(stmt)
    rows = []
    for r in result.all():
        t = r.MediaArticleTraffic
        rows.append(
            {
                "id":              t.id,
                "account_id":      t.account_id,
                "account_name":    r.account_name,
                "external_id":     t.external_id,
                "title":           t.title,
                "publish_date":    str(t.publish_date) if t.publish_date else None,
                "read_user_count": t.read_user_count,
                "read_count":      t.read_count,
                "like_user":       t.like_user,
                "share_user_count": t.share_user_count,
                "comment_count":   t.comment_count,
                "collection_user": t.collection_user,
                "read_avg_time":   float(t.read_avg_time) if t.read_avg_time is not None else None,
                "updated_at":      str(t.updated_at) if t.updated_at else None,
            }
        )
    return rows


@router.get("/traffic/overview")
async def article_traffic_overview(
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    account_id: int | None = Query(None),
    _u=Depends(current_analyst_user),
    session: AsyncSession = Depends(get_session),
):
    """Return aggregate overview for manually-uploaded article traffic."""
    stmt = select(
        func.count(MediaArticleTraffic.id).label("articles"),
        func.sum(MediaArticleTraffic.read_user_count).label("read_user_count"),
        func.sum(MediaArticleTraffic.read_count).label("read_count"),
        func.sum(MediaArticleTraffic.like_user).label("like_user"),
        func.sum(MediaArticleTraffic.share_user_count).label("share_user_count"),
        func.sum(MediaArticleTraffic.comment_count).label("comment_count"),
        func.sum(MediaArticleTraffic.collection_user).label("collection_user"),
        func.avg(MediaArticleTraffic.read_avg_time).label("avg_read_time"),
    )
    if account_id:
        stmt = stmt.where(MediaArticleTraffic.account_id == account_id)
    if start_date:
        stmt = stmt.where(MediaArticleTraffic.publish_date >= start_date)
    if end_date:
        stmt = stmt.where(MediaArticleTraffic.publish_date <= end_date)

    row = (await session.execute(stmt)).one()
    articles = int(row.articles or 0)
    read_user_count = int(row.read_user_count or 0)
    return {
        "articles":        articles,
        "read_user_count": read_user_count,
        "read_count":      int(row.read_count or 0),
        "like_user":       int(row.like_user or 0),
        "share_user_count": int(row.share_user_count or 0),
        "comment_count":   int(row.comment_count or 0),
        "collection_user": int(row.collection_user or 0),
        "avg_read_per_article": round(read_user_count / articles, 1) if articles else 0,
        "avg_read_time":   round(float(row.avg_read_time), 2) if row.avg_read_time else None,
    }


@router.get("/source-by-post")
async def source_by_post(
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    account_id: int | None = Query(None),
    _u=Depends(current_analyst_user),
    session: AsyncSession = Depends(get_session),
):
    """Return per-post aggregated source breakdown as {str(post_id): {scene_desc: count}}.

    Metric rows with null read_user_source are ignored. Multiple metric days
    for the same post are merged by summing scene counts.
    """
    stmt = (
        select(MediaPostMetricDaily.post_id, MediaPostMetricDaily.read_user_source)
        .join(MediaPost, MediaPostMetricDaily.post_id == MediaPost.id)
        .where(MediaPostMetricDaily.read_user_source.isnot(None))
    )
    if account_id:
        stmt = stmt.where(MediaPost.account_id == account_id)
    if start_date:
        stmt = stmt.where(MediaPostMetricDaily.metric_date >= start_date)
    if end_date:
        stmt = stmt.where(MediaPostMetricDaily.metric_date <= end_date)

    result = await session.execute(stmt)
    per_post: dict[int, list] = {}
    for post_id, source_list in result.all():
        per_post.setdefault(post_id, []).append(source_list)

    return {
        str(post_id): aggregate_read_sources(source_lists)
        for post_id, source_lists in per_post.items()
        if aggregate_read_sources(source_lists)  # omit posts where all sources were 全部-only
    }


@router.get("/source-breakdown")
async def source_breakdown(
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    account_id: int | None = Query(None),
    _u=Depends(current_analyst_user),
    session: AsyncSession = Depends(get_session),
):
    """Return aggregated traffic-source breakdown across posts in the given date range.

    Reads and aggregates the read_user_source JSON arrays from metric rows,
    returning {scene_desc: total_user_count} with '全部' excluded.
    """
    stmt = (
        select(MediaPostMetricDaily.read_user_source)
        .join(MediaPost, MediaPostMetricDaily.post_id == MediaPost.id)
        .where(MediaPostMetricDaily.read_user_source.isnot(None))
    )
    if account_id:
        stmt = stmt.where(MediaPost.account_id == account_id)
    if start_date:
        stmt = stmt.where(MediaPostMetricDaily.metric_date >= start_date)
    if end_date:
        stmt = stmt.where(MediaPostMetricDaily.metric_date <= end_date)

    result = await session.execute(stmt)
    source_lists = [row[0] for row in result.all()]
    return aggregate_read_sources(source_lists)


async def _wechat_posts_data(
    session: AsyncSession,
    start_date: date | None,
    end_date: date | None,
    account_id: int | None,
) -> list[dict]:
    metric_subq = _latest_metric_subquery()
    post_stmt = (
        select(
            MediaPost.id,
            MediaPost.title,
            MediaPost.publish_date,
            metric_subq.c.read_user_count,
            metric_subq.c.share_user_count,
        )
        .join(metric_subq, MediaPost.id == metric_subq.c.post_id)
        .where(MediaPost.publish_date.isnot(None))
    )
    if start_date:
        post_stmt = post_stmt.where(MediaPost.publish_date >= start_date)
    if end_date:
        post_stmt = post_stmt.where(MediaPost.publish_date <= end_date)
    if account_id:
        post_stmt = post_stmt.where(MediaPost.account_id == account_id)

    post_rows = (await session.execute(post_stmt)).all()
    return [
        {
            "id": r.id,
            "title": r.title,
            "publish_date": str(r.publish_date),
            "read_user_count": int(r.read_user_count or 0),
            "share_user_count": int(r.share_user_count or 0),
        }
        for r in post_rows
    ]


async def _xhs_posts_data(
    session: AsyncSession,
    start_date: date | None,
    end_date: date | None,
    account_id: int | None,
) -> list[dict]:
    stmt = select(
        XhsPost.id, XhsPost.title, XhsPost.publish_date, XhsPost.views, XhsPost.shares
    ).where(XhsPost.publish_date.isnot(None))
    if start_date:
        stmt = stmt.where(XhsPost.publish_date >= start_date)
    if end_date:
        stmt = stmt.where(XhsPost.publish_date <= end_date)
    if account_id:
        stmt = stmt.where(XhsPost.account_id == account_id)

    rows = (await session.execute(stmt)).all()
    return [
        {
            "id": r.id,
            "title": r.title,
            "publish_date": str(r.publish_date),
            "read_user_count": int(r.views or 0),
            "share_user_count": int(r.shares or 0),
        }
        for r in rows
    ]


async def _zhihu_posts_data(
    session: AsyncSession,
    start_date: date | None,
    end_date: date | None,
) -> list[dict]:
    stmt = select(
        ZhihuPost.id, ZhihuPost.title, ZhihuPost.publish_date, ZhihuPost.reads, ZhihuPost.shares
    ).where(ZhihuPost.publish_date.isnot(None))
    if start_date:
        stmt = stmt.where(ZhihuPost.publish_date >= start_date)
    if end_date:
        stmt = stmt.where(ZhihuPost.publish_date <= end_date)

    rows = (await session.execute(stmt)).all()
    return [
        {
            "id": r.id,
            "title": r.title,
            "publish_date": str(r.publish_date),
            "read_user_count": int(r.reads or 0),
            "share_user_count": int(r.shares or 0),
        }
        for r in rows
    ]


@router.get("/content-impact")
async def content_impact(
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    window_days: int = Query(7, ge=1, le=30),
    account_id: int | None = Query(None),
    platform: str | None = Query(None),
    source: str = Query("wechat", pattern="^(wechat|xhs|zhihu)$"),
    _u=Depends(current_analyst_user),
    session: AsyncSession = Depends(get_session),
):
    """For each article, compare total store orders/revenue in the N days before vs.
    after its publish_date and return the per-post lift metrics.

    ``source`` selects which self-media platform's posts feed the lift
    calculation; defaults to WeChat (unchanged behavior). ``account_id`` only
    applies to wechat/xhs (both have an account concept); it's a no-op for
    zhihu, which has no account dimension.
    """
    # ── Step 1: fetch articles with their latest engagement numbers ─────────
    if source == "wechat":
        posts_data = await _wechat_posts_data(session, start_date, end_date, account_id)
    elif source == "xhs":
        posts_data = await _xhs_posts_data(session, start_date, end_date, account_id)
    else:  # "zhihu"
        posts_data = await _zhihu_posts_data(session, start_date, end_date)

    if not posts_data:
        return []

    # ── Step 2: determine overall order query range ──────────────────────────
    all_pub_dates = [date.fromisoformat(p["publish_date"]) for p in posts_data]
    order_start = min(all_pub_dates) - timedelta(days=window_days)
    order_end = max(all_pub_dates) + timedelta(days=window_days - 1)

    # ── Step 3: daily order aggregates for the full range ───────────────────
    order_stmt = (
        select(
            Order.order_date,
            func.count(Order.id).label("orders"),
            # Order.price is the order's paid total (订单实付金额); quantity is
            # 商品种类数 (distinct SKUs), so price * quantity over-counts.
            func.sum(net_amount()).label("revenue"),
        )
        .where(counted())
        .where(Order.order_date >= order_start)
        .where(Order.order_date <= order_end)
        .group_by(Order.order_date)
    )
    if platform:
        order_stmt = order_stmt.where(Order.platform == platform)

    latest_stmt = select(func.max(Order.order_date))
    if platform:
        latest_stmt = latest_stmt.where(Order.platform == platform)
    latest_order_date = (await session.execute(latest_stmt)).scalar()

    order_rows = (await session.execute(order_stmt)).all()
    daily_totals = {
        str(r.order_date): {"orders": int(r.orders or 0), "revenue": float(r.revenue or 0.0)}
        for r in order_rows
    }

    # ── Step 4: compute per-post impact ─────────────────────────────────────
    # Clip windows at the last uploaded order day (see compute_content_impact).
    return compute_content_impact(posts_data, daily_totals, window_days, latest_order_date)


@router.get("/posts/{post_id}/metrics")
async def post_metrics(
    post_id: int,
    _u=Depends(current_analyst_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(MediaPostMetricDaily)
        .where(MediaPostMetricDaily.post_id == post_id)
        .order_by(MediaPostMetricDaily.metric_date)
    )
    rows = result.scalars().all()
    if not rows:
        raise HTTPException(status_code=404, detail="Media post metrics not found")
    return [
        {
            "metric_date": str(row.metric_date),
            "read_user_count": row.read_user_count,
            "share_user_count": row.share_user_count,
            "like_user": row.like_user,
            "comment_count": row.comment_count,
            "collection_user": row.collection_user,
            "add_to_fav_count": row.add_to_fav_count,
            "read_avg_time": float(row.read_avg_time) if row.read_avg_time is not None else None,
            "read_user_source": row.read_user_source,
        }
        for row in rows
    ]
