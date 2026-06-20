from __future__ import annotations
import streamlit as st
import pandas as pd

# Static field definitions: table → field → metadata
_FIELD_DEFS: dict[str, dict[str, dict]] = {
    "orders": {
        "id": {
            "zh": "主键（系统自增）",
            "type": "integer",
            "example": "1234",
            "youzan": "—",
            "jd": "—",
            "tmall": "—",
            "nullable": False,
        },
        "order_date": {
            "zh": "下单日期",
            "type": "date",
            "example": "2024-03-15",
            "youzan": "订单创建时间",
            "jd": "下单时间",
            "tmall": "订单创建时间",
            "nullable": False,
        },
        "order_id": {
            "zh": "原始平台订单号",
            "type": "text",
            "example": "E20240315001",
            "youzan": "订单号",
            "jd": "订单号",
            "tmall": "订单编号",
            "nullable": False,
        },
        "customer_key": {
            "zh": "客户唯一标识（手机号 / 地址）",
            "type": "text",
            "example": "138****8888",
            "youzan": "买家手机号",
            "jd": "联系电话",
            "tmall": "收货地址",
            "nullable": False,
        },
        "platform": {
            "zh": "来源平台",
            "type": "text",
            "example": "youzan",
            "youzan": "youzan",
            "jd": "jd",
            "tmall": "tmall",
            "nullable": False,
        },
        "sku": {
            "zh": "商品名称 / SKU",
            "type": "text",
            "example": "联名款 100g",
            "youzan": "全部商品名称",
            "jd": "商品名称",
            "tmall": "商品标题",
            "nullable": True,
        },
        "quantity": {
            "zh": "购买数量",
            "type": "integer",
            "example": "2",
            "youzan": "（从商品名称解析）",
            "jd": "订购数量",
            "tmall": "—",
            "nullable": True,
        },
        "price": {
            "zh": "实付金额（¥）",
            "type": "numeric",
            "example": "199.00",
            "youzan": "订单实付金额",
            "jd": "商家应收",
            "tmall": "买家应付货款",
            "nullable": True,
        },
        "receiver": {
            "zh": "收货人姓名",
            "type": "text",
            "example": "张三",
            "youzan": "收货人/提货人",
            "jd": "客户姓名",
            "tmall": "（收货地址解析）",
            "nullable": True,
        },
        "receiver_phone": {
            "zh": "收货人手机号",
            "type": "text",
            "example": "138****8888",
            "youzan": "收货人手机号/提货人手机号",
            "jd": "联系电话",
            "tmall": "—",
            "nullable": True,
        },
        "province": {
            "zh": "收货省份",
            "type": "text",
            "example": "广东省",
            "youzan": "收货人省份",
            "jd": "（客户地址解析）",
            "tmall": "（收货地址解析）",
            "nullable": True,
        },
        "area": {
            "zh": "收货城市/地区",
            "type": "text",
            "example": "深圳市南山区",
            "youzan": "收货人城市 + 地区",
            "jd": "（客户地址解析）",
            "tmall": "（收货地址解析）",
            "nullable": True,
        },
        "full_address": {
            "zh": "完整收货地址",
            "type": "text",
            "example": "广东省深圳市南山区XX路",
            "youzan": "详细收货地址/提货地址",
            "jd": "客户地址",
            "tmall": "收货地址",
            "nullable": True,
        },
        "buyer_nick": {
            "zh": "买家昵称",
            "type": "text",
            "example": "用户_abc123",
            "youzan": "买家昵称",
            "jd": "下单帐号",
            "tmall": "—",
            "nullable": True,
        },
        "coupon_name": {
            "zh": "优惠券名称",
            "type": "text",
            "example": "满200减50",
            "youzan": "优惠券码名称",
            "jd": "—",
            "tmall": "—",
            "nullable": True,
        },
        "distributor": {
            "zh": "分销员/导购",
            "type": "text",
            "example": "导购小王",
            "youzan": "分销员",
            "jd": "导购员账号",
            "tmall": "—",
            "nullable": True,
        },
    },
    "customers": {
        "customer_key": {
            "zh": "客户唯一标识（主键）",
            "type": "text",
            "example": "138****8888",
            "youzan": "买家手机号",
            "jd": "联系电话",
            "tmall": "收货地址",
            "nullable": False,
        },
        "platform": {
            "zh": "来源平台",
            "type": "text",
            "example": "youzan",
            "youzan": "youzan",
            "jd": "jd",
            "tmall": "tmall",
            "nullable": False,
        },
        "first_order_date": {
            "zh": "首次下单日期",
            "type": "date",
            "example": "2023-01-15",
            "youzan": "订单创建时间",
            "jd": "下单时间",
            "tmall": "订单创建时间",
            "nullable": False,
        },
    },
    "upload_batches": {
        "id": {
            "zh": "批次 ID（主键）",
            "type": "integer",
            "example": "42",
            "youzan": "—",
            "jd": "—",
            "tmall": "—",
            "nullable": False,
        },
        "filename": {
            "zh": "上传文件名",
            "type": "text",
            "example": "youzan_2024Q1.xlsx",
            "youzan": "—",
            "jd": "—",
            "tmall": "—",
            "nullable": False,
        },
        "platform": {
            "zh": "检测到的平台",
            "type": "text",
            "example": "youzan",
            "youzan": "—",
            "jd": "—",
            "tmall": "—",
            "nullable": False,
        },
        "uploaded_at": {
            "zh": "上传时间（UTC）",
            "type": "timestamp",
            "example": "2024-03-15 09:23:11",
            "youzan": "—",
            "jd": "—",
            "tmall": "—",
            "nullable": False,
        },
        "row_count": {
            "zh": "文件总行数",
            "type": "integer",
            "example": "2500",
            "youzan": "—",
            "jd": "—",
            "tmall": "—",
            "nullable": False,
        },
        "inserted_orders": {
            "zh": "新增订单数",
            "type": "integer",
            "example": "2100",
            "youzan": "—",
            "jd": "—",
            "tmall": "—",
            "nullable": False,
        },
        "duplicate_rows": {
            "zh": "重复跳过行数",
            "type": "integer",
            "example": "340",
            "youzan": "—",
            "jd": "—",
            "tmall": "—",
            "nullable": False,
        },
        "invalid_rows": {
            "zh": "拒绝行数（格式错误等）",
            "type": "integer",
            "example": "60",
            "youzan": "—",
            "jd": "—",
            "tmall": "—",
            "nullable": False,
        },
        "status": {
            "zh": "入库状态",
            "type": "text",
            "example": "completed",
            "youzan": "—",
            "jd": "—",
            "tmall": "—",
            "nullable": False,
        },
    },
}


_DF_COLUMNS = [
    "字段名", "中文说明", "类型", "示例值",
    "有赞原始列", "京东原始列", "天猫原始列", "非 null 覆盖率",
]


def _build_df(table: str, coverage: dict | None, search: str = "") -> pd.DataFrame:
    rows = []
    for field, meta in _FIELD_DEFS[table].items():
        q = search.lower()
        if q and q not in field.lower() and q not in meta["zh"].lower() and q not in meta["example"].lower():
            continue
        if not meta["nullable"]:
            cov_display = "N/A（非空列）"
        elif coverage is not None and field in coverage:
            cov_display = f"{coverage[field] * 100:.1f}%"
        else:
            cov_display = "—"
        rows.append({
            "字段名": field,
            "中文说明": meta["zh"],
            "类型": meta["type"],
            "示例值": meta["example"],
            "有赞原始列": meta["youzan"],
            "京东原始列": meta["jd"],
            "天猫原始列": meta["tmall"],
            "非 null 覆盖率": cov_display,
        })
    if not rows:
        return pd.DataFrame(columns=_DF_COLUMNS)
    return pd.DataFrame(rows)


def page_data_dictionary() -> None:
    client = st.session_state.get("client")

    st.markdown(
        "<div class='page-hero'>"
        "<div class='ph-icon'>≡</div>"
        "<div><div class='ph-title'>数据字典</div>"
        "<div class='ph-sub'>字段定义、平台映射与实时覆盖率 — 覆盖率 = 非 null 行数 ÷ 总订单行数</div>"
        "</div></div>",
        unsafe_allow_html=True,
    )

    # Fetch live coverage for orders table nullable columns
    coverage: dict | None = None
    total_rows = 0
    if client:
        try:
            r = client.field_coverage()
            if r.status_code == 200:
                payload = r.json()
                total_rows = payload.get("total_rows", 0)
                coverage = payload.get("columns") or {}
        except Exception:
            pass

    search = st.text_input(
        "搜索字段名 / 说明 / 示例值",
        placeholder="e.g. 价格、province、SKU、coupon…",
        key="dict_search",
    )

    tab_orders, tab_customers, tab_batches = st.tabs(
        ["orders（订单）", "customers（客户）", "upload_batches（批次）"]
    )

    col_cfg = {
        "字段名":        st.column_config.TextColumn(width="small"),
        "中文说明":      st.column_config.TextColumn(width="medium"),
        "类型":          st.column_config.TextColumn(width="small"),
        "示例值":        st.column_config.TextColumn(width="medium"),
        "有赞原始列":    st.column_config.TextColumn(width="medium"),
        "京东原始列":    st.column_config.TextColumn(width="medium"),
        "天猫原始列":    st.column_config.TextColumn(width="medium"),
        "非 null 覆盖率": st.column_config.TextColumn(width="small"),
    }

    with tab_orders:
        if total_rows:
            st.caption(f"orders 表共 {total_rows:,} 行（实时）")
        elif coverage is None:
            st.caption("数据库中暂无订单数据，覆盖率列显示 N/A。")
        df_orders = _build_df("orders", coverage, search)
        if df_orders.empty:
            st.info("没有匹配的字段。")
        else:
            st.dataframe(df_orders, use_container_width=True, hide_index=True, column_config=col_cfg)

    with tab_customers:
        df_customers = _build_df("customers", None, search)
        if df_customers.empty:
            st.info("没有匹配的字段。")
        else:
            st.dataframe(df_customers, use_container_width=True, hide_index=True, column_config=col_cfg)

    with tab_batches:
        df_batches = _build_df("upload_batches", None, search)
        if df_batches.empty:
            st.info("没有匹配的字段。")
        else:
            st.dataframe(df_batches, use_container_width=True, hide_index=True, column_config=col_cfg)
