"""Pugongying (蒲公英) KOL/KOC Collaboration Analytics Streamlit Page."""
from __future__ import annotations

import json
from datetime import date, timedelta

import altair as alt
import pandas as pd
import streamlit as st

from app.ui._helpers import _page_hero, _styled_chart, show_api_error


def _load_accounts(client) -> list[dict]:
    try:
        r = client.xhs_accounts()
        return r.json() if r.status_code == 200 else []
    except Exception:
        return []


def _parse_float(v) -> float:
    try:
        if str(v).endswith("%"):
            return float(str(v).rstrip("%"))
        return float(v)
    except (ValueError, TypeError):
        return 0.0


def _generate_insight(df: pd.DataFrame) -> str:
    if df.empty:
        return "暂无数据。"

    total_notes = len(df)
    total_spend = df["blogger_quote"].sum() + df["service_fee"].sum()
    total_impressions = df["impressions"].sum()
    total_interactions = df["interactions"].sum()
    avg_cpe = df["cost_per_interaction"].mean() if "cost_per_interaction" in df else 0.0

    top_interaction_note = df.loc[df["interactions"].idxmax()] if "interactions" in df and not df.empty else None

    lines = [
        f"- 本期共分析 **{total_notes}** 篇蒲公英合作笔记，总投放下发金额（含服务费）约 **¥{total_spend:,.2f}**。",
        f"- 累计带来 **{total_impressions:,}** 次总曝光，**{total_interactions:,}** 次总互动，整体平均互动成本 (CPE) 为 **¥{avg_cpe:.2f}** / 次。",
    ]

    if top_interaction_note is not None:
        lines.append(
            f"- 最热互动笔记：《**{top_interaction_note['note_title']}**》（博主：{top_interaction_note['blogger_nickname']}），"
            f"产生 **{int(top_interaction_note['interactions']):,}** 次互动，曝光量 **{int(top_interaction_note['impressions']):,}**。"
        )

    return "\n".join(lines)


def page_pgy_dashboard() -> None:
    _page_hero("蒲公英合作", "小红书蒲公英 KOL/KOC 商业合作投效分析与项目数据管理")

    client = st.session_state["client"]
    accounts = _load_accounts(client)

    if not accounts:
        st.info("尚未创建小红书账号，请先在「小红书数据」页面添加账号。")
        return

    acc_options = {"全部账号": None}
    acc_options.update({a["name"]: a["id"] for a in accounts})

    col_acc, col_date = st.columns([1, 2])
    with col_acc:
        selected_acc_name = st.selectbox("选择账号", list(acc_options.keys()), key="pgy_acc_select")
        selected_account_id = acc_options[selected_acc_name]

    with col_date:
        today = date.today()
        default_start = today - timedelta(days=180)
        date_range = st.date_input(
            "发布时间范围",
            value=(default_start, today),
            key="pgy_date_range",
        )
        if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
            start_date, end_date = date_range[0].isoformat(), date_range[1].isoformat()
        else:
            start_date, end_date = None, None

    # Fetch notes data
    res = client.pgy_notes(
        account_id=selected_account_id,
        start_date=start_date,
        end_date=end_date,
        limit=1000,
    )

    if res.status_code != 200:
        show_api_error(res)
        return

    notes_data = res.json()
    if not notes_data:
        st.warning("所选范围内暂无蒲公英合作数据。您可以在右侧或自动采集功能中导入数据文件。")
        return

    df = pd.DataFrame(notes_data)

    # Fill default numeric columns
    num_cols = [
        "blogger_quote", "service_fee", "impressions", "reads", "read_uv",
        "interactions", "likes", "collects", "comments", "shares", "follows",
        "cost_per_read", "cost_per_interaction", "organic_impressions",
        "organic_reads", "paid_impressions", "paid_reads", "boosted_impressions", "boosted_reads"
    ]
    for c in num_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
        else:
            df[c] = 0

    df["total_cost"] = df["blogger_quote"] + df["service_fee"]

    # Render summary metrics
    m1, m2, m3, m4, m5 = st.columns(5)
    total_cost = df["total_cost"].sum()
    total_notes = len(df)
    total_impressions = int(df["impressions"].sum())
    total_interactions = int(df["interactions"].sum())
    avg_cpe = total_cost / total_interactions if total_interactions > 0 else 0.0

    m1.metric("合作笔记数", f"{total_notes} 篇")
    m2.metric("总消耗金额", f"¥{total_cost:,.2f}")
    m3.metric("总曝光量", f"{total_impressions:,}")
    m4.metric("总互动量", f"{total_interactions:,}")
    m5.metric("平均 CPE", f"¥{avg_cpe:.2f}")

    st.markdown("---")

    # Tabs
    tab_overview, tab_notes, tab_bloggers, tab_campaigns, tab_traffic, tab_raw = st.tabs(
        ["概览分析", "合作笔记", "达人分析", "项目分析", "流量与效率", "数据明细"]
    )

    # ── Tab 1: Overview ────────────────────────────────────────────────────────
    with tab_overview:
        st.markdown("#### 核心投效洞察")
        st.markdown(_generate_insight(df))

        st.markdown("<br>", unsafe_allow_html=True)
        col_c1, col_c2 = st.columns(2)

        with col_c1:
            st.markdown("##### 互动量 Top 10 合作笔记")
            top10_interactions = df.sort_values(by="interactions", ascending=False).head(10)
            chart_top_int = (
                alt.Chart(top10_interactions)
                .mark_bar(color="#EE2746", cornerRadiusEnd=4)
                .encode(
                    x=alt.X("interactions:Q", title="互动量"),
                    y=alt.Y("blogger_nickname:N", sort="-x", title="博主昵称"),
                    tooltip=["blogger_nickname", "note_title", "interactions", "impressions", "total_cost"],
                )
                .properties(height=320)
            )
            _styled_chart(chart_top_int)

        with col_c2:
            st.markdown("##### CPE (互动成本) 最优 Top 10 笔记")
            valid_cpe_df = df[df["interactions"] >= 10].copy()
            top10_cpe = valid_cpe_df.sort_values(by="cost_per_interaction", ascending=True).head(10)
            chart_top_cpe = (
                alt.Chart(top10_cpe)
                .mark_bar(color="#2EAD7A", cornerRadiusEnd=4)
                .encode(
                    x=alt.X("cost_per_interaction:Q", title="CPE (元/互动)"),
                    y=alt.Y("blogger_nickname:N", sort="x", title="博主昵称"),
                    tooltip=["blogger_nickname", "note_title", "cost_per_interaction", "interactions", "total_cost"],
                )
                .properties(height=320)
            )
            _styled_chart(chart_top_cpe)

    # ── Tab 2: Notes ───────────────────────────────────────────────────────────
    with tab_notes:
        st.markdown("#### 合作笔记明细与效率散点")

        col_scatter, col_type = st.columns([2, 1])
        with col_scatter:
            st.markdown("##### 投放成本 vs 产生互动量 (气泡大小 = 曝光量)")
            scatter_chart = (
                alt.Chart(df)
                .mark_circle(opacity=0.75)
                .encode(
                    x=alt.X("total_cost:Q", title="笔记费用 (元)"),
                    y=alt.Y("interactions:Q", title="互动量"),
                    size=alt.Size("impressions:Q", title="曝光量", scale=alt.Scale(range=[50, 800])),
                    color=alt.Color("note_type:N", title="体裁"),
                    tooltip=["blogger_nickname", "note_title", "total_cost", "interactions", "impressions", "cost_per_interaction"],
                )
                .properties(height=350)
            )
            _styled_chart(scatter_chart)

        with col_type:
            st.markdown("##### 图文 vs 视频 体裁分布")
            genre_df = df.groupby("note_type").agg(
                count=("id", "count"),
                spend=("total_cost", "sum"),
                interactions=("interactions", "sum"),
            ).reset_index()

            donut_chart = (
                alt.Chart(genre_df)
                .mark_arc(innerRadius=40)
                .encode(
                    theta=alt.Theta("spend:Q", title="总费用"),
                    color=alt.Color("note_type:N", title="体裁"),
                    tooltip=["note_type", "count", "spend", "interactions"],
                )
                .properties(height=350)
            )
            _styled_chart(donut_chart)

        st.markdown("##### 合作笔记完整列表")
        note_display_cols = [
            "publish_date", "blogger_nickname", "note_title", "note_type",
            "total_cost", "impressions", "reads", "interactions",
            "cost_per_interaction", "cost_per_read"
        ]
        available_display_cols = [c for c in note_display_cols if c in df.columns]
        st.dataframe(
            df[available_display_cols].sort_values(by="publish_date", ascending=False),
            use_container_width=True,
            hide_index=True,
            column_config={
                "publish_date": st.column_config.DateColumn("发布时间"),
                "blogger_nickname": st.column_config.TextColumn("博主昵称"),
                "note_title": st.column_config.TextColumn("笔记标题", width="large"),
                "note_type": st.column_config.TextColumn("体裁"),
                "total_cost": st.column_config.NumberColumn("投放费用", format="¥%.2f"),
                "impressions": st.column_config.NumberColumn("曝光量", format="%d"),
                "reads": st.column_config.NumberColumn("阅读量", format="%d"),
                "interactions": st.column_config.NumberColumn("互动量", format="%d"),
                "cost_per_interaction": st.column_config.NumberColumn("CPE", format="¥%.2f"),
                "cost_per_read": st.column_config.NumberColumn("CPM/CPM单价", format="¥%.2f"),
            },
        )

    # ── Tab 3: Bloggers ────────────────────────────────────────────────────────
    with tab_bloggers:
        st.markdown("#### 博主投放汇总分析")

        blogger_df = df.groupby("blogger_nickname").agg(
            notes_count=("id", "count"),
            blogger_fans=("blogger_fans", "max"),
            total_spend=("total_cost", "sum"),
            total_impressions=("impressions", "sum"),
            total_interactions=("interactions", "sum"),
        ).reset_index()

        blogger_df["avg_cpe"] = blogger_df.apply(
            lambda r: r["total_spend"] / r["total_interactions"] if r["total_interactions"] > 0 else 0.0,
            axis=1,
        )

        col_b1, col_b2 = st.columns(2)
        with col_b1:
            st.markdown("##### 合作费用 Top 博主")
            chart_b_spend = (
                alt.Chart(blogger_df.sort_values(by="total_spend", ascending=False).head(10))
                .mark_bar(color="#3B82F6")
                .encode(
                    x=alt.X("total_spend:Q", title="投放总额 (元)"),
                    y=alt.Y("blogger_nickname:N", sort="-x", title="博主"),
                    tooltip=["blogger_nickname", "notes_count", "total_spend", "total_interactions", "avg_cpe"],
                )
                .properties(height=320)
            )
            _styled_chart(chart_b_spend)

        with col_b2:
            st.markdown("##### 带来总互动 Top 博主")
            chart_b_int = (
                alt.Chart(blogger_df.sort_values(by="total_interactions", ascending=False).head(10))
                .mark_bar(color="#EC4899")
                .encode(
                    x=alt.X("total_interactions:Q", title="产生互动量"),
                    y=alt.Y("blogger_nickname:N", sort="-x", title="博主"),
                    tooltip=["blogger_nickname", "notes_count", "total_spend", "total_interactions", "avg_cpe"],
                )
                .properties(height=320)
            )
            _styled_chart(chart_b_int)

        st.markdown("##### 博主合作统计表")
        st.dataframe(
            blogger_df.sort_values(by="total_spend", ascending=False),
            use_container_width=True,
            hide_index=True,
            column_config={
                "blogger_nickname": st.column_config.TextColumn("博主昵称"),
                "blogger_fans": st.column_config.NumberColumn("粉丝量", format="%d"),
                "notes_count": st.column_config.NumberColumn("合作篇数"),
                "total_spend": st.column_config.NumberColumn("总花费", format="¥%.2f"),
                "total_impressions": st.column_config.NumberColumn("总曝光", format="%d"),
                "total_interactions": st.column_config.NumberColumn("总互动", format="%d"),
                "avg_cpe": st.column_config.NumberColumn("平均 CPE", format="¥%.2f"),
            },
        )

    # ── Tab 4: Campaigns ──────────────────────────────────────────────────────
    with tab_campaigns:
        st.markdown("#### 项目 / 合作名称汇总分析")

        campaign_df = df.groupby("cooperation_name").agg(
            notes_count=("id", "count"),
            total_spend=("total_cost", "sum"),
            total_impressions=("impressions", "sum"),
            total_interactions=("interactions", "sum"),
        ).reset_index()

        campaign_df["avg_cpe"] = campaign_df.apply(
            lambda r: r["total_spend"] / r["total_interactions"] if r["total_interactions"] > 0 else 0.0,
            axis=1,
        )

        st.markdown("##### 项目预算花费与产出")
        chart_camp = (
            alt.Chart(campaign_df)
            .mark_bar(color="#6366F1")
            .encode(
                x=alt.X("cooperation_name:N", title="项目名称", sort="-y"),
                y=alt.Y("total_spend:Q", title="总预算 (元)"),
                tooltip=["cooperation_name", "notes_count", "total_spend", "total_interactions", "avg_cpe"],
            )
            .properties(height=300)
        )
        _styled_chart(chart_camp)

        st.dataframe(
            campaign_df.sort_values(by="total_spend", ascending=False),
            use_container_width=True,
            hide_index=True,
            column_config={
                "cooperation_name": st.column_config.TextColumn("合作项目名称"),
                "notes_count": st.column_config.NumberColumn("合作笔记数"),
                "total_spend": st.column_config.NumberColumn("总投入", format="¥%.2f"),
                "total_impressions": st.column_config.NumberColumn("总曝光", format="%d"),
                "total_interactions": st.column_config.NumberColumn("总互动", format="%d"),
                "avg_cpe": st.column_config.NumberColumn("平均 CPE", format="¥%.2f"),
            },
        )

    # ── Tab 5: Traffic & Efficiency ───────────────────────────────────────────
    with tab_traffic:
        st.markdown("#### 流量来源结构 (自然流量 vs 推广流量)")

        organic_imp = df["organic_impressions"].sum()
        paid_imp = df["paid_impressions"].sum()
        boosted_imp = df["boosted_impressions"].sum()

        traffic_df = pd.DataFrame([
            {"type": "自然流量曝光", "impressions": organic_imp},
            {"type": "推广流量曝光", "impressions": paid_imp},
            {"type": "加热流量曝光", "impressions": boosted_imp},
        ])

        col_t1, col_t2 = st.columns(2)
        with col_t1:
            st.markdown("##### 曝光流量构成占比")
            chart_t_pie = (
                alt.Chart(traffic_df)
                .mark_arc(innerRadius=50)
                .encode(
                    theta=alt.Theta("impressions:Q"),
                    color=alt.Color("type:N", title="流量来源"),
                    tooltip=["type", "impressions"],
                )
                .properties(height=300)
            )
            _styled_chart(chart_t_pie)

        with col_t2:
            st.markdown("##### 互动构成 (点赞/收藏/评论/分享)")
            likes_sum = df["likes"].sum()
            collects_sum = df["collects"].sum()
            comments_sum = df["comments"].sum()
            shares_sum = df["shares"].sum()

            inter_df = pd.DataFrame([
                {"type": "点赞", "count": likes_sum},
                {"type": "收藏", "count": collects_sum},
                {"type": "评论", "count": comments_sum},
                {"type": "分享", "count": shares_sum},
            ])

            chart_inter_pie = (
                alt.Chart(inter_df)
                .mark_arc(innerRadius=50)
                .encode(
                    theta=alt.Theta("count:Q"),
                    color=alt.Color("type:N", title="互动类型"),
                    tooltip=["type", "count"],
                )
                .properties(height=300)
            )
            _styled_chart(chart_inter_pie)

    # ── Tab 6: Raw Data & Export ──────────────────────────────────────────────
    with tab_raw:
        st.markdown("#### 数据明细与 CSV 导出")
        st.dataframe(df, use_container_width=True)

        csv_bytes = df.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            label="下载蒲公英合作明细 CSV",
            data=csv_bytes,
            file_name=f"pgy_notes_{selected_acc_name}_{date.today().isoformat()}.csv",
            mime="text/csv",
        )
