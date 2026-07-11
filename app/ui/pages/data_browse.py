from __future__ import annotations
import pandas as pd
import streamlit as st

from app.ui._helpers import _page_hero, show_api_error

_PAGE_SIZE = 5000


def page_data() -> None:
    client = st.session_state["client"]
    _page_hero("数据浏览")

    col_refresh, _ = st.columns([1, 5])
    refresh = col_refresh.button("刷新")
    if refresh:
        st.session_state.pop("orders_df", None)
        st.session_state["orders_loaded_limit"] = _PAGE_SIZE

    loaded_limit = st.session_state.get("orders_loaded_limit", _PAGE_SIZE)
    df = st.session_state.get("orders_df")
    if df is None:
        r = client.orders_all(limit=loaded_limit)
        if r.status_code != 200:
            show_api_error(r)
            return
        df = pd.DataFrame(r.json())
        st.session_state["orders_df"] = df
        st.session_state["orders_total_count"] = int(
            r.headers.get("X-Total-Count", len(df))
        )
        st.session_state["orders_loaded_limit"] = loaded_limit

    if df.empty:
        st.info("暂无数据，请先上传订单文件。")
        return

    total_count = st.session_state.get("orders_total_count", len(df))
    if len(df) < total_count:
        info_col, load_col = st.columns([4, 1])
        info_col.info(
            f"已加载 {len(df):,} / 共 {total_count:,} 条订单（按日期排序）。"
            f"下方搜索和筛选仅作用于已加载的数据。"
        )
        if load_col.button(f"加载更多 (+{_PAGE_SIZE:,})", use_container_width=True):
            st.session_state["orders_loaded_limit"] = loaded_limit + _PAGE_SIZE
            st.session_state.pop("orders_df", None)
            st.rerun()

    # 搜索 + 列筛选
    search = st.text_input("搜索所有文本列", placeholder="例：山东省、订单号、SKU 名称…")
    chosen = st.multiselect("按列筛选", list(df.columns), placeholder="选择要添加筛选的列…")

    df_view = df.copy()
    if search:
        mask = (
            df_view.select_dtypes(include="object")
            .apply(lambda c: c.str.contains(search, case=False, na=False))
            .any(axis=1)
        )
        df_view = df_view[mask]

    for c in chosen:
        if pd.api.types.is_numeric_dtype(df_view[c]):
            col_min = float(df_view[c].min())
            col_max = float(df_view[c].max())
            if col_min < col_max:
                rng = st.slider(c, col_min, col_max, (col_min, col_max))
                df_view = df_view[(df_view[c] >= rng[0]) & (df_view[c] <= rng[1])]
        else:
            vals = sorted(df_view[c].dropna().unique())
            sel = st.multiselect(c, vals, default=vals, key=f"filter_{c}")
            df_view = df_view[df_view[c].isin(sel)]

    row_col, download_col = st.columns([3, 1])
    row_col.write(f"显示 **{len(df_view):,} / {len(df):,}** 条已加载记录")
    download_col.download_button(
        "下载 CSV",
        df_view.to_csv(index=False).encode("utf-8-sig"),
        "filtered_orders.csv",
        mime="text/csv",
    )

    display_df = df_view.reset_index(drop=True)
    selection = st.dataframe(
        display_df,
        use_container_width=True,
        on_select="rerun",
        selection_mode="single-row",
    )

    selected_rows = getattr(getattr(selection, "selection", None), "rows", [])
    if not selected_rows:
        st.caption("点击某行可查看原始平台记录。")
        return

    selected_order = display_df.iloc[selected_rows[0]]
    raw_response = client.order_raw(int(selected_order["id"]))
    st.subheader("原始平台记录")
    if raw_response.status_code != 200:
        show_api_error(raw_response, "无法加载原始平台记录。")
        return

    raw_data = raw_response.json()
    raw_rows = raw_data.get("rows") or []
    if not raw_rows:
        st.info("该订单暂无原始平台记录。")
        return

    st.caption(
        f"平台：{raw_data['order']['platform']} · "
        f"订单号：{raw_data['order']['order_id']} · "
        f"原始行数：{raw_data.get('row_count', len(raw_rows))}"
    )
    st.dataframe(pd.DataFrame(raw_rows), use_container_width=True)
