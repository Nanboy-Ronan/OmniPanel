"""内容带货分析 — 公众号文章发布前后的电商订单涨跌对比。"""
from __future__ import annotations

from datetime import date, timedelta

import altair as alt
import pandas as pd
import streamlit as st

from app.ui._helpers import _page_hero, _styled_chart, show_api_error

_QUADRANT_COLORS = {
    "高流量·强带货": "#10b981",
    "低流量·强带货": "#6366f1",
    "高流量·弱带货": "#f59e0b",
    "低流量·弱带货": "#94a3b8",
    "无对比基准": "#cbd5e1",
}


def _quadrant_label(row: dict, avg_reads: float, avg_lift: float) -> str:
    lift = row.get("order_lift_pct")
    reads = row.get("read_user_count", 0)
    if lift is None:
        return "无对比基准"
    high_read = reads >= avg_reads
    high_lift = lift >= avg_lift
    if high_read and high_lift:
        return "高流量·强带货"
    if not high_read and high_lift:
        return "低流量·强带货"
    if high_read and not high_lift:
        return "高流量·弱带货"
    return "低流量·弱带货"


def page_content_impact() -> None:
    _page_hero("内容带货分析")
    client = st.session_state["client"]

    default_end = date.today()
    default_start = default_end - timedelta(days=60)

    # ── Filters ───────────────────────────────────────────────────────────────
    c1, c2, c3, c4, c5 = st.columns([1.8, 1.8, 1.2, 1.5, 1.5])
    start = c1.date_input("文章发布起", value=default_start, key="ci_start")
    end = c2.date_input("文章发布止", value=default_end, key="ci_end")
    window = c3.number_input("比对窗口（天）", min_value=1, max_value=30, value=7, step=1, key="ci_window")
    platform = c4.selectbox("电商平台", ["全部", "youzan", "jd", "tmall"], key="ci_platform")
    ec_platform = None if platform == "全部" else platform
    source_label = c5.selectbox("内容来源", ["微信", "小红书", "知乎"], key="ci_source")
    source = {"微信": "wechat", "小红书": "xhs", "知乎": "zhihu"}[source_label]

    if start > end:
        st.error("开始日期不能晚于结束日期。")
        return

    r = client.media_content_impact(
        str(start), str(end),
        window_days=int(window),
        platform=ec_platform,
        source=source,
    )
    if r.status_code != 200:
        show_api_error(r)
        return

    data = r.json()
    if not data:
        st.info(f"当前区间内没有{source_label}内容数据。请先上传/同步数据或检查日期范围。")
        return

    df = pd.DataFrame(data)
    df["order_lift_pct"] = pd.to_numeric(df["order_lift_pct"], errors="coerce")
    df["revenue_lift_pct"] = pd.to_numeric(df["revenue_lift_pct"], errors="coerce")
    for col in ["read_user_count", "pre_orders", "post_orders"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    has_lift = df["order_lift_pct"].notna()
    n_positive = (df.loc[has_lift, "order_lift_pct"] > 0).sum()
    n_negative = (df.loc[has_lift, "order_lift_pct"] <= 0).sum()
    n_none = (~has_lift).sum()

    # ── Summary banner ────────────────────────────────────────────────────────
    b1, b2, b3, b4 = st.columns(4)
    b1.metric("分析文章数", len(df))
    b2.metric("发文后订单提升", f"{n_positive} 篇", help="订单涨幅 > 0%")
    b3.metric("发文后订单下降", f"{n_negative} 篇")
    b4.metric("无订单基准数据", f"{n_none} 篇",
              help="发文前窗口内无订单，无法计算涨幅")

    st.markdown("---")
    tab_scatter, tab_rank, tab_table = st.tabs(["四象限分布", "带货排行", "全部明细"])

    # ── Tab: 四象限散点图 ────────────────────────────────────────────────────
    with tab_scatter:
        st.markdown("### 流量 × 带货四象限")
        st.caption(
            f"X 轴：文章阅读人数（越右越高流量）  |  "
            f"Y 轴：发文后 {window} 天订单涨幅（越高带货越强）  |  "
            f"气泡大小：发文后窗口内订单数"
        )

        scatter_df = df[has_lift].copy()
        if scatter_df.empty:
            st.info("没有可计算涨幅的文章（需要发文前窗口有订单数据作为基准）。")
        else:
            avg_reads = scatter_df["read_user_count"].mean()
            avg_lift = scatter_df["order_lift_pct"].mean()
            scatter_df["象限"] = scatter_df.apply(
                lambda row: _quadrant_label(row.to_dict(), avg_reads, avg_lift), axis=1
            )

            color_scale = alt.Scale(
                domain=list(_QUADRANT_COLORS.keys()),
                range=list(_QUADRANT_COLORS.values()),
            )
            chart = (
                alt.Chart(scatter_df)
                .mark_circle(opacity=0.8, stroke="white", strokeWidth=1)
                .encode(
                    x=alt.X("read_user_count:Q", title="阅读人数",
                            scale=alt.Scale(zero=False)),
                    y=alt.Y("order_lift_pct:Q", title=f"订单涨幅（%，{window} 天窗口）",
                            axis=alt.Axis(format=".0f")),
                    size=alt.Size("post_orders:Q", title="发文后订单数",
                                  scale=alt.Scale(range=[60, 900])),
                    color=alt.Color("象限:N", scale=color_scale,
                                    legend=alt.Legend(title="象限", orient="bottom")),
                    tooltip=[
                        alt.Tooltip("title:N", title="标题"),
                        alt.Tooltip("publish_date:N", title="发布日期"),
                        alt.Tooltip("read_user_count:Q", title="阅读人数", format=","),
                        alt.Tooltip("order_lift_pct:Q", title="订单涨幅", format=".1f"),
                        alt.Tooltip("pre_orders:Q", title=f"发文前 {window} 天订单"),
                        alt.Tooltip("post_orders:Q", title=f"发文后 {window} 天订单"),
                        alt.Tooltip("revenue_lift_pct:Q", title="营收涨幅", format=".1f"),
                    ],
                )
                .properties(height=460)
                .interactive()
            )
            # quadrant reference lines at medians
            h_line = (
                alt.Chart(pd.DataFrame({"y": [avg_lift]}))
                .mark_rule(strokeDash=[4, 4], color="#64748b", opacity=0.6)
                .encode(y="y:Q")
            )
            v_line = (
                alt.Chart(pd.DataFrame({"x": [avg_reads]}))
                .mark_rule(strokeDash=[4, 4], color="#64748b", opacity=0.6)
                .encode(x="x:Q")
            )
            st.altair_chart(_styled_chart(chart + h_line + v_line), use_container_width=True)
            st.caption(
                f"虚线：阅读人数中位 {avg_reads:,.0f}，订单涨幅中位 {avg_lift:.1f}%"
            )

    # ── Tab: 带货排行 ────────────────────────────────────────────────────────
    with tab_rank:
        top_df = df[has_lift].sort_values("order_lift_pct", ascending=False).head(15).copy()
        top_df["short_title"] = top_df["title"].str[:30]
        top_df["涨跌色"] = top_df["order_lift_pct"].apply(
            lambda x: "#10b981" if x > 0 else "#f43f5e"
        )

        if top_df.empty:
            st.info("没有可计算涨幅的文章。")
        else:
            col_a, col_b = st.columns(2)
            with col_a:
                st.markdown("#### 订单涨幅最高")
                top10 = top_df.head(10)
                bar = (
                    alt.Chart(top10)
                    .mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4)
                    .encode(
                        x=alt.X("order_lift_pct:Q", title="订单涨幅（%）",
                                axis=alt.Axis(format=".0f")),
                        y=alt.Y("short_title:N", sort="-x", title=""),
                        color=alt.Color("涨跌色:N", scale=None, legend=None),
                        tooltip=[
                            alt.Tooltip("title:N", title="标题"),
                            alt.Tooltip("order_lift_pct:Q", title="订单涨幅", format=".1f"),
                            alt.Tooltip("pre_orders:Q", title="发文前订单"),
                            alt.Tooltip("post_orders:Q", title="发文后订单"),
                            alt.Tooltip("read_user_count:Q", title="阅读人数", format=","),
                        ],
                    )
                    .properties(height=max(260, 28 * len(top10)))
                )
                st.altair_chart(_styled_chart(bar), use_container_width=True)

            with col_b:
                st.markdown("#### 发文后绝对订单数最高")
                abs_top = df.sort_values("post_orders", ascending=False).head(10).copy()
                abs_top["short_title"] = abs_top["title"].str[:30]
                bar2 = (
                    alt.Chart(abs_top)
                    .mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4, color="#0ea5e9")
                    .encode(
                        x=alt.X("post_orders:Q", title=f"发文后 {window} 天订单数"),
                        y=alt.Y("short_title:N", sort="-x", title=""),
                        tooltip=[
                            alt.Tooltip("title:N", title="标题"),
                            alt.Tooltip("post_orders:Q", title="发文后订单"),
                            alt.Tooltip("order_lift_pct:Q", title="涨幅", format=".1f"),
                            alt.Tooltip("read_user_count:Q", title="阅读人数", format=","),
                        ],
                    )
                    .properties(height=max(260, 28 * len(abs_top)))
                )
                st.altair_chart(_styled_chart(bar2), use_container_width=True)

    # ── Tab: 全部明细 ────────────────────────────────────────────────────────
    with tab_table:
        show_df = df[[
            "title", "publish_date", "read_user_count",
            "pre_orders", "post_orders", "order_lift_pct",
            "pre_revenue", "post_revenue", "revenue_lift_pct",
        ]].copy()
        st.dataframe(
            show_df.sort_values("order_lift_pct", ascending=False, na_position="last"),
            use_container_width=True,
            hide_index=True,
            column_config={
                "title": st.column_config.TextColumn("标题", width="large"),
                "publish_date": st.column_config.TextColumn("发布日期"),
                "read_user_count": st.column_config.NumberColumn("阅读人数", format="%d"),
                "pre_orders": st.column_config.NumberColumn(f"发文前 {window} 天订单", format="%d"),
                "post_orders": st.column_config.NumberColumn(f"发文后 {window} 天订单", format="%d"),
                "order_lift_pct": st.column_config.NumberColumn("订单涨幅（%）", format="%.1f"),
                "pre_revenue": st.column_config.NumberColumn("发文前营收", format="¥%.2f"),
                "post_revenue": st.column_config.NumberColumn("发文后营收", format="¥%.2f"),
                "revenue_lift_pct": st.column_config.NumberColumn("营收涨幅（%）", format="%.1f"),
            },
        )
