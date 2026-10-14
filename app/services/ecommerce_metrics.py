"""Shared commerce aggregates for the dashboard and authenticated integrations."""
import datetime as dt
from fastapi import HTTPException
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from ..db.models import Order
from ..db.order_status import COUNTED_GROUPS, net_amount, counted

def _platform_filter(platform: str | None):
    if platform:
        return Order.platform == platform
    return None

def _window(stmt, start_date: dt.date, end_date: dt.date, pf):
    """Apply the standard order-date window, optional platform filter and status rule.

    Centralises the ``where(order_date.between(...))`` + optional platform
    clause that nearly every analysis query repeats. Cancelled and unpaid orders
    are excluded (app/db/order_status.py).
    """
    stmt = stmt.where(Order.order_date.between(start_date, end_date), counted())
    if pf is not None:
        stmt = stmt.where(pf)
    return stmt

def _metrics():
    return (
        func.count(Order.id).label("orders"),
        func.count(Order.price).label("priced_orders"),
        func.coalesce(func.sum(net_amount()), 0).label("revenue"),
        func.count(func.distinct(Order.customer_key)).label("customers"),
    )

def _values(row=None):
    orders = int(row["orders"]) if row else 0
    priced = int(row["priced_orders"]) if row else 0
    revenue = float(row["revenue"]) if row else 0.0
    return {
        "orders": orders,
        "priced_orders": priced,
        "revenue": round(revenue, 2),
        "customers": int(row["customers"]) if row else 0,
        "aov": round(revenue / priced, 2) if priced else None,
        "missing_amount": orders - priced,
    }

async def commerce_metrics(session: AsyncSession, start_date: dt.date, end_date: dt.date, platform: str | None = None):
    days = (end_date - start_date).days + 1
    if not 1 <= days <= 1096 or start_date.toordinal() <= days:
        raise HTTPException(422, "请选择有效日期范围，最长支持 1096 天。")
    prior_start, prior_end = start_date - dt.timedelta(
        days=days
    ), start_date - dt.timedelta(days=1)
    pf = _platform_filter(platform)
    period = case((Order.order_date >= start_date, "current"), else_="prior").label(
        "period"
    )

    summary_rows = (
        (
            await session.execute(
                _window(
                    select(period, *_metrics()).group_by(period),
                    prior_start,
                    end_date,
                    pf,
                )
            )
        )
        .mappings()
        .all()
    )
    summaries = {r["period"]: _values(r) for r in summary_rows}
    daily_rows = (
        (
            await session.execute(
                _window(
                    select(Order.order_date.label("date"), *_metrics()).group_by(
                        Order.order_date
                    ),
                    prior_start,
                    end_date,
                    pf,
                )
            )
        )
        .mappings()
        .all()
    )
    daily = {r["date"]: _values(r) for r in daily_rows}
    series = []
    for offset in range(days):
        day = start_date + dt.timedelta(days=offset)
        prior_day = prior_start + dt.timedelta(days=offset)
        series.append(
            {
                "date": day.isoformat(),
                "prior_date": prior_day.isoformat(),
                **daily.get(day, _values()),
                **{f"prior_{k}": v for k, v in daily.get(prior_day, _values()).items()},
            }
        )

    channel_rows = (
        (
            await session.execute(
                _window(
                    select(Order.platform, period, *_metrics()).group_by(
                        Order.platform, period
                    ),
                    prior_start,
                    end_date,
                    pf,
                )
            )
        )
        .mappings()
        .all()
    )
    channels = {}
    for row in channel_rows:
        name = row["platform"]
        channel = channels.setdefault(
            name, {"platform": name, "current": _values(), "prior": _values()}
        )
        channel[row["period"]] = _values(row)

    async def ranking(column, missing, limit):
        name = func.coalesce(func.nullif(func.trim(column), ""), missing).label("name")
        rows = (
            (
                await session.execute(
                    _window(
                        select(name, *_metrics())
                        .group_by(name)
                        .order_by(func.coalesce(func.sum(net_amount()), 0).desc(), name)
                        .limit(limit),
                        start_date,
                        end_date,
                        pf,
                    )
                )
            )
            .mappings()
            .all()
        )
        return [{"name": r["name"], **_values(r)} for r in rows]

    counted_flag = Order.status_group.in_(COUNTED_GROUPS)
    status_stmt = select(
        func.count(case((~counted_flag, Order.id))).label("excluded_orders"),
        func.coalesce(func.sum(case((~counted_flag, Order.price))), 0).label("excluded_amount"),
        func.coalesce(func.sum(case((counted_flag, Order.refunded_amount))), 0).label("refunds"),
        func.count(case((Order.status_group == "unknown", Order.id))).label("unknown_orders"),
        func.count(case((Order.status_group == "deleted", Order.id))).label("deleted_orders"),
        func.coalesce(
            func.sum(case((Order.status_group == "deleted", net_amount()))), 0
        ).label("deleted_amount"),
    ).where(Order.order_date.between(start_date, end_date))
    if pf is not None:
        status_stmt = status_stmt.where(pf)
    status = (await session.execute(status_stmt)).mappings().one()

    return {
        "status": {
            "excluded_orders": int(status["excluded_orders"]),
            "excluded_amount": round(float(status["excluded_amount"]), 2),
            "refunds": round(float(status["refunds"]), 2),
            "unknown_orders": int(status["unknown_orders"]),
            "deleted_orders": int(status["deleted_orders"]),
            "deleted_amount": round(float(status["deleted_amount"]), 2),
        },
        "start_date": str(start_date),
        "end_date": str(end_date),
        "prior_start": str(prior_start),
        "prior_end": str(prior_end),
        "current": summaries.get("current", _values()),
        "prior": summaries.get("prior", _values()),
        "series": series,
        "channels": sorted(
            channels.values(), key=lambda r: (-r["current"]["revenue"], r["platform"])
        ),
        "products": await ranking(Order.sku, "未标注商品", 10),
        "regions": await ranking(Order.province, "未标注地区", 12),
    }
