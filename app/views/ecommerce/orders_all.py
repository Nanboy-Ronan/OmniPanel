# rap/app/views/ecommerce/orders_all.py
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import date, datetime
from decimal import Decimal

from ...auth import current_active_user
from ...config import settings
from ...db import get_session
from ...db.models import JdOrder, Order, TmallOrder, YouzanOrder
from ...utils.logger import log_operation

router = APIRouter(prefix="/orders_all", tags=["orders"])

RAW_MODEL_BY_PLATFORM = {
    "youzan": YouzanOrder,
    "jd": JdOrder,
    "tmall": TmallOrder,
}


def _jsonable(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _raw_row_to_dict(row) -> dict:
    return {
        prop.columns[0].name: _jsonable(getattr(row, prop.key))
        for prop in row.__mapper__.column_attrs
    }


@router.get("/", summary="Return a page of order rows as JSON")
async def get_all_orders(
    response: Response,
    limit: int = Query(
        None,
        ge=1,
        le=20000,
        description="Max rows to return. Defaults to settings.analysis_rows_cap.",
    ),
    offset: int = Query(0, ge=0),
    _u=Depends(current_active_user),
    session: AsyncSession = Depends(get_session),
):
    """Paginated order listing — total row count is returned in the
    ``X-Total-Count`` response header so the client can show "N / total" and
    decide whether to fetch more, without the API ever serialising the whole
    table in one response.
    """
    effective_limit = limit or settings.analysis_rows_cap
    try:
        total = (await session.execute(select(func.count(Order.id)))).scalar() or 0
        result = await session.execute(
            select(Order)
            .order_by(Order.order_date, Order.id)
            .offset(offset)
            .limit(effective_limit)
        )
        orders = result.scalars().all()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    response.headers["X-Total-Count"] = str(total)

    rows = [
        {
            "id": o.id,
            "order_id": o.order_id,
            "order_date": str(o.order_date) if o.order_date else None,
            "customer_key": o.customer_key,
            "platform": o.platform,
            "sku": o.sku,
            "quantity": o.quantity,
            "price": float(o.price or 0),
            "receiver": o.receiver,
            "receiver_phone": o.receiver_phone,
            "province": o.province,
            "area": o.area,
            "full_address": o.full_address,
            "buyer_nick": o.buyer_nick,
            "coupon_name": o.coupon_name,
            "distributor": o.distributor,
        }
        for o in orders
    ]

    await log_operation(str(_u.id), "download", {"count": len(rows)}, session=session)
    return rows


@router.get("/{order_pk}/raw", summary="Return raw platform row(s) for one order")
async def get_order_raw_rows(
    order_pk: int,
    _u=Depends(current_active_user),
    session: AsyncSession = Depends(get_session),
):
    try:
        order_result = await session.execute(select(Order).where(Order.id == order_pk))
        order = order_result.scalar_one_or_none()
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")

        model = RAW_MODEL_BY_PLATFORM.get(order.platform)
        if model is None:
            raise HTTPException(status_code=404, detail="Raw table not found for platform")

        raw_result = await session.execute(
            select(model)
            .where(model.normalized_order_id == order.order_id)
            .order_by(model.source_row_number)
        )
        raw_rows = raw_result.scalars().all()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {
        "order": {
            "id": order.id,
            "order_id": order.order_id,
            "platform": order.platform,
        },
        "rows": [_raw_row_to_dict(row) for row in raw_rows],
        "row_count": len(raw_rows),
    }
