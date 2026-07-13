# rap/app/views/ecommerce/analysis.py
from __future__ import annotations
import datetime as dt
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func, case, text, cast, extract, Integer, Date
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from ...auth import current_analyst_user
from ...config import settings
from ...db import get_session
from ...db.models import Order, Customer
from ...utils.logger import log_operation
from ...utils.cache import analysis_cache
from ...utils.sql_validator import validate_sql_query, enforce_limit

router = APIRouter(prefix="/analysis", tags=["analysis"])


class AnalysisDataNotReady(HTTPException):
    """Raised when order data has not been ingested."""

    def __init__(self, detail: str = "Analysis data not initialised. Upload order data first."):
        super().__init__(status_code=503, detail=detail)


async def _has_orders(session: AsyncSession) -> bool:
    result = await session.execute(select(func.count(Order.id)).limit(1))
    return result.scalar() > 0


async def _ensure_data(session: AsyncSession) -> None:
    if not await _has_orders(session):
        raise AnalysisDataNotReady(
            "Analysis data not initialised. Upload order data before using analysis endpoints."
        )


def _platform_filter(platform: str | None):
    if platform:
        return Order.platform == platform
    return None


def _window(stmt, start_date: dt.date, end_date: dt.date, pf):
    """Apply the standard order-date window and optional platform filter.

    Centralises the ``where(order_date.between(...))`` + optional platform
    clause that nearly every analysis query repeats.
    """
    stmt = stmt.where(Order.order_date.between(start_date, end_date))
    if pf is not None:
        stmt = stmt.where(pf)
    return stmt


def _month_index(year_month: str) -> int:
    """Turn a 'YYYY-MM' string into a comparable integer month index."""
    y, m = year_month.split("-")
    return int(y) * 12 + int(m)


def build_cohort_matrix(
    cohort_sizes: dict[str, int],
    per_period_counts: dict[tuple[str, int], int],
    first_repeat_counts: dict[tuple[str, int], int],
    latest_month_index: int,
    max_offset: int = 12,
) -> list[dict]:
    """Assemble the ragged retention matrix for both lenses, with right-censoring.

    Pure function (no DB, no date objects) so it can be unit-tested in isolation,
    mirroring ``compute_content_impact`` / ``build_phone_clusters``.

    Args:
        cohort_sizes: ``{"YYYY-MM": size}`` — distinct customers acquired that month.
        per_period_counts: ``{(cohort_month, offset): distinct_active_customers}``.
        first_repeat_counts: ``{(cohort_month, first_repeat_offset): customers}`` —
            distribution of each customer's *first* repeat-order month offset
            (offset >= 1); customers who never repeat in a later month are absent.
        latest_month_index: ``year*12+month`` of the latest activity month in the
            (platform-filtered) data — the right-censoring horizon.
        max_offset: highest month offset to emit (arrays have length ``max_offset+1``).

    Returns:
        A list of cohort dicts sorted ascending by ``cohort_month``. A cell is
        ``None`` when ``cohort_month_index + offset > latest_month_index``
        (not yet observable); for ``cumulative`` that ``None`` propagates to all
        larger offsets.
    """
    cohorts: list[dict] = []
    for cohort_month in sorted(cohort_sizes):
        size = cohort_sizes[cohort_month]
        cm_index = _month_index(cohort_month)
        max_observable = latest_month_index - cm_index  # largest offset with data

        per_period: list[float | None] = []
        for n in range(max_offset + 1):
            if n > max_observable:
                per_period.append(None)
            else:
                count = per_period_counts.get((cohort_month, n), 0)
                per_period.append(round(count / size, 4) if size else 0.0)

        cumulative: list[float | None] = [0.0]  # offset 0: no later-month repeat yet
        acc = 0
        for n in range(1, max_offset + 1):
            if n > max_observable:
                cumulative.append(None)
            else:
                acc += first_repeat_counts.get((cohort_month, n), 0)
                cumulative.append(round(acc / size, 4) if size else 0.0)

        cohorts.append({
            "cohort_month": cohort_month,
            "cohort_size": size,
            "per_period": per_period,
            "cumulative": cumulative,
        })
    return cohorts


@router.get("/", summary="Old vs new customer breakdown")
async def analyse(
    start_date: dt.date = Query(...),
    end_date:   dt.date = Query(...),
    platform: str | None = Query(None),
    _u = Depends(current_analyst_user),
    session: AsyncSession = Depends(get_session),
):
    cache_key = analysis_cache._make_key(
        "analyse",
        start_date=str(start_date),
        end_date=str(end_date),
        platform=platform,
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


@router.get("/repurchase_rate", summary="Repurchase rate for new customers")
async def repurchase_rate(
    start_date: dt.date = Query(...),
    end_date: dt.date = Query(...),
    platform: str | None = Query(None),
    window_days: int | None = Query(
        None, ge=1,
        description="Repurchase window in days (e.g. 60, 90, 180, 365). Omit for all-time.",
    ),
    _u=Depends(current_analyst_user),
    session: AsyncSession = Depends(get_session),
):
    cache_key = analysis_cache._make_key(
        "repurchase_rate",
        start_date=str(start_date),
        end_date=str(end_date),
        platform=platform,
        window_days=window_days,
    )
    cached = await analysis_cache.get(cache_key)
    if cached is not None:
        return cached

    await _ensure_data(session)

    pf = _platform_filter(platform)

    # ── Subquery 1: first_date + total order count per customer ──────────────
    first_subq = select(
        Order.customer_key,
        func.min(Order.order_date).label("first_date"),
        func.count(Order.id).label("order_count"),
    ).where(Order.customer_key.isnot(None))
    if pf is not None:
        first_subq = first_subq.where(pf)
    first_subq = first_subq.group_by(Order.customer_key).subquery()

    # ── Subquery 2: earliest return date (first date strictly after first_date)
    Order2 = aliased(Order)
    second_join_cond = (
        (Order2.customer_key == first_subq.c.customer_key)
        & (Order2.order_date > first_subq.c.first_date)
    )
    if platform:
        second_join_cond = second_join_cond & (Order2.platform == platform)
    second_subq = (
        select(
            first_subq.c.customer_key,
            func.min(Order2.order_date).label("second_date"),
        )
        .select_from(first_subq)
        .join(Order2, second_join_cond)
        .group_by(first_subq.c.customer_key)
        .subquery()
    )

    # ── Joined view: new customers only (first_date in acquisition window) ───
    new_customers_subq = (
        select(
            first_subq.c.customer_key,
            first_subq.c.first_date,
            first_subq.c.order_count,
            second_subq.c.second_date,
        )
        .select_from(first_subq)
        .join(second_subq, first_subq.c.customer_key == second_subq.c.customer_key, isouter=True)
        .where(first_subq.c.first_date.between(start_date, end_date))
        .subquery()
    )

    # ── Repurchase condition (window-aware or all-time) ──────────────────────
    if window_days is not None:
        repurchase_cond = (
            new_customers_subq.c.second_date.isnot(None)
            & ((new_customers_subq.c.second_date - new_customers_subq.c.first_date) <= window_days)
        )
    else:
        repurchase_cond = new_customers_subq.c.second_date.isnot(None)

    # ── Main aggregation ─────────────────────────────────────────────────────
    main_row = (
        await session.execute(
            select(
                func.count().label("new_customers"),
                func.sum(case((repurchase_cond, 1), else_=0)).label("repurchasing_customers"),
                func.avg(
                    case(
                        (repurchase_cond, new_customers_subq.c.second_date - new_customers_subq.c.first_date),
                        else_=None,
                    )
                ).label("avg_days_to_repurchase"),
            )
        )
    ).one()

    # ── Frequency distribution: new customers by lifetime order-count bucket ─
    bucket_expr = case(
        (new_customers_subq.c.order_count == 1, "1"),
        (new_customers_subq.c.order_count == 2, "2"),
        (new_customers_subq.c.order_count == 3, "3"),
        else_="4+",
    )
    freq_rows = (
        await session.execute(
            select(bucket_expr.label("bucket"), func.count().label("customers"))
            .group_by(bucket_expr)
        )
    ).all()
    frequency_distribution = {r.bucket: r.customers for r in freq_rows}

    new_customers = int(main_row.new_customers or 0)
    repurchasing_customers = int(main_row.repurchasing_customers or 0)
    rate = repurchasing_customers / new_customers if new_customers else 0.0
    avg_days = (
        round(float(main_row.avg_days_to_repurchase), 1)
        if main_row.avg_days_to_repurchase is not None
        else None
    )

    result_data = {
        "new_customers": new_customers,
        "repurchasing_customers": repurchasing_customers,
        "repurchase_rate": rate,
        "avg_days_to_repurchase": avg_days,
        "frequency_distribution": frequency_distribution,
        "window_days": window_days,
    }
    await analysis_cache.set(cache_key, result_data)
    return result_data


@router.get("/cohort_retention", summary="Cohort retention analysis")
async def cohort_retention(
    start_date: dt.date | None = Query(None, description="只看首单月在此之后的队列"),
    end_date: dt.date | None = Query(None, description="只看首单月在此之前的队列"),
    platform: str | None = Query(None),
    max_offset: int = Query(12, ge=1, le=36, description="最大月偏移（数组长度 = max_offset+1）"),
    _u=Depends(current_analyst_user),
    session: AsyncSession = Depends(get_session),
):
    """Monthly cohort retention: group customers by first-order month, then track
    both per-period retention and cumulative repeat-purchase across month offsets.
    """
    cache_key = analysis_cache._make_key(
        "cohort_retention",
        start_date=str(start_date) if start_date else None,
        end_date=str(end_date) if end_date else None,
        platform=platform,
        max_offset=max_offset,
    )
    cached = await analysis_cache.get(cache_key)
    if cached is not None:
        return cached

    await _ensure_data(session)

    pf = _platform_filter(platform)

    # ── cohort_subq: each customer's acquisition month ───────────────────────
    cohort_month_col = func.date_trunc("month", func.min(Order.order_date)).label("cohort_month")
    cohort_q = select(Order.customer_key, cohort_month_col).where(Order.customer_key.isnot(None))
    if pf is not None:
        cohort_q = cohort_q.where(pf)
    cohort_q = cohort_q.group_by(Order.customer_key)
    if start_date is not None:
        cohort_q = cohort_q.having(
            func.min(Order.order_date) >= func.date_trunc("month", cast(start_date, Date))
        )
    if end_date is not None:
        cohort_q = cohort_q.having(
            func.date_trunc("month", func.min(Order.order_date))
            <= func.date_trunc("month", cast(end_date, Date))
        )
    cohort_subq = cohort_q.subquery()

    # ── cohort sizes ─────────────────────────────────────────────────────────
    # Format the month label server-side via to_char so only a plain "YYYY-MM"
    # string crosses into Python. Pulling the raw date_trunc timestamp back and
    # reading .year/.month is timezone-unsafe: under APP_TIMEZONE=Asia/Shanghai
    # the naive midnight-first-of-month shifts back across the month boundary.
    size_rows = (
        await session.execute(
            select(
                func.to_char(cohort_subq.c.cohort_month, "YYYY-MM").label("cohort_month"),
                func.count().label("size"),
            ).group_by(cohort_subq.c.cohort_month)
        )
    ).all()
    cohort_sizes = {r.cohort_month: int(r.size) for r in size_rows}
    if not cohort_sizes:
        result_data = {
            "cohorts": [],
            "max_offset": max_offset,
            "latest_data_month": None,
            "platform": platform,
            "start_date": str(start_date) if start_date else None,
            "end_date": str(end_date) if end_date else None,
        }
        await analysis_cache.set(cache_key, result_data)
        return result_data

    # ── shared activity set: (cohort_month, customer_key, month_offset) ──────
    active_month = func.date_trunc("month", Order.order_date)
    month_offset = cast(
        (extract("year", active_month) * 12 + extract("month", active_month))
        - (extract("year", cohort_subq.c.cohort_month) * 12 + extract("month", cohort_subq.c.cohort_month)),
        Integer,
    ).label("month_offset")

    activity_q = (
        select(cohort_subq.c.cohort_month, Order.customer_key, month_offset)
        .select_from(Order)
        .join(cohort_subq, Order.customer_key == cohort_subq.c.customer_key)
    )
    if pf is not None:
        activity_q = activity_q.where(pf)
    activity = activity_q.where(month_offset <= max_offset).distinct().subquery()

    # ── per-period: distinct active customers per (cohort_month, offset) ─────
    pp_rows = (
        await session.execute(
            select(
                func.to_char(activity.c.cohort_month, "YYYY-MM").label("cohort_month"),
                activity.c.month_offset,
                func.count().label("n"),
            ).group_by(activity.c.cohort_month, activity.c.month_offset)
        )
    ).all()
    per_period_counts = {
        (r.cohort_month, int(r.month_offset)): int(r.n) for r in pp_rows
    }

    # ── first-repeat distribution (for cumulative) ───────────────────────────
    first_repeat = (
        select(
            activity.c.cohort_month,
            activity.c.customer_key,
            func.min(activity.c.month_offset)
            .filter(activity.c.month_offset > 0)
            .label("fro"),
        )
        .group_by(activity.c.cohort_month, activity.c.customer_key)
        .subquery()
    )
    fr_rows = (
        await session.execute(
            select(
                func.to_char(first_repeat.c.cohort_month, "YYYY-MM").label("cohort_month"),
                first_repeat.c.fro,
                func.count().label("n"),
            )
            .where(first_repeat.c.fro.isnot(None))
            .group_by(first_repeat.c.cohort_month, first_repeat.c.fro)
        )
    ).all()
    first_repeat_counts = {
        (r.cohort_month, int(r.fro)): int(r.n) for r in fr_rows
    }

    # ── right-censoring horizon ──────────────────────────────────────────────
    latest_q = select(
        func.to_char(func.max(func.date_trunc("month", Order.order_date)), "YYYY-MM")
    )
    if pf is not None:
        latest_q = latest_q.where(pf)
    latest_str = (await session.execute(latest_q)).scalar()
    latest_month_index = _month_index(latest_str)

    cohorts = build_cohort_matrix(
        cohort_sizes, per_period_counts, first_repeat_counts, latest_month_index, max_offset
    )

    result_data = {
        "cohorts": cohorts,
        "max_offset": max_offset,
        "latest_data_month": latest_str,
        "platform": platform,
        "start_date": str(start_date) if start_date else None,
        "end_date": str(end_date) if end_date else None,
    }
    await analysis_cache.set(cache_key, result_data)
    return result_data


@router.get("/customers", summary="Aggregated customer metrics")
async def customers(
    start_date: dt.date | None = Query(None),
    end_date: dt.date | None = Query(None),
    min_orders: int | None = Query(None, ge=1),
    platform: str | None = Query(None),
    _u=Depends(current_analyst_user),
    session: AsyncSession = Depends(get_session),
):
    """Return per-customer order metrics sorted by revenue."""

    cache_key = analysis_cache._make_key(
        "customers",
        start_date=str(start_date) if start_date else None,
        end_date=str(end_date) if end_date else None,
        min_orders=min_orders,
        platform=platform,
    )
    cached = await analysis_cache.get(cache_key)
    if cached is not None:
        return cached

    await _ensure_data(session)

    pf = _platform_filter(platform)

    stmt = select(
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
    ).where(Order.customer_key.isnot(None))

    if start_date:
        stmt = stmt.where(Order.order_date >= start_date)
    if end_date:
        stmt = stmt.where(Order.order_date <= end_date)
    if pf is not None:
        stmt = stmt.where(pf)

    stmt = stmt.group_by(Order.customer_key)

    if min_orders:
        stmt = stmt.having(func.count(Order.id) >= min_orders)

    stmt = stmt.order_by(func.sum(Order.price).desc())

    result = await session.execute(stmt)
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

    await analysis_cache.set(cache_key, rows)
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


class SqlQueryRequest(BaseModel):
    """Request body for POST /analysis/sql."""
    sql: str


class NLSqlRequest(BaseModel):
    """Request body for POST /analysis/nl-sql.

    ``provider`` / ``model`` are the user's dropdown selection; both optional and
    fall back to the server defaults. Only the provider id + model name travel —
    API keys stay server-side.
    """
    question: str
    provider: str | None = None
    model: str | None = None


@router.post("/sql", summary="Ad-hoc SQL query console (analyst+)")
async def run_sql_query(
    body: SqlQueryRequest,
    _u=Depends(current_analyst_user),
    session: AsyncSession = Depends(get_session),
):
    try:
        validate_sql_query(body.sql)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        safe_sql = enforce_limit(body.sql)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        await session.execute(text("SET LOCAL transaction_read_only = on"))
        await session.execute(text("SET LOCAL statement_timeout = '10000'"))
        result = await session.execute(text(safe_sql))
        rows = result.fetchall()
        columns: list[str] = list(result.keys())
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Query execution error: {exc}",
        )

    row_data = [list(row) for row in rows]
    row_count = len(row_data)

    # session is read-only (SET LOCAL transaction_read_only = on); open a
    # separate connection for the log write rather than passing session=.
    await log_operation(
        str(_u.id),
        "sql_query",
        {"sql": body.sql, "row_count": row_count},
    )

    return {"rows": row_data, "columns": columns, "row_count": row_count}


@router.get("/nl-sql/providers", summary="中文问数据可用服务商与模型 (analyst+)")
async def nl_sql_providers(_u=Depends(current_analyst_user)):
    """Return the AI providers that have an API key configured server-side, plus
    each one's selectable models, so the UI can populate provider/model dropdowns.
    """
    from ...utils.nl_to_sql import available_providers, default_provider_id

    return {
        "providers": available_providers(),
        "default_provider": default_provider_id(),
    }


@router.post("/nl-sql", summary="中文问数据：自然语言 → SQL → 执行 (analyst+)")
async def run_nl_sql(
    body: NLSqlRequest,
    _u=Depends(current_analyst_user),
    session: AsyncSession = Depends(get_session),
):
    """Translate a Chinese question into SQL, then run it through the exact same
    read-only safety pipeline as the manual SQL console.

    Always returns the generated SQL (even on failure) so the UI can show what
    was attempted — transparency is what makes the feature trustworthy. ``error``
    is non-null when generation, validation, or execution failed.
    """
    from ...utils.nl_to_sql import (
        generate_sql,
        NLToSQLNotConfigured,
        NLToSQLError,
    )

    question = (body.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="请输入问题。")

    try:
        sql, explanation = await generate_sql(question, body.provider, body.model)
    except NLToSQLNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except NLToSQLError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    result: dict[str, Any] = {
        "question": question,
        "sql": sql,
        "explanation": explanation,
        "rows": [],
        "columns": [],
        "row_count": 0,
        "error": None,
    }

    if not sql:
        result["error"] = explanation or "无法将该问题转换为查询。"
        return result

    # Reuse the SQL console guards — never trust model-generated SQL.
    try:
        validate_sql_query(sql)
        safe_sql = enforce_limit(sql)
    except ValueError as exc:
        result["error"] = f"生成的 SQL 未通过安全校验：{exc}"
        return result
    result["sql"] = safe_sql

    try:
        await session.execute(text("SET LOCAL transaction_read_only = on"))
        await session.execute(text("SET LOCAL statement_timeout = '10000'"))
        exec_result = await session.execute(text(safe_sql))
        rows = exec_result.fetchall()
        columns = list(exec_result.keys())
    except Exception as exc:  # noqa: BLE001 - report execution errors to the UI
        result["error"] = f"查询执行错误：{exc}"
        await log_operation(
            str(_u.id),
            "nl_sql_query",
            {
                "question": question,
                "sql": safe_sql,
                "provider": body.provider,
                "model": body.model,
                "error": str(exc)[:500],
            },
        )
        return result

    row_data = [list(row) for row in rows]
    result["rows"] = row_data
    result["columns"] = columns
    result["row_count"] = len(row_data)

    await log_operation(
        str(_u.id),
        "nl_sql_query",
        {
            "question": question,
            "sql": safe_sql,
            "provider": body.provider,
            "model": body.model,
            "row_count": len(row_data),
        },
    )
    return result
