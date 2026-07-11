from __future__ import annotations

from datetime import date, timedelta

import streamlit as st

from app.ui._helpers import _page_hero


def _week_range(d: date) -> tuple[date, date]:
    start = d - timedelta(days=d.weekday())
    return start, d


def _month_range(d: date) -> tuple[date, date]:
    return d.replace(day=1), d


def _prior_same_length_week(this_start: date, this_end: date) -> tuple[date, date]:
    """Same weekday-count window in the prior week, anchored to that week's Monday.

    Comparing a partial "week to date" against a *full* prior week always
    looks like a crash on Monday/Tuesday. Matching the day-count instead
    makes the comparison fair.
    """
    day_count = (this_end - this_start).days + 1
    prior_start = this_start - timedelta(weeks=1)
    prior_end = prior_start + timedelta(days=day_count - 1)
    return prior_start, prior_end


def _prior_same_length_month(this_start: date, this_end: date) -> tuple[date, date]:
    """Same day-count window in the prior calendar month, anchored to its 1st.

    Capped to the prior month's actual length (e.g. comparing Mar 29-31
    against Feb, which may only have 28 days).
    """
    day_count = (this_end - this_start).days + 1
    prev_month_last_day = this_start - timedelta(days=1)
    prev_month_start = prev_month_last_day.replace(day=1)
    prev_month_len = (prev_month_last_day - prev_month_start).days + 1
    n = min(day_count, prev_month_len)
    prior_end = prev_month_start + timedelta(days=n - 1)
    return prev_month_start, prior_end


def _fetch_overview(client, start: date, end: date) -> dict | None:
    try:
        r = client.overview(start, end)
    except Exception:
        return None
    if r.status_code != 200:
        return None
    return r.json()


def _delta_str(current: float, prior: float, fmt: str = ".0f") -> tuple[str, str]:
    """Return (delta_text, delta_color) for st.metric."""
    if prior == 0:
        return "—", "off"
    pct = (current - prior) / prior * 100
    sign = "+" if pct >= 0 else ""
    color = "normal" if pct >= 0 else "inverse"
    return f"{sign}{pct:{fmt}}%", color


def page_kpi_overview() -> None:
    _page_hero("KPI 看板")

    client = st.session_state["client"]

    r_latest = client.latest_order_date()
    latest_str = r_latest.json().get("latest_order_date") if r_latest.status_code == 200 else None
    if not latest_str:
        st.info("暂无订单数据，请先上传文件。")
        return
    anchor = date.fromisoformat(latest_str)
    prior_day = anchor - timedelta(days=1)

    # Date ranges — all anchored to the latest uploaded order date, not the
    # real calendar today, since orders arrive in manual upload batches
    # rather than in real time.
    this_week_start, this_week_end = _week_range(anchor)
    last_week_start, last_week_end = _prior_same_length_week(this_week_start, this_week_end)

    this_month_start, this_month_end = _month_range(anchor)
    prev_month_start, prev_month_end = _prior_same_length_month(this_month_start, this_month_end)

    with st.spinner("加载 KPI 数据…"):
        anchor_data     = _fetch_overview(client, anchor, anchor)
        prior_day_data  = _fetch_overview(client, prior_day, prior_day)
        this_week_data  = _fetch_overview(client, this_week_start, this_week_end)
        last_week_data  = _fetch_overview(client, last_week_start, last_week_end)
        this_mon_data   = _fetch_overview(client, this_month_start, this_month_end)
        prev_mon_data   = _fetch_overview(client, prev_month_start, prev_month_end)

    def _get(d: dict | None, key: str, default: float = 0.0) -> float:
        return float((d or {}).get(key, default) or default)

    # ── 最新数据日 vs 前一日 ─────────────────────────────────────────────────
    st.markdown(f"#### 最新数据日（{anchor}）")
    c1, c2, c3, c4 = st.columns(4)
    tod_orders = _get(anchor_data, "orders")
    yest_orders = _get(prior_day_data, "orders")
    d_orders, d_orders_color = _delta_str(tod_orders, yest_orders)
    c1.metric("订单数", int(tod_orders), delta=d_orders, delta_color=d_orders_color, help=f"{anchor} vs {prior_day}")

    tod_rev = _get(anchor_data, "revenue")
    yest_rev = _get(prior_day_data, "revenue")
    d_rev, d_rev_color = _delta_str(tod_rev, yest_rev)
    c2.metric("营业额", f"¥{tod_rev:,.0f}", delta=d_rev, delta_color=d_rev_color, help=f"{anchor} vs {prior_day}")

    tod_aov = _get(anchor_data, "aov")
    yest_aov = _get(prior_day_data, "aov")
    d_aov, d_aov_color = _delta_str(tod_aov, yest_aov)
    c3.metric("客单价", f"¥{tod_aov:,.2f}", delta=d_aov, delta_color=d_aov_color, help=f"{anchor} vs {prior_day}")

    tod_cust = _get(anchor_data, "unique_customers")
    yest_cust = _get(prior_day_data, "unique_customers")
    d_cust, d_cust_color = _delta_str(tod_cust, yest_cust)
    c4.metric("独立客户数", int(tod_cust), delta=d_cust, delta_color=d_cust_color, help=f"{anchor} vs {prior_day}")

    st.markdown("---")

    # ── 本周至今 vs 上周同期 ─────────────────────────────────────────────────
    st.markdown("#### 本周至今")
    wk_help = f"{this_week_start}–{this_week_end} vs {last_week_start}–{last_week_end}（同天数对比）"
    wc1, wc2, wc3, wc4 = st.columns(4)
    wk_orders = _get(this_week_data, "orders")
    lwk_orders = _get(last_week_data, "orders")
    dw_o, dw_o_c = _delta_str(wk_orders, lwk_orders)
    wc1.metric("订单数", int(wk_orders), delta=dw_o, delta_color=dw_o_c, help=wk_help)

    wk_rev = _get(this_week_data, "revenue")
    lwk_rev = _get(last_week_data, "revenue")
    dw_r, dw_r_c = _delta_str(wk_rev, lwk_rev)
    wc2.metric("营业额", f"¥{wk_rev:,.0f}", delta=dw_r, delta_color=dw_r_c, help=wk_help)

    wk_aov = _get(this_week_data, "aov")
    lwk_aov = _get(last_week_data, "aov")
    dw_a, dw_a_c = _delta_str(wk_aov, lwk_aov)
    wc3.metric("客单价", f"¥{wk_aov:,.2f}", delta=dw_a, delta_color=dw_a_c, help=wk_help)

    wk_cust = _get(this_week_data, "unique_customers")
    lwk_cust = _get(last_week_data, "unique_customers")
    dw_c, dw_c_c = _delta_str(wk_cust, lwk_cust)
    wc4.metric("独立客户数", int(wk_cust), delta=dw_c, delta_color=dw_c_c, help=wk_help)

    st.markdown("---")

    # ── 本月至今 vs 上月同期 ─────────────────────────────────────────────────
    st.markdown("#### 本月至今")
    mo_help = f"{this_month_start}–{this_month_end} vs {prev_month_start}–{prev_month_end}（同天数对比）"
    mc1, mc2, mc3, mc4 = st.columns(4)
    mon_orders = _get(this_mon_data, "orders")
    pmon_orders = _get(prev_mon_data, "orders")
    dm_o, dm_o_c = _delta_str(mon_orders, pmon_orders)
    mc1.metric("订单数", int(mon_orders), delta=dm_o, delta_color=dm_o_c, help=mo_help)

    mon_rev = _get(this_mon_data, "revenue")
    pmon_rev = _get(prev_mon_data, "revenue")
    dm_r, dm_r_c = _delta_str(mon_rev, pmon_rev)
    mc2.metric("营业额", f"¥{mon_rev:,.0f}", delta=dm_r, delta_color=dm_r_c, help=mo_help)

    mon_aov = _get(this_mon_data, "aov")
    pmon_aov = _get(prev_mon_data, "aov")
    dm_a, dm_a_c = _delta_str(mon_aov, pmon_aov)
    mc3.metric("客单价", f"¥{mon_aov:,.2f}", delta=dm_a, delta_color=dm_a_c, help=mo_help)

    mon_cust = _get(this_mon_data, "unique_customers")
    pmon_cust = _get(prev_mon_data, "unique_customers")
    dm_c, dm_c_c = _delta_str(mon_cust, pmon_cust)
    mc4.metric("独立客户数", int(mon_cust), delta=dm_c, delta_color=dm_c_c, help=mo_help)

    st.markdown("---")
    st.caption(
        f"数据截至最新上传订单日期（{anchor}），非自然日期「今天」。"
        f"本周至今：{this_week_start}–{this_week_end}；本月至今：{this_month_start}–{this_month_end}。"
        f"周/月对比均按相同天数与上一周期同期比较，避免不完整周期对比整周期造成的失真。"
    )
