# rap/app/views/ecommerce/analysis/segmentation.py
"""Old-vs-new customer segmentation and dataset overview endpoints."""
from __future__ import annotations
import datetime as dt

from fastapi import Depends, Query
from sqlalchemy import select, func, case
from sqlalchemy.ext.asyncio import AsyncSession

from ....auth import current_analyst_user
from ....config import settings
from ....db import get_session
from ....db.models import Order
from ....utils.logger import log_operation
from ....utils.cache import analysis_cache
from ._common import router, _ensure_data, _platform_filter, _window


@router.get("/", summary="Old vs new customer breakdown")
async def analyse(
    start_date: dt.date = Query(...),
    end_date:   dt.date = Query(...),
    platform: str | None = Query(None),
    include_rows: bool = Query(
        True,
        description="Set false to skip the raw-row payload (up to 2*rows_cap rows) "
        "when only the aggregates/daily series are needed, e.g. the overview page's "
        "trend chart.",
    ),
    _u = Depends(current_analyst_user),
    session: AsyncSession = Depends(get_session),
):
    cache_key = analysis_cache._make_key(
        "analyse",
        start_date=str(start_date),
        end_date=str(end_date),
        platform=platform,
        include_rows=include_rows,
    )
    cached = await analysis_cache.get(cache_key)
    if cached is not None:
        return cached

    await _ensure_data(session)

    pf = _platform_filter(platform)
    rows_cap = settings.analysis_rows_cap

    # ── Subquery: earliest order date per customer (platform-aware) ───────────
    first_subq = select(
        Order.customer_key,
        func.min(Order.order_date).label("first_date"),
    ).where(Order.customer_key.isnot(None))
    if pf is not None:
        first_subq = first_subq.where(pf)
    first_subq = first_subq.group_by(Order.customer_key).subquery()

    # ── Old/new label per order in window ────────────────────────────────────
    # An order is "old" if its customer's first order predates the window start.
    is_old = (first_subq.c.first_date < start_date)

    base = (
        select(
            Order.order_date,
            Order.customer_key,
            Order.sku,
            Order.price,
            Order.platform,
            case((is_old, True), else_=False).label("is_old"),
        )
        .join(first_subq, Order.customer_key == first_subq.c.customer_key)
        .where(Order.order_date.between(start_date, end_date))
    )
    if pf is not None:
        base = base.where(pf)
    base = base.subquery()

    # ── Aggregate totals per segment ─────────────────────────────────────────
    seg_rows = (await session.execute(
        select(
            base.c.is_old,
            func.count().label("order_count"),
            func.count(func.distinct(base.c.customer_key)).label("customer_count"),
            func.sum(base.c.price).label("paid_sum"),
        ).group_by(base.c.is_old)
    )).all()

    old_count = new_count = 0
    old_cust = new_cust = 0
    old_paid = new_paid = 0.0
    for r in seg_rows:
        if r.is_old:
            old_count, old_cust, old_paid = r.order_count, r.customer_count, float(r.paid_sum or 0)
        else:
            new_count, new_cust, new_paid = r.order_count, r.customer_count, float(r.paid_sum or 0)

    # ── Daily unique-customer counts per segment ─────────────────────────────
    daily_rows = (await session.execute(
        select(
            base.c.is_old,
            base.c.order_date,
            func.count(func.distinct(base.c.customer_key)).label("customers"),
        ).group_by(base.c.is_old, base.c.order_date)
    )).all()

    old_daily: dict[str, int] = {}
    new_daily: dict[str, int] = {}
    for r in daily_rows:
        key = str(r.order_date)
        if r.is_old:
            old_daily[key] = r.customers
        else:
            new_daily[key] = r.customers

    # ── Capped raw rows (for the collapsible detail tables in the UI) ─────────
    # Skipped entirely when the caller only needs aggregates/daily series
    # (e.g. the overview page's trend chart) — this is the expensive part of
    # the query, up to 2*rows_cap rows serialised to JSON.
    if include_rows:
        raw_stmt = (
            select(
                base.c.order_date,
                base.c.customer_key,
                base.c.sku,
                base.c.price,
                base.c.platform,
                base.c.is_old,
            )
            .order_by(base.c.order_date)
            .limit(rows_cap * 2)  # fetch cap*2 then split — keeps one query
        )
        raw_rows = (await session.execute(raw_stmt)).all()

        old_rows_raw = [
            {
                "order_date": str(r.order_date),
                "customer_key": r.customer_key,
                "sku": r.sku,
                "price": float(r.price or 0),
                "platform": r.platform,
            }
            for r in raw_rows if r.is_old
        ][:rows_cap]
        new_rows_raw = [
            {
                "order_date": str(r.order_date),
                "customer_key": r.customer_key,
                "sku": r.sku,
                "price": float(r.price or 0),
                "platform": r.platform,
            }
            for r in raw_rows if not r.is_old
        ][:rows_cap]
    else:
        old_rows_raw, new_rows_raw = [], []

    result_data = {
        "old": {
            "rows": old_rows_raw,
            "rows_capped": len(old_rows_raw) >= rows_cap,
            "rows_cap": rows_cap,
            "count": old_count,
            "customer_count": old_cust,
            "paid_sum": old_paid,
        },
        "new": {
            "rows": new_rows_raw,
            "rows_capped": len(new_rows_raw) >= rows_cap,
            "rows_cap": rows_cap,
            "count": new_count,
            "customer_count": new_cust,
            "paid_sum": new_paid,
        },
        "old_daily": old_daily,
        "new_daily": new_daily,
    }

    await analysis_cache.set(cache_key, result_data)
    await log_operation(
        str(_u.id),
        "analysis",
        {
            "start_date": str(start_date),
            "end_date": str(end_date),
            "platform": platform,
            "old_orders": old_count,
            "new_orders": new_count,
        },
        session=session,
    )
    return result_data


@router.get("/overview", summary="Order dataset overview")
async def analyse_overview(
    start_date: dt.date = Query(...),
    end_date: dt.date = Query(...),
    platform: str | None = Query(None),
    _u=Depends(current_analyst_user),
    session: AsyncSession = Depends(get_session),
):
    """Return overall stats and top products for the dataset."""
    cache_key = analysis_cache._make_key(
        "overview",
        start_date=str(start_date),
        end_date=str(end_date),
        platform=platform,
    )
    cached = await analysis_cache.get(cache_key)
    if cached is not None:
        return cached

    await _ensure_data(session)

    pf = _platform_filter(platform)

    # Scalar summary stats — all resolved in one query to avoid round-trips
    summary_row = (await session.execute(
        _window(
            select(
                func.count(Order.id).label("total_orders"),
                func.sum(Order.price).label("total_revenue"),
                func.avg(Order.price).label("avg_order_value"),
                func.count(func.distinct(Order.customer_key)).label("unique_customers"),
            ),
            start_date, end_date, pf,
        )
    )).one()
    total_orders = int(summary_row.total_orders or 0)
    total_revenue = float(summary_row.total_revenue or 0)
    avg_order_value = float(summary_row.avg_order_value or 0)
    unique_customers = int(summary_row.unique_customers or 0)

    # Top SKUs
    sku_result = await session.execute(
        _window(
            select(
                Order.sku,
                func.count(Order.id).label("orders"),
                func.sum(Order.price).label("revenue"),
            ).group_by(Order.sku).order_by(func.count(Order.id).desc()).limit(5),
            start_date, end_date, pf,
        )
    )
    top_sku = [{"sku": r.sku, "orders": r.orders, "revenue": float(r.revenue or 0)} for r in sku_result.all()]

    # Top provinces by order volume
    prov_result = await session.execute(
        _window(
            select(
                Order.province,
                func.count(Order.id).label("orders"),
                func.sum(Order.price).label("revenue"),
            )
            .where(Order.province.isnot(None))
            .group_by(Order.province)
            .order_by(func.count(Order.id).desc())
            .limit(5),
            start_date, end_date, pf,
        )
    )
    top_province = [{"province": r.province, "orders": r.orders, "revenue": float(r.revenue or 0)} for r in prov_result.all()] or None

    # Top provinces by unique customers
    unique_prov_result = await session.execute(
        _window(
            select(
                Order.province,
                func.count(func.distinct(Order.customer_key)).label("customers"),
            )
            .where(Order.province.isnot(None))
            .group_by(Order.province)
            .order_by(func.count(func.distinct(Order.customer_key)).desc())
            .limit(5),
            start_date, end_date, pf,
        )
    )
    top_province_unique = [{"province": r.province, "customers": r.customers} for r in unique_prov_result.all()] or None

    result_data = {
        "orders": total_orders,
        "revenue": float(total_revenue),
        "aov": float(avg_order_value),
        "unique_customers": unique_customers,
        "top_sku": top_sku,
        "top_province": top_province,
        "top_province_unique": top_province_unique,
    }
    await analysis_cache.set(cache_key, result_data)
    return result_data


@router.get("/latest_order_date", summary="Most recent order_date in the dataset")
async def latest_order_date(
    _u=Depends(current_analyst_user),
    session: AsyncSession = Depends(get_session),
):
    """Return the most recent order_date across all platforms, or None if empty.

    Orders are uploaded in batches rather than captured in real time, so the
    calendar "today" is almost always empty. The KPI dashboard uses this as
    its "as of" anchor instead of ``date.today()``.
    """
    cache_key = analysis_cache._make_key("latest_order_date")
    cached = await analysis_cache.get(cache_key)
    if cached is not None:
        return cached

    latest = (await session.execute(select(func.max(Order.order_date)))).scalar()
    result_data = {"latest_order_date": str(latest) if latest else None}
    await analysis_cache.set(cache_key, result_data)
    return result_data
