from __future__ import annotations

import streamlit as st
import streamlit.components.v1 as components

from app.ui._helpers import _page_hero, show_api_error


def page_weekly_report() -> None:
    client = st.session_state["client"]
    _page_hero("周报", "公众号 + 小红书 每周数据报告")

    if st.session_state.get("is_admin"):
        if st.button("立即生成/重试本周周报"):
            with st.spinner("生成中，可能需要一点时间…"):
                r = client.trigger_weekly_report()
            if r.status_code != 200:
                show_api_error(r)
            else:
                st.success("已生成，企微通知已发送（如已配置）。")
                st.rerun()

    r = client.weekly_reports()
    if r.status_code != 200:
        show_api_error(r)
        return
    runs = r.json()
    if not runs:
        st.info("还没有生成过周报。")
        return

    options = {
        f"{run['week_start']} ~ {run['week_end']}（{run['status']}）": run["id"]
        for run in runs
    }
    label = st.selectbox("选择周期", list(options.keys()))
    run_id = options[label]

    detail_r = client.weekly_report_detail(run_id)
    if detail_r.status_code != 200:
        show_api_error(detail_r)
        return
    detail = detail_r.json()

    if detail["status"] != "success" or not detail.get("html_content"):
        st.error(f"这一期周报生成失败：{detail.get('error_message') or '未知错误'}")
        return

    components.html(detail["html_content"], height=2400, scrolling=True)
