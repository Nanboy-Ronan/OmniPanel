from __future__ import annotations
from datetime import date, timedelta

import altair as alt
import pandas as pd
import streamlit as st

from app.ui._helpers import (
    PALETTE,
    _page_hero,
    _styled_chart,
    show_api_error,
)

_REPURCHASE_WINDOWS: dict[str, int | None] = {
    "全历史": None,
    "60 天": 60,
    "90 天": 90,
    "180 天": 180,
    "365 天": 365,
}


def _province_charts(overview: dict) -> None:
    """Render top-province charts (by orders and by unique customers) side by side."""
    top_prov = overview.get("top_province")
    top_prov_unique = overview.get("top_province_unique")
    if not top_prov and not top_prov_unique:
        return
    left, right = st.columns(2)
    if top_prov:
        prov_df = pd.DataFrame(top_prov)
        chart = (
            alt.Chart(prov_df)
            .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4, color="#0ea5e9")
            .encode(
                x=alt.X("province:N", sort="-y", title="省份"),
                y=alt.Y("orders:Q", title="订单数"),
                tooltip=["province", "orders", alt.Tooltip("revenue:Q", format=",.2f")],
            )
            .properties(title="按订单量排名的省份")
        )
        left.altair_chart(_styled_chart(chart), use_container_width=True)
    if top_prov_unique:
        uniq_df = pd.DataFrame(top_prov_unique)
        chart2 = (
            alt.Chart(uniq_df)
            .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4, color="#8b5cf6")
            .encode(
                x=alt.X("province:N", sort="-y", title="省份"),
                y=alt.Y("customers:Q", title="独立客户数"),
                tooltip=["province", "customers"],
            )
            .properties(title="按独立客户数排名的省份")
        )
        right.altair_chart(_styled_chart(chart2), use_container_width=True)


def _repurchase_frequency_chart(freq_dist: dict, window_label: str) -> None:
    """Render a horizontal bar chart for new-customer order-count distribution."""
    order = ["1", "2", "3", "4+"]
    labels = {"1": "仅 1 单", "2": "2 单", "3": "3 单", "4+": "4 单及以上"}
    rows = [
        {"购买次数": labels.get(k, k), "客户数": freq_dist.get(k, 0), "_sort": i}
        for i, k in enumerate(order)
    ]
    df = pd.DataFrame(rows)
    total = df["客户数"].sum()
    if total == 0:
        return
    df["占比"] = df["客户数"] / total

    title = f"新客购买频次分布（{window_label}）" if window_label != "全历史" else "新客购买频次分布（全历史）"
    chart = (
        alt.Chart(df)
        .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
        .encode(
            x=alt.X("购买次数:N", sort=None, title="购买次数"),
            y=alt.Y("客户数:Q", title="客户数"),
            color=alt.Color(
                "购买次数:N",
                scale=alt.Scale(
                    domain=["仅 1 单", "2 单", "3 单", "4 单及以上"],
                    range=["#94a3b8", "#60a5fa", "#3b82f6", "#1d4ed8"],
                ),
                legend=None,
            ),
            tooltip=[
                alt.Tooltip("购买次数:N"),
                alt.Tooltip("客户数:Q"),
                alt.Tooltip("占比:Q", format=".1%", title="占比"),
            ],
        )
        .properties(title=title, height=200)
    )
    st.altair_chart(_styled_chart(chart), use_container_width=True)


def analysis_overview_page(start: date, end: date, platform: str | None = None) -> None:
    client = st.session_state["client"]
    # ── 复购时间窗口选择 ───────────────────────────────────────────────────────
    selected_window_label = st.selectbox(
        "复购统计窗口",
        options=list(_REPURCHASE_WINDOWS.keys()),
        index=0,
        key="repurchase_window",
        help="统计新客户在首单后多少天内发生复购（全历史 = 不限时间）。",
    )
    window_days = _REPURCHASE_WINDOWS[selected_window_label]

    r_over = client.overview(start, end, platform)
    r_rep = client.repurchase_rate(start, end, platform, window_days)
    r_trend = client.analysis(start, end, platform)
    if r_over.status_code != 200:
        show_api_error(r_over)
        return
    repurchase = r_rep.json() if r_rep.status_code == 200 else None
    overview = r_over.json()
    trend_data = r_trend.json() if r_trend.status_code == 200 else None

    # ── 顶部指标卡 ────────────────────────────────────────────────────────────
    if repurchase:
        m1, m2, m3, m4, m5, m6 = st.columns(6)
    else:
        m1, m2, m3, m4 = st.columns(4)
    m1.metric("总订单数", overview.get("orders", 0))
    m2.metric("营业额", f"¥{overview.get('revenue', 0):,.2f}")
    m3.metric("客单价", f"¥{overview.get('aov', 0):,.2f}")
    m4.metric("独立客户数", overview.get("unique_customers", 0))
    if repurchase:
        rate = repurchase.get("repurchase_rate", 0) or 0
        new_c = repurchase.get("new_customers", 0) or 0
        returning = repurchase.get("repurchasing_customers", 0) or 0
        avg_days = repurchase.get("avg_days_to_repurchase")
        window_suffix = f"（{selected_window_label}）" if window_days else ""
        m5.metric(
            f"复购率{window_suffix}",
            f"{rate * 100:.1f}%",
            delta=f"{returning}/{new_c} 新客户",
            delta_color="off",
        )
        m6.metric(
            "平均复购间隔",
            f"{avg_days:.0f} 天" if avg_days is not None else "—",
            help="复购客户从首单到再次下单的平均天数",
        )

    # ── 复购频次分布图 ────────────────────────────────────────────────────────
    if repurchase and repurchase.get("frequency_distribution"):
        _repurchase_frequency_chart(repurchase["frequency_distribution"], selected_window_label)

    # 每日客户活动趋势
    if trend_data:
        frames = []
        if trend_data.get("old_daily"):
            df_old = pd.DataFrame(
                {"date": list(trend_data["old_daily"].keys()), "customers": list(trend_data["old_daily"].values())}
            )
            df_old["客户类型"] = "老客户"
            frames.append(df_old)
        if trend_data.get("new_daily"):
            df_new = pd.DataFrame(
                {"date": list(trend_data["new_daily"].keys()), "customers": list(trend_data["new_daily"].values())}
            )
            df_new["客户类型"] = "新客户"
            frames.append(df_new)
        if frames:
            line_df = pd.concat(frames)
            line_df["date"] = pd.to_datetime(line_df["date"])
            _color_scale = alt.Scale(domain=["老客户", "新客户"], range=[PALETTE["old"], PALETTE["new"]])
            line = (
                alt.Chart(line_df)
                .mark_line(point=alt.OverlayMarkDef(filled=True, size=60))
                .encode(
                    x=alt.X("date:T", title="日期"),
                    y=alt.Y("customers:Q", title="每日活跃客户"),
                    color=alt.Color("客户类型:N", scale=_color_scale),
                    tooltip=["date:T", "customers:Q", "客户类型:N"],
                )
                .properties(title="每日客户活动")
            )
            st.altair_chart(_styled_chart(line), use_container_width=True)

    if overview.get("top_sku"):
        top_df = pd.DataFrame(overview["top_sku"])
        sku_chart = (
            alt.Chart(top_df)
            .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4, color=PALETTE["old"])
            .encode(
                x=alt.X("sku:N", sort="-y", title="SKU"),
                y=alt.Y("orders:Q", title="订单数"),
                tooltip=["sku", "orders", alt.Tooltip("revenue:Q", format=",.2f")],
            )
            .properties(title="订单量最高的 SKU")
        )
        st.altair_chart(_styled_chart(sku_chart), use_container_width=True)

    _province_charts(overview)


def analysis_old_vs_new_page(start: date, end: date, platform: str | None = None) -> None:
    client = st.session_state["client"]
    r_over = client.overview(start, end, platform)
    r = client.analysis(start, end, platform)
    if r.status_code != 200:
        show_api_error(r)
        return
    overview = r_over.json() if r_over.status_code == 200 else None
    data = r.json()
    old_df = pd.DataFrame(data["old"]["rows"])
    new_df = pd.DataFrame(data["new"]["rows"])

    if overview:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("总订单数", overview.get("orders", 0))
        m2.metric("营业额", f"¥{overview.get('revenue', 0):,.2f}")
        m3.metric("客单价", f"¥{overview.get('aov', 0):,.2f}")
        m4.metric("独立客户数", overview.get("unique_customers", 0))

    col_old, col_new = st.columns(2)
    with col_old:
        st.markdown(
            f"<p style='color:{PALETTE['old']};font-weight:700;font-size:0.8rem;"
            f"text-transform:uppercase;letter-spacing:0.07em;"
            f"border-left:3px solid {PALETTE['old']};padding-left:0.6rem;margin-bottom:0.75rem;'>"
            f"老客户</p>",
            unsafe_allow_html=True,
        )
        st.metric("订单数", data["old"]["count"])
        st.metric("客户数", data["old"].get("customer_count", 0))
        st.metric("营业额", f"¥{data['old']['paid_sum']:,.2f}")

    with col_new:
        st.markdown(
            f"<p style='color:{PALETTE['new']};font-weight:700;font-size:0.8rem;"
            f"text-transform:uppercase;letter-spacing:0.07em;"
            f"border-left:3px solid {PALETTE['new']};padding-left:0.6rem;margin-bottom:0.75rem;'>"
            f"新客户</p>",
            unsafe_allow_html=True,
        )
        st.metric("订单数", data["new"]["count"])
        st.metric("客户数", data["new"].get("customer_count", 0))
        st.metric("营业额", f"¥{data['new']['paid_sum']:,.2f}")

    # 新老客户对比柱状图
    chart_df = pd.DataFrame(
        {
            "客户类型": ["老客户", "新客户"],
            "订单数": [data["old"]["count"], data["new"]["count"]],
            "营业额": [data["old"]["paid_sum"], data["new"]["paid_sum"]],
        }
    )
    bar = (
        alt.Chart(chart_df)
        .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
        .encode(
            x=alt.X("客户类型:N", axis=alt.Axis(labelAngle=0)),
            y=alt.Y("订单数:Q"),
            color=alt.Color(
                "客户类型:N",
                scale=alt.Scale(domain=["老客户", "新客户"], range=[PALETTE["old"], PALETTE["new"]]),
                legend=alt.Legend(title="客户类型"),
            ),
            tooltip=["客户类型", "订单数", alt.Tooltip("营业额:Q", format=",.2f")],
        )
        .properties(title="新老客户订单对比")
    )
    st.altair_chart(_styled_chart(bar), use_container_width=True)

    # 每日趋势
    _color_scale = alt.Scale(domain=["老客户", "新客户"], range=[PALETTE["old"], PALETTE["new"]])
    if data.get("old_daily") or data.get("new_daily"):
        frames = []
        if data.get("old_daily"):
            old_by_day = pd.DataFrame(
                {"date": list(data["old_daily"].keys()), "customers": list(data["old_daily"].values())}
            )
            old_by_day["客户类型"] = "老客户"
            frames.append(old_by_day)
        if data.get("new_daily"):
            new_by_day = pd.DataFrame(
                {"date": list(data["new_daily"].keys()), "customers": list(data["new_daily"].values())}
            )
            new_by_day["客户类型"] = "新客户"
            frames.append(new_by_day)
        if frames:
            line_df = pd.concat(frames)
            line_df["date"] = pd.to_datetime(line_df["date"])
            line = (
                alt.Chart(line_df)
                .mark_line(point=alt.OverlayMarkDef(filled=True, size=60))
                .encode(
                    x=alt.X("date:T", title="日期"),
                    y=alt.Y("customers:Q", title="客户数"),
                    color=alt.Color("客户类型:N", scale=_color_scale),
                    tooltip=["date:T", "customers:Q", "客户类型:N"],
                )
                .properties(title="每日客户趋势")
            )
            st.altair_chart(_styled_chart(line), use_container_width=True)

    # Top SKU 图表
    if overview and overview.get("top_sku"):
        top_df = pd.DataFrame(overview["top_sku"])
        sku_chart = (
            alt.Chart(top_df)
            .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4, color=PALETTE["old"])
            .encode(
                x=alt.X("sku:N", sort="-y", title="SKU"),
                y=alt.Y("orders:Q", title="订单数"),
                tooltip=["sku", "orders", alt.Tooltip("revenue:Q", format=",.2f")],
            )
            .properties(title="订单量最高的 SKU")
        )
        st.altair_chart(_styled_chart(sku_chart), use_container_width=True)

    # 省份图表
    if overview:
        _province_charts(overview)

    # 原始订单表（折叠）
    with st.expander(f"老客户订单（{data['old']['count']} 条）", expanded=False):
        if old_df.empty:
            st.info("该时段无老客户订单。")
        else:
            st.dataframe(old_df, use_container_width=True)

    with st.expander(f"新客户订单（{data['new']['count']} 条）", expanded=False):
        if new_df.empty:
            st.info("该时段无新客户订单。")
        else:
            st.dataframe(new_df, use_container_width=True)


def page_analysis() -> None:
    _page_hero("数据分析")
    client = st.session_state["client"]

    with st.container():
        col1, col2, col3, col4, col5 = st.columns([2, 2, 2, 1, 1])
        start = col1.date_input("开始日期", value=date.today() - timedelta(days=30), key="analysis_start")
        end = col2.date_input("结束日期", value=date.today(), key="analysis_end")
        platform_options = ["全部", "youzan", "jd", "tmall"]
        selected_platform = col3.selectbox("平台", platform_options, index=0, key="analysis_platform")
        col4.markdown("<div style='height:1.85rem'></div>", unsafe_allow_html=True)
        mode = col4.radio("视图", ["概览", "新老客户"], horizontal=False, key="analysis_mode", label_visibility="collapsed")
        col5.markdown("<div style='height:1.85rem'></div>", unsafe_allow_html=True)
        if col5.button("保存筛选", use_container_width=True, help="将当前筛选条件保存为视图"):
            st.session_state["_save_view_open"] = True

    pf = None if selected_platform == "全部" else selected_platform
    if start > end:
        st.error("开始日期不能晚于结束日期。")
        return

    # ── Save view dialog ─────────────────────────────────────────────────────
    if st.session_state.get("_save_view_open"):
        with st.form("save-view-form"):
            view_name = st.text_input("视图名称", placeholder="例：上月广东概览")
            is_shared = st.checkbox("共享给所有用户")
            sa, sb = st.columns(2)
            if sa.form_submit_button("保存"):
                if not view_name.strip():
                    st.warning("请输入视图名称。")
                else:
                    filters = {
                        "start_date": str(start),
                        "end_date": str(end),
                        "platform": selected_platform,
                        "mode": mode,
                    }
                    r_sv = client.save_query(view_name.strip(), filters, is_shared)
                    if r_sv.status_code == 201:
                        st.success(f"视图「{view_name.strip()}」已保存。")
                        st.session_state["_save_view_open"] = False
                        st.rerun()
                    else:
                        st.error(f"保存失败：{r_sv.text}")
            if sb.form_submit_button("取消"):
                st.session_state["_save_view_open"] = False
                st.rerun()

    st.markdown(f"<p style='color:#475569;font-size:0.82rem;margin-bottom:0.5rem'>当前视图：<b>{mode}</b></p>", unsafe_allow_html=True)
    if mode == "概览":
        analysis_overview_page(start, end, pf)
    else:
        analysis_old_vs_new_page(start, end, pf)
