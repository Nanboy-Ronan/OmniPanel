"""Source coverage and ingestion timestamps for the dashboard."""
from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_active_user
from ..db import get_session
from ..db.models import (
    Order, UploadBatch, MediaPostMetricDaily, MediaSyncRun,
    XhsPost, ZhihuPost, WxChannelsPost, PgyNote,
)

router = APIRouter(prefix="/data", tags=["data"])


@router.get("/freshness")
async def data_freshness(
    _u=Depends(current_active_user),
    session: AsyncSession = Depends(get_session),
):
    """Keep content coverage separate from the time data reached this system."""
    specs = {
        "orders": (Order.order_date, UploadBatch.uploaded_at, UploadBatch.status == "completed"),
        "wechat": (MediaPostMetricDaily.metric_date, MediaSyncRun.finished_at, MediaSyncRun.status == "success"),
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
    return result
