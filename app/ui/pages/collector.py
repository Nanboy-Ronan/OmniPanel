from __future__ import annotations

import pandas as pd
import streamlit as st

from app.ui._helpers import _page_hero, show_api_error

_PLATFORM_LABEL = {"xhs": "小红书", "zhihu": "知乎"}
_STATUS_LABEL = {
    "running": "运行中",
    "success": "成功",
    "session_expired": "登录态过期",
    "download_failed": "下载失败",
    "upload_failed": "上传失败",
    "error": "未知错误",
}


def _sessions_section(client) -> None:
    st.markdown("#### 已保存的登录态")
    r = client.collector_sessions()
    if r.status_code != 200:
        show_api_error(r)
        return
    sessions = r.json()
    if not sessions:
        st.info("暂无已保存的登录态，请先在下方上传。")
    else:
        for s in sessions:
            label = s["account_name"] or _PLATFORM_LABEL.get(s["platform"], s["platform"])
            status = _STATUS_LABEL.get(s["last_run_status"], s["last_run_status"] or "尚未运行")
            col_info, col_del = st.columns([5, 1])
            col_info.markdown(
                f"**{_PLATFORM_LABEL.get(s['platform'], s['platform'])}** · {label} "
                f"— 最近运行：`{status}` · 更新于 {s['updated_at'][:19]}"
            )
            with col_del:
                st.markdown("<div class='danger-btn'>", unsafe_allow_html=True)
                if st.button("删除", key=f"collector-del-{s['platform']}-{s['account_id']}", use_container_width=True):
                    dr = client.delete_collector_session(s["platform"], s.get("account_id"))
                    if dr.status_code == 204:
                        st.success("已删除。")
                        st.rerun()
                    else:
                        show_api_error(dr)
                st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("#### 上传新的登录态")
    st.caption(
        "在自己的电脑上运行 `python -m app.collector bootstrap-login --platform xhs --out session.json`"
        "（知乎同理，无需 --account-id），有头浏览器扫码登录后会生成一个 JSON 文件，在此上传。"
    )
    with st.form("collector-session-upload", clear_on_submit=True):
        platform = st.selectbox("平台", options=["xhs", "zhihu"], format_func=lambda p: _PLATFORM_LABEL[p])
        account_id = None
        if platform == "xhs":
            acc_r = client.xhs_accounts()
            accounts = acc_r.json() if acc_r.status_code == 200 else []
            if accounts:
                acc_options = {a["name"]: a["id"] for a in accounts}
                acc_label = st.selectbox("小红书账号", list(acc_options.keys()))
                account_id = acc_options[acc_label]
            else:
                st.warning("请先在「小红书数据」页面创建账号。")
        uploaded = st.file_uploader("storage_state.json", type=["json"])
        submitted = st.form_submit_button("上传")
    if submitted:
        if uploaded is None:
            st.warning("请先选择文件。")
        elif platform == "xhs" and account_id is None:
            st.warning("小红书登录态必须关联一个账号。")
        else:
            r2 = client.upload_collector_session(uploaded.read(), uploaded.name, platform, account_id)
            if r2.status_code == 201:
                st.success("登录态已上传。")
                st.rerun()
            else:
                show_api_error(r2)


def _runs_section(client) -> None:
    st.markdown("#### 最近运行记录")
    r = client.collector_runs(limit=50)
    if r.status_code != 200:
        show_api_error(r)
        return
    runs = r.json()
    if not runs:
        st.info("暂无采集运行记录。")
        return
    df = pd.DataFrame(runs)
    df["platform"] = df["platform"].map(_PLATFORM_LABEL).fillna(df["platform"])
    df["status"] = df["status"].map(_STATUS_LABEL).fillna(df["status"])
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "platform": st.column_config.TextColumn("平台"),
            "account_id": st.column_config.NumberColumn("账号ID"),
            "content_type": st.column_config.TextColumn("内容类型"),
            "started_at": st.column_config.DatetimeColumn("开始时间", format="YYYY-MM-DD HH:mm:ss"),
            "finished_at": st.column_config.DatetimeColumn("结束时间", format="YYYY-MM-DD HH:mm:ss"),
            "status": st.column_config.TextColumn("状态"),
            "rows_upserted": st.column_config.NumberColumn("处理行数"),
            "error_message": st.column_config.TextColumn("错误信息", width="large"),
            "triggered_by": st.column_config.TextColumn("触发方式"),
        },
    )


def page_collector() -> None:
    _page_hero("自动采集")
    st.caption("小红书 / 知乎创作者后台自动导出与上传。首次使用需在本地扫码登录并上传登录态；过期后企微群会收到告警。")
    _sessions_section(st.session_state["client"])
    st.markdown("---")
    _runs_section(st.session_state["client"])
