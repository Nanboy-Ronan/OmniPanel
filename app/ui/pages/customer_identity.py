from __future__ import annotations
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from app.ui._helpers import _page_hero, show_api_error


def _platforms_label(platforms: list[str]) -> str:
    names = {"youzan": "有赞", "jd": "京东", "tmall": "天猫"}
    return " + ".join(names.get(p, p) for p in platforms)


def _clusters_df(clusters: list[dict]) -> pd.DataFrame:
    if not clusters:
        return pd.DataFrame()
    rows = []
    for c in clusters:
        rows.append({
            "覆盖平台": _platforms_label(c["platforms"]),
            "订单数": c["order_count"],
            "营收": c["revenue"],
            "首单日期": c["first_order_date"],
            "末单日期": c["last_order_date"],
            "客户标识": "; ".join(
                f"{pf}:{','.join(keys)}" for pf, keys in c["customer_keys"].items()
            ),
        })
    return pd.DataFrame(rows).sort_values("营收", ascending=False)


def page_customer_identity() -> None:
    client = st.session_state["client"]
    _page_hero("跨平台客户")
    st.caption("按手机号把有赞/京东/天猫的订单关联到同一个真实客户，揭示被现有「按平台」客户视图低估的复购率与客单价。")

    col1, col2 = st.columns([2, 2])
    start = col1.date_input("开始日期", value=date.today() - timedelta(days=90), key="ident_start")
    end = col2.date_input("结束日期", value=date.today(), key="ident_end")
    if start > end:
        st.error("开始日期不能晚于结束日期。")
        return

    r = client.identity_clusters(str(start), str(end))
    if r.status_code != 200:
        show_api_error(r)
        return

    data = r.json()
    exact = data["exact"]
    fuzzy = data["fuzzy"]

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("精确匹配客户数", exact["cluster_count"])
    m2.metric("精确匹配营收", f"¥{exact['total_revenue']:,.2f}")
    m3.metric("模糊匹配客户数", fuzzy["cluster_count"])
    m4.metric("模糊匹配营收", f"¥{fuzzy['total_revenue']:,.2f}")
    st.caption("⚠️ 精确匹配与模糊匹配的数字不应相加汇总——置信度不同，分开看。")

    tab_exact, tab_fuzzy = st.tabs(["精确匹配（有赞 ↔ 天猫）", "模糊匹配（含京东，置信度较低）"])

    with tab_exact:
        cross_platform = [c for c in exact["clusters"] if len(c["platforms"]) > 1]
        st.caption(f"共 {exact['cluster_count']} 个客户身份，其中 {len(cross_platform)} 个跨平台。")
        df = _clusters_df(exact["clusters"])
        if df.empty:
            st.info("当前筛选范围内没有数据。")
        else:
            st.dataframe(df, use_container_width=True, hide_index=True)

    with tab_fuzzy:
        st.warning(fuzzy.get("caveat", ""))
        df = _clusters_df(fuzzy["clusters"])
        if df.empty:
            st.info("当前筛选范围内没有数据。")
        else:
            st.dataframe(df, use_container_width=True, hide_index=True)
