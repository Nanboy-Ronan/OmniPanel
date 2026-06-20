from __future__ import annotations
import json
import pandas as pd
import streamlit as st

from app.ui._helpers import _page_hero, show_api_error


def page_logs() -> None:
    client = st.session_state["client"]
    _page_hero("操作日志")
    r = client.list_logs()
    if r.status_code != 200:
        show_api_error(r)
        return
    df = pd.DataFrame(r.json())
    if df.empty:
        st.info("暂无日志。")
        return

    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"])

    col1, col2 = st.columns(2)
    email_filter = col1.text_input("按邮箱筛选")
    action_options = ["全部"] + sorted(df["action"].dropna().unique().tolist())
    action_filter = col2.selectbox("按操作类型筛选", action_options)

    if email_filter:
        df = df[df["email"].astype(str).str.contains(email_filter, case=False, na=False)]
    if action_filter != "全部":
        df = df[df["action"] == action_filter]

    if "detail" in df.columns:
        df["detail"] = df["detail"].apply(
            lambda x: json.dumps(x, ensure_ascii=False, indent=None) if isinstance(x, dict) else x
        )

    st.dataframe(
        df,
        use_container_width=True,
        column_config={
            "timestamp": st.column_config.DatetimeColumn("时间", format="YYYY-MM-DD HH:mm:ss"),
            "detail": st.column_config.TextColumn("详情", width="large"),
        },
        hide_index=True,
    )

    st.download_button(
        "导出 CSV",
        data=df.to_csv(index=False).encode("utf-8-sig"),
        file_name="operation_logs.csv",
        mime="text/csv",
    )
