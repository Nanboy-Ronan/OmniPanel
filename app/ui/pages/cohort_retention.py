from __future__ import annotations
from datetime import date

import altair as alt
import pandas as pd
import streamlit as st

from app.ui._helpers import _page_hero, _styled_chart, show_api_error


def _long_dataframe(cohorts: list[dict], field: str) -> pd.DataFrame:
    """Flatten cohorts into a long DataFrame, dropping censored (None) cells."""
    records = []
    for c in cohorts:
        for offset, value in enumerate(c[field]):
            if value is None:
                continue
            records.append({
                "cohort_month": c["cohort_month"],
                "offset": offset,
                "value": value,
                "cohort_size": c["cohort_size"],
            })
    return pd.DataFrame(records)


def page_cohort_retention() -> None:
    client = st.session_state["client"]
    _page_hero("客户留存")
    st.caption("按首单月份把客户分成队列（cohort），观察留存随月份推移的衰减形状，看出流失发生在哪个节点。")

    today = date.today()
    default_start = date(today.year - 1, today.month, 1)

    c1, c2, c3, c4 = st.columns([2, 2, 1.4, 1.6])
    start = c1.date_input("起始队列月", value=default_start, key="cohort_start")
    end = c2.date_input("截止队列月", value=today, key="cohort_end")
    platform = c3.selectbox("平台", ["全部", "youzan", "jd", "tmall"], key="cohort_platform")
    pf = None if platform == "全部" else platform
    max_offset = c4.slider("最大月偏移", min_value=3, max_value=24, value=12, key="cohort_max_offset")

    lens = st.radio(
        "口径", ["累计回归曲线", "逐期留存三角"], horizontal=True, key="cohort_lens",
        help="累计：到第 N 个月为止已回购过的比例；逐期：在第 N 个日历月仍下单的比例。",
    )

    if start > end:
        st.error("起始不能晚于截止。")
        return

    r = client.cohort_retention(str(start), str(end), pf, max_offset)
    if r.status_code != 200:
        show_api_error(r)
        return

    data = r.json()
    cohorts = data["cohorts"]
    if not cohorts:
        st.info("当前筛选范围内没有队列数据。请先上传订单或放宽日期范围。")
        return

    st.caption(
        f"最新数据月：{data['latest_data_month']}。"
        "空白单元格＝该队列尚未到达观测期（右删失），并非 0；最近一个月数据可能不完整。"
    )

    if lens == "逐期留存三角":
        df = _long_dataframe(cohorts, "per_period")
        heat = (
            alt.Chart(df)
            .mark_rect()
            .encode(
                x=alt.X("offset:O", title="距首单月（月）"),
                y=alt.Y("cohort_month:O", title="队列月", sort="descending"),
                color=alt.Color("value:Q", title="留存率",
                                scale=alt.Scale(scheme="blues"),
                                legend=alt.Legend(format=".0%")),
                tooltip=[
                    alt.Tooltip("cohort_month:O", title="队列月"),
                    alt.Tooltip("cohort_size:Q", title="队列规模"),
                    alt.Tooltip("offset:O", title="月偏移"),
                    alt.Tooltip("value:Q", title="留存率", format=".1%"),
                ],
            )
            .properties(height=max(240, 26 * len(cohorts)), title="逐期留存热力图")
        )
        st.altair_chart(_styled_chart(heat), use_container_width=True)
    else:
        df = _long_dataframe(cohorts, "cumulative")
        line = (
            alt.Chart(df)
            .mark_line(point=True)
            .encode(
                x=alt.X("offset:O", title="距首单月（月）"),
                y=alt.Y("value:Q", title="累计回购比例", axis=alt.Axis(format=".0%")),
                color=alt.Color("cohort_month:N", title="队列月"),
                tooltip=[
                    alt.Tooltip("cohort_month:N", title="队列月"),
                    alt.Tooltip("offset:O", title="月偏移"),
                    alt.Tooltip("value:Q", title="累计回购", format=".1%"),
                ],
            )
            .properties(height=420, title="累计回购回归曲线（每条线一个队列）")
        )
        st.altair_chart(_styled_chart(line), use_container_width=True)

    # ── Cohort size reference table ──────────────────────────────────────────
    with st.expander("各队列规模", expanded=False):
        size_df = pd.DataFrame(
            [{"队列月": c["cohort_month"], "新客户数": c["cohort_size"]} for c in cohorts]
        )
        st.dataframe(size_df, use_container_width=True, hide_index=True)
