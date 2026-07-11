# rap/app/views/ecommerce/analysis/customers.py
"""Per-customer aggregation, order drilldown, and field-coverage endpoints."""
from __future__ import annotations
import datetime as dt

from fastapi import Depends, HTTPException, Query, Response
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from ....auth import current_analyst_user
from ....config import settings
from ....db import get_session
from ....db.models import Order
from ....utils.cache import analysis_cache
from ._common import router, _ensure_data, _platform_filter

_SEARCH_COLUMNS = (
    Order.customer_key,
    Order.receiver,
    Order.receiver_phone,
    Order.province,
    Order.area,
    Order.full_address,
    Order.buyer_nick,
)


@router.get("/customers", summary="Aggregated customer metrics")
async def customers(
    response: Response,
    start_date: dt.date | None = Query(None),
    end_date: dt.date | None = Query(None),
    min_orders: int | None = Query(None, ge=1),
    platform: str | None = Query(None),
    search: str | None = Query(
        None, description="Substring match across customer_key/receiver/phone/address/nick."
    ),
    limit: int = Query(
        None,
        ge=1,
        le=20000,
        description="Max customers to return. Defaults to settings.analysis_rows_cap.",
    ),
    offset: int = Query(0, ge=0),
    _u=Depends(current_analyst_user),
    session: AsyncSession = Depends(get_session),
):
    """Return per-customer order metrics sorted by revenue.

    Paginated — the total distinct-customer count (post-filter) is returned
    in the ``X-Total-Count`` response header. Without a limit, this query
    would aggregate and serialise every customer in the dataset on every
    call, which stops scaling once the customer base grows past a few tens
    of thousands.
    """
    effective_limit = limit or settings.analysis_rows_cap

    cache_key = analysis_cache._make_key(
        "customers",
        start_date=str(start_date) if start_date else None,
        end_date=str(end_date) if end_date else None,
        min_orders=min_orders,
        platform=platform,
        search=search,
        limit=effective_limit,
        offset=offset,
    )
    cached = await analysis_cache.get(cache_key)
    if cached is not None:
        response.headers["X-Total-Count"] = str(cached["_total"])
        return cached["rows"]

    await _ensure_data(session)

    pf = _platform_filter(platform)

    base_filters = [Order.customer_key.isnot(None)]
    if start_date:
        base_filters.append(Order.order_date >= start_date)
    if end_date:
        base_filters.append(Order.order_date <= end_date)
    if pf is not None:
        base_filters.append(pf)
    if search:
        pattern = f"%{search}%"
        base_filters.append(or_(*(col.ilike(pattern) for col in _SEARCH_COLUMNS)))

    grouped = (
        select(
            Order.customer_key.label("customer_key"),
            func.min(Order.order_date).label("first_date"),
            func.max(Order.order_date).label("last_date"),
            func.count(Order.id).label("orders"),
            func.sum(Order.price).label("revenue"),
            func.min(Order.receiver).label("receiver"),
            func.min(Order.province).label("province"),
            func.min(Order.area).label("area"),
            func.min(Order.full_address).label("full_address"),
            func.min(Order.buyer_nick).label("buyer_nick"),
            func.min(Order.coupon_name).label("coupon_name"),
            func.min(Order.distributor).label("distributor"),
            func.min(Order.receiver_phone).label("phone"),
        )
        .where(*base_filters)
        .group_by(Order.customer_key)
    )
    if min_orders:
        grouped = grouped.having(func.count(Order.id) >= min_orders)
    grouped_subq = grouped.subquery()

    total = (
        await session.execute(select(func.count()).select_from(grouped_subq))
    ).scalar() or 0

    # customer_key as a tie-breaker: revenue ties are common (e.g. two
    # single-order customers with the same price), and without a stable
    # secondary sort, Postgres is free to return ties in a different order on
    # each call — which would corrupt pagination (a customer could appear on
    # two pages, or neither).
    page_stmt = (
        select(grouped_subq)
        .order_by(grouped_subq.c.revenue.desc(), grouped_subq.c.customer_key)
        .offset(offset)
        .limit(effective_limit)
    )
    result = await session.execute(page_stmt)
    rows = [
        {
            "customer_key": r.customer_key,
            "mobile": r.phone,
            "first_date": str(r.first_date) if r.first_date else None,
            "last_date": str(r.last_date) if r.last_date else None,
            "orders": r.orders,
            "revenue": float(r.revenue or 0),
            "receiver": r.receiver,
            "province": r.province,
            "area": r.area,
            "full_address": r.full_address,
            "buyer_nick": r.buyer_nick,
            "coupon_name": r.coupon_name,
            "distributor": r.distributor,
            "phone": r.phone,
        }
        for r in result.all()
    ]

    await analysis_cache.set(cache_key, {"rows": rows, "_total": total})
    response.headers["X-Total-Count"] = str(total)
    return rows


@router.get("/customers/{customer_id}", summary="Orders for a specific customer")
async def customer_orders(
    customer_id: str,
    start_date: dt.date | None = Query(None),
    end_date: dt.date | None = Query(None),
    _u=Depends(current_analyst_user),
    session: AsyncSession = Depends(get_session),
):
    cache_key = analysis_cache._make_key(
        "customer_orders",
        customer_id=customer_id,
        start_date=str(start_date) if start_date else None,
        end_date=str(end_date) if end_date else None,
    )
    cached = await analysis_cache.get(cache_key)
    if cached is not None:
        return cached

    await _ensure_data(session)

    stmt = select(Order).where(Order.customer_key == customer_id)

    if start_date:
        stmt = stmt.where(Order.order_date >= start_date)
    if end_date:
        stmt = stmt.where(Order.order_date <= end_date)

    stmt = stmt.order_by(Order.order_date)

    result = await session.execute(stmt)
    orders = result.scalars().all()

    if not orders:
        raise HTTPException(status_code=404, detail="Customer not found")

    rows = [
        {
            "order_date": str(o.order_date),
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

    total_spend = sum(float(o.price or 0) for o in orders)

    result_data = {"orders": rows, "count": len(rows), "total_spend": total_spend}
    await analysis_cache.set(cache_key, result_data)
    return result_data


@router.get("/field_coverage", summary="Non-null coverage rate for orders columns")
async def field_coverage(
    _u=Depends(current_analyst_user),
    session: AsyncSession = Depends(get_session),
):
    """Return non-null rate for each nullable column in the orders table."""
    total = (await session.execute(select(func.count(Order.id)))).scalar() or 0
    if total == 0:
        return {"total_rows": 0, "columns": {}}

    nullable_cols = [
        "sku", "quantity", "price", "receiver", "receiver_phone",
        "province", "area", "full_address", "buyer_nick", "coupon_name", "distributor",
    ]
    count_exprs = [func.count(getattr(Order, c)).label(c) for c in nullable_cols]
    row = (await session.execute(select(*count_exprs))).one()

    return {
        "total_rows": total,
        "columns": {c: round(getattr(row, c) / total, 4) for c in nullable_cols},
    }
