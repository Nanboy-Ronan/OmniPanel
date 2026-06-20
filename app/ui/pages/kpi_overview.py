from __future__ import annotations

from datetime import date, timedelta

import streamlit as st

from app.ui._helpers import _page_hero, show_api_error


def _week_range(d: date) -> tuple[date, date]:
    start = d - timedelta(days=d.weekday())
    return start, d


def _month_range(d: date) -> tuple[date, date]:
    return d.replace(day=1), d


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
    today = date.today()
    yesterday = today - timedelta(days=1)

    # Date ranges
    this_week_start, this_week_end = _week_range(today)
    last_week_start = this_week_start - timedelta(weeks=1)
    last_week_end = this_week_start - timedelta(days=1)

    this_month_start, this_month_end = _month_range(today)
    # Previous month end = day before this month start
    prev_month_end = this_month_start - timedelta(days=1)
    prev_month_start = prev_month_end.replace(day=1)

    with st.spinner("加载 KPI 数据…"):
        today_data     = _fetch_overview(client, today, today)
        yest_data      = _fetch_overview(client, yesterday, yesterday)
        this_week_data = _fetch_overview(client, this_week_start, this_week_end)
        last_week_data = _fetch_overview(client, last_week_start, last_week_end)
        this_mon_data  = _fetch_overview(client, this_month_start, this_month_end)
        prev_mon_data  = _fetch_overview(client, prev_month_start, prev_month_end)

    if not any([today_data, this_week_data, this_mon_data]):
        st.info("暂无订单数据，请先上传文件。")
        return

    def _get(d: dict | None, key: str, default: float = 0.0) -> float:
        return float((d or {}).get(key, default) or default)

    # ── 今日 vs 昨日 ─────────────────────────────────────────────────────────
    st.markdown("#### 今日")
    c1, c2, c3, c4 = st.columns(4)
    tod_orders = _get(today_data, "orders")
    yest_orders = _get(yest_data, "orders")
    d_orders, d_orders_color = _delta_str(tod_orders, yest_orders)
    c1.metric("订单数", int(tod_orders), delta=d_orders, delta_color=d_orders_color, help="今日 vs 昨日")

    tod_rev = _get(today_data, "revenue")
    yest_rev = _get(yest_data, "revenue")
    d_rev, d_rev_color = _delta_str(tod_rev, yest_rev)
    c2.metric("营业额", f"¥{tod_rev:,.0f}", delta=d_rev, delta_color=d_rev_color, help="今日 vs 昨日")

    tod_aov = _get(today_data, "aov")
    yest_aov = _get(yest_data, "aov")
    d_aov, d_aov_color = _delta_str(tod_aov, yest_aov)
    c3.metric("客单价", f"¥{tod_aov:,.2f}", delta=d_aov, delta_color=d_aov_color, help="今日 vs 昨日")

    tod_cust = _get(today_data, "unique_customers")
    yest_cust = _get(yest_data, "unique_customers")
    d_cust, d_cust_color = _delta_str(tod_cust, yest_cust)
    c4.metric("独立客户数", int(tod_cust), delta=d_cust, delta_color=d_cust_color, help="今日 vs 昨日")

    st.markdown("---")

    # ── 本周 vs 上周 ─────────────────────────────────────────────────────────
    st.markdown("#### 本周")
    wc1, wc2, wc3, wc4 = st.columns(4)
    wk_orders = _get(this_week_data, "orders")
    lwk_orders = _get(last_week_data, "orders")
    dw_o, dw_o_c = _delta_str(wk_orders, lwk_orders)
    wc1.metric("订单数", int(wk_orders), delta=dw_o, delta_color=dw_o_c, help="本周 vs 上周同期")

    wk_rev = _get(this_week_data, "revenue")
    lwk_rev = _get(last_week_data, "revenue")
    dw_r, dw_r_c = _delta_str(wk_rev, lwk_rev)
    wc2.metric("营业额", f"¥{wk_rev:,.0f}", delta=dw_r, delta_color=dw_r_c, help="本周 vs 上周同期")

    wk_aov = _get(this_week_data, "aov")
    lwk_aov = _get(last_week_data, "aov")
    dw_a, dw_a_c = _delta_str(wk_aov, lwk_aov)
    wc3.metric("客单价", f"¥{wk_aov:,.2f}", delta=dw_a, delta_color=dw_a_c, help="本周 vs 上周同期")

    wk_cust = _get(this_week_data, "unique_customers")
    lwk_cust = _get(last_week_data, "unique_customers")
    dw_c, dw_c_c = _delta_str(wk_cust, lwk_cust)
    wc4.metric("独立客户数", int(wk_cust), delta=dw_c, delta_color=dw_c_c, help="本周 vs 上周同期")

    st.markdown("---")

    # ── 本月 vs 上月 ─────────────────────────────────────────────────────────
    st.markdown("#### 本月")
    mc1, mc2, mc3, mc4 = st.columns(4)
    mon_orders = _get(this_mon_data, "orders")
    pmon_orders = _get(prev_mon_data, "orders")
    dm_o, dm_o_c = _delta_str(mon_orders, pmon_orders)
    mc1.metric("订单数", int(mon_orders), delta=dm_o, delta_color=dm_o_c, help="本月 vs 上月")

    mon_rev = _get(this_mon_data, "revenue")
    pmon_rev = _get(prev_mon_data, "revenue")
    dm_r, dm_r_c = _delta_str(mon_rev, pmon_rev)
    mc2.metric("营业额", f"¥{mon_rev:,.0f}", delta=dm_r, delta_color=dm_r_c, help="本月 vs 上月")

    mon_aov = _get(this_mon_data, "aov")
    pmon_aov = _get(prev_mon_data, "aov")
    dm_a, dm_a_c = _delta_str(mon_aov, pmon_aov)
    mc3.metric("客单价", f"¥{mon_aov:,.2f}", delta=dm_a, delta_color=dm_a_c, help="本月 vs 上月")

    mon_cust = _get(this_mon_data, "unique_customers")
    pmon_cust = _get(prev_mon_data, "unique_customers")
    dm_c, dm_c_c = _delta_str(mon_cust, pmon_cust)
    mc4.metric("独立客户数", int(mon_cust), delta=dm_c, delta_color=dm_c_c, help="本月 vs 上月")

    st.markdown("---")
    st.caption(
        f"数据截至今日（{today}）。本周自 {this_week_start} 起，本月自 {this_month_start} 起。"
        f"对比基准：昨日 / 上周完整周（{last_week_start}–{last_week_end}）/ 上月（{prev_month_start}–{prev_month_end}）。"
    )
