from __future__ import annotations

from datetime import date, timedelta

import altair as alt
import pandas as pd
import streamlit as st

from app.ui._helpers import _page_hero, _styled_chart, show_api_error
from app.utils.topic_matching import match_article_topics
from app.views.media.analysis import aggregate_read_sources, build_topic_source_matrix


def _account_options(accounts: list[dict]) -> tuple[list[str], dict[str, int | None]]:
    labels = ["全部账号"]
    mapping: dict[str, int | None] = {"全部账号": None}
    for account in accounts:
        label = f"{account.get('name') or '未命名账号'} ({account.get('platform')})"
        labels.append(label)
        mapping[label] = account.get("id")
    return labels, mapping


def _diagnose_article(row, avg_read, avg_share_rate, avg_like_rate):
    read = row.get("read_user_count", 0)
    share_rate = row.get("share_rate", 0)
    like_rate = row.get("like_rate", 0)
    
    if read == 0:
        return "暂无流量数据"
        
    insights = []
    
    if avg_read > 0 and read > avg_read * 1.5:
        insights.append("🌟 高流量爆款潜力")
    elif avg_read > 0 and read < avg_read * 0.5:
        insights.append("📉 流量遇冷")
    else:
        insights.append("流量平稳")
        
    if avg_share_rate > 0 and share_rate > max(avg_share_rate * 1.2, 0.03):
        insights.append("🚀 社交传播力强")
    elif avg_share_rate > 0 and share_rate < avg_share_rate * 0.5 and read > avg_read:
        insights.append("⚠️ 高阅读低分享(可能为标题党或快讯)")
        
    if avg_like_rate > 0 and like_rate > max(avg_like_rate * 1.2, 0.02):
        insights.append("💖 内容认可度高(深度干货)")
        
    return " | ".join(insights)


def _generate_overall_analysis(df, overview):
    if df.empty:
        return "当前区间暂无发文或流量数据。"
    
    total_reads = overview.get("read_user_count", 0)
    total_shares = overview.get("share_user_count", 0)
    avg_read = overview.get("avg_read_user_count", 0)
    
    avg_share_rate = (total_shares / total_reads) if total_reads > 0 else 0
    
    top_post = df.sort_values("read_user_count", ascending=False).iloc[0]
    
    analysis = f"**🤖 数据洞察结论**：\n"
    analysis += f"- **大盘表现**：在选定区间内，共发布 {overview.get('posts', 0)} 篇文章，累计收获阅读 {total_reads:,} 次。平均单篇阅读量约 {avg_read:,.0f}。\n"
    analysis += f"- **互动健康度**：整体分享率达 {avg_share_rate:.2%}。" + ("分享意愿较好，账号具备一定的自发传播力。" if avg_share_rate >= 0.03 else "整体分享动力较弱，建议增加引导分享或提升内容共鸣。") + "\n"
    analysis += f"- **流量担当**：本期最受关注的文章是《{top_post['title']}》（阅读 {top_post['read_user_count']:,} 次，分享 {top_post['share_user_count']:,} 次）。"
    return analysis


def page_media() -> None:
    _page_hero("公众号数据")
    client = st.session_state["client"]

    default_end = date.today()
    default_start = default_end - timedelta(days=7)

    r_accounts = client.media_accounts()
    if r_accounts.status_code != 200:
        show_api_error(r_accounts)
        return
    accounts = r_accounts.json()
    labels, account_map = _account_options(accounts)

    col1, col2, col3, col4 = st.columns([2, 2, 2, 1.4])
    start = col1.date_input("开始日期", value=default_start, key="media_start")
    end = col2.date_input("结束日期", value=default_end, key="media_end")
    selected_account = col3.selectbox("账号", labels, key="media_account")
    account_id = account_map.get(selected_account)
    query = col4.text_input("搜索", value="", key="media_query", placeholder="标题")

    if start > end:
        st.error("开始日期不能晚于结束日期。")
        return

    if st.session_state.get("is_admin"):
        sync_cols = st.columns([1.2, 4])
        if sync_cols[0].button("同步公众号", use_container_width=True):
            with st.spinner("正在同步公众号数据…"):
                r_sync = client.media_sync_wechat(str(start), str(end), account_id)
            if r_sync.status_code == 200:
                data = r_sync.json()
                st.success(
                    f"同步完成：文章 {data.get('posts_upserted', 0)} 篇，"
                    f"指标 {data.get('metrics_upserted', 0)} 条。"
                )
            else:
                show_api_error(r_sync)
                return

    r_overview = client.media_overview(str(start), str(end), account_id)
    r_posts = client.media_posts(str(start), str(end), account_id, query.strip() or None)
    if r_overview.status_code != 200:
        show_api_error(r_overview)
        return
    if r_posts.status_code != 200:
        show_api_error(r_posts)
        return

    overview = r_overview.json()
    posts = r_posts.json()

    if not posts:
        st.info("当前筛选范围内没有公众号文章数据。")
        return

    df = pd.DataFrame(posts)
    
    # Preprocess dataframe for analysis
    for col in ["read_user_count", "share_user_count", "like_user", "comment_count", "collection_user"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    if "read_finish_rate" in df.columns:
        df["read_finish_rate"] = pd.to_numeric(df["read_finish_rate"], errors="coerce")
            
    df["share_rate"] = (df["share_user_count"] / df["read_user_count"].replace(0, 1)).round(4)
    df["like_rate"] = (df["like_user"] / df["read_user_count"].replace(0, 1)).round(4)
    if "publish_date" in df.columns:
        df["publish_date_only"] = pd.to_datetime(df["publish_date"]).dt.date
        
    avg_r = overview.get("avg_read_user_count", 0)
    avg_sr = df["share_rate"].mean() if not df.empty else 0
    avg_lr = df["like_rate"].mean() if not df.empty else 0
    df["diagnosis"] = df.apply(lambda row: _diagnose_article(row, avg_r, avg_sr, avg_lr), axis=1)

    tab_overview, tab_trend, tab_engagement, tab_source, tab_topic, tab_list = st.tabs(
        ["概览分析", "趋势分析", "互动分析", "流量来源", "话题标签", "文章明细"]
    )

    with tab_overview:
        st.info(_generate_overall_analysis(df, overview))
        
        m1, m2, m3, m4, m5, m6 = st.columns(6)
        m1.metric("文章数", overview.get("posts", 0))
        m2.metric("阅读人数", f"{overview.get('read_user_count', 0):,}")
        m3.metric("均篇阅读", f"{overview.get('avg_read_user_count', 0):,.0f}")
        m4.metric("分享人数", f"{overview.get('share_user_count', 0):,}")
        m5.metric("点赞人数", f"{overview.get('like_user', 0):,}")
        m6.metric("评论数", f"{overview.get('comment_count', 0):,}")
        
        st.markdown("### 表现最佳的文章")
        col_top1, col_top2 = st.columns(2)
        
        with col_top1:
            top_read_df = df.sort_values("read_user_count", ascending=False).head(10)
            top_read_chart = (
                alt.Chart(top_read_df)
                .mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4, color="#0ea5e9")
                .encode(
                    x=alt.X("read_user_count:Q", title="阅读人数"),
                    y=alt.Y("title:N", sort="-x", title="", axis=alt.Axis(labelLimit=200)),
                    tooltip=[
                        alt.Tooltip("title:N", title="标题"), 
                        alt.Tooltip("read_user_count:Q", title="阅读人数"), 
                        alt.Tooltip("share_user_count:Q", title="分享人数"), 
                        alt.Tooltip("like_user:Q", title="点赞数")
                    ]
                )
                .properties(title="按阅读量Top 10", height=300)
            )
            st.altair_chart(_styled_chart(top_read_chart), use_container_width=True)
            
        with col_top2:
            top_share_df = df.sort_values("share_user_count", ascending=False).head(10)
            top_share_chart = (
                alt.Chart(top_share_df)
                .mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4, color="#10b981")
                .encode(
                    x=alt.X("share_user_count:Q", title="分享人数"),
                    y=alt.Y("title:N", sort="-x", title="", axis=alt.Axis(labelLimit=200)),
                    tooltip=[
                        alt.Tooltip("title:N", title="标题"), 
                        alt.Tooltip("read_user_count:Q", title="阅读人数"), 
                        alt.Tooltip("share_user_count:Q", title="分享人数"), 
                        alt.Tooltip("like_user:Q", title="点赞数")
                    ]
                )
                .properties(title="按分享量Top 10", height=300)
            )
            st.altair_chart(_styled_chart(top_share_chart), use_container_width=True)

    with tab_trend:
        st.markdown("### 发布趋势与阅读量表现")
        if "publish_date_only" in df.columns:
            trend_df = df.groupby("publish_date_only").agg(
                post_count=("id", "count"),
                total_reads=("read_user_count", "sum"),
                total_shares=("share_user_count", "sum")
            ).reset_index()
            
            base = alt.Chart(trend_df).encode(x=alt.X("publish_date_only:T", title="发布日期"))
            bar = base.mark_bar(opacity=0.6, color="#94a3b8", size=15).encode(
                y=alt.Y("post_count:Q", title="发文数", axis=alt.Axis(grid=False))
            )
            line = base.mark_line(point=alt.OverlayMarkDef(filled=True, size=60), color="#0ea5e9", strokeWidth=3).encode(
                y=alt.Y("total_reads:Q", title="阅读总数")
            )
            trend_chart = alt.layer(bar, line).resolve_scale(y="independent").properties(height=350, title="每日发文数与阅读总量趋势")
            st.altair_chart(_styled_chart(trend_chart), use_container_width=True)
        else:
            st.info("数据中缺少发布时间，无法绘制趋势图。")

    with tab_engagement:
        st.markdown("### 互动效率分析 (阅读转化率)")
        
        scatter_chart = (
            alt.Chart(df)
            .mark_circle(size=80, opacity=0.7, color="#8b5cf6")
            .encode(
                x=alt.X("read_user_count:Q", title="阅读人数"),
                y=alt.Y("share_user_count:Q", title="分享人数"),
                size=alt.Size("like_user:Q", title="点赞数", scale=alt.Scale(range=[40, 600])),
                tooltip=[
                    alt.Tooltip("title:N", title="标题"), 
                    alt.Tooltip("read_user_count:Q", title="阅读人数"), 
                    alt.Tooltip("share_user_count:Q", title="分享人数"), 
                    alt.Tooltip("like_user:Q", title="点赞数"), 
                    alt.Tooltip("share_rate:Q", title="分享率", format=".2%")
                ]
            )
            .properties(height=400, title="阅读量与分享量的相关性 (气泡大小=点赞数)")
            .interactive()
        )
        st.altair_chart(_styled_chart(scatter_chart), use_container_width=True)
        
        min_reads = 50
        filtered_df = df[df["read_user_count"] >= min_reads]
        if not filtered_df.empty:
            top_share_rate_df = filtered_df.sort_values("share_rate", ascending=False).head(10)
            share_rate_chart = (
                alt.Chart(top_share_rate_df)
                .mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4, color="#f59e0b")
                .encode(
                    x=alt.X("share_rate:Q", title="分享率", axis=alt.Axis(format="%")),
                    y=alt.Y("title:N", sort="-x", title="", axis=alt.Axis(labelLimit=200)),
                    tooltip=[
                        alt.Tooltip("title:N", title="标题"), 
                        alt.Tooltip("share_rate:Q", title="分享率", format=".2%"),
                        alt.Tooltip("read_user_count:Q", title="阅读人数"), 
                        alt.Tooltip("share_user_count:Q", title="分享人数")
                    ]
                )
                .properties(title=f"最高分享率的文章 (仅统计阅读数≥{min_reads}的文章)", height=300)
            )
            st.altair_chart(_styled_chart(share_rate_chart), use_container_width=True)
        else:
            st.info(f"没有阅读数达到 {min_reads} 的文章，无法计算有效分享率排行。")

        # ── 层次三: 完读率 × 互动矩阵 ──────────────────────────────────────────
        if "read_finish_rate" in df.columns and df["read_finish_rate"].notna().any():
            st.markdown("### 完读率 × 分享率矩阵")
            st.caption("高完读高分享 = 深度爆款 | 高完读低分享 = 深度留存 | 低完读高分享 = 标题党 | 低完读低分享 = 待优化")
            finish_df = df[df["read_finish_rate"].notna() & (df["read_user_count"] > 0)].copy()
            if not finish_df.empty:
                avg_finish = finish_df["read_finish_rate"].mean()
                avg_share_r = finish_df["share_rate"].mean()
                quadrant_chart = (
                    alt.Chart(finish_df)
                    .mark_circle(opacity=0.75)
                    .encode(
                        x=alt.X("read_finish_rate:Q", title="完读率", axis=alt.Axis(format="%"), scale=alt.Scale(domain=[0, 1])),
                        y=alt.Y("share_rate:Q", title="分享率", axis=alt.Axis(format="%")),
                        size=alt.Size("read_user_count:Q", title="阅读人数", scale=alt.Scale(range=[40, 700])),
                        color=alt.Color("read_user_count:Q", scale=alt.Scale(scheme="blues"), title="阅读人数"),
                        tooltip=[
                            alt.Tooltip("title:N", title="标题"),
                            alt.Tooltip("read_finish_rate:Q", title="完读率", format=".1%"),
                            alt.Tooltip("share_rate:Q", title="分享率", format=".2%"),
                            alt.Tooltip("read_user_count:Q", title="阅读人数"),
                        ],
                    )
                    .properties(height=420, title="完读率 × 分享率分布（气泡大小=阅读人数）")
                    .interactive()
                )
                avg_finish_line = alt.Chart(pd.DataFrame({"x": [avg_finish]})).mark_rule(strokeDash=[4, 4], color="#94a3b8").encode(x="x:Q")
                avg_share_line = alt.Chart(pd.DataFrame({"y": [avg_share_r]})).mark_rule(strokeDash=[4, 4], color="#94a3b8").encode(y="y:Q")
                st.altair_chart(_styled_chart(quadrant_chart + avg_finish_line + avg_share_line), use_container_width=True)
                st.caption(f"虚线：完读率均值 {avg_finish:.1%}，分享率均值 {avg_share_r:.2%}")
            else:
                st.info("没有同时具备完读率和阅读数据的文章。")
        else:
            st.info("当前同步数据中没有完读率字段。确认账号已使用 getarticletotaldetail API 同步数据。")

    # ── 层次一: 流量来源分析 ───────────────────────────────────────────────────
    with tab_source:
        st.markdown("### 流量来源分析")
        r_source = client.media_source_breakdown(str(start), str(end), account_id)
        if r_source.status_code != 200:
            show_api_error(r_source)
        else:
            source_data = r_source.json()
            if not source_data:
                st.info("当前区间内没有流量来源数据。请先同步公众号数据，并确认 API 权限包含 getarticletotaldetail 接口。")
            else:
                source_df = pd.DataFrame(
                    [{"来源渠道": k, "阅读人数": v} for k, v in sorted(source_data.items(), key=lambda x: -x[1])]
                )
                source_chart = (
                    alt.Chart(source_df)
                    .mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4, color="#6366f1")
                    .encode(
                        x=alt.X("阅读人数:Q", title="阅读人数"),
                        y=alt.Y("来源渠道:N", sort="-x", title=""),
                        tooltip=[
                            alt.Tooltip("来源渠道:N", title="来源渠道"),
                            alt.Tooltip("阅读人数:Q", title="阅读人数", format=","),
                        ],
                    )
                    .properties(title="各来源渠道汇总阅读人数", height=max(180, 36 * len(source_df)))
                )
                st.altair_chart(_styled_chart(source_chart), use_container_width=True)

                total_sourced = sum(source_data.values())
                st.markdown("#### 各渠道占比")
                share_rows = [
                    {"渠道": k, "人数": v, "占比": f"{v / total_sourced:.1%}" if total_sourced else "—"}
                    for k, v in sorted(source_data.items(), key=lambda x: -x[1])
                ]
                st.dataframe(pd.DataFrame(share_rows), use_container_width=True, hide_index=True)

        st.markdown("---")
        st.markdown("### 单篇文章流量来源拆解")
        st.caption("选择一篇文章，查看其各日期的读者来源分布。")
        post_labels_src = dict(zip(df["id"], df["title"]))
        src_choice = st.selectbox(
            "选择文章",
            ["—"] + df["id"].tolist(),
            format_func=lambda pid: "—" if pid == "—" else post_labels_src.get(pid, str(pid)),
            key="media_src_post_choice",
        )
        if src_choice != "—":
            r_metrics_src = client.media_post_metrics(int(src_choice))
            if r_metrics_src.status_code == 200:
                metric_rows = r_metrics_src.json()
                all_sources = [m.get("read_user_source") for m in metric_rows]
                post_source_data = aggregate_read_sources(all_sources)
                if not post_source_data:
                    st.info("该文章暂无流量来源数据。")
                else:
                    ps_df = pd.DataFrame(
                        [{"来源渠道": k, "阅读人数": v} for k, v in sorted(post_source_data.items(), key=lambda x: -x[1])]
                    )
                    ps_chart = (
                        alt.Chart(ps_df)
                        .mark_arc(innerRadius=60)
                        .encode(
                            theta=alt.Theta("阅读人数:Q"),
                            color=alt.Color("来源渠道:N", legend=alt.Legend(title="来源渠道")),
                            tooltip=[
                                alt.Tooltip("来源渠道:N", title="来源"),
                                alt.Tooltip("阅读人数:Q", title="阅读人数", format=","),
                            ],
                        )
                        .properties(height=300, title=f"《{post_labels_src.get(src_choice)}》来源分布")
                    )
                    st.altair_chart(_styled_chart(ps_chart), use_container_width=True)
            else:
                show_api_error(r_metrics_src)

    # ── 层次二: 话题标签分析 ──────────────────────────────────────────────────
    with tab_topic:
        st.markdown("### 话题标签分析")
        st.caption("自定义关键词组，系统将按标题匹配归类，对比各话题的流量与互动表现。")

        with st.expander("⚙️ 设置话题关键词", expanded=True):
            n_topics = st.number_input("话题数量", min_value=1, max_value=6, value=3, step=1, key="topic_n")
            topic_map: dict[str, list[str]] = {}
            for i in range(int(n_topics)):
                c1, c2 = st.columns([1, 3])
                topic_name = c1.text_input(f"话题 {i+1} 名称", value=["新品发布", "行业报告", "促销活动"][i] if i < 3 else f"话题{i+1}", key=f"topic_name_{i}")
                kw_raw = c2.text_input(f"关键词（逗号分隔）", value=["新品,上市,发布", "行业,报告,趋势", "折扣,优惠,特惠"][i] if i < 3 else "", key=f"topic_kw_{i}")
                if topic_name and kw_raw:
                    topic_map[topic_name] = [k.strip() for k in kw_raw.split(",") if k.strip()]

        if not topic_map:
            st.info("请至少设置一个话题及其关键词。")
        else:
            topic_df = df.copy()
            topic_df["话题"] = topic_df["title"].apply(lambda t: match_article_topics(t, topic_map))
            topic_df = topic_df.explode("话题")

            grouped = (
                topic_df.groupby("话题")
                .agg(
                    文章数=("id", "count"),
                    均篇阅读=("read_user_count", "mean"),
                    均篇分享=("share_user_count", "mean"),
                    均篇点赞=("like_user", "mean"),
                    总阅读=("read_user_count", "sum"),
                )
                .round(1)
                .reset_index()
                .sort_values("均篇阅读", ascending=False)
            )

            col_t1, col_t2 = st.columns(2)
            with col_t1:
                avg_read_chart = (
                    alt.Chart(grouped)
                    .mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4, color="#0ea5e9")
                    .encode(
                        x=alt.X("均篇阅读:Q", title="均篇阅读人数"),
                        y=alt.Y("话题:N", sort="-x", title=""),
                        tooltip=[
                            alt.Tooltip("话题:N", title="话题"),
                            alt.Tooltip("文章数:Q", title="文章数"),
                            alt.Tooltip("均篇阅读:Q", title="均篇阅读", format=".0f"),
                            alt.Tooltip("总阅读:Q", title="总阅读", format=","),
                        ],
                    )
                    .properties(title="各话题均篇阅读量", height=max(200, 40 * len(grouped)))
                )
                st.altair_chart(_styled_chart(avg_read_chart), use_container_width=True)

            with col_t2:
                avg_share_chart = (
                    alt.Chart(grouped)
                    .mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4, color="#10b981")
                    .encode(
                        x=alt.X("均篇分享:Q", title="均篇分享人数"),
                        y=alt.Y("话题:N", sort="-x", title=""),
                        tooltip=[
                            alt.Tooltip("话题:N", title="话题"),
                            alt.Tooltip("均篇分享:Q", title="均篇分享", format=".1f"),
                            alt.Tooltip("均篇点赞:Q", title="均篇点赞", format=".1f"),
                        ],
                    )
                    .properties(title="各话题均篇分享量", height=max(200, 40 * len(grouped)))
                )
                st.altair_chart(_styled_chart(avg_share_chart), use_container_width=True)

            st.markdown("#### 话题汇总表")
            st.dataframe(grouped, use_container_width=True, hide_index=True)

            st.markdown("#### 各话题文章明细")
            selected_topic = st.selectbox("选择查看的话题", grouped["话题"].tolist(), key="topic_detail_select")
            detail_df = topic_df[topic_df["话题"] == selected_topic][
                [c for c in ["title", "publish_date_only", "read_user_count", "share_user_count", "like_user", "diagnosis"] if c in topic_df.columns]
            ].sort_values("read_user_count", ascending=False)
            st.dataframe(detail_df, use_container_width=True, hide_index=True,
                column_config={"title": st.column_config.TextColumn("标题", width="large"),
                               "publish_date_only": st.column_config.TextColumn("发布日期"),
                               "read_user_count": st.column_config.NumberColumn("阅读人数", format="%d"),
                               "share_user_count": st.column_config.NumberColumn("分享人数", format="%d"),
                               "like_user": st.column_config.NumberColumn("点赞数", format="%d"),
                               "diagnosis": st.column_config.TextColumn("诊断", width="medium")})

            # ── 话题 × 来源交叉分析 ────────────────────────────────────────────
            st.markdown("---")
            st.markdown("### 话题 × 流量来源交叉分析")
            st.caption("各话题文章的读者分别从哪些渠道进来——判断哪类内容靠搜索流量、哪类靠社交扩散。")

            r_sbp = client.media_source_by_post(str(start), str(end), account_id)
            if r_sbp.status_code != 200:
                show_api_error(r_sbp)
            else:
                post_sources: dict[str, dict[str, int]] = r_sbp.json()
                posts_for_matrix = df[["id", "title"]].to_dict("records")
                matrix = build_topic_source_matrix(posts_for_matrix, post_sources, topic_map)

                if not matrix:
                    st.info("当前区间内没有带流量来源数据的文章。请确认同步数据包含 read_user_source 字段。")
                else:
                    # Build long-form DataFrame for stacked bar
                    cross_rows = [
                        {"话题": topic, "来源渠道": scene, "阅读人数": count}
                        for topic, scenes in matrix.items()
                        for scene, count in scenes.items()
                    ]
                    cross_df = pd.DataFrame(cross_rows)

                    # Sort scenes by total volume for stable color assignment
                    scene_order = (
                        cross_df.groupby("来源渠道")["阅读人数"].sum()
                        .sort_values(ascending=False)
                        .index.tolist()
                    )

                    stacked = (
                        alt.Chart(cross_df)
                        .mark_bar(cornerRadiusTopRight=3, cornerRadiusBottomRight=3)
                        .encode(
                            x=alt.X("阅读人数:Q", title="阅读人数（来源合计）", stack="zero"),
                            y=alt.Y("话题:N", sort=alt.SortField("阅读人数", order="descending"), title=""),
                            color=alt.Color(
                                "来源渠道:N",
                                scale=alt.Scale(scheme="tableau10"),
                                sort=scene_order,
                                legend=alt.Legend(title="来源渠道", orient="bottom"),
                            ),
                            tooltip=[
                                alt.Tooltip("话题:N", title="话题"),
                                alt.Tooltip("来源渠道:N", title="来源渠道"),
                                alt.Tooltip("阅读人数:Q", title="阅读人数", format=","),
                            ],
                            order=alt.Order("来源渠道:N"),
                        )
                        .properties(
                            title="各话题流量来源构成（横向堆叠柱图）",
                            height=max(220, 48 * len(matrix)),
                        )
                    )
                    st.altair_chart(_styled_chart(stacked), use_container_width=True)

                    # Percentage breakdown table
                    pct_rows = []
                    for topic, scenes in matrix.items():
                        total = sum(scenes.values())
                        for scene, count in sorted(scenes.items(), key=lambda x: -x[1]):
                            pct_rows.append({
                                "话题": topic,
                                "来源渠道": scene,
                                "阅读人数": count,
                                "占该话题比例": f"{count / total:.1%}" if total else "—",
                            })
                    st.markdown("#### 各话题来源占比明细")
                    st.dataframe(
                        pd.DataFrame(pct_rows).sort_values(["话题", "阅读人数"], ascending=[True, False]),
                        use_container_width=True,
                        hide_index=True,
                    )

    with tab_list:
        display_cols = [
            "title",
            "publish_date",
            "diagnosis",
            "read_user_count",
            "share_user_count",
            "like_user",
            "comment_count",
            "collection_user",
            "share_rate",
            "url",
        ]
        st.dataframe(
            df[[c for c in display_cols if c in df.columns]],
            use_container_width=True,
            hide_index=True,
            column_config={
                "title": st.column_config.TextColumn("标题", width="medium"),
                "publish_date": st.column_config.TextColumn("发布日期"),
                "diagnosis": st.column_config.TextColumn("逐篇诊断分析", width="large"),
                "read_user_count": st.column_config.NumberColumn("阅读人数", format="%d"),
                "share_user_count": st.column_config.NumberColumn("分享人数", format="%d"),
                "like_user": st.column_config.NumberColumn("点赞数", format="%d"),
                "comment_count": st.column_config.NumberColumn("评论数", format="%d"),
                "collection_user": st.column_config.NumberColumn("收藏数", format="%d"),
                "share_rate": st.column_config.NumberColumn("分享率", format="%.2%"),
                "url": st.column_config.LinkColumn("链接"),
            },
        )

        st.markdown("#### 单篇文章详细趋势")
        post_labels = dict(zip(df["id"], df["title"]))
        choice = st.selectbox(
            "选择文章查看每日趋势",
            ["—"] + df["id"].tolist(),
            format_func=lambda post_id: "—" if post_id == "—" else post_labels.get(post_id, str(post_id)),
            key="media_post_choice",
        )
        if choice != "—":
            r_metrics = client.media_post_metrics(int(choice))
            if r_metrics.status_code != 200:
                show_api_error(r_metrics)
                return
            metric_df = pd.DataFrame(r_metrics.json())
            if not metric_df.empty:
                metric_df["metric_date"] = pd.to_datetime(metric_df["metric_date"])
                trend_vars = [c for c in ["read_user_count", "share_user_count", "like_user", "comment_count", "collection_user"] if c in metric_df.columns]
                long_df = metric_df.melt(
                    id_vars=["metric_date"],
                    value_vars=trend_vars,
                    var_name="指标",
                    value_name="数值",
                )
                labels_map = {
                    "read_user_count": "阅读人数",
                    "share_user_count": "分享人数",
                    "like_user": "点赞人数",
                    "comment_count": "评论数",
                    "collection_user": "收藏人数",
                }
                long_df["指标"] = long_df["指标"].map(labels_map)
                line = (
                    alt.Chart(long_df)
                    .mark_line(point=alt.OverlayMarkDef(filled=True, size=50))
                    .encode(
                        x=alt.X("metric_date:T", title="日期"),
                        y=alt.Y("数值:Q", title="数值"),
                        color=alt.Color("指标:N", legend=alt.Legend(title="指标", orient="bottom")),
                        tooltip=["metric_date:T", "指标:N", "数值:Q"],
                    )
                    .properties(height=300)
                )
                st.altair_chart(_styled_chart(line), use_container_width=True)
            else:
                st.info("该文章暂无每日趋势数据。")
