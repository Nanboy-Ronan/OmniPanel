# rap/app/views/ecommerce/analysis/retention.py
"""Repurchase-rate and monthly cohort-retention endpoints."""
from __future__ import annotations
import datetime as dt

from fastapi import Depends, Query
from sqlalchemy import select, func, case, cast, extract, Integer, Date
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from ....auth import current_analyst_user
from ....db import get_session
from ....db.models import Order
from ....utils.cache import analysis_cache
from ._common import router, _ensure_data, _platform_filter


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
