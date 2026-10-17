"""Source coverage and ingestion timestamps for the dashboard."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..collector.runs import LOGIN_CHECK_TRIGGERS
from ..db.models import (
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


# An order platform whose latest order date trails the freshest platform by
# more than this many days is flagged stale (e.g. 天猫 not uploaded while
# 有赞 is current). Relative to the freshest platform rather than today,
# because orders arrive in batches and "today" is normally empty.
ORDERS_STALE_LAG_DAYS = 3

# CollectorRun.platform for each collected media source.
_COLLECTOR_PLATFORM = {"xhs": "xhs", "zhihu": "zhihu", "channels": "channels", "pgy": "pugongying"}

# Verify and login-repair runs only check a login session; they never bring in data.
_DATA_RUN = CollectorRun.triggered_by.not_in(LOGIN_CHECK_TRIGGERS)


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


async def get_data_freshness(
    session: AsyncSession,
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
