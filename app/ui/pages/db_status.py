from __future__ import annotations
import pandas as pd
import streamlit as st

from app.ui._helpers import _page_hero, show_api_error, clear_cached_orders


def page_db_status() -> None:
    client = st.session_state["client"]
    _page_hero("数据库状态")
    r = client.db_status()
    if r.status_code != 200:
        show_api_error(r)
        return
    data = r.json()

    ready = data.get("analysis_ready", False)
    count = data.get("all_orders_count") or 0
    tables = data.get("tables") or []

    m1, m2, m3 = st.columns(3)
    m1.metric("状态", "就绪" if ready else "未就绪")
    m2.metric("总订单数", f"{count:,}")
    m3.metric("数据表数", len(tables))

    if ready:
        st.success("数据库已就绪，可进行数据分析。")
    else:
        st.warning("暂无订单数据，请先上传文件以开始分析。")

    missing = []
    missing.extend(data.get("missing_analysis_tables") or [])
    missing.extend(data.get("missing_analysis_mappings") or [])
    missing.extend(data.get("missing_analysis_columns") or [])
    if missing:
        st.error("缺失项：" + ", ".join(str(m) for m in missing))

    if tables:
        st.subheader("数据表")
        st.dataframe(
            pd.DataFrame({"表名": tables}),
            use_container_width=True,
            hide_index=True,
        )

    # 危险操作区
    st.markdown("<div class='danger-zone'>", unsafe_allow_html=True)
    st.markdown("**危险操作区**")
    st.caption(
        "清除数据库中所有订单数据、上传批次与日志记录。"
        "用户账号将保留。清除前将自动创建备份。"
    )
    if not st.session_state.get("confirm_clear_db"):
        st.markdown("<div class='danger-btn'>", unsafe_allow_html=True)
        if st.button("清除所有数据", key="clear_db_open"):
            st.session_state["confirm_clear_db"] = True
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)
    else:
        st.warning("此操作将永久删除所有订单数据，确定继续？")
        c_yes, c_no, _ = st.columns([1, 1, 6])
        if c_yes.button("确认清除", type="primary", key="clear_db_yes"):
            with st.spinner("正在清除数据库…"):
                r_clear = client.clear_db()
            if r_clear.status_code == 200:
                st.success("数据库已清除，备份已保存至服务器。")
                st.session_state["confirm_clear_db"] = False
                clear_cached_orders()
                st.rerun()
            else:
                show_api_error(r_clear, "数据库清除失败。")
                st.session_state["confirm_clear_db"] = False
        if c_no.button("取消", key="clear_db_no"):
            st.session_state["confirm_clear_db"] = False
            st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)
