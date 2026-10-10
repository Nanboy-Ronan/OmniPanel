"""Source coverage and ingestion timestamps for the dashboard."""

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_active_user, current_analyst_user
from ..db import get_session
from ..db.models import (
    MediaPost,
    MediaArticleTraffic,
    CollectorRun,
    Order,
    UploadBatch,
    MediaPostMetricDaily,
    MediaSyncRun,
    XhsPost,
    ZhihuPost,
    WxChannelsPost,
    PgyNote,
)

router = APIRouter(prefix="/data", tags=["data"])

# An order platform whose latest order date trails the freshest platform by
# more than this many days is flagged stale (e.g. 天猫 not uploaded while
# 有赞 is current). Relative to the freshest platform rather than today,
# because orders arrive in batches and "today" is normally empty.
ORDERS_STALE_LAG_DAYS = 3

# CollectorRun.platform for each collected media source.
_COLLECTOR_PLATFORM = {"xhs": "xhs", "zhihu": "zhihu", "channels": "channels", "pgy": "pugongying"}

# Verify runs only check a login session; they never bring in data.
_DATA_RUN = CollectorRun.triggered_by != "verify"


async def _order_platform_freshness(session: AsyncSession) -> dict:
    coverage = dict(
        (await session.execute(
            select(Order.platform, func.max(Order.order_date)).group_by(Order.platform)
        )).all()
    )
    imports = dict(
        (await session.execute(
            select(UploadBatch.platform, func.max(UploadBatch.uploaded_at))
            .where(UploadBatch.status == "completed")
            .group_by(UploadBatch.platform)
        )).all()
    )
    freshest = max(coverage.values()) if coverage else None
    platforms = {}
    for platform in sorted(set(coverage) | {p for p in imports if p in coverage}):
        latest = coverage.get(platform)
        stale = bool(latest and freshest and (freshest - latest).days > ORDERS_STALE_LAG_DAYS)
        platforms[platform] = {
            "coverage_through": latest.isoformat() if latest else None,
            "last_import_at": imports[platform].isoformat() if imports.get(platform) else None,
            "stale": stale,
        }
    return platforms


@router.get("/freshness")
async def data_freshness(
    _u=Depends(current_active_user),
    session: AsyncSession = Depends(get_session),
):
    """Keep content coverage separate from the time data reached this system."""
    specs = {
        "orders": (
            Order.order_date,
            UploadBatch.uploaded_at,
            UploadBatch.status == "completed",
        ),
        "wechat": (
            MediaPostMetricDaily.metric_date,
            MediaSyncRun.finished_at,
            MediaSyncRun.status == "success",
        ),
        "xhs": (XhsPost.publish_date, XhsPost.updated_at, None),
        "zhihu": (ZhihuPost.publish_date, ZhihuPost.updated_at, None),
        "channels": (WxChannelsPost.publish_date, WxChannelsPost.updated_at, None),
        "pgy": (PgyNote.publish_date, PgyNote.updated_at, None),
    }
    result = {}
    for key, (coverage_col, update_col, success_condition) in specs.items():
        coverage = (await session.execute(select(func.max(coverage_col)))).scalar()
        update_query = select(func.max(update_col))
        if success_condition is not None:
            update_query = update_query.where(success_condition)
        updated = (await session.execute(update_query)).scalar()
        result[key] = {
            "coverage_through": coverage.isoformat() if coverage else None,
            "last_import_at": updated.isoformat() if updated else None,
        }

    # Per-platform order coverage: the max across platforms above hides a
    # stale platform behind a fresh one.
    platforms = await _order_platform_freshness(session)
    result["orders"]["platforms"] = platforms
    result["orders"]["stale_platforms"] = [p for p, v in platforms.items() if v["stale"]]
    result["orders"]["stale"] = bool(result["orders"]["stale_platforms"])
    result["orders"]["stale_after_days"] = ORDERS_STALE_LAG_DAYS

    # Latest successful *collect* per collected media source (verify runs and
    # failed runs never count as fresh data).
    for key, platform in _COLLECTOR_PLATFORM.items():
        last_collect = (await session.execute(
            select(func.max(CollectorRun.finished_at)).where(
                CollectorRun.platform == platform, CollectorRun.status == "success", _DATA_RUN,
            )
        )).scalar()
        result[key]["last_collect_at"] = last_collect.isoformat() if last_collect else None
    return result


# This endpoint exposes aggregate provenance only, never collector credentials or raw errors.


@router.get("/source-status")
async def source_status(
    source: Literal["orders", "wechat", "traffic", "xhs", "zhihu", "channels", "pgy"],
    account_id: int | None = Query(None, ge=1),
    content_type: Literal["article", "qa"] | None = None,
    _u=Depends(current_analyst_user),
    session: AsyncSession = Depends(get_session),
):
    model = {
        "orders": Order,
        "wechat": MediaPost,
        "traffic": MediaArticleTraffic,
        "xhs": XhsPost,
        "zhihu": ZhihuPost,
        "channels": WxChannelsPost,
        "pgy": PgyNote,
    }[source]
    date_col = Order.order_date if source == "orders" else model.publish_date
    conditions = []
    if account_id is not None and hasattr(model, "account_id"):
        conditions.append(model.account_id == account_id)
    if source == "zhihu" and content_type:
        conditions.append(model.content_type == content_type)
    counts = (
        (
            await session.execute(
                select(
                    func.count(model.id).label("records"),
                    func.count(date_col).label("dated"),
                    func.min(date_col).label("first"),
                    func.max(date_col).label("last"),
                ).where(*conditions)
            )
        )
        .mappings()
        .one()
    )
    first, last = counts["first"], counts["last"]
    date_basis = "下单日期" if source == "orders" else "发布日期"
    if source == "wechat":
        first, last = (
            await session.execute(
                select(
                    func.min(MediaPostMetricDaily.metric_date),
                    func.max(MediaPostMetricDaily.metric_date),
                )
                .join(MediaPost, MediaPost.id == MediaPostMetricDaily.post_id)
                .where(*conditions)
            )
        ).one()
        date_basis = "指标日期"
    if source == "orders":
        updated = (
            await session.execute(
                select(func.max(UploadBatch.uploaded_at)).where(
                    UploadBatch.status == "completed"
                )
            )
        ).scalar()
    elif source == "wechat":
        updated_query = select(func.max(MediaSyncRun.finished_at)).where(
            MediaSyncRun.status == "success", MediaSyncRun.source == "api"
        )
        if account_id is not None:
            updated_query = updated_query.where(MediaSyncRun.account_id == account_id)
        updated = (await session.execute(updated_query)).scalar()
    else:
        updated = (
            await session.execute(select(func.max(model.updated_at)).where(*conditions))
        ).scalar()
    runs = []
    if source in {"xhs", "zhihu", "channels", "pgy"}:
        run_filters = [
            CollectorRun.platform == ("pugongying" if source == "pgy" else source),
            _DATA_RUN,
        ]
        if account_id is not None:
            run_filters.append(CollectorRun.account_id == account_id)
        if source == "zhihu" and content_type:
            run_filters.append(CollectorRun.content_type == content_type)
        recent = (
            select(
                CollectorRun.account_id,
                CollectorRun.content_type,
                CollectorRun.status,
                CollectorRun.started_at,
                CollectorRun.finished_at,
                func.row_number()
                .over(
                    partition_by=(CollectorRun.account_id, CollectorRun.content_type),
                    order_by=(CollectorRun.started_at.desc(), CollectorRun.id.desc()),
                )
                .label("rank"),
            )
            .where(*run_filters)
            .subquery()
        )
        rows = (
            (await session.execute(select(recent).where(recent.c.rank == 1)))
            .mappings()
            .all()
        )
        runs = [
            {
                k: r[k]
                for k in (
                    "account_id",
                    "content_type",
                    "status",
                    "started_at",
                    "finished_at",
                )
            }
            for r in rows
        ]
    elif source == "wechat":
        stmt = select(
            MediaSyncRun.account_id,
            MediaSyncRun.status,
            MediaSyncRun.started_at,
            MediaSyncRun.finished_at,
            func.row_number()
            .over(
                partition_by=MediaSyncRun.account_id,
                order_by=(MediaSyncRun.started_at.desc(), MediaSyncRun.id.desc()),
            )
            .label("rank"),
        ).where(MediaSyncRun.source == "api")
        if account_id is not None:
            stmt = stmt.where(MediaSyncRun.account_id == account_id)
        recent = stmt.subquery()
        runs = [
            dict(r)
            for r in (
                await session.execute(
                    select(
                        recent.c.account_id,
                        recent.c.status,
                        recent.c.started_at,
                        recent.c.finished_at,
                    ).where(recent.c.rank == 1)
                )
            ).mappings()
        ]
    return {
        "source": source,
        "records": counts["records"],
        "undated": counts["records"] - counts["dated"],
        "first_date": first,
        "last_date": last,
        "date_basis": date_basis,
        "updated_at": updated,
        "runs": runs,
    }
