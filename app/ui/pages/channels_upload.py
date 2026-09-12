"""WeChat Channels (视频号) upload + analytics Streamlit page.

Mirrors app/ui/pages/xhs_upload.py's shape (account management + upload +
tabs). Field set verified live against a real 视频号助手 export — see
app/db/etl/channels.py for the confirmed column list and metric semantics
(notably: 推荐 vs 喜欢 are two distinct "like" signals, and there's a
WeCom-integration engagement cluster no other collected platform has).
"""
from __future__ import annotations

from datetime import date, timedelta

import altair as alt
import pandas as pd
import streamlit as st

from app.ui._helpers import _page_hero, _styled_chart, show_api_error

_MIN_PLAYS = 50  # minimum plays required to include a video in rate-based rankings


def _load_accounts(client) -> list[dict]:
    try:
        r = client.wx_channels_accounts()
        return r.json() if r.status_code == 200 else []
    except Exception:
        return []


def _account_section(client, is_admin: bool) -> None:
    """Render the account management expander (admin only)."""
    if not is_admin:
        return
    with st.expander("账号管理", expanded=False):
        accounts = _load_accounts(client)

        if accounts:
            for acc in accounts:
                col_name, col_rename, col_del = st.columns([5, 1, 1])
                status_icon = "🟢" if acc["is_active"] else "⚫"
                col_name.markdown(f"{status_icon} **{acc['name']}** `id={acc['id']}`")
                if col_rename.button("改名", key=f"ch-rename-btn-{acc['id']}", use_container_width=True):
                    st.session_state[f"_ch_rename_{acc['id']}"] = True
                with col_del:
                    st.markdown("<div class='danger-btn'>", unsafe_allow_html=True)
                    if st.button("删除", key=f"ch-del-{acc['id']}", use_container_width=True):
                        st.session_state[f"_ch_del_confirm_{acc['id']}"] = True
                    st.markdown("</div>", unsafe_allow_html=True)

                if st.session_state.get(f"_ch_rename_{acc['id']}"):
                    new_name = st.text_input("新名称", value=acc["name"], key=f"ch-rename-input-{acc['id']}")
                    save_col, cancel_col, _ = st.columns([1, 1, 6])
                    if save_col.button("保存", key=f"ch-rename-save-{acc['id']}", type="primary"):
                        if not new_name.strip():
                            st.warning("名称不能为空。")
                        else:
                            r = client.rename_wx_channels_account(acc["id"], new_name.strip())
                            if r.status_code == 200:
                                st.success(f"已改名为「{new_name.strip()}」。")
                                st.session_state.pop(f"_ch_rename_{acc['id']}", None)
                                st.rerun()
                            else:
                                show_api_error(r)
                    if cancel_col.button("取消", key=f"ch-rename-cancel-{acc['id']}"):
                        st.session_state.pop(f"_ch_rename_{acc['id']}", None)
                        st.rerun()

                if st.session_state.get(f"_ch_del_confirm_{acc['id']}"):
                    st.warning(f"确认删除账号「{acc['name']}」及其全部视频数据？")
                    yes, no, _ = st.columns([1, 1, 6])
                    if yes.button("确认", key=f"ch-del-yes-{acc['id']}", type="primary"):
                        r = client.delete_wx_channels_account(acc["id"])
                        if r.status_code == 204:
                            st.success("已删除。")
                            st.session_state.pop(f"_ch_del_confirm_{acc['id']}", None)
                            st.rerun()
                        else:
                            show_api_error(r)
                    if no.button("取消", key=f"ch-del-no-{acc['id']}"):
                        st.session_state.pop(f"_ch_del_confirm_{acc['id']}", None)
                        st.rerun()
        else:
            st.info("还没有视频号账号，请先添加。")

        st.markdown("---")
        with st.form("channels-add-account"):
            new_name = st.text_input("账号名称", placeholder="例：示例品牌视频号")
            if st.form_submit_button("添加账号"):
                if not new_name.strip():
                    st.warning("请输入账号名称。")
                else:
                    r = client.create_wx_channels_account(new_name.strip())
                    if r.status_code == 201:
                        st.success(f"账号「{new_name.strip()}」已创建。")
                        st.rerun()
                    else:
                        show_api_error(r)


def _preprocess(df: pd.DataFrame) -> pd.DataFrame:
    numeric_cols = [
        "plays", "recommends", "likes_thumb", "comments", "shares", "new_fans",
        "forwards_chat_moments", "set_as_ringtone", "set_as_status", "set_as_moments_cover",
        "wecom_link_clicks", "wecom_link_click_users", "added_to_contacts", "added_to_contacts_users",
        "avg_watch_duration", "completion_rate",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
        else:
            df[col] = 0

    if "publish_date" in df.columns:
        df["publish_date_dt"] = pd.to_datetime(df["publish_date"], errors="coerce")

    plays_safe = df["plays"].replace(0, float("nan"))
    # 推荐(♡) and 喜欢(👍) are two distinct signals in 视频号 — summed here for
    # an "engagement" total the way other platforms' likes+comments+shares is.
    df["total_engagement"] = df["recommends"] + df["likes_thumb"] + df["comments"] + df["shares"]
    df["like_rate"] = ((df["recommends"] + df["likes_thumb"]) / plays_safe).round(4)
    df["engagement_rate"] = (df["total_engagement"] / plays_safe).round(4)
    df["fan_rate"] = (df["new_fans"] / plays_safe).round(4)
    return df


def _generate_insight(df: pd.DataFrame) -> str:
    total_posts = len(df)
    total_plays = int(df["plays"].sum())
    total_fans = int(df["new_fans"].sum())
    total_engagement = int(df["total_engagement"].sum())

    qualified = df[df["plays"] >= _MIN_PLAYS]
    avg_engagement_rate = qualified["engagement_rate"].mean() if not qualified.empty else float("nan")

    top_plays = df.sort_values("plays", ascending=False).iloc[0] if not df.empty else None
    top_fan = df.sort_values("new_fans", ascending=False).iloc[0] if not df.empty else None

    insight = "**🤖 数据洞察结论**：\n"
    insight += (
        f"- **大盘表现**：共发布 {total_posts} 条视频，累计播放 {total_plays:,} 次，"
        f"新增粉丝 {total_fans:,} 人，累计互动（推荐+喜欢+评论+分享）{total_engagement:,} 次。\n"
    )
    if pd.notna(avg_engagement_rate):
        insight += f"- **互动效率**：平均互动率 {avg_engagement_rate:.1%}（播放量≥{_MIN_PLAYS} 的视频）。\n"
    if top_plays is not None:
        insight += f"- **播放担当**：《{top_plays['title']}》播放量最高（{top_plays['plays']:.0f} 次）。"
    if top_fan is not None and top_fan["new_fans"] > 0:
        insight += f"\n- **涨粉担当**：《{top_fan['title']}》本期涨粉最多（{top_fan['new_fans']:.0f} 人）。"
    return insight


def page_channels_upload() -> None:
    client = st.session_state["client"]
    is_admin = st.session_state.get("is_admin", False)
    _page_hero("视频号数据", "微信视频号内容数据自动采集与分析")

    _account_section(client, is_admin)

    accounts = _load_accounts(client)
    if not accounts:
        st.warning("请管理员先在上方「账号管理」中添加视频号账号。")
        return

    acc_options = {a["name"]: a["id"] for a in accounts if a["is_active"]}
    if not acc_options:
        st.warning("暂无可用账号（所有账号均已停用）。")
        return

    selected_name = st.selectbox("选择账号", list(acc_options.keys()), key="channels_account_sel")
    selected_id = acc_options[selected_name]

    st.markdown("---")
    st.markdown("#### 上传导出文件")
    st.caption(
        "从视频号助手「数据中心 → 视频数据 → 单篇视频」页面点击「下载表格」导出，上传后自动 upsert。"
        "已有视频（按视频ID匹配）更新流量数据；未出现在本次文件中的视频保持不变。"
    )

    with st.form(f"channels-upload-form-{selected_id}", clear_on_submit=True):
        uploaded = st.file_uploader(
            "选择视频号导出文件",
            type=["xlsx", "xls", "csv", "json"],
            key=f"channels_file_uploader_{selected_id}",
        )
        submitted = st.form_submit_button("上传")
    if submitted:
        if uploaded is None:
            st.warning("请先选择文件。")
        else:
            with st.spinner("上传中…"):
                r = client.upload_channels(uploaded.read(), uploaded.name, selected_id)
            if r.status_code == 200:
                data = r.json()
                st.success(f"账号「{selected_name}」上传成功：共处理 **{data['total']}** 条视频。")
                st.session_state.pop(f"channels_posts_cache_{selected_id}", None)
            else:
                show_api_error(r, "上传失败。")

    st.markdown("---")
    st.markdown(f"#### {selected_name} · 视频数据")

    col1, col2 = st.columns(2)
    start = col1.date_input("开始日期", value=date.today() - timedelta(days=90), key="channels_start")
    end = col2.date_input("结束日期", value=date.today(), key="channels_end")
    if start > end:
        st.error("开始日期不能晚于结束日期。")
        return

    cache_key = f"channels_posts_cache_{selected_id}_{start}_{end}"
    if (
        st.session_state.get("_channels_cache_key") != cache_key
        or f"channels_posts_cache_{selected_id}" not in st.session_state
    ):
        r_posts = client.channels_posts(
            account_id=selected_id, start_date=str(start), end_date=str(end), limit=500
        )
        if r_posts.status_code != 200:
            show_api_error(r_posts)
            return
        st.session_state[f"channels_posts_cache_{selected_id}"] = r_posts.json()
        st.session_state["_channels_cache_key"] = cache_key

    posts = st.session_state[f"channels_posts_cache_{selected_id}"]
    if not posts:
        st.info("该时间段内暂无数据，请先上传文件。")
        return

    df = _preprocess(pd.DataFrame(posts))

    tab_overview, tab_engagement, tab_wecom, tab_trend, tab_list = st.tabs(
        ["概览分析", "互动效率", "企微联动", "发布趋势", "视频明细"]
    )

    # ── 概览分析 ──────────────────────────────────────────────────────────────
    with tab_overview:
        st.info(_generate_insight(df))

        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("视频数", len(df))
        m2.metric("总播放", f"{df['plays'].sum():,.0f}")
        m3.metric("总互动", f"{int(df['total_engagement'].sum()):,}")
        m4.metric("总涨粉", f"{df['new_fans'].sum():,.0f}")
        m5.metric("平均完播率", f"{df['completion_rate'].mean():.1%}" if df["completion_rate"].mean() else "—")

        col_a, col_b = st.columns(2)
        with col_a:
            top_plays = df.nlargest(10, "plays")
            st.altair_chart(
                _styled_chart(
                    alt.Chart(top_plays)
                    .mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4, color="#10b981")
                    .encode(
                        x=alt.X("plays:Q", title="播放量"),
                        y=alt.Y("title:N", sort="-x", title="", axis=alt.Axis(labelLimit=200)),
                        tooltip=[
                            alt.Tooltip("title:N", title="标题"),
                            alt.Tooltip("plays:Q", title="播放", format=","),
                            alt.Tooltip("new_fans:Q", title="涨粉", format=".0f"),
                        ],
                    )
                    .properties(title="按播放量 Top 10", height=300)
                ),
                use_container_width=True,
            )
        with col_b:
            top_fan = df.nlargest(10, "new_fans")
            st.altair_chart(
                _styled_chart(
                    alt.Chart(top_fan)
                    .mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4, color="#f43f5e")
                    .encode(
                        x=alt.X("new_fans:Q", title="新增粉丝数"),
                        y=alt.Y("title:N", sort="-x", title="", axis=alt.Axis(labelLimit=200)),
                        tooltip=[
                            alt.Tooltip("title:N", title="标题"),
                            alt.Tooltip("new_fans:Q", title="涨粉", format=".0f"),
                            alt.Tooltip("plays:Q", title="播放", format=","),
                        ],
                    )
                    .properties(title="按涨粉数 Top 10", height=300)
                ),
                use_container_width=True,
            )

    # ── 互动效率 ──────────────────────────────────────────────────────────────
    with tab_engagement:
        st.caption("视频号的「点赞」拆分为两个独立信号：推荐（♡ 图标）与喜欢（👍 图标），下方分开展示。")
        qualified = df[df["plays"] >= _MIN_PLAYS].copy()
        if qualified.empty:
            st.info(f"播放数≥{_MIN_PLAYS} 的视频不足，无法分析互动效率。")
        else:
            ea, eb, ec, ed = st.columns(4)
            ea.metric("均推荐率", f"{(qualified['recommends']/qualified['plays']).mean():.2%}", help="推荐 / 播放")
            eb.metric("均喜欢率", f"{(qualified['likes_thumb']/qualified['plays']).mean():.2%}", help="喜欢 / 播放")
            ec.metric("均互动率", f"{qualified['engagement_rate'].mean():.2%}", help="(推荐+喜欢+评论+分享) / 播放")
            ed.metric("均涨粉率", f"{qualified['fan_rate'].mean():.2%}", help="新粉 / 播放")

            top_er = qualified.nlargest(10, "engagement_rate")
            st.altair_chart(
                _styled_chart(
                    alt.Chart(top_er)
                    .mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4, color="#8b5cf6")
                    .encode(
                        x=alt.X("engagement_rate:Q", title="互动率", axis=alt.Axis(format="%")),
                        y=alt.Y("title:N", sort="-x", title="", axis=alt.Axis(labelLimit=200)),
                        tooltip=[
                            alt.Tooltip("title:N", title="标题"),
                            alt.Tooltip("engagement_rate:Q", title="互动率", format=".2%"),
                            alt.Tooltip("plays:Q", title="播放", format=","),
                        ],
                    )
                    .properties(title=f"互动率 Top 10（播放≥{_MIN_PLAYS}）", height=300)
                ),
                use_container_width=True,
            )

    # ── 企微联动（视频号独有，其他平台没有对应指标） ────────────────────────────
    with tab_wecom:
        st.caption("以下指标是视频号独有的企业微信联动行为，其他已接入平台没有对应字段。")
        w1, w2, w3, w4 = st.columns(4)
        w1.metric("转发聊天/朋友圈", f"{int(df['forwards_chat_moments'].sum()):,}")
        w2.metric("企微链接点击（次数/人数）", f"{int(df['wecom_link_clicks'].sum()):,} / {int(df['wecom_link_click_users'].sum()):,}")
        w3.metric("添加到通讯录（次数/人数）", f"{int(df['added_to_contacts'].sum()):,} / {int(df['added_to_contacts_users'].sum()):,}")
        w4.metric("设为铃声/状态/朋友圈封面", f"{int(df['set_as_ringtone'].sum())} / {int(df['set_as_status'].sum())} / {int(df['set_as_moments_cover'].sum())}")

        wecom_df = df[df["wecom_link_clicks"] > 0][
            ["title", "publish_date", "wecom_link_clicks", "wecom_link_click_users", "added_to_contacts", "added_to_contacts_users"]
        ]
        if wecom_df.empty:
            st.info("当前区间内没有产生企微链接点击的视频。")
        else:
            st.dataframe(wecom_df, use_container_width=True, hide_index=True)

    # ── 发布趋势 ──────────────────────────────────────────────────────────────
    with tab_trend:
        if "publish_date_dt" not in df.columns or df["publish_date_dt"].isna().all():
            st.info("当前数据中缺少发布日期，无法绘制趋势图。")
        else:
            trend_df = (
                df.groupby(df["publish_date_dt"].dt.date)
                .agg(视频数=("id", "count"), 总播放=("plays", "sum"), 总涨粉=("new_fans", "sum"))
                .reset_index()
                .rename(columns={"publish_date_dt": "发布日期"})
            )
            trend_df["发布日期"] = pd.to_datetime(trend_df["发布日期"])

            base = alt.Chart(trend_df).encode(x=alt.X("发布日期:T", title="发布日期"))
            bar = base.mark_bar(opacity=0.5, color="#94a3b8", size=12).encode(
                y=alt.Y("视频数:Q", title="发布数", axis=alt.Axis(grid=False))
            )
            line = base.mark_line(
                point=alt.OverlayMarkDef(filled=True, size=50), color="#10b981", strokeWidth=2.5
            ).encode(y=alt.Y("总播放:Q", title="总播放"))
            st.altair_chart(
                _styled_chart(
                    alt.layer(bar, line).resolve_scale(y="independent").properties(height=320, title="每日发布数与总播放趋势")
                ),
                use_container_width=True,
            )

    # ── 视频明细 ──────────────────────────────────────────────────────────────
    with tab_list:
        col_map = {
            "title": "标题",
            "publish_date": "发布日期",
            "plays": "播放",
            "recommends": "推荐",
            "likes_thumb": "喜欢",
            "comments": "评论",
            "shares": "分享",
            "new_fans": "涨粉",
            "forwards_chat_moments": "转发聊天/朋友圈",
            "avg_watch_duration": "平均播放时长(s)",
            "completion_rate": "完播率",
            "engagement_rate": "综合互动率",
        }
        show_cols = [c for c in col_map if c in df.columns]
        display_df = df[show_cols].rename(columns=col_map).sort_values("播放", ascending=False)
        st.dataframe(
            display_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "标题": st.column_config.TextColumn("标题", width="medium"),
                "发布日期": st.column_config.TextColumn("发布日期"),
                "播放": st.column_config.NumberColumn("播放", format="%d"),
                "推荐": st.column_config.NumberColumn("推荐", format="%d"),
                "喜欢": st.column_config.NumberColumn("喜欢", format="%d"),
                "评论": st.column_config.NumberColumn("评论", format="%d"),
                "分享": st.column_config.NumberColumn("分享", format="%d"),
                "涨粉": st.column_config.NumberColumn("涨粉", format="%d"),
                "转发聊天/朋友圈": st.column_config.NumberColumn("转发聊天/朋友圈", format="%d"),
                "平均播放时长(s)": st.column_config.NumberColumn("平均播放时长(s)", format="%.1f"),
                "完播率": st.column_config.NumberColumn("完播率", format="%.2%"),
                "综合互动率": st.column_config.NumberColumn("综合互动率", format="%.2%"),
            },
        )
        st.download_button(
            "导出 CSV",
            data=display_df.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"channels_{selected_name}_{start}_{end}.csv",
            mime="text/csv",
        )
