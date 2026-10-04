from __future__ import annotations
from datetime import date
from decimal import Decimal, InvalidOperation
import pandas as pd
import streamlit as st

from app.ui._helpers import _page_hero, show_api_error, data_cache_expired, mark_data_cache_fetched, data_cache_caption

_PAGE_SIZE = 200
_FILTER_COLUMNS = {
    "order_id": "订单号", "order_date": "订单日期", "customer_key": "客户标识",
    "sku": "商品", "quantity": "数量", "price": "金额", "receiver": "收货人",
    "receiver_phone": "手机号", "province": "省份", "area": "地区",
    "full_address": "地址", "buyer_nick": "买家昵称", "coupon_name": "优惠券",
    "distributor": "分销员",
}


def page_data() -> None:
    client = st.session_state["client"]
    _page_hero("数据浏览")

    search = st.text_input("搜索全部订单", placeholder="订单号、商品、地区、客户…", key="orders_search")
    platform_label = st.selectbox("平台", ["全部", "有赞", "京东", "天猫"], key="orders_platform")
    platform = {"有赞": "youzan", "京东": "jd", "天猫": "tmall"}.get(platform_label)
    chosen_columns = st.multiselect(
        "按列筛选（作用于全部订单）", list(_FILTER_COLUMNS),
        format_func=lambda field: _FILTER_COLUMNS[field], key="orders_filter_columns",
        max_selections=12,
    )
    column_filters = []
    for field in chosen_columns:
        if field in {"quantity", "price", "order_date"}:
            low_col, high_col = st.columns(2)
            low = low_col.text_input(f"{_FILTER_COLUMNS[field]}从", key=f"orders_col_{field}_min").strip()
            high = high_col.text_input(f"{_FILTER_COLUMNS[field]}到", key=f"orders_col_{field}_max").strip()
            if not low and not high:
                continue
            try:
                parse = date.fromisoformat if field == "order_date" else Decimal
                parsed = [parse(value) for value in (low, high) if value]
                if field != "order_date" and not all(value.is_finite() for value in parsed):
                    raise ValueError
                if low and high and parsed[0] > parsed[1]:
                    raise ValueError
            except (ValueError, InvalidOperation):
                st.warning(f"{_FILTER_COLUMNS[field]}范围无效。日期请使用 YYYY-MM-DD，起始值不能大于结束值。")
                return
            column_filters.append({"field": field, **({"min": low} if low else {}), **({"max": high} if high else {})})
        else:
            value = st.text_input(f"{_FILTER_COLUMNS[field]}包含", key=f"orders_col_{field}").strip()
            if value:
                column_filters.append({"field": field, "value": value})
    filters = (search.strip(), platform, tuple(tuple(sorted(f.items())) for f in column_filters))
    if st.session_state.get("orders_filters") != filters:
        st.session_state["orders_filters"] = filters
        st.session_state["orders_offset"] = 0
        st.session_state.pop("orders_df", None)
        st.session_state.pop("orders_export_bytes", None)

    col_refresh, _ = st.columns([1, 5])
    refresh = col_refresh.button("刷新")
    if refresh:
        st.session_state.pop("orders_df", None)
    elif data_cache_expired("orders"):
        st.session_state.pop("orders_df", None)

    offset = st.session_state.get("orders_offset", 0)
    df = st.session_state.get("orders_df")
    if df is None:
        r = client.orders_all(limit=_PAGE_SIZE, offset=offset, search=filters[0],
                              platform=platform, column_filters=column_filters)
        if r.status_code != 200:
            show_api_error(r)
            return
        df = pd.DataFrame(r.json())
        st.session_state["orders_df"] = df
        st.session_state["orders_total_count"] = int(
            r.headers.get("X-Total-Count", len(df))
        )
        mark_data_cache_fetched("orders")

    data_cache_caption("orders")

    if df.empty:
        st.info("没有匹配的订单。" if filters[0] or platform or column_filters else "暂无数据，请先上传订单文件。")
        return

    total_count = st.session_state.get("orders_total_count", len(df))
    st.caption(f"共匹配 {total_count:,} 条；显示第 {offset + 1:,}–{offset + len(df):,} 条")
    prev_col, next_col, page_col, export_col = st.columns([1, 1, 2, 2])
    if prev_col.button("上一页", disabled=offset == 0):
        st.session_state["orders_offset"] = max(0, offset - _PAGE_SIZE)
        st.session_state.pop("orders_df", None)
        st.rerun()
    if next_col.button("下一页", disabled=offset + len(df) >= total_count):
        st.session_state["orders_offset"] = offset + _PAGE_SIZE
        st.session_state.pop("orders_df", None)
        st.rerun()
    page_col.download_button("下载当前页 CSV", df.to_csv(index=False).encode("utf-8-sig"),
                             "orders_current_page.csv", mime="text/csv")
    if export_col.button("准备导出全部匹配结果"):
        with st.spinner("正在生成导出文件…"):
            r_export = client.orders_export(search=filters[0], platform=platform,
                                            column_filters=column_filters)
        if r_export.status_code == 200:
            st.session_state["orders_export_bytes"] = r_export.content
        else:
            show_api_error(r_export, "导出失败。")
    if st.session_state.get("orders_export_bytes"):
        st.download_button("下载全部匹配结果 CSV", st.session_state["orders_export_bytes"],
                           "filtered_orders.csv", mime="text/csv")

    display_df = df.reset_index(drop=True)
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
        f"平台：{(raw_data.get('order') or {}).get('platform', '—')} · "
        f"订单号：{(raw_data.get('order') or {}).get('order_id', '—')} · "
        f"原始行数：{raw_data.get('row_count', len(raw_rows))}"
    )
    st.dataframe(pd.DataFrame(raw_rows), use_container_width=True)
