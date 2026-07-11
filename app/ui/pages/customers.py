from __future__ import annotations
from datetime import date, timedelta

import altair as alt
import pandas as pd
import streamlit as st

from app.ui._helpers import PALETTE, _page_hero, _styled_chart, show_api_error

_PAGE_SIZE = 2000


def page_customers() -> None:
    client = st.session_state["client"]

    def _short_text(value, limit: int = 36) -> str:
        text = "" if pd.isna(value) else str(value).strip()
        return text if len(text) <= limit else text[: limit - 3] + "..."

    def _customer_label(row) -> str:
        name = _short_text(row.get("receiver"), 18)
        region = " ".join(
            part
            for part in (
                _short_text(row.get("province"), 10),
                _short_text(row.get("area"), 10),
            )
            if part
        )
        label = " / ".join(part for part in (name, region) if part)
        return label or _short_text(row.get("buyer_nick"), 36) or _short_text(row.get("mobile"), 20) or "Unknown customer"

    _page_hero("客户管理")
    col1, col2, col3, col4 = st.columns([2, 2, 1, 2])
    start = col1.date_input("开始日期", value=date.today() - timedelta(days=30), key="cust_start")
    end = col2.date_input("结束日期", value=date.today(), key="cust_end")
    min_orders = col3.number_input("最少订单数", min_value=1, value=1, key="cust_min_orders")
    platform_options = ["全部", "youzan", "jd", "tmall"]
    selected_platform = col4.selectbox("平台", platform_options, index=0, key="cust_platform")
    pf = None if selected_platform == "全部" else selected_platform
    if start > end:
        st.error("开始日期不能晚于结束日期。")
        return

    # Reset the loaded page size whenever the filters themselves change —
    # otherwise switching platforms keeps an inflated limit from a previous,
    # unrelated "加载更多" click.
    filter_sig = (str(start), str(end), int(min_orders), pf)
    if st.session_state.get("cust_filter_sig") != filter_sig:
        st.session_state["cust_filter_sig"] = filter_sig
        st.session_state["cust_loaded_limit"] = _PAGE_SIZE
    loaded_limit = st.session_state["cust_loaded_limit"]

    r = client.customers(str(start), str(end), int(min_orders), pf, limit=loaded_limit)
    if r.status_code != 200:
        show_api_error(r)
        return
    df = pd.DataFrame(r.json())
    total_count = int(r.headers.get("X-Total-Count", len(df)))
    if df.empty:
        st.info("未找到符合条件的客户。")
        return
    df["customer"] = df.apply(_customer_label, axis=1)

    if len(df) < total_count:
        info_col, load_col = st.columns([4, 1])
        info_col.info(
            f"已加载 {len(df):,} / 共 {total_count:,} 位符合条件的客户（按消费额排序）。"
            f"下方搜索仅作用于已加载的客户。"
        )
        if load_col.button(f"加载更多 (+{_PAGE_SIZE:,})", use_container_width=True):
            st.session_state["cust_loaded_limit"] = loaded_limit + _PAGE_SIZE
            st.rerun()

    search_col, export_col = st.columns([4, 1])
    search = search_col.text_input("搜索客户（手机号 / 地址 / 姓名）", key="cust_search")
    if search:
        searchable_cols = [
            c for c in ("customer_key", "phone", "receiver", "province", "area", "full_address", "buyer_nick")
            if c in df.columns
        ]
        mask = (
            df[searchable_cols]
            .astype(str)
            .apply(lambda c: c.str.contains(search, case=False, na=False))
            .any(axis=1)
        )
        df = df[mask]

    display_cols = [
        "customer", "first_date", "last_date", "orders", "revenue",
        "receiver", "phone", "province", "area", "full_address",
        "buyer_nick", "coupon_name", "distributor",
    ]
    df_display = df[[c for c in display_cols if c in df.columns]]
    export_col.markdown("<div style='height:1.85rem'></div>", unsafe_allow_html=True)
    export_col.download_button(
        "导出 CSV",
        df_display.to_csv(index=False).encode("utf-8-sig"),
        "customers.csv",
        mime="text/csv",
        use_container_width=True,
    )
    st.dataframe(
        df_display,
        use_container_width=True,
        column_config={
            "customer": st.column_config.TextColumn("客户", width="medium"),
            "phone": st.column_config.TextColumn("手机号", width="small"),
            "full_address": st.column_config.TextColumn("收货地址", width="large"),
            "revenue": st.column_config.NumberColumn("消费额", format="¥%.2f"),
        },
        hide_index=True,
    )

    # 省份分布
    if "province" in df.columns:
        with st.expander("按省份分客户", expanded=False):
            counts = (
                df.groupby("province")["customer_key"].nunique()
                .reset_index(name="customers")
                .sort_values("customers", ascending=False)
            )
            left, right = st.columns([1, 2])
            left.dataframe(counts, use_container_width=True, hide_index=True)
            prov_chart = (
                alt.Chart(counts)
                .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4, color="#0ea5e9")
                .encode(
                    x=alt.X("province:N", sort="-y", title="省份"),
                    y=alt.Y("customers:Q", title="客户数"),
                    tooltip=["province", "customers"],
                )
            )
            right.altair_chart(_styled_chart(prov_chart), use_container_width=True)

    # 客户详情
    st.subheader("客户详情")
    if df.empty:
        return
    customer_labels = dict(zip(df["customer_key"].astype(str), df["customer"]))
    customer_ids = ["—"] + df["customer_key"].astype(str).tolist()
    choice = st.selectbox(
        "选择客户",
        customer_ids,
        format_func=lambda key: customer_labels.get(key, key),
        key="cust_select",
    )
    if not choice or choice == "—":
        return

    all_time = st.checkbox("显示全部订单历史（忽略日期筛选）", value=False, key="cust_all_time")
    if all_time:
        r2 = client.customer_orders(choice)
    else:
        r2 = client.customer_orders(choice, str(start), str(end))

    if r2.status_code != 200:
        show_api_error(r2)
        return

    data = r2.json()
    orders_df = pd.DataFrame(data["orders"])
    order_cols = [
        "order_date", "sku", "quantity", "price",
        "receiver", "receiver_phone", "province", "area",
        "full_address", "buyer_nick", "coupon_name", "distributor",
    ]
    orders_df = orders_df[[c for c in order_cols if c in orders_df.columns]]

    m1, m2 = st.columns(2)
    m1.metric("订单数", data["count"])
    m2.metric("累计消费", f"¥{data['total_spend']:,.2f}")

    st.dataframe(
        orders_df,
        use_container_width=True,
        column_config={"price": st.column_config.NumberColumn("Price", format="¥%.2f")},
        hide_index=True,
    )

    by_day = (
        orders_df.assign(order_date=pd.to_datetime(orders_df["order_date"]))
        .groupby("order_date")["price"]
        .sum()
        .reset_index(name="revenue")
    )
    if not by_day.empty and len(by_day) > 1:
        line = (
            alt.Chart(by_day)
            .mark_line(point=alt.OverlayMarkDef(filled=True, size=60), color=PALETTE["old"])
            .encode(
                x=alt.X("order_date:T", title="日期"),
                y=alt.Y("revenue:Q", title="消费额（¥）"),
                tooltip=["order_date:T", alt.Tooltip("revenue:Q", format=",.2f")],
            )
            .properties(title="消费趋势")
        )
        st.altair_chart(_styled_chart(line), use_container_width=True)
