from __future__ import annotations

from datetime import date, timedelta

import altair as alt
import pandas as pd
import streamlit as st

from app.ui._helpers import _page_hero, _styled_chart, show_api_error

_CONTENT_TYPES = {"文章": "article", "问答": "qa"}

_COL_LABELS = {
    "title": "标题", "publish_date": "发布日期", "url": "链接",
    "reads": "阅读", "plays": "播放", "likes": "点赞",
    "favorites": "喜欢", "comments": "评论", "collects": "收藏", "shares": "分享",
}

_MIN_READS = 100  # minimum reads to include in rate-based rankings

_WEEKDAY_CN = {
    "Monday": "周一", "Tuesday": "周二", "Wednesday": "周三",
    "Thursday": "周四", "Friday": "周五", "Saturday": "周六", "Sunday": "周日",
}
_WEEKDAY_ORDER = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _preprocess(df: pd.DataFrame, content_type: str) -> pd.DataFrame:
    numeric_cols = ["reads", "plays", "likes", "favorites", "comments", "collects", "shares"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    if "publish_date" in df.columns:
        df["publish_date_dt"] = pd.to_datetime(df["publish_date"], errors="coerce")
        df["weekday_cn"] = df["publish_date_dt"].dt.day_name().map(_WEEKDAY_CN)

    reads_safe = df["reads"].replace(0, float("nan"))
    df["like_rate"]    = (df["likes"]    / reads_safe).round(4)
    df["collect_rate"] = (df["collects"] / reads_safe).round(4)
    df["comment_rate"] = (df["comments"] / reads_safe).round(4)
    df["share_rate"]   = (df["shares"]   / reads_safe).round(4)
    df["engagement_rate"] = (
        (df["likes"] + df["comments"] + df["collects"] + df["shares"]) / reads_safe
    ).round(4)
    return df


def _generate_insight(df: pd.DataFrame, label: str, content_type: str) -> str:
    total       = len(df)
    total_reads = int(df["reads"].sum())
    avg_reads   = df["reads"].mean()
    qualified   = df[df["reads"] >= _MIN_READS]
    avg_like_rate    = qualified["like_rate"].mean()    if not qualified.empty else float("nan")
    avg_collect_rate = qualified["collect_rate"].mean() if not qualified.empty else float("nan")

    top = df.sort_values("reads", ascending=False).iloc[0]

    insight = "**🤖 数据洞察结论**：\n"
    insight += (
        f"- **大盘表现**：共发布 {total} 条{label}，"
        f"累计阅读 {total_reads:,} 次，均篇阅读 {avg_reads:,.0f} 次。\n"
    )
    if content_type == "qa" and "plays" in df.columns and df["plays"].sum() > 0:
        insight += f"- **视频播放**：累计播放 {int(df['plays'].sum()):,} 次。\n"
    if pd.notna(avg_like_rate):
        like_eval    = "互动质量较好。" if avg_like_rate >= 0.02 else "点赞转化有提升空间。"
        collect_eval = "内容收藏价值较高。" if pd.notna(avg_collect_rate) and avg_collect_rate >= 0.01 else ""
        insight += (
            f"- **互动质量**：点赞率均值 {avg_like_rate:.2%}，收藏率均值 "
            f"{avg_collect_rate:.2%}（阅读≥{_MIN_READS} 的内容）。"
            f"{like_eval}"
            + (f" {collect_eval}" if collect_eval else "") + "\n"
        )
    insight += f"- **阅读担当**：《{top['title']}》本期阅读最多（{top['reads']:,.0f} 次）。"
    return insight


def _render_tab(client, label: str, content_type: str) -> None:
    # ── Upload section ────────────────────────────────────────────────────────
    st.markdown(f"#### 上传{label}导出文件")
    st.caption("从知乎创作中心导出 CSV/XLS，上传后自动 upsert。已有内容更新数据；未出现的内容保持不变。")

    with st.form(f"zhihu-upload-form-{content_type}", clear_on_submit=True):
        uploaded = st.file_uploader(
            f"选择知乎{label}文件",
            type=["csv", "xls", "xlsx"],
            key=f"zhihu_uploader_{content_type}",
        )
        submitted = st.form_submit_button("上传")
    if submitted and uploaded is None:
        st.warning("请先选择文件。")
    elif submitted:
        with st.spinner("上传中…"):
            r = client.upload_zhihu(uploaded.read(), uploaded.name, content_type)
        if r.status_code == 200:
            data = r.json()
            st.success(f"上传成功：共处理 **{data['total']}** 条{label}。")
            st.session_state.pop(f"zhihu_cache_{content_type}", None)
        else:
            show_api_error(r, "上传失败。")

    st.markdown("---")

    # ── Date filter ───────────────────────────────────────────────────────────
    st.markdown(f"#### {label}数据")
    col1, col2 = st.columns(2)
    start = col1.date_input(
        "开始日期", value=date.today() - timedelta(days=90),
        key=f"zhihu_start_{content_type}",
    )
    end = col2.date_input(
        "结束日期", value=date.today(),
        key=f"zhihu_end_{content_type}",
    )
    if start > end:
        st.error("开始日期不能晚于结束日期。")
        return

    cache_key = f"zhihu_cache_{content_type}_{start}_{end}"
    if (
        st.session_state.get(f"_zhihu_cache_key_{content_type}") != cache_key
        or f"zhihu_cache_{content_type}" not in st.session_state
    ):
        r = client.zhihu_posts(content_type=content_type,
                               start_date=str(start), end_date=str(end), limit=500)
        if r.status_code != 200:
            show_api_error(r)
            return
        st.session_state[f"zhihu_cache_{content_type}"] = r.json()
        st.session_state[f"_zhihu_cache_key_{content_type}"] = cache_key

    posts = st.session_state[f"zhihu_cache_{content_type}"]
    if not posts:
        st.info("该时间段内暂无数据，请先上传文件。")
        return

    df = _preprocess(pd.DataFrame(posts), content_type)

    sub_overview, sub_engagement, sub_trend, sub_list = st.tabs(
        ["概览分析", "互动效率", "发布趋势", "内容明细"]
    )

    # ── 概览分析 ──────────────────────────────────────────────────────────────
    with sub_overview:
        st.info(_generate_insight(df, label, content_type))

        metric_cols = st.columns(5 if content_type == "qa" else 4)
        metric_cols[0].metric("内容数", len(df))
        metric_cols[1].metric("总阅读", f"{df['reads'].sum():,.0f}")
        metric_cols[2].metric("总点赞", f"{df['likes'].sum():,.0f}")
        metric_cols[3].metric("总收藏", f"{df['collects'].sum():,.0f}")
        if content_type == "qa" and "plays" in df.columns:
            metric_cols[4].metric("总播放", f"{df['plays'].sum():,.0f}")

        col_a, col_b = st.columns(2)
        with col_a:
            top_reads = df.nlargest(10, "reads")
            st.altair_chart(
                _styled_chart(
                    alt.Chart(top_reads)
                    .mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4, color="#0ea5e9")
                    .encode(
                        x=alt.X("reads:Q", title="阅读量"),
                        y=alt.Y("title:N", sort="-x", title="", axis=alt.Axis(labelLimit=220)),
                        tooltip=[
                            alt.Tooltip("title:N",    title="标题"),
                            alt.Tooltip("reads:Q",    title="阅读",  format=","),
                            alt.Tooltip("likes:Q",    title="点赞",  format=","),
                            alt.Tooltip("collects:Q", title="收藏",  format=","),
                        ],
                    )
                    .properties(title=f"阅读量 Top 10", height=300)
                ),
                use_container_width=True,
            )
        with col_b:
            top_collects = df.nlargest(10, "collects")
            st.altair_chart(
                _styled_chart(
                    alt.Chart(top_collects)
                    .mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4, color="#8b5cf6")
                    .encode(
                        x=alt.X("collects:Q", title="收藏数"),
                        y=alt.Y("title:N", sort="-x", title="", axis=alt.Axis(labelLimit=220)),
                        tooltip=[
                            alt.Tooltip("title:N",    title="标题"),
                            alt.Tooltip("collects:Q", title="收藏",  format=","),
                            alt.Tooltip("reads:Q",    title="阅读",  format=","),
                            alt.Tooltip("likes:Q",    title="点赞",  format=","),
                        ],
                    )
                    .properties(title="收藏数 Top 10", height=300)
                ),
                use_container_width=True,
            )

    # ── 互动效率 ──────────────────────────────────────────────────────────────
    with sub_engagement:
        qualified = df[df["reads"] >= _MIN_READS].copy()
        if qualified.empty:
            st.info(f"阅读数≥{_MIN_READS} 的内容不足，无法计算有效互动率。")
        else:
            st.markdown("### 互动效率概览")
            ea, eb, ec, ed = st.columns(4)
            ea.metric("均点赞率",  f"{qualified['like_rate'].mean():.2%}",    help="点赞 / 阅读")
            eb.metric("均收藏率",  f"{qualified['collect_rate'].mean():.2%}", help="收藏 / 阅读")
            ec.metric("均评论率",  f"{qualified['comment_rate'].mean():.2%}", help="评论 / 阅读")
            ed.metric("均分享率",  f"{qualified['share_rate'].mean():.2%}",   help="分享 / 阅读")

            st.markdown("### 收藏率 × 点赞率分布")
            st.caption("高收藏高点赞 = 高价值干货 | 高点赞低收藏 = 情感共鸣 | 高收藏低点赞 = 实用参考")
            avg_cr = qualified["collect_rate"].mean()
            avg_lr = qualified["like_rate"].mean()

            scatter = (
                alt.Chart(qualified)
                .mark_circle(opacity=0.72)
                .encode(
                    x=alt.X("collect_rate:Q", title="收藏率", axis=alt.Axis(format="%")),
                    y=alt.Y("like_rate:Q",    title="点赞率", axis=alt.Axis(format="%")),
                    size=alt.Size("reads:Q", title="阅读量", scale=alt.Scale(range=[40, 600])),
                    color=alt.Color("reads:Q", scale=alt.Scale(scheme="blues"), title="阅读量"),
                    tooltip=[
                        alt.Tooltip("title:N",        title="标题"),
                        alt.Tooltip("collect_rate:Q", title="收藏率", format=".2%"),
                        alt.Tooltip("like_rate:Q",    title="点赞率", format=".2%"),
                        alt.Tooltip("reads:Q",        title="阅读量", format=","),
                        alt.Tooltip("collects:Q",     title="收藏数", format=","),
                    ],
                )
                .properties(height=400, title="收藏率 × 点赞率（气泡大小=阅读量）")
                .interactive()
            )
            vline = alt.Chart(pd.DataFrame({"x": [avg_cr]})).mark_rule(strokeDash=[4, 4], color="#94a3b8").encode(x="x:Q")
            hline = alt.Chart(pd.DataFrame({"y": [avg_lr]})).mark_rule(strokeDash=[4, 4], color="#94a3b8").encode(y="y:Q")
            st.altair_chart(_styled_chart(scatter + vline + hline), use_container_width=True)
            st.caption(f"虚线：收藏率均值 {avg_cr:.2%}，点赞率均值 {avg_lr:.2%}")

            col_c1, col_c2 = st.columns(2)
            with col_c1:
                top_cr = qualified.nlargest(10, "collect_rate")
                st.altair_chart(
                    _styled_chart(
                        alt.Chart(top_cr)
                        .mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4, color="#8b5cf6")
                        .encode(
                            x=alt.X("collect_rate:Q", title="收藏率", axis=alt.Axis(format="%")),
                            y=alt.Y("title:N", sort="-x", title="", axis=alt.Axis(labelLimit=220)),
                            tooltip=[
                                alt.Tooltip("title:N",        title="标题"),
                                alt.Tooltip("collect_rate:Q", title="收藏率", format=".2%"),
                                alt.Tooltip("reads:Q",        title="阅读",   format=","),
                                alt.Tooltip("collects:Q",     title="收藏数", format=","),
                            ],
                        )
                        .properties(title=f"收藏率 Top 10（阅读≥{_MIN_READS}）", height=300)
                    ),
                    use_container_width=True,
                )
            with col_c2:
                top_lr = qualified.nlargest(10, "like_rate")
                st.altair_chart(
                    _styled_chart(
                        alt.Chart(top_lr)
                        .mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4, color="#0ea5e9")
                        .encode(
                            x=alt.X("like_rate:Q", title="点赞率", axis=alt.Axis(format="%")),
                            y=alt.Y("title:N", sort="-x", title="", axis=alt.Axis(labelLimit=220)),
                            tooltip=[
                                alt.Tooltip("title:N",     title="标题"),
                                alt.Tooltip("like_rate:Q", title="点赞率", format=".2%"),
                                alt.Tooltip("reads:Q",     title="阅读",   format=","),
                                alt.Tooltip("likes:Q",     title="点赞数", format=","),
                            ],
                        )
                        .properties(title=f"点赞率 Top 10（阅读≥{_MIN_READS}）", height=300)
                    ),
                    use_container_width=True,
                )

            # Engagement rate distribution histogram
            st.markdown("---")
            st.markdown("### 综合互动率分布")
            hist = (
                alt.Chart(qualified)
                .mark_bar(color="#10b981", cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
                .encode(
                    x=alt.X("engagement_rate:Q", bin=alt.Bin(maxbins=20),
                             title="综合互动率（点赞+评论+收藏+分享 / 阅读）",
                             axis=alt.Axis(format="%")),
                    y=alt.Y("count():Q", title="内容数"),
                    tooltip=[
                        alt.Tooltip("engagement_rate:Q", bin=True, title="互动率区间", format=".1%"),
                        alt.Tooltip("count():Q", title="内容数"),
                    ],
                )
                .properties(height=240, title=f"综合互动率分布（阅读≥{_MIN_READS}）")
            )
            st.altair_chart(_styled_chart(hist), use_container_width=True)

    # ── 发布趋势 ──────────────────────────────────────────────────────────────
    with sub_trend:
        if "publish_date_dt" not in df.columns or df["publish_date_dt"].isna().all():
            st.info("当前数据中缺少发布日期，无法绘制趋势图。")
        else:
            trend_df = (
                df.groupby(df["publish_date_dt"].dt.date)
                .agg(内容数=("id", "count"), 总阅读=("reads", "sum"), 总点赞=("likes", "sum"))
                .reset_index()
                .rename(columns={"publish_date_dt": "发布日期"})
            )
            trend_df["发布日期"] = pd.to_datetime(trend_df["发布日期"])

            st.markdown("### 发布量与阅读趋势")
            base = alt.Chart(trend_df).encode(x=alt.X("发布日期:T", title="发布日期"))
            bar  = base.mark_bar(opacity=0.5, color="#94a3b8", size=12).encode(
                y=alt.Y("内容数:Q", title="发文数", axis=alt.Axis(grid=False))
            )
            line = base.mark_line(
                point=alt.OverlayMarkDef(filled=True, size=50), color="#0ea5e9", strokeWidth=2.5
            ).encode(y=alt.Y("总阅读:Q", title="总阅读"))
            st.altair_chart(
                _styled_chart(
                    alt.layer(bar, line)
                    .resolve_scale(y="independent")
                    .properties(height=300, title="每日发文数与总阅读趋势")
                ),
                use_container_width=True,
            )

            if "weekday_cn" in df.columns:
                st.markdown("---")
                st.markdown("### 发布日期规律（按星期）")
                wd_df = (
                    df.dropna(subset=["weekday_cn"])
                    .groupby("weekday_cn")
                    .agg(内容数=("id", "count"), 均阅读=("reads", "mean"), 均点赞=("likes", "mean"))
                    .round(0)
                    .reset_index()
                )
                wd_df["weekday_cn"] = pd.Categorical(
                    wd_df["weekday_cn"], categories=_WEEKDAY_ORDER, ordered=True
                )
                wd_df = wd_df.sort_values("weekday_cn")

                wc1, wc2 = st.columns(2)
                with wc1:
                    st.altair_chart(
                        _styled_chart(
                            alt.Chart(wd_df)
                            .mark_bar(color="#0ea5e9", cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
                            .encode(
                                x=alt.X("weekday_cn:N", sort=_WEEKDAY_ORDER, title="星期"),
                                y=alt.Y("内容数:Q", title="发文数"),
                                tooltip=[
                                    alt.Tooltip("weekday_cn:N", title="星期"),
                                    alt.Tooltip("内容数:Q"),
                                ],
                            )
                            .properties(height=220, title="各星期发文数")
                        ),
                        use_container_width=True,
                    )
                with wc2:
                    st.altair_chart(
                        _styled_chart(
                            alt.Chart(wd_df)
                            .mark_bar(color="#8b5cf6", cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
                            .encode(
                                x=alt.X("weekday_cn:N", sort=_WEEKDAY_ORDER, title="星期"),
                                y=alt.Y("均阅读:Q", title="平均阅读量"),
                                tooltip=[
                                    alt.Tooltip("weekday_cn:N", title="星期"),
                                    alt.Tooltip("均阅读:Q", title="平均阅读", format=".0f"),
                                ],
                            )
                            .properties(height=220, title="各星期平均阅读量")
                        ),
                        use_container_width=True,
                    )

    # ── 内容明细 ──────────────────────────────────────────────────────────────
    with sub_list:
        base_cols = ["title", "publish_date", "reads"]
        if content_type == "qa" and "plays" in df.columns:
            base_cols.append("plays")
        base_cols += ["likes", "favorites", "comments", "collects", "shares",
                      "like_rate", "collect_rate", "engagement_rate", "url"]
        display_cols = [c for c in base_cols if c in df.columns]
        display_df   = (
            df[display_cols]
            .rename(columns={**_COL_LABELS,
                             "like_rate": "点赞率", "collect_rate": "收藏率",
                             "engagement_rate": "综合互动率"})
            .sort_values("阅读", ascending=False)
        )
        col_cfg = {
            "标题":       st.column_config.TextColumn("标题", width="medium"),
            "发布日期":   st.column_config.TextColumn("发布日期"),
            "阅读":       st.column_config.NumberColumn("阅读",     format="%d"),
            "播放":       st.column_config.NumberColumn("播放",     format="%d"),
            "点赞":       st.column_config.NumberColumn("点赞",     format="%d"),
            "收藏":       st.column_config.NumberColumn("收藏",     format="%d"),
            "评论":       st.column_config.NumberColumn("评论",     format="%d"),
            "分享":       st.column_config.NumberColumn("分享",     format="%d"),
            "点赞率":     st.column_config.NumberColumn("点赞率",   format="%.2%"),
            "收藏率":     st.column_config.NumberColumn("收藏率",   format="%.2%"),
            "综合互动率": st.column_config.NumberColumn("综合互动率", format="%.2%"),
            "链接":       st.column_config.LinkColumn("链接"),
        }
        st.dataframe(display_df, use_container_width=True, hide_index=True, column_config=col_cfg)
        st.download_button(
            "导出 CSV",
            data=display_df.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"zhihu_{content_type}_{start}_{end}.csv",
            mime="text/csv",
        )


def page_zhihu_upload() -> None:
    client = st.session_state["client"]
    _page_hero("知乎数据")

    tab_article, tab_qa = st.tabs(["文章", "问答"])

    for tab, (label, content_type) in zip([tab_article, tab_qa], _CONTENT_TYPES.items()):
        with tab:
            _render_tab(client, label, content_type)
