from __future__ import annotations

from datetime import date, timedelta

import altair as alt
import pandas as pd
import streamlit as st

from app.ui._helpers import _page_hero, _styled_chart, show_api_error

_MIN_VIEWS = 50  # minimum views required to include a post in rate-based rankings

_WEEKDAY_CN = {
    "Monday": "周一", "Tuesday": "周二", "Wednesday": "周三",
    "Thursday": "周四", "Friday": "周五", "Saturday": "周六", "Sunday": "周日",
}
_WEEKDAY_ORDER = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _load_accounts(client) -> list[dict]:
    try:
        r = client.xhs_accounts()
        return r.json() if r.status_code == 200 else []
    except Exception:
        return []


def _account_section(client, is_admin: bool) -> None:
    """Render the account management expander (admin only)."""
    if not is_admin:
        return
    with st.expander("账号管理", expanded=False):
        accounts = _load_accounts(client)
        _TYPE_LABEL = {"company": "公司号", "self_media": "自媒体号"}

        if accounts:
            for acc in accounts:
                col_name, col_rename, col_del = st.columns([5, 1, 1])
                status_icon = "🟢" if acc["is_active"] else "⚫"
                type_tag = _TYPE_LABEL.get(acc.get("account_type", "company"), "公司号")
                col_name.markdown(f"{status_icon} **{acc['name']}** `{type_tag}` `id={acc['id']}`")
                if col_rename.button("改名", key=f"xhs-rename-btn-{acc['id']}", use_container_width=True):
                    st.session_state[f"_xhs_rename_{acc['id']}"] = True
                with col_del:
                    st.markdown("<div class='danger-btn'>", unsafe_allow_html=True)
                    if st.button("删除", key=f"xhs-del-{acc['id']}", use_container_width=True):
                        st.session_state[f"_xhs_del_confirm_{acc['id']}"] = True
                    st.markdown("</div>", unsafe_allow_html=True)

                if st.session_state.get(f"_xhs_rename_{acc['id']}"):
                    new_name = st.text_input(
                        "新名称", value=acc["name"], key=f"xhs-rename-input-{acc['id']}"
                    )
                    save_col, cancel_col, _ = st.columns([1, 1, 6])
                    if save_col.button("保存", key=f"xhs-rename-save-{acc['id']}", type="primary"):
                        if not new_name.strip():
                            st.warning("名称不能为空。")
                        else:
                            r = client.rename_xhs_account(acc["id"], new_name.strip())
                            if r.status_code == 200:
                                st.success(f"已改名为「{new_name.strip()}」。")
                                st.session_state.pop(f"_xhs_rename_{acc['id']}", None)
                                st.rerun()
                            else:
                                show_api_error(r)
                    if cancel_col.button("取消", key=f"xhs-rename-cancel-{acc['id']}"):
                        st.session_state.pop(f"_xhs_rename_{acc['id']}", None)
                        st.rerun()

                if st.session_state.get(f"_xhs_del_confirm_{acc['id']}"):
                    st.warning(f"确认删除账号「{acc['name']}」及其全部文章？")
                    yes, no, _ = st.columns([1, 1, 6])
                    if yes.button("确认", key=f"xhs-del-yes-{acc['id']}", type="primary"):
                        r = client.delete_xhs_account(acc["id"])
                        if r.status_code == 204:
                            st.success("已删除。")
                            st.session_state.pop(f"_xhs_del_confirm_{acc['id']}", None)
                            st.rerun()
                        else:
                            show_api_error(r)
                    if no.button("取消", key=f"xhs-del-no-{acc['id']}"):
                        st.session_state.pop(f"_xhs_del_confirm_{acc['id']}", None)
                        st.rerun()
        else:
            st.info("还没有小红书账号，请先添加。")

        st.markdown("---")
        with st.form("xhs-add-account"):
            new_name = st.text_input("账号名称", placeholder="例：示例品牌小红书")
            new_type = st.radio(
                "账号类型",
                options=["company", "self_media"],
                format_func=lambda x: "公司号" if x == "company" else "自媒体号",
                horizontal=True,
            )
            if st.form_submit_button("添加账号"):
                if not new_name.strip():
                    st.warning("请输入账号名称。")
                else:
                    r = client.create_xhs_account(new_name.strip(), account_type=new_type)
                    if r.status_code == 201:
                        type_label = "公司号" if new_type == "company" else "自媒体号"
                        st.success(f"账号「{new_name.strip()}」（{type_label}）已创建。")
                        st.rerun()
                    else:
                        show_api_error(r)


def _preprocess(df: pd.DataFrame) -> pd.DataFrame:
    numeric_cols = [
        "impressions", "views", "cover_click_rate", "likes", "comments",
        "collects", "new_followers", "shares", "avg_watch_time", "danmu",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    if "publish_date" in df.columns:
        df["publish_date_dt"] = pd.to_datetime(df["publish_date"], errors="coerce")
        df["weekday_cn"] = df["publish_date_dt"].dt.day_name().map(_WEEKDAY_CN)

    views_safe = df["views"].replace(0, float("nan"))
    df["total_engagement"] = df["likes"] + df["comments"] + df["collects"] + df["shares"]
    df["collect_rate"]  = (df["collects"]      / views_safe).round(4)
    df["like_rate"]     = (df["likes"]          / views_safe).round(4)
    df["comment_rate"]  = (df["comments"]       / views_safe).round(4)
    df["follower_rate"] = (df["new_followers"]  / views_safe).round(4)
    df["engagement_rate"] = (df["total_engagement"] / views_safe).round(4)
    return df


def _generate_insight(df: pd.DataFrame) -> str:
    total_posts    = len(df)
    total_imp      = int(df["impressions"].sum())
    total_views    = int(df["views"].sum())
    total_followers = int(df["new_followers"].sum())
    avg_ctr        = df["cover_click_rate"].mean()

    qualified = df[df["views"] >= _MIN_VIEWS]
    avg_collect_rate = qualified["collect_rate"].mean() if not qualified.empty else float("nan")

    ctr_eval = "封面吸引力较强" if avg_ctr >= 0.03 else "封面点击率偏低，建议优化封面设计"

    top_follower = df.sort_values("new_followers", ascending=False).iloc[0]

    insight = "**🤖 数据洞察结论**：\n"
    insight += (
        f"- **大盘表现**：共发布 {total_posts} 篇笔记，累计曝光 {total_imp:,} 次，"
        f"观看 {total_views:,} 次，新增粉丝 {total_followers:,} 人。\n"
    )
    insight += f"- **封面效率**：封面点击率均值 {avg_ctr:.1%}，{ctr_eval}。\n"
    if pd.notna(avg_collect_rate):
        collect_eval = "内容干货价值较高。" if avg_collect_rate >= 0.05 else "收藏转化有提升空间。"
        insight += f"- **内容价值**：平均收藏率 {avg_collect_rate:.1%}（观看≥{_MIN_VIEWS} 的笔记），{collect_eval}\n"
    insight += f"- **涨粉担当**：《{top_follower['title']}》本期涨粉最多（{top_follower['new_followers']:.0f} 人）。"
    if not qualified.empty:
        top_c = qualified.sort_values("collect_rate", ascending=False).iloc[0]
        insight += f"\n- **收藏之星**：《{top_c['title']}》收藏率 {top_c['collect_rate']:.1%}（观看 {top_c['views']:.0f}，收藏 {top_c['collects']:.0f}）。"
    return insight


def page_xhs_upload() -> None:
    client   = st.session_state["client"]
    is_admin = st.session_state.get("is_admin", False)
    _page_hero("小红书数据")

    _account_section(client, is_admin)

    accounts = _load_accounts(client)
    if not accounts:
        st.warning("请管理员先在上方「账号管理」中添加小红书账号。")
        return

    _TYPE_LABEL_SHORT = {"company": "公司号", "self_media": "自媒体号"}
    acc_options = {
        f"{a['name']} [{_TYPE_LABEL_SHORT.get(a.get('account_type', 'company'), '公司号')}]": a["id"]
        for a in accounts if a["is_active"]
    }
    if not acc_options:
        st.warning("暂无可用账号（所有账号均已停用）。")
        return

    selected_label = st.selectbox("选择账号", list(acc_options.keys()), key="xhs_account_sel")
    selected_id    = acc_options[selected_label]
    selected_name  = selected_label  # used in section header & file export

    st.markdown("---")
    st.markdown("#### 上传导出文件")
    st.caption(
        "从小红书专业号后台「内容分析」导出 xlsx，上传后自动 upsert。"
        "已有文章更新流量数据；未出现在本次文件中的文章保持不变。"
    )

    with st.form(f"xhs-upload-form-{selected_id}", clear_on_submit=True):
        uploaded = st.file_uploader(
            "选择小红书 xlsx 文件",
            type=["xlsx", "xls"],
            key=f"xhs_file_uploader_{selected_id}",
        )
        submitted = st.form_submit_button("上传")
    if submitted:
        if uploaded is None:
            st.warning("请先选择文件。")
        else:
            with st.spinner("上传中…"):
                r = client.upload_xhs(uploaded.read(), uploaded.name, selected_id)
            if r.status_code == 200:
                data = r.json()
                st.success(f"账号「{selected_name}」上传成功：共处理 **{data['total']}** 篇笔记。")
                st.session_state.pop(f"xhs_posts_cache_{selected_id}", None)
            else:
                show_api_error(r, "上传失败。")

    st.markdown("---")
    st.markdown(f"#### {selected_name} · 笔记数据")

    col1, col2 = st.columns(2)
    start = col1.date_input("开始日期", value=date.today() - timedelta(days=90), key="xhs_start")
    end   = col2.date_input("结束日期", value=date.today(), key="xhs_end")
    if start > end:
        st.error("开始日期不能晚于结束日期。")
        return

    cache_key = f"xhs_posts_cache_{selected_id}_{start}_{end}"
    if (
        st.session_state.get("_xhs_cache_key") != cache_key
        or f"xhs_posts_cache_{selected_id}" not in st.session_state
    ):
        r_posts = client.xhs_posts(
            account_id=selected_id, start_date=str(start), end_date=str(end), limit=500
        )
        if r_posts.status_code != 200:
            show_api_error(r_posts)
            return
        st.session_state[f"xhs_posts_cache_{selected_id}"] = r_posts.json()
        st.session_state["_xhs_cache_key"] = cache_key

    posts = st.session_state[f"xhs_posts_cache_{selected_id}"]
    if not posts:
        st.info("该时间段内暂无数据，请先上传文件。")
        return

    df = _preprocess(pd.DataFrame(posts))

    tab_overview, tab_funnel, tab_engagement, tab_genre, tab_trend, tab_list = st.tabs(
        ["概览分析", "转化漏斗", "互动效率", "体裁对比", "发布趋势", "笔记明细"]
    )

    # ── 概览分析 ──────────────────────────────────────────────────────────────
    with tab_overview:
        st.info(_generate_insight(df))

        m1, m2, m3, m4, m5, m6 = st.columns(6)
        m1.metric("笔记数",       len(df))
        m2.metric("总曝光",       f"{df['impressions'].sum():,.0f}")
        m3.metric("总观看",       f"{df['views'].sum():,.0f}")
        m4.metric("封面点击率均值", f"{df['cover_click_rate'].mean():.1%}")
        m5.metric("总互动",       f"{int(df['total_engagement'].sum()):,}")
        m6.metric("总涨粉",       f"{df['new_followers'].sum():,.0f}")

        col_a, col_b = st.columns(2)
        with col_a:
            top_imp = df.nlargest(10, "impressions")
            st.altair_chart(
                _styled_chart(
                    alt.Chart(top_imp)
                    .mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4, color="#f43f5e")
                    .encode(
                        x=alt.X("impressions:Q", title="曝光量"),
                        y=alt.Y("title:N", sort="-x", title="", axis=alt.Axis(labelLimit=200)),
                        tooltip=[
                            alt.Tooltip("title:N", title="标题"),
                            alt.Tooltip("impressions:Q", title="曝光", format=","),
                            alt.Tooltip("views:Q", title="观看", format=","),
                            alt.Tooltip("new_followers:Q", title="涨粉", format=".0f"),
                        ],
                    )
                    .properties(title="按曝光量 Top 10", height=300)
                ),
                use_container_width=True,
            )
        with col_b:
            top_fol = df.nlargest(10, "new_followers")
            st.altair_chart(
                _styled_chart(
                    alt.Chart(top_fol)
                    .mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4, color="#10b981")
                    .encode(
                        x=alt.X("new_followers:Q", title="新增粉丝数"),
                        y=alt.Y("title:N", sort="-x", title="", axis=alt.Axis(labelLimit=200)),
                        tooltip=[
                            alt.Tooltip("title:N", title="标题"),
                            alt.Tooltip("new_followers:Q", title="涨粉", format=".0f"),
                            alt.Tooltip("views:Q", title="观看", format=","),
                            alt.Tooltip("follower_rate:Q", title="涨粉率", format=".2%"),
                        ],
                    )
                    .properties(title="按涨粉数 Top 10", height=300)
                ),
                use_container_width=True,
            )

    # ── 转化漏斗 ──────────────────────────────────────────────────────────────
    with tab_funnel:
        st.markdown("### 曝光 → 观看 → 互动 转化漏斗")
        total_imp  = df["impressions"].sum()
        total_v    = df["views"].sum()
        total_eng  = df["total_engagement"].sum()
        ctr_agg    = total_v  / total_imp  if total_imp  > 0 else 0
        eng_r_agg  = total_eng / total_v   if total_v   > 0 else 0

        fa, fb, fc, fd, fe = st.columns(5)
        fa.metric("总曝光",   f"{total_imp:,.0f}")
        fb.metric("封面点击率", f"{ctr_agg:.1%}", help="总观看 / 总曝光")
        fc.metric("总观看",   f"{total_v:,.0f}")
        fd.metric("互动转化率", f"{eng_r_agg:.1%}", help="总互动 / 总观看")
        fe.metric("总互动",   f"{total_eng:,.0f}")

        funnel_df = pd.DataFrame({
            "阶段": ["曝光", "观看", "互动"],
            "数量": [total_imp, total_v, total_eng],
        })
        st.altair_chart(
            _styled_chart(
                alt.Chart(funnel_df)
                .mark_bar(color="#f43f5e", cornerRadiusTopRight=4, cornerRadiusBottomRight=4)
                .encode(
                    x=alt.X("数量:Q", title="用户数"),
                    y=alt.Y("阶段:N", sort=["曝光", "观看", "互动"], title=""),
                    tooltip=[alt.Tooltip("阶段:N"), alt.Tooltip("数量:Q", format=",")],
                )
                .properties(height=160, title="整体转化漏斗（全期汇总）")
            ),
            use_container_width=True,
        )

        st.markdown("---")
        st.markdown("### 封面点击率分布")
        st.caption("仅统计曝光量≥100 的笔记，过滤掉噪声样本。")
        ctr_data = df[df["impressions"] >= 100].copy()
        if ctr_data.empty:
            st.info("曝光≥100 的笔记不足，无法分析封面点击率分布。")
        else:
            hist = (
                alt.Chart(ctr_data)
                .mark_bar(color="#f59e0b", cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
                .encode(
                    x=alt.X("cover_click_rate:Q", bin=alt.Bin(maxbins=20),
                             title="封面点击率", axis=alt.Axis(format="%")),
                    y=alt.Y("count():Q", title="笔记数"),
                    tooltip=[
                        alt.Tooltip("cover_click_rate:Q", bin=True, title="点击率区间", format=".1%"),
                        alt.Tooltip("count():Q", title="笔记数"),
                    ],
                )
                .properties(height=240, title="封面点击率分布（曝光≥100）")
            )
            st.altair_chart(_styled_chart(hist), use_container_width=True)

            top_ctr = ctr_data.nlargest(10, "cover_click_rate")
            st.altair_chart(
                _styled_chart(
                    alt.Chart(top_ctr)
                    .mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4, color="#f59e0b")
                    .encode(
                        x=alt.X("cover_click_rate:Q", title="封面点击率",
                                 axis=alt.Axis(format="%")),
                        y=alt.Y("title:N", sort="-x", title="", axis=alt.Axis(labelLimit=200)),
                        tooltip=[
                            alt.Tooltip("title:N", title="标题"),
                            alt.Tooltip("cover_click_rate:Q", title="封面点击率", format=".2%"),
                            alt.Tooltip("impressions:Q", title="曝光", format=","),
                            alt.Tooltip("views:Q", title="观看", format=","),
                        ],
                    )
                    .properties(title="封面点击率 Top 10", height=300)
                ),
                use_container_width=True,
            )

    # ── 互动效率 ──────────────────────────────────────────────────────────────
    with tab_engagement:
        qualified = df[df["views"] >= _MIN_VIEWS].copy()
        if qualified.empty:
            st.info(f"观看数≥{_MIN_VIEWS} 的笔记不足，无法分析互动效率。")
        else:
            st.markdown("### 互动效率分析")
            ea, eb, ec, ed = st.columns(4)
            ea.metric("均收藏率", f"{qualified['collect_rate'].mean():.2%}",  help="收藏 / 观看")
            eb.metric("均点赞率", f"{qualified['like_rate'].mean():.2%}",     help="点赞 / 观看")
            ec.metric("均评论率", f"{qualified['comment_rate'].mean():.2%}",  help="评论 / 观看")
            ed.metric("均涨粉率", f"{qualified['follower_rate'].mean():.2%}", help="新粉 / 观看")

            st.markdown("### 收藏率 × 点赞率分布")
            st.caption("高收藏高点赞 = 深度干货 | 高收藏低点赞 = 实用攻略 | 低收藏高点赞 = 情感共鸣 | 低收藏低点赞 = 待优化")
            avg_cr = qualified["collect_rate"].mean()
            avg_lr = qualified["like_rate"].mean()
            scatter = (
                alt.Chart(qualified)
                .mark_circle(opacity=0.72)
                .encode(
                    x=alt.X("collect_rate:Q", title="收藏率", axis=alt.Axis(format="%")),
                    y=alt.Y("like_rate:Q",    title="点赞率", axis=alt.Axis(format="%")),
                    size=alt.Size("views:Q", title="观看量", scale=alt.Scale(range=[40, 600])),
                    color=alt.Color("views:Q", scale=alt.Scale(scheme="reds"), title="观看量"),
                    tooltip=[
                        alt.Tooltip("title:N",        title="标题"),
                        alt.Tooltip("collect_rate:Q", title="收藏率",  format=".2%"),
                        alt.Tooltip("like_rate:Q",    title="点赞率",  format=".2%"),
                        alt.Tooltip("views:Q",        title="观看量",  format=","),
                        alt.Tooltip("new_followers:Q",title="涨粉",    format=".0f"),
                    ],
                )
                .properties(height=420, title="收藏率 × 点赞率（气泡大小=观看量）")
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
                            y=alt.Y("title:N", sort="-x", title="", axis=alt.Axis(labelLimit=200)),
                            tooltip=[
                                alt.Tooltip("title:N",        title="标题"),
                                alt.Tooltip("collect_rate:Q", title="收藏率", format=".2%"),
                                alt.Tooltip("views:Q",        title="观看",   format=","),
                                alt.Tooltip("collects:Q",     title="收藏数", format=".0f"),
                            ],
                        )
                        .properties(title=f"收藏率 Top 10（观看≥{_MIN_VIEWS}）", height=300)
                    ),
                    use_container_width=True,
                )
            with col_c2:
                top_fr = qualified.nlargest(10, "follower_rate")
                st.altair_chart(
                    _styled_chart(
                        alt.Chart(top_fr)
                        .mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4, color="#10b981")
                        .encode(
                            x=alt.X("follower_rate:Q", title="涨粉率", axis=alt.Axis(format="%")),
                            y=alt.Y("title:N", sort="-x", title="", axis=alt.Axis(labelLimit=200)),
                            tooltip=[
                                alt.Tooltip("title:N",         title="标题"),
                                alt.Tooltip("follower_rate:Q", title="涨粉率", format=".2%"),
                                alt.Tooltip("views:Q",         title="观看",   format=","),
                                alt.Tooltip("new_followers:Q", title="涨粉数", format=".0f"),
                            ],
                        )
                        .properties(title=f"涨粉率 Top 10（观看≥{_MIN_VIEWS}）", height=300)
                    ),
                    use_container_width=True,
                )

    # ── 体裁对比 ──────────────────────────────────────────────────────────────
    with tab_genre:
        genre_df = df.dropna(subset=["genre"]).copy() if "genre" in df.columns else pd.DataFrame()
        genre_df = genre_df[genre_df["genre"].str.strip() != ""]

        if genre_df.empty:
            st.info("当前数据中没有体裁字段，无法进行体裁对比分析。")
        else:
            st.markdown("### 体裁发布分布")
            genre_count = genre_df.groupby("genre").size().reset_index(name="笔记数")
            pie = (
                alt.Chart(genre_count)
                .mark_arc(innerRadius=60)
                .encode(
                    theta=alt.Theta("笔记数:Q"),
                    color=alt.Color("genre:N", legend=alt.Legend(title="体裁")),
                    tooltip=[alt.Tooltip("genre:N", title="体裁"), alt.Tooltip("笔记数:Q")],
                )
                .properties(height=260, title="各体裁笔记数分布")
            )
            st.altair_chart(_styled_chart(pie), use_container_width=True)

            st.markdown("### 各体裁平均表现对比")
            genre_agg = (
                genre_df.groupby("genre")
                .agg(
                    笔记数=("id", "count"),
                    均曝光=("impressions", "mean"),
                    均观看=("views", "mean"),
                    均封面点击率=("cover_click_rate", "mean"),
                    均点赞=("likes", "mean"),
                    均收藏=("collects", "mean"),
                    均涨粉=("new_followers", "mean"),
                )
                .round(2)
                .reset_index()
                .rename(columns={"genre": "体裁"})
                .sort_values("均观看", ascending=False)
            )
            st.dataframe(genre_agg, use_container_width=True, hide_index=True)

            views_chart = (
                alt.Chart(genre_agg)
                .mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4, color="#0ea5e9")
                .encode(
                    x=alt.X("均观看:Q", title="平均观看量"),
                    y=alt.Y("体裁:N", sort="-x", title=""),
                    tooltip=[
                        alt.Tooltip("体裁:N"),
                        alt.Tooltip("均观看:Q",  title="均观看",  format=".0f"),
                        alt.Tooltip("均收藏:Q",  title="均收藏",  format=".1f"),
                        alt.Tooltip("均涨粉:Q",  title="均涨粉",  format=".1f"),
                    ],
                )
                .properties(title="各体裁平均观看量", height=max(160, 50 * len(genre_agg)))
            )
            st.altair_chart(_styled_chart(views_chart), use_container_width=True)

            video_df = genre_df[genre_df["avg_watch_time"] > 0].copy()
            if not video_df.empty:
                st.markdown("---")
                st.markdown("### 视频内容：观看时长分析")
                st.caption("仅展示 avg_watch_time > 0 的笔记（视频类型）。")

                wt_agg = (
                    video_df.groupby("genre")
                    .agg(均观看时长=("avg_watch_time", "mean"), 笔记数=("id", "count"))
                    .round(1)
                    .reset_index()
                    .rename(columns={"genre": "体裁"})
                )
                wt_chart = (
                    alt.Chart(wt_agg)
                    .mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4, color="#f59e0b")
                    .encode(
                        x=alt.X("均观看时长:Q", title="平均观看时长（秒）"),
                        y=alt.Y("体裁:N", sort="-x"),
                        tooltip=[
                            alt.Tooltip("体裁:N"),
                            alt.Tooltip("均观看时长:Q", format=".1f"),
                            alt.Tooltip("笔记数:Q"),
                        ],
                    )
                    .properties(height=max(160, 50 * len(wt_agg)), title="各体裁平均观看时长（视频）")
                )
                st.altair_chart(_styled_chart(wt_chart), use_container_width=True)

                eligible = video_df[video_df["views"] >= _MIN_VIEWS]
                if not eligible.empty:
                    scatter_wt = (
                        alt.Chart(eligible)
                        .mark_circle(opacity=0.72, color="#f59e0b")
                        .encode(
                            x=alt.X("avg_watch_time:Q", title="平均观看时长（秒）"),
                            y=alt.Y("collect_rate:Q",   title="收藏率", axis=alt.Axis(format="%")),
                            size=alt.Size("views:Q", title="观看量", scale=alt.Scale(range=[40, 500])),
                            tooltip=[
                                alt.Tooltip("title:N",           title="标题"),
                                alt.Tooltip("avg_watch_time:Q",  title="观看时长(s)", format=".1f"),
                                alt.Tooltip("collect_rate:Q",    title="收藏率",      format=".2%"),
                                alt.Tooltip("views:Q",           title="观看量",      format=","),
                            ],
                        )
                        .properties(height=340, title="视频观看时长 × 收藏率相关性")
                        .interactive()
                    )
                    st.altair_chart(_styled_chart(scatter_wt), use_container_width=True)

    # ── 发布趋势 ──────────────────────────────────────────────────────────────
    with tab_trend:
        if "publish_date_dt" not in df.columns or df["publish_date_dt"].isna().all():
            st.info("当前数据中缺少发布日期，无法绘制趋势图。")
        else:
            trend_df = (
                df.groupby(df["publish_date_dt"].dt.date)
                .agg(笔记数=("id", "count"), 总曝光=("impressions", "sum"), 总观看=("views", "sum"))
                .reset_index()
                .rename(columns={"publish_date_dt": "发布日期"})
            )
            trend_df["发布日期"] = pd.to_datetime(trend_df["发布日期"])

            st.markdown("### 发布量与曝光趋势")
            base = alt.Chart(trend_df).encode(x=alt.X("发布日期:T", title="发布日期"))
            bar  = base.mark_bar(opacity=0.5, color="#94a3b8", size=12).encode(
                y=alt.Y("笔记数:Q", title="发文数", axis=alt.Axis(grid=False))
            )
            line = base.mark_line(
                point=alt.OverlayMarkDef(filled=True, size=50), color="#f43f5e", strokeWidth=2.5
            ).encode(y=alt.Y("总曝光:Q", title="总曝光"))
            st.altair_chart(
                _styled_chart(
                    alt.layer(bar, line)
                    .resolve_scale(y="independent")
                    .properties(height=320, title="每日发文数与总曝光趋势")
                ),
                use_container_width=True,
            )

            if "weekday_cn" in df.columns:
                st.markdown("---")
                st.markdown("### 发布日期规律（按星期）")
                wd_df = (
                    df.dropna(subset=["weekday_cn"])
                    .groupby("weekday_cn")
                    .agg(笔记数=("id", "count"), 均曝光=("impressions", "mean"), 均观看=("views", "mean"))
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
                            .mark_bar(color="#f43f5e", cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
                            .encode(
                                x=alt.X("weekday_cn:N", sort=_WEEKDAY_ORDER, title="星期"),
                                y=alt.Y("笔记数:Q", title="发文数"),
                                tooltip=[alt.Tooltip("weekday_cn:N", title="星期"), alt.Tooltip("笔记数:Q")],
                            )
                            .properties(height=220, title="各星期发文数")
                        ),
                        use_container_width=True,
                    )
                with wc2:
                    st.altair_chart(
                        _styled_chart(
                            alt.Chart(wd_df)
                            .mark_bar(color="#0ea5e9", cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
                            .encode(
                                x=alt.X("weekday_cn:N", sort=_WEEKDAY_ORDER, title="星期"),
                                y=alt.Y("均曝光:Q", title="平均曝光"),
                                tooltip=[
                                    alt.Tooltip("weekday_cn:N", title="星期"),
                                    alt.Tooltip("均曝光:Q", title="平均曝光", format=".0f"),
                                ],
                            )
                            .properties(height=220, title="各星期平均曝光量")
                        ),
                        use_container_width=True,
                    )

    # ── 笔记明细 ──────────────────────────────────────────────────────────────
    with tab_list:
        col_map = {
            "title":            "标题",
            "publish_date":     "发布日期",
            "genre":            "体裁",
            "impressions":      "曝光",
            "views":            "观看",
            "cover_click_rate": "封面点击率",
            "likes":            "点赞",
            "comments":         "评论",
            "collects":         "收藏",
            "shares":           "分享",
            "new_followers":    "涨粉",
            "collect_rate":     "收藏率",
            "like_rate":        "点赞率",
            "engagement_rate":  "综合互动率",
            "avg_watch_time":   "人均观看时长(s)",
        }
        show_cols  = [c for c in col_map if c in df.columns]
        display_df = (
            df[show_cols]
            .rename(columns=col_map)
            .sort_values("曝光", ascending=False)
        )
        st.dataframe(
            display_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "标题":         st.column_config.TextColumn("标题", width="medium"),
                "发布日期":     st.column_config.TextColumn("发布日期"),
                "体裁":         st.column_config.TextColumn("体裁"),
                "曝光":         st.column_config.NumberColumn("曝光",    format="%d"),
                "观看":         st.column_config.NumberColumn("观看",    format="%d"),
                "封面点击率":   st.column_config.NumberColumn("封面点击率",  format="%.2%"),
                "点赞":         st.column_config.NumberColumn("点赞",    format="%d"),
                "评论":         st.column_config.NumberColumn("评论",    format="%d"),
                "收藏":         st.column_config.NumberColumn("收藏",    format="%d"),
                "分享":         st.column_config.NumberColumn("分享",    format="%d"),
                "涨粉":         st.column_config.NumberColumn("涨粉",    format="%d"),
                "收藏率":       st.column_config.NumberColumn("收藏率",  format="%.2%"),
                "点赞率":       st.column_config.NumberColumn("点赞率",  format="%.2%"),
                "综合互动率":   st.column_config.NumberColumn("综合互动率", format="%.2%"),
                "人均观看时长(s)": st.column_config.NumberColumn("人均观看时长(s)", format="%.1f"),
            },
        )
        st.download_button(
            "导出 CSV",
            data=display_df.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"xhs_{selected_name}_{start}_{end}.csv",
            mime="text/csv",
        )
