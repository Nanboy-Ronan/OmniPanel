"""app/ui/pages/media_traffic.py — 公众号文章流量分析（手动上传数据）"""
from __future__ import annotations

from datetime import date, timedelta

import altair as alt
import pandas as pd
import streamlit as st

from app.ui._helpers import _page_hero, _styled_chart, show_api_error

_PALETTE = ["#0ea5e9", "#6366f1", "#f97316", "#10b981", "#f43f5e", "#8b5cf6"]


def _account_options(accounts: list[dict]) -> tuple[list[str], dict[str, int | None]]:
    labels = ["全部账号"]
    mapping: dict[str, int | None] = {"全部账号": None}
    for a in accounts:
        label = a.get("name") or "未命名账号"
        labels.append(label)
        mapping[label] = a.get("id")
    return labels, mapping


def page_media_traffic() -> None:
    _page_hero("公众号流量")
    client = st.session_state["client"]

    default_end = date.today()
    default_start = default_end - timedelta(days=365)

    # ── 账号列表 ──────────────────────────────────────────────────────────────
    r_accounts = client.media_accounts()
    if r_accounts.status_code != 200:
        show_api_error(r_accounts)
        return
    accounts = r_accounts.json()
    labels, account_map = _account_options(accounts)

    # ── 筛选栏 ────────────────────────────────────────────────────────────────
    col1, col2, col3, col4 = st.columns([2, 2, 2, 1.4])
    start = col1.date_input("发布日期（起）", value=default_start, key="traffic_start")
    end   = col2.date_input("发布日期（止）", value=default_end,   key="traffic_end")
    selected_account = col3.selectbox("账号", labels, key="traffic_account")
    query = col4.text_input("搜索标题", value="", key="traffic_query", placeholder="关键词")
    account_id = account_map.get(selected_account)

    if start > end:
        st.error("开始日期不能晚于结束日期。")
        return

    # ── 拉数据 ────────────────────────────────────────────────────────────────
    r_overview = client.media_traffic_overview(str(start), str(end), account_id)
    r_traffic  = client.media_traffic(str(start), str(end), account_id, query.strip() or None)

    if r_overview.status_code != 200:
        show_api_error(r_overview)
        return
    if r_traffic.status_code != 200:
        show_api_error(r_traffic)
        return

    overview = r_overview.json()
    rows = r_traffic.json()

    # ── 总览卡片 ──────────────────────────────────────────────────────────────
    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("文章数",     overview.get("articles", 0))
    m2.metric("阅读人数",   f"{overview.get('read_user_count', 0):,}")
    m3.metric("均篇阅读",   f"{overview.get('avg_read_per_article', 0):,.0f}")
    m4.metric("分享人数",   f"{overview.get('share_user_count', 0):,}")
    m5.metric("点赞人数",   f"{overview.get('like_user', 0):,}")
    m6.metric("留言条数",   f"{overview.get('comment_count', 0):,}")

    if not rows:
        st.info("当前筛选范围内没有文章流量数据。请先在「公众号上传」页上传 xlsx 数据。")
        return

    df = pd.DataFrame(rows)
    if "publish_date" in df.columns:
        df["publish_date"] = pd.to_datetime(df["publish_date"])

    st.markdown("---")

    # ── Tab: 排行 / 趋势 ──────────────────────────────────────────────────────
    tab_rank, tab_trend = st.tabs(["📊 文章排行", "📈 发布趋势"])

    with tab_rank:
        # Top-N 阅读排行横柱
        top_n = min(15, len(df))
        top_df = df.sort_values("read_user_count", ascending=False).head(top_n).copy()
        # Truncate long titles for display
        top_df["short_title"] = top_df["title"].str[:28]

        bar = (
            alt.Chart(top_df)
            .mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4, color="#0ea5e9")
            .encode(
                x=alt.X("read_user_count:Q", title="阅读人数"),
                y=alt.Y("short_title:N", sort="-x", title=""),
                tooltip=[
                    alt.Tooltip("title:N",           title="标题"),
                    alt.Tooltip("publish_date:T",     title="发布日期", format="%Y-%m-%d"),
                    alt.Tooltip("read_user_count:Q",  title="阅读人数",  format=","),
                    alt.Tooltip("read_count:Q",       title="阅读次数",  format=","),
                    alt.Tooltip("share_user_count:Q", title="分享人数",  format=","),
                    alt.Tooltip("like_user:Q",        title="点赞人数",  format=","),
                    alt.Tooltip("comment_count:Q",    title="留言条数",  format=","),
                ],
            )
            .properties(
                title=f"阅读人数最高的文章（前 {top_n} 篇）",
                height=max(260, 26 * top_n),
            )
        )
        st.altair_chart(_styled_chart(bar), use_container_width=True)

        # 完整明细表
        display_cols = [
            "title", "publish_date", "account_name",
            "read_user_count", "read_count", "share_user_count",
            "like_user", "comment_count", "collection_user", "read_avg_time",
        ]
        show_df = df[[c for c in display_cols if c in df.columns]].copy()
        st.dataframe(
            show_df.sort_values("read_user_count", ascending=False),
            use_container_width=True,
            hide_index=True,
            column_config={
                "title":           st.column_config.TextColumn("标题", width="large"),
                "publish_date":    st.column_config.DateColumn("发布日期", format="YYYY-MM-DD"),
                "account_name":    st.column_config.TextColumn("账号"),
                "read_user_count": st.column_config.NumberColumn("阅读人数", format="%d"),
                "read_count":      st.column_config.NumberColumn("阅读次数", format="%d"),
                "share_user_count": st.column_config.NumberColumn("分享人数", format="%d"),
                "like_user":       st.column_config.NumberColumn("点赞人数", format="%d"),
                "comment_count":   st.column_config.NumberColumn("留言条数", format="%d"),
                "collection_user": st.column_config.NumberColumn("划线人数", format="%d"),
                "read_avg_time":   st.column_config.NumberColumn("均阅读时长(分)", format="%.1f"),
            },
        )

    with tab_trend:
        if "publish_date" not in df.columns or df["publish_date"].isna().all():
            st.info("数据中没有发布日期，无法显示趋势。")
        else:
            # Monthly aggregation
            trend_df = df.copy()
            trend_df["month"] = trend_df["publish_date"].dt.to_period("M").dt.to_timestamp()
            monthly = (
                trend_df.groupby("month", as_index=False)
                .agg(
                    articles=("id", "count"),
                    read_user_count=("read_user_count", "sum"),
                    share_user_count=("share_user_count", "sum"),
                    like_user=("like_user", "sum"),
                )
                .sort_values("month")
            )

            col_a, col_b = st.columns(2)

            with col_a:
                articles_bar = (
                    alt.Chart(monthly)
                    .mark_bar(color="#6366f1", cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
                    .encode(
                        x=alt.X("month:T", title="月份", timeUnit="yearmonth"),
                        y=alt.Y("articles:Q", title="文章数"),
                        tooltip=[
                            alt.Tooltip("month:T", title="月份", timeUnit="yearmonth"),
                            alt.Tooltip("articles:Q", title="文章数"),
                        ],
                    )
                    .properties(title="每月发文数", height=220)
                )
                st.altair_chart(_styled_chart(articles_bar), use_container_width=True)

            with col_b:
                reads_bar = (
                    alt.Chart(monthly)
                    .mark_bar(color="#0ea5e9", cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
                    .encode(
                        x=alt.X("month:T", title="月份", timeUnit="yearmonth"),
                        y=alt.Y("read_user_count:Q", title="阅读人数"),
                        tooltip=[
                            alt.Tooltip("month:T", title="月份", timeUnit="yearmonth"),
                            alt.Tooltip("read_user_count:Q", title="阅读人数", format=","),
                        ],
                    )
                    .properties(title="每月总阅读人数", height=220)
                )
                st.altair_chart(_styled_chart(reads_bar), use_container_width=True)

            # Engagement breakdown line chart
            long_df = monthly.melt(
                id_vars=["month"],
                value_vars=["read_user_count", "share_user_count", "like_user"],
                var_name="指标",
                value_name="数值",
            )
            label_map = {
                "read_user_count":  "阅读人数",
                "share_user_count": "分享人数",
                "like_user":        "点赞人数",
            }
            long_df["指标"] = long_df["指标"].map(label_map)
            engage_line = (
                alt.Chart(long_df)
                .mark_line(point=alt.OverlayMarkDef(filled=True, size=50))
                .encode(
                    x=alt.X("month:T", title="月份", timeUnit="yearmonth"),
                    y=alt.Y("数值:Q", title="数值"),
                    color=alt.Color(
                        "指标:N",
                        scale=alt.Scale(range=_PALETTE[:3]),
                        legend=alt.Legend(title="指标"),
                    ),
                    tooltip=["month:T", "指标:N", "数值:Q"],
                )
                .properties(title="互动趋势（月度）", height=240)
            )
            st.altair_chart(_styled_chart(engage_line), use_container_width=True)
